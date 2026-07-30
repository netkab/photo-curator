"""Reclaim disk space by MOVING redundant local duplicates out of the Takeout folder.

Nothing is deleted. Files are moved to a quarantine folder, the catalog follows them, and a manifest
is written so the whole run can be undone.

Safety rule (the strict one). A file is a candidate only when **all** of these hold:

1. it is a non-keeper member of a duplicate group;
2. its group's keeper file still exists **on disk**;
3. that keeper is **live in Google Photos** (a ``gp_items`` row with a ``dedup_key``, not trashed);
4. the candidate is not itself a keeper of some *other* group;
5. no other catalog row shares its ``sha256`` outside this group.

Conditions 2 and 3 together mean two independent copies of the content survive every move — one on
disk, one in Google. That matters because the naive version of this idea is dangerous: the photos
that look most "already handled" are often ones you deleted from Google Photos long ago, which makes
your local file the *only* copy left. Those are excluded here by construction.

Videos are skipped by default: they are the bulk of the bytes but also the hardest to re-acquire,
and their "duplicates" are more often re-encodes than true copies.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope
from ..models import DupGroup, DupMember, GpItem, Media

Progress = Callable[[float, str], None]


def _noop(_p: float, _m: str) -> None:
    pass


def _manifest_path(run_id: str) -> Path:
    return settings.exports_dir / f"reclaim_{run_id}.json"


def plan(include_videos: bool = False, limit: int | None = None) -> dict:
    """Work out what could be moved. Pure read — touches nothing."""
    with session_scope() as db:
        candidates, skipped = _candidates(db, include_videos)

    if limit:
        candidates = candidates[:limit]

    total_bytes = sum(c["bytes"] or 0 for c in candidates)
    return {
        "candidates": len(candidates),
        "bytes": total_bytes,
        "gb": round(total_bytes / 1024 ** 3, 2),
        "quarantine_dir": str(settings.quarantine_path),
        "include_videos": include_videos,
        "skipped": skipped,
        "sample": candidates[:15],
    }


def _candidates(db: Session, include_videos: bool) -> tuple[list[dict], dict]:
    """Return (movable rows, counts of why others were rejected)."""
    skipped = {
        "keeper": 0,              # the row is a keeper somewhere — never move a keeper
        "keeper_file_missing": 0, # keeper not on disk, so moving this would leave no local copy
        "keeper_not_in_google": 0,# keeper not live in Google — disk copy would be the only one
        "already_archived": 0,
        "file_missing": 0,
        "video": 0,
        "sha_unique_elsewhere": 0,
    }

    # Every media id that is the keeper of any group. Cheap set membership beats a per-row query.
    keeper_ids = {k for (k,) in db.query(DupGroup.keeper_media_id)
                  .filter(DupGroup.keeper_media_id.isnot(None)).all()}

    live_ids = {mid for (mid,) in db.query(GpItem.media_id)
                .filter(GpItem.media_id.isnot(None),
                        GpItem.dedup_key.isnot(None),
                        GpItem.trashed.is_(False)).all()}

    rows = (db.query(DupMember, DupGroup)
            .join(DupGroup, DupGroup.id == DupMember.group_id)
            .all())

    out: list[dict] = []
    seen: set[int] = set()

    for mem, group in rows:
        mid = mem.media_id
        if mid in seen or mid == group.keeper_media_id:
            continue

        m = db.get(Media, mid)
        if not m:
            continue

        if mid in keeper_ids:
            skipped["keeper"] += 1
            continue
        if m.archived_at:
            skipped["already_archived"] += 1
            continue
        if m.media_type == "video" and not include_videos:
            skipped["video"] += 1
            continue

        keeper = db.get(Media, group.keeper_media_id) if group.keeper_media_id else None
        if not keeper or not Path(keeper.abs_path).exists():
            skipped["keeper_file_missing"] += 1
            continue
        if keeper.id not in live_ids:
            skipped["keeper_not_in_google"] += 1
            continue
        if not Path(m.abs_path).exists():
            skipped["file_missing"] += 1
            continue

        # Guard against a row that happens to share bytes with something outside this group — if the
        # only other copy of those bytes is this file, moving it is not the no-op it looks like.
        if m.sha256 and keeper.sha256 != m.sha256:
            others = (db.query(Media.id)
                      .filter(Media.sha256 == m.sha256, Media.id != m.id,
                              Media.archived_at.is_(None))
                      .count())
            if others == 0 and mid not in live_ids:
                skipped["sha_unique_elsewhere"] += 1
                continue

        seen.add(mid)
        out.append({
            "media_id": m.id,
            "name": m.rel_name,
            "abs_path": m.abs_path,
            "bytes": m.bytes,
            "media_type": m.media_type,
            "group_id": group.id,
            "method": group.method,
            "keeper_media_id": keeper.id,
            "keeper_name": keeper.rel_name,
        })

    out.sort(key=lambda c: c["bytes"] or 0, reverse=True)
    return out, skipped


def run(include_videos: bool = False, limit: int | None = None,
        progress: Progress | None = None) -> dict:
    """Move the candidates into quarantine and point the catalog at their new location."""
    on = progress or _noop
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = settings.quarantine_path
    root.mkdir(parents=True, exist_ok=True)

    with session_scope() as db:
        candidates, skipped = _candidates(db, include_videos)
        if limit:
            candidates = candidates[:limit]
        if not candidates:
            return {"moved": 0, "bytes": 0, "gb": 0.0, "skipped": skipped, "manifest": None}

        moved: list[dict] = []
        failed: list[dict] = []
        total = len(candidates)

        for i, c in enumerate(candidates):
            if i % 25 == 0:
                on(i / total, f"Moving {i}/{total}…")

            src = Path(c["abs_path"])
            dest = _dest_for(root, src)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
            except Exception as exc:      # noqa: BLE001 — report and carry on, don't abort the run
                failed.append({**c, "error": str(exc)})
                continue

            m = db.get(Media, c["media_id"])
            if m:
                m.archived_from = m.abs_path
                m.abs_path = str(dest)
                m.archived_at = datetime.utcnow()
            moved.append({**c, "moved_to": str(dest)})

        total_bytes = sum(m["bytes"] or 0 for m in moved)
        manifest = {
            "run_id": run_id,
            "created_at": datetime.utcnow().isoformat(),
            "quarantine_dir": str(root),
            "include_videos": include_videos,
            "moved": moved,
            "failed": failed,
            "bytes": total_bytes,
        }
        _manifest_path(run_id).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        on(1.0, f"Moved {len(moved)} files")

    return {
        "run_id": run_id,
        "moved": len(moved),
        "failed": len(failed),
        "bytes": total_bytes,
        "gb": round(total_bytes / 1024 ** 3, 2),
        "quarantine_dir": str(root),
        "manifest": str(_manifest_path(run_id)),
        "skipped": skipped,
    }


def _dest_for(root: Path, src: Path) -> Path:
    """Mirror the source layout under the quarantine root, disambiguating collisions.

    Takeout splits one library across several archive folders, so the same relative name can occur
    more than once. A blind flatten would silently overwrite — and overwriting is the one thing this
    module must never do.
    """
    try:
        rel = src.relative_to(settings.takeout_dir)
    except ValueError:
        rel = Path(src.name)

    dest = root / rel
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for n in range(1, 10000):
        candidate = dest.with_name(f"{stem}__{n}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot find a free name for {dest}")


def undo(run_id: str, progress: Progress | None = None) -> dict:
    """Move a run's files back where they came from and clear the archive markers."""
    on = progress or _noop
    path = _manifest_path(run_id)
    if not path.exists():
        raise FileNotFoundError(f"no manifest for run {run_id} at {path}")

    manifest = json.loads(path.read_text(encoding="utf-8"))
    entries = manifest.get("moved", [])
    restored, failed = 0, []

    with session_scope() as db:
        for i, e in enumerate(entries):
            if i % 25 == 0:
                on(i / max(len(entries), 1), f"Restoring {i}/{len(entries)}…")
            src, dest = Path(e["moved_to"]), Path(e["abs_path"])
            try:
                if src.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dest))
                m = db.get(Media, e["media_id"])
                if m:
                    m.abs_path = str(dest)
                    m.archived_at = None
                    m.archived_from = None
                restored += 1
            except Exception as exc:      # noqa: BLE001
                failed.append({"media_id": e["media_id"], "error": str(exc)})

    on(1.0, f"Restored {restored} files")
    return {"run_id": run_id, "restored": restored, "failed": failed}


def runs() -> list[dict]:
    """List past reclaim runs, newest first, from their manifests."""
    out = []
    for p in sorted(settings.exports_dir.glob("reclaim_*.json"), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a truncated manifest shouldn't hide the others
            continue
        out.append({
            "run_id": d.get("run_id"),
            "created_at": d.get("created_at"),
            "moved": len(d.get("moved", [])),
            "failed": len(d.get("failed", [])),
            "bytes": d.get("bytes", 0),
            "gb": round(d.get("bytes", 0) / 1024 ** 3, 2),
            "quarantine_dir": d.get("quarantine_dir"),
            "manifest": str(p),
        })
    return out
