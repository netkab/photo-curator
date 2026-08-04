"""Duplicate / near-duplicate detection.

Layer 1 (exact bytes) is handled at ingest by the ``sha256`` unique constraint. This module adds:
  * Layer 2 — perceptual hash (resizes / recompresses / minor edits) via Hamming distance.
  * Layer 3 — CLIP embeddings (bursts / crops / semantic near-dupes), optional & GPU-backed.

For each group it auto-picks a keeper (resolution x sharpness x face count) and records the rest.
Nothing is deleted — review-actions are created elsewhere when the user approves a group.

Two properties this module has to guarantee, both learned the hard way:

**Each photo belongs to at most one group.** The layers used to run independently and persist their
findings separately, so any pair caught by *both* pHash and CLIP produced two identical groups —
2,280 of them on a real library. Layers now run strongest-first over the photos still unassigned.

**Groups are seeded, not chained.** Clustering used transitive union-find, i.e. single linkage:
A≈B and B≈C put A and C in one group even when A and C are nothing alike. Over 28k photos at cosine
0.92 that chained into groups of 687 members — and "trash the duplicates" on such a group means
binning 686 distinct photos to keep one. Each group is now everything similar to a single seed, so
membership always means "close to *this* photo" rather than "reachable from it".
"""
from __future__ import annotations

from typing import Callable, Iterable

from ..config import settings
from ..db import session_scope
from ..models import DupGroup, DupMember, Face, Media
from . import quality


def _hamming_hex(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _seed_clusters(ids: list[int], neighbours: Callable[[int, Iterable[int]], list[int]],
                   rank: Callable[[int], float]) -> list[list[int]]:
    """Greedy seed-based clustering — the complete-linkage-ish alternative to union-find.

    Takes the best remaining photo as a seed, claims everything similar *to that seed*, and repeats.
    No transitivity, so a chain of gradual similarity can never collapse into one giant group.

    ``neighbours(seed, pool)`` returns the members of ``pool`` similar to ``seed``.
    ``rank`` orders seed selection so the best copy anchors its group (and becomes its keeper).
    """
    remaining = set(ids)
    out: list[list[int]] = []
    for seed in sorted(ids, key=rank, reverse=True):
        if seed not in remaining:
            continue
        remaining.discard(seed)
        near = [i for i in neighbours(seed, remaining) if i in remaining]
        if not near:
            continue
        remaining.difference_update(near)
        out.append([seed, *near])
    return out


def _quality_score(m: Media, face_counts: dict[int, int]) -> float:
    """Cheap rank used only to choose clustering *seeds*.

    Keeper selection uses ``pipeline.quality``, which scores relatively within a group. Seeding
    just needs a stable order over the whole library before any group exists, so it stays simple.
    """
    res = (m.width or 0) * (m.height or 0)
    sharp = m.blur_score or 0.0
    faces = face_counts.get(m.id, 0)
    return res / 1_000_000.0 + sharp / 100.0 + faces * 0.5


def run(use_clip: bool = True, progress: Callable[[float, str], None] | None = None) -> dict:
    summary = {"phash_groups": 0, "clip_groups": 0, "candidates_to_delete": 0}
    with session_scope() as s:
        # Reset previous auto-detected groups (keep user-reviewed ones).
        for g in s.query(DupGroup).filter(DupGroup.reviewed.is_(False)).all():
            s.delete(g)
        s.flush()

        photos = s.query(Media).filter(Media.media_type == "photo", Media.phash.isnot(None)).all()
        # A hash of all-zeros (or all-ones) carries no information — it comes from an unreadable or
        # entirely uniform image, not from a picture. Every such row is within Hamming 0 of every
        # other, so leaving them in produced one meaningless "duplicate group" of 116 unrelated
        # files. Drop them rather than let a degenerate signature stand in for a signal.
        degenerate = {0, 64}
        skipped_degenerate = sum(1 for p in photos if bin(int(p.phash, 16)).count("1") in degenerate)
        photos = [p for p in photos if bin(int(p.phash, 16)).count("1") not in degenerate]
        summary["skipped_degenerate_phash"] = skipped_degenerate
        face_counts: dict[int, int] = {}
        for (mid,) in s.query(Face.media_id).all():
            face_counts[mid] = face_counts.get(mid, 0) + 1

        by_id = {p.id: p for p in photos}
        rank = lambda mid: _quality_score(by_id[mid], face_counts)  # noqa: E731

        # --- Layer 2: perceptual hash ---
        # Unpack each 64-bit hash into a row of 64 bits once, so a seed's Hamming distance to the
        # whole pool is one vectorised XOR+sum. Comparing hex strings pair-by-pair re-parsed both
        # operands on every one of ~10^8 comparisons and dominated the entire pipeline.
        import numpy as np

        max_dist = settings.phash_max_distance
        n = len(photos)
        ids = [p.id for p in photos]
        row_of = {mid: k for k, mid in enumerate(ids)}
        packed = np.array([int(p.phash, 16) for p in photos], dtype=">u8")
        bits = np.unpackbits(packed.view(np.uint8).reshape(len(ids), 8), axis=1)

        # `alive` mirrors _seed_clusters' `remaining`, as a mask rather than a set. Materialising the
        # 27k-element pool as a Python list on every one of 27k seeds costs ~10^9 interpreter steps
        # and dominated everything; with a mask the whole layer is numpy end to end.
        alive = np.ones(len(ids), dtype=bool)
        id_arr = np.array(ids, dtype=np.int64)
        done = 0

        def phash_neighbours(seed: int, _pool):
            nonlocal done
            done += 1
            if progress and done % 500 == 0:
                progress(0.5 * done / (n or 1), "pHash comparison")

            si = row_of[seed]
            alive[si] = False                       # _seed_clusters already dropped the seed
            dist = np.count_nonzero(bits ^ bits[si], axis=1)
            hit = alive & (dist <= max_dist)
            if not hit.any():
                return []
            alive[hit] = False
            return id_arr[hit].tolist()

        assigned: set[int] = set()
        for members in _seed_clusters([p.id for p in photos], phash_neighbours, rank):
            summary["phash_groups"] += 1
            summary["candidates_to_delete"] += len(members) - 1
            assigned.update(members)
            _persist_group(s, "phash", members, by_id, face_counts)

        # --- Layer 3: CLIP near-duplicates (optional) ---
        # Only over photos pHash didn't already claim, so the same pair can't yield two groups.
        if use_clip:
            remaining = [p for p in photos if p.id not in assigned]
            try:
                for members in _clip_groups(remaining, rank, progress):
                    summary["clip_groups"] += 1
                    summary["candidates_to_delete"] += len(members) - 1
                    _persist_group(s, "clip", members, by_id, face_counts)
            except Exception as exc:  # noqa: BLE001 — CLIP optional
                summary["clip_error"] = str(exc)

    if progress:
        progress(1.0, "Dedup complete")
    return summary


def cleanup(apply: bool = False, max_group_size: int | None = None) -> dict:
    """Repair groups produced by the old independent-layers / chained-union-find detection.

    Two defects, both fixable without re-embedding 28k photos on the GPU:

    * **Exact repeats** — the same member set found by pHash *and* CLIP. Keeps the pHash group
      (tighter signal, so its keeper choice is the more trustworthy) and drops the CLIP twin.
    * **Oversized groups** — chaining artifacts. These aren't deleted, because real duplicates are
      buried inside them; they're reported so the UI can fence them off from bulk actions. A full
      ``dedup`` re-run is what actually rebuilds them properly.

    Preview by default.
    """
    limit = max_group_size or settings.dedup_max_group_size
    rank = {"phash": 0, "clip": 1}   # lower wins when the same member set appears twice

    with session_scope() as s:
        groups = s.query(DupGroup).filter(DupGroup.reviewed.is_(False)).all()

        by_members: dict[tuple[int, ...], list[DupGroup]] = {}
        oversized: list[dict] = []
        for g in groups:
            key = tuple(sorted(m.media_id for m in g.members))
            if not key:
                continue
            by_members.setdefault(key, []).append(g)
            if len(key) > limit:
                oversized.append({"group_id": g.id, "method": g.method, "size": len(key)})

        removed = 0
        samples: list[dict] = []
        for key, dupes in by_members.items():
            if len(dupes) < 2:
                continue
            dupes.sort(key=lambda g: (rank.get(g.method, 9), g.id))
            keep, drop = dupes[0], dupes[1:]
            for g in drop:
                if len(samples) < 10:
                    samples.append({"removed_group": g.id, "method": g.method,
                                    "kept_group": keep.id, "kept_method": keep.method,
                                    "members": len(key)})
                if apply:
                    s.delete(g)
                removed += 1

        oversized.sort(key=lambda o: -o["size"])
        total_groups = len(groups)

    return {
        "applied": apply,
        "groups_examined": total_groups,
        "duplicate_groups_removed": removed,
        "remaining": total_groups - removed,
        "oversized_limit": limit,
        "oversized_groups": len(oversized),
        "oversized_photos": sum(o["size"] for o in oversized),
        "oversized_sample": oversized[:10],
        "samples": samples,
    }


def _pick_keeper(scored: dict[int, "quality.Signals"], candidate_ids: list[int]) -> int:
    """Highest-scoring *eligible* candidate. See ``quality._mark_eligibility`` for why eligibility
    is a hard gate rather than just another weighted signal."""
    eligible = [mid for mid in candidate_ids if scored[mid].eligible]
    pool = eligible or candidate_ids
    return max(pool, key=lambda mid: scored[mid].score)


def _persist_group(s, method: str, members: list[int], by_id, face_counts) -> None:
    rows = [by_id[mid] for mid in members]
    faces_by_media = _faces_for(s, members)
    scored = quality.score_group(rows, faces_by_media)

    keeper = _pick_keeper(scored, members)
    g = DupGroup(method=method, keeper_media_id=keeper,
                 keeper_reason=scored[keeper].reason or None)
    s.add(g)
    s.flush()
    for mid in members:
        s.add(DupMember(group_id=g.id, media_id=mid, score=scored[mid].score))


def _faces_for(s, media_ids: list[int]) -> dict[int, list]:
    out: dict[int, list] = {}
    if not media_ids:
        return out
    for f in s.query(Face).filter(Face.media_id.in_(media_ids)).all():
        out.setdefault(f.media_id, []).append(f)
    return out


def rescore(progress: Callable[[float, str], None] | None = None) -> dict:
    """Recompute keepers for existing unreviewed groups using the current scoring rules.

    Detection is the expensive part (hours of GPU); *choosing* within an already-detected group is
    cheap and reads no image files. So improving the scorer — or running the face pass to unlock its
    face-aware signals — never requires re-detecting anything.

    Groups the user has already touched by hand are left alone.
    """
    on = progress or (lambda _p, _m: None)
    changed = unchanged = 0
    reasons: dict[str, int] = {}

    with session_scope() as s:
        groups = s.query(DupGroup).filter(DupGroup.reviewed.is_(False)).all()
        total = len(groups) or 1

        for i, g in enumerate(groups):
            if i % 200 == 0:
                on(i / total, f"Re-scoring {i}/{total}…")

            member_ids = [m.media_id for m in g.members]
            rows = s.query(Media).filter(Media.id.in_(member_ids)).all()
            if len(rows) < 2:
                continue

            scored = quality.score_group(rows, _faces_for(s, member_ids))
            best_id = _pick_keeper(scored, [m.id for m in rows])
            best = next(m for m in rows if m.id == best_id)

            for mem in g.members:
                if mem.media_id in scored:
                    mem.score = scored[mem.media_id].score

            g.keeper_reason = scored[best.id].reason or None
            if g.keeper_media_id != best.id:
                g.keeper_media_id = best.id
                changed += 1
            else:
                unchanged += 1
            r = scored[best.id].reason or "best overall"
            reasons[r] = reasons.get(r, 0) + 1

        on(1.0, "Re-scoring complete")

    return {"groups": changed + unchanged, "keeper_changed": changed,
            "keeper_unchanged": unchanged,
            "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:10])}


def _clip_groups(photos: list[Media], rank, progress) -> list[list[int]]:
    """Embed photos with CLIP and cluster each seed with everything close *to that seed*."""
    import numpy as np
    import torch
    import open_clip
    from PIL import Image

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
    model = model.to(device).eval()

    embeds: dict[int, "np.ndarray"] = {}
    with torch.no_grad():
        for k, p in enumerate(photos):
            if progress and k % 25 == 0:
                progress(0.5 + 0.4 * k / (len(photos) or 1), "CLIP embedding")
            try:
                img = preprocess(Image.open(p.abs_path).convert("RGB")).unsqueeze(0).to(device)
                v = model.encode_image(img)[0]
                v = (v / v.norm()).cpu().numpy()
                embeds[p.id] = v
            except Exception:
                continue

    ids = list(embeds)
    if not ids:
        return []

    mat = np.stack([embeds[i] for i in ids])
    row_of = {mid: k for k, mid in enumerate(ids)}
    id_arr = np.array(ids, dtype=np.int64)
    alive = np.ones(len(ids), dtype=bool)
    threshold = settings.clip_similarity

    def neighbours(seed: int, _pool):
        # Same masking trick as the pHash layer: score the seed against the whole matrix and filter
        # with a boolean mask. Materialising the pool as a Python list per seed — and gathering its
        # rows out of a (n, 512) float32 matrix — costs tens of GB of copying over a full run.
        # Vectors are L2-normalised, so a dot product IS the cosine similarity.
        si = row_of[seed]
        alive[si] = False                      # _seed_clusters already dropped the seed
        hit = alive & ((mat @ mat[si]) >= threshold)
        if not hit.any():
            return []
        alive[hit] = False
        return id_arr[hit].tolist()

    if progress:
        progress(0.9, "CLIP clustering")
    return _seed_clusters(ids, neighbours, rank)
