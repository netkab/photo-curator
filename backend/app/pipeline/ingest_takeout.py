"""Ingest a Google Takeout export into the catalog.

Handles Google's sidecar quirks:
  * ``photo.jpg.json`` (older) and ``photo.jpg.supplemental-metadata.json`` (newer)
  * names truncated to a fixed length before the extension
  * ``(1)`` collision suffixes (``photo(1).jpg`` -> ``photo.jpg(1).json``)
  * the same library split across multiple ``Takeout``/archive folders (collapsed by sha256)

Originals are never moved or modified — only indexed (paths) and thumbnailed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..db import session_scope
from ..models import Media
from . import metadata as md

_SUFFIXES = (".supplemental-metadata.json", ".suppl.json", ".json")


def _find_sidecar(media_path: Path, json_index: dict[str, Path]) -> Path | None:
    """Resolve the JSON sidecar for a media file using exact then truncated-stem matching."""
    name = media_path.name
    # Exact candidates: full filename + each known sidecar suffix.
    candidates = [name + suf for suf in _SUFFIXES]
    # Google's "(n)" relocation: photo(1).jpg -> photo.jpg(1).json
    if "(" in name and ")" in name:
        stem, _, rest = name.partition("(")
        n = rest.split(")", 1)[0]
        ext = rest.split(")", 1)[1] if ")" in rest else media_path.suffix
        candidates.append(f"{stem}{ext}({n}).json")
    for cand in candidates:
        if cand in json_index:
            return json_index[cand]
    # Truncated-stem fallback: Google truncates long names; match the longest json whose
    # base (minus suffix) is a prefix of this media file's name.
    base_no_ext = media_path.name
    best: Path | None = None
    best_len = 0
    for jname, jpath in json_index.items():
        jbase = jname
        for suf in _SUFFIXES:
            if jbase.endswith(suf):
                jbase = jbase[: -len(suf)]
                break
        if jbase and base_no_ext.startswith(jbase) and len(jbase) > best_len:
            best, best_len = jpath, len(jbase)
    return best


def _parse_sidecar(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict = {}
    geo = data.get("geoData") or data.get("geoDataExif") or {}
    lat, lng = geo.get("latitude"), geo.get("longitude")
    if lat or lng:  # Takeout writes 0.0/0.0 when unknown
        if lat or lng:
            out["gps_lat"], out["gps_lng"] = (lat or None), (lng or None)
            if out.get("gps_lat") == 0.0 and out.get("gps_lng") == 0.0:
                out.pop("gps_lat", None)
                out.pop("gps_lng", None)
    ts = (data.get("photoTakenTime") or data.get("creationTime") or {}).get("timestamp")
    if ts:
        out["taken_at"] = datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)
    desc = data.get("description")
    if desc:
        out["description"] = desc
    return out


def ingest(takeout_dir: Path, progress: Callable[[float, str], None] | None = None) -> dict:
    """Walk ``takeout_dir`` and index new media. Returns a summary dict.

    Commits every 100 new files so progress is saved even if the process is killed.
    On restart, already-committed files are skipped by sha256 hash.
    """
    from ..db import get_session

    takeout_dir = Path(takeout_dir)
    if not takeout_dir.exists():
        raise FileNotFoundError(f"Takeout folder not found: {takeout_dir}")

    summary = {"scanned": 0, "indexed": 0, "skipped_dupe": 0, "no_sidecar": 0, "with_gps": 0}
    COMMIT_EVERY = 100  # commit batch — balances speed vs crash-safety

    # Per-directory json index, built lazily, keyed by directory.
    json_indexes: dict[Path, dict[str, Path]] = {}

    media_files = [p for p in takeout_dir.rglob("*") if p.is_file() and md.media_type(p)]
    total = len(media_files) or 1

    s = get_session()
    try:
        existing = {row[0] for row in s.query(Media.sha256).all()}
        uncommitted = 0

        for i, mpath in enumerate(media_files):
            summary["scanned"] += 1
            if progress and i % 25 == 0:
                progress(i / total,
                         f"[{summary['indexed']:,} new, {summary['skipped_dupe']:,} skipped] {mpath.name}")

            mtype = md.media_type(mpath)
            sha = md.sha256_file(mpath)
            if sha in existing:
                summary["skipped_dupe"] += 1
                continue

            # sidecar
            d = mpath.parent
            if d not in json_indexes:
                json_indexes[d] = {p.name: p for p in d.glob("*.json")}
            sidecar = _find_sidecar(mpath, json_indexes[d])
            meta = _parse_sidecar(sidecar) if sidecar else {}
            if not sidecar:
                summary["no_sidecar"] += 1

            # EXIF fallback for GPS / timestamp / camera
            if mtype == "photo":
                exif = md.read_exif(mpath)
                if "gps_lat" not in meta and exif.get("GPSLatitude"):
                    meta["gps_lat"] = exif.get("GPSLatitude")
                    meta["gps_lng"] = exif.get("GPSLongitude")
                camera = " ".join(str(x) for x in (exif.get("Make"), exif.get("Model")) if x) or None
                w, h = md.image_dimensions(mpath)
                vinfo = {}
            else:
                camera = None
                vinfo = md.probe_video(mpath)
                w, h = vinfo.get("width"), vinfo.get("height")

            row = Media(
                abs_path=str(mpath),
                rel_name=mpath.name,
                media_type=mtype,
                sha256=sha,
                phash=md.compute_phash(mpath) if mtype == "photo" else None,
                width=w,
                height=h,
                bytes=mpath.stat().st_size,
                taken_at=meta.get("taken_at"),
                gps_lat=meta.get("gps_lat"),
                gps_lng=meta.get("gps_lng"),
                camera=camera,
                codec=vinfo.get("codec"),
                bitrate=vinfo.get("bitrate"),
                duration=vinfo.get("duration"),
                description=meta.get("description"),
            )
            s.add(row)
            s.flush()  # assign id for thumbnail naming
            row.thumb_path = md.make_thumbnail(mpath, row.id, mtype)
            existing.add(sha)
            summary["indexed"] += 1
            uncommitted += 1
            if row.gps_lat is not None:
                summary["with_gps"] += 1

            # Commit every N new files so progress survives crashes
            if uncommitted >= COMMIT_EVERY:
                s.commit()
                uncommitted = 0

        # Final commit for any remaining
        if uncommitted:
            s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

    if progress:
        progress(1.0, "Ingest complete")
    return summary
