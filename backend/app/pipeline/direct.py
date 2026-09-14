"""Thumbnail-only analysis. Thumbnail equality is never proof of original-file equality."""
from __future__ import annotations
import hashlib
import io
import re
from datetime import datetime
from pathlib import Path
from bisect import bisect_left, bisect_right
from collections import defaultdict
from urllib.parse import urlsplit, urlunsplit
import imagehash
import numpy as np
import requests
from PIL import Image, ImageOps
from ..config import settings
from ..db import session_scope
from ..models import Media, GpItem, DupGroup, DupMember

MAX_BYTES = 4 * 1024 * 1024
MAX_PIXELS = 4096 * 4096

def thumbnail_url(raw: str) -> str:
    u = urlsplit(raw)
    if (u.scheme != "https" or not re.fullmatch(r"lh[0-9]+\.googleusercontent\.com", u.hostname or "")
            or u.username or u.password or u.port not in (None, 443) or not u.path.startswith("/")):
        raise ValueError("Thumbnail host is not an allowed Google image host; rescan if Google changed its URLs")
    # Replace image transforms; never request an original or forward arbitrary query arguments.
    return urlunsplit(("https", u.hostname, u.path.split("=")[0] + "=w512-h512-no", "", ""))

def fetch_thumbnail(raw: str) -> bytes:
    url = thumbnail_url(raw)
    with requests.Session() as http:
        http.trust_env = False  # do not send private image URLs through an environment proxy
        with http.get(url, stream=True, timeout=(5, 20), allow_redirects=False) as r:
            if r.status_code != 200:
                raise ValueError(f"Thumbnail HTTP {r.status_code}; rescan to refresh expired URLs")
            if not r.headers.get("Content-Type", "").lower().startswith("image/"):
                raise ValueError("Thumbnail response is not an image")
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Thumbnail exceeds 4 MB")
                chunks.append(chunk)
            return b"".join(chunks)

def image_features(data: bytes):
    if len(data) > MAX_BYTES:
        raise ValueError("Thumbnail exceeds 4 MB")
    with Image.open(io.BytesIO(data)) as im:
        if im.width < 8 or im.height < 8:
            raise ValueError("Thumbnail is too small for analysis")
        if im.width * im.height > MAX_PIXELS:
            raise ValueError("Thumbnail pixel dimensions are too large")
        if getattr(im, "n_frames", 1) != 1:
            raise ValueError("Animated images require manual review")
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((512, 512))
        gray = np.asarray(im.convert("L"), dtype=np.float32)
        contrast = float(gray.std())
        phash = str(imagehash.phash(im)) if contrast >= 5 else None
        sharpness = float(np.diff(gray, axis=0).var() + np.diff(gray, axis=1).var())
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=90)
        return out.getvalue(), phash, sharpness

def catalog_item(s, row: GpItem):
    if row.media_id:
        return
    # sha256 is legacy non-null/unique; remote identity gets a namespaced digest, NEVER a file hash.
    identity = hashlib.sha256(f"google-photos:{row.account}:{row.media_key}".encode()).hexdigest()
    media = Media(abs_path=f"gp://{identity}", rel_name=row.file_name or row.media_key,
                  sha256=identity, source="google-photos-thumbnail",
                  media_type="video" if row.duration is not None else "photo",
                  width=row.width, height=row.height, bytes=row.bytes, taken_at=row.taken_at,
                  duration=row.duration)
    s.add(media)
    s.flush()
    row.media_id, row.match_method, row.match_score = media.id, "direct", 1.0

def analyze(handle, use_clip=False):
    # Cache one image at a time; each commit survives a stop/restart. No original downloads.
    with session_scope() as s:
        ids = [g.id for g in s.query(GpItem).filter(GpItem.trashed.is_(False)).all()]
    cached = skipped = 0
    errors = []
    for index, gid in enumerate(ids):
        if handle.cancelled:
            return {"cached": cached, "skipped": skipped, "errors": errors, "cancelled": True}
        with session_scope() as s:
            g = s.get(GpItem, gid)
            m = s.get(Media, g.media_id)
            if not m or m.source != "google-photos-thumbnail" or m.media_type != "photo":
                skipped += 1
                continue
            if m.thumb_path and (settings.thumbs_dir / m.thumb_path).is_file():
                skipped += 1
                continue
            try:
                data, phash, sharp = image_features(fetch_thumbnail(g.thumb_url or ""))
                name = f"gp-{m.sha256}.jpg"
                dest = settings.thumbs_dir / name
                temp = dest.with_suffix(".tmp")
                temp.write_bytes(data)
                temp.replace(dest)
                m.thumb_path, m.phash, m.blur_score = name, phash, sharp
                m.thumbnail_sha256 = hashlib.sha256(data).hexdigest()
                m.analyzed_at = datetime.utcnow()
                cached += 1
            except Exception as exc:
                # Do not expose signed thumbnail URLs in logs or API errors.
                errors.append({"media_id": m.id, "error": str(exc) if isinstance(exc, ValueError)
                               else type(exc).__name__ + ": thumbnail unavailable; rescan and retry"})
        handle.update(0.65 * (index + 1) / max(1, len(ids)), f"Cached {cached}; {len(errors)} unavailable")
    if use_clip:
        embed(handle)
    groups = cluster(handle, use_clip)
    return {"cached": cached, "skipped": skipped, "failed": len(errors), "errors": errors[:50],
            "groups": groups, "evidence": "thumbnails only; original equality is unknown"}

def embed(handle):
    # Explicit opt-in; a user-provided local checkpoint prevents implicit model downloads.
    import os
    checkpoint = Path(os.environ.get("PC_CLIP_CHECKPOINT", ""))
    if not checkpoint.is_file():
        raise ValueError("Set PC_CLIP_CHECKPOINT to a local OpenCLIP ViT-B-32 checkpoint first")
    import torch
    import open_clip
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained=str(checkpoint))
    model.eval()
    with session_scope() as s:
        rows = s.query(Media).filter(Media.source == "google-photos-thumbnail",
                    Media.thumb_path.isnot(None), Media.clip_embedding.is_(None)).all()
        for i, m in enumerate(rows):
            if handle.cancelled:
                return
            with Image.open(settings.thumbs_dir / m.thumb_path) as im, torch.no_grad():
                vec = model.encode_image(preprocess(im.convert("RGB")).unsqueeze(0))
                vec = vec / vec.norm(dim=-1, keepdim=True)
                m.clip_embedding = vec.cpu().numpy().astype(np.float32).tobytes()
            s.commit()
            handle.update(0.65 + 0.2 * (i + 1) / max(1, len(rows)), "Computing local CLIP embeddings")

def cluster(handle, use_clip=False):
    with session_scope() as s:
        reviewed_ids = {i for (i,) in s.query(DupMember.media_id).join(DupGroup)
                        .filter(DupGroup.reviewed.is_(True)).all()}
        rows = (s.query(Media, GpItem).join(GpItem, GpItem.media_id == Media.id)
                .filter(Media.source == "google-photos-thumbnail", Media.phash.isnot(None),
                        GpItem.trashed.is_(False)).all())
        rows = [(m, g) for m, g in rows if m.id not in reviewed_ids]
        # Stronger-quality metadata is only a suggestion; dimensions refer to originals, not cache.
        rows.sort(key=lambda pair: ((pair[0].width or 0) * (pair[0].height or 0),
                                   pair[0].blur_score or 0, -pair[0].id), reverse=True)
        used, proposed = set(), []
        # Multi-index hashing: at Hamming distance <=4 at least one of five disjoint
        # hash chunks must match. This avoids quadratic full-library Python comparisons.
        buckets = defaultdict(list)
        hashes = [int(m.phash, 16) for m, _ in rows]
        for index, value in enumerate(hashes):
            for shift, bits in ((0,13), (13,13), (26,13), (39,13), (52,12)):
                buckets[(shift, (value >> shift) & ((1 << bits)-1))].append(index)
        timeline = sorted((m.taken_at.timestamp(), i) for i, (m, _) in enumerate(rows) if m.taken_at)
        times = [t for t, _ in timeline]
        # Memory stays linear in library size; no N x N similarity matrix.
        for i, (seed, owner) in enumerate(rows):
            if handle.cancelled:
                return 0  # retain the previous groups on cancellation
            if seed.id in used:
                continue
            members, method = [seed], "thumbnail-phash"
            candidate_indices = set()
            for shift, bits in ((0,13), (13,13), (26,13), (39,13), (52,12)):
                candidate_indices.update(buckets[(shift, (hashes[i] >> shift) & ((1 << bits)-1))])
            if use_clip and seed.clip_embedding and seed.taken_at:
                when = seed.taken_at.timestamp()
                left, right = bisect_left(times, when-120), bisect_right(times, when+120)
                candidate_indices.update(j for _, j in timeline[left:right])
            for j in sorted(candidate_indices):
                if j <= i:
                    continue
                candidate, other = rows[j]
                if candidate.id in used or owner.account != other.account:
                    continue
                # Same content key can be one photo surfaced twice. Never recommend trashing it.
                if not owner.dedup_key or not other.dedup_key or owner.dedup_key == other.dedup_key:
                    continue
                a = (seed.width or 0) / (seed.height or 1)
                b = (candidate.width or 0) / (candidate.height or 1)
                if not a or not b or abs(a / b - 1) > 0.06:
                    continue
                distance = (hashes[i] ^ hashes[j]).bit_count()
                match = distance <= 4
                semantic = False
                # Similar scenes alone are weak evidence: restrict CLIP to a 2-minute burst.
                if not match and use_clip and seed.clip_embedding and candidate.clip_embedding:
                    if seed.taken_at and candidate.taken_at and abs((seed.taken_at-candidate.taken_at).total_seconds()) <= 120:
                        similarity = float(np.dot(np.frombuffer(seed.clip_embedding, np.float32),
                                                  np.frombuffer(candidate.clip_embedding, np.float32)))
                        semantic = similarity >= 0.96
                if match or semantic:
                    members.append(candidate)
                    if semantic:
                        method = "thumbnail-clip"
                    if len(members) >= settings.dedup_max_group_size:
                        break
            if len(members) > 1:
                used.update(m.id for m in members)
                proposed.append((members, method))
            handle.update(0.85 + 0.15 * (i + 1) / max(1, len(rows)), f"Found {len(proposed)} candidate groups")
        # Recheck reviews made while analysis was running before publishing the candidates.
        reviewed_ids = {i for (i,) in s.query(DupMember.media_id).join(DupGroup)
                        .filter(DupGroup.reviewed.is_(True)).all()}
        proposed = [(members, method) for members, method in proposed
                    if not any(m.id in reviewed_ids for m in members)]
        # Publish only complete results; reviewed/ignored groups remain intact across reruns.
        for g in s.query(DupGroup).filter(DupGroup.reviewed.is_(False),
                                         DupGroup.method.like("thumbnail-%")).all():
            s.delete(g)
        s.flush()
        for members, method in proposed:
            group = DupGroup(method=method, keeper_media_id=members[0].id,
                             keeper_reason="largest reported dimensions, then thumbnail sharpness; check originals")
            s.add(group); s.flush()
            for m in members:
                s.add(DupMember(group_id=group.id, media_id=m.id))
        return len(proposed)
