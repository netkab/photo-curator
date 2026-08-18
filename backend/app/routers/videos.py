"""Video endpoints — compress + highlight reels. Approving queues an `upload` review-action.
Originals are never replaced automatically."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import db_dependency
from ..jobs import manager
from ..models import DerivedMedia, GpItem, GpOperation, Media, ReviewAction
from ..pipeline import metadata as md, video_compress, video_highlights, video_metadata_backfill
from ..services import uploader

router = APIRouter(prefix="/api/videos", tags=["videos"])


@router.post("/compress")
def compress() -> dict:
    if manager.is_running("video"):
        raise HTTPException(409, "A video job is already running")
    job = manager.submit("video", lambda h: video_compress.run(
        progress=lambda p, m: h.update(p, m), should_cancel=lambda: h.cancelled))
    return job.to_dict()


class HighlightOpts(BaseModel):
    limit: int | None = None     # cap the number of reels — handy for a quick test batch


@router.post("/highlights")
def highlights(opts: HighlightOpts | None = None) -> dict:
    if manager.is_running("video"):
        raise HTTPException(409, "A video job is already running")
    mx = opts.limit if opts else None
    job = manager.submit("video", lambda h: video_highlights.run(
        max_reels=mx, progress=lambda p, m: h.update(p, m), should_cancel=lambda: h.cancelled))
    return job.to_dict()


@router.post("/cancel")
def cancel() -> dict:
    """Flag the running compress/highlights job to stop (cooperative — finishes its current clip)."""
    return {"cancelled": manager.cancel_name("video")}


@router.post("/reset")
def reset(db: Session = Depends(db_dependency)) -> dict:
    """Start from scratch: delete all not-yet-uploaded compressed/highlight results (DB rows + files)
    and the pending upload actions that point at them. Already-uploaded results are kept so we never
    re-push duplicates to Google Photos. Refuses while a job runs — cancel and retry once it stops."""
    if manager.is_running("video"):
        manager.cancel_name("video")
        raise HTTPException(409, "Stopping the running video job — wait a few seconds, then reset again.")

    removed_ids: list[int] = []
    kept_uploaded = 0
    for d in db.query(DerivedMedia).filter(DerivedMedia.kind.in_(["compressed", "highlight"])).all():
        if d.status == "uploaded":
            kept_uploaded += 1
            continue
        try:
            (settings.derived_dir / d.path).unlink(missing_ok=True)
        except Exception:
            pass
        removed_ids.append(d.id)
        db.delete(d)

    cleared_actions = 0
    if removed_ids:
        removed = set(removed_ids)
        for a in db.query(ReviewAction).filter(
            ReviewAction.kind == "upload", ReviewAction.status.in_(["pending", "approved"])
        ).all():
            try:
                payload = json.loads(a.payload)
            except Exception:
                payload = {}
            if payload.get("derived_id") in removed:
                db.delete(a)
                cleared_actions += 1
    db.commit()

    # Sweep orphaned derived files (left on disk by interrupted/older runs with no DB row) so the
    # folder matches the catalog. Only our generated naming is touched; originals live elsewhere.
    tracked = {d.path for d in db.query(DerivedMedia.path).all()}
    orphans = 0
    for f in settings.derived_dir.glob("*.mp4"):
        if f.name.startswith(("Highlights_", "highlight_", "compressed_")) and f.name not in tracked:
            try:
                f.unlink()
                orphans += 1
            except Exception:
                pass
    return {"removed": len(removed_ids), "kept_uploaded": kept_uploaded,
            "cleared_actions": cleared_actions, "orphans_removed": orphans}


@router.get("/infer-dates")
def infer_dates_preview(db: Session = Depends(db_dependency)) -> dict:
    """Preview (read-only): how many undated videos have a parseable date in their filename."""
    rows = db.query(Media).filter(Media.media_type == "video", Media.taken_at.is_(None)).all()
    matched = [(m, dt) for m in rows if (dt := md.parse_datetime_from_name(m.rel_name))]
    samples = [{"id": m.id, "name": m.rel_name, "date": dt.isoformat()} for m, dt in matched[:12]]
    return {"undated": len(rows), "matched": len(matched), "samples": samples}


@router.post("/infer-dates")
def infer_dates_apply(db: Session = Depends(db_dependency)) -> dict:
    """Write filename-inferred capture dates onto every undated video that has one. Catalog-only —
    original files are untouched. Once dated, a clip clusters by day on the next highlights run."""
    rows = db.query(Media).filter(Media.media_type == "video", Media.taken_at.is_(None)).all()
    updated = 0
    for m in rows:
        dt = md.parse_datetime_from_name(m.rel_name)
        if dt:
            m.taken_at = dt
            updated += 1
    db.commit()
    return {"updated": updated}


@router.get("/backfill-embedded-metadata")
def backfill_embedded_metadata_preview(sample: int = 20, db: Session = Depends(db_dependency)) -> dict:
    """Preview (read-only): how many undated/unplaced videos have a capture date and/or GPS embedded
    in the file itself (e.g. iPhone Live Photo .MP4 companions, whose Takeout sidecar often lacks
    it). Probes a small sample via ffprobe rather than the whole library, so this stays fast."""
    rows = db.query(Media).filter(
        Media.media_type == "video",
        Media.taken_at.is_(None), Media.place_name.is_(None), Media.gps_lat.is_(None),
    ).all()
    probed = rows[:sample]
    dated = geotagged = 0
    samples = []
    for m in probed:
        taken_at, lat, lng = md.read_embedded_datetime_gps(Path(m.abs_path))
        if taken_at:
            dated += 1
        if lat is not None:
            geotagged += 1
        samples.append({"id": m.id, "name": m.rel_name,
                        "date": taken_at.isoformat() if taken_at else None,
                        "gps": [lat, lng] if lat is not None else None})
    return {"eligible": len(rows), "probed": len(probed),
            "probe_dated": dated, "probe_geotagged": geotagged, "samples": samples}


@router.post("/backfill-embedded-metadata")
def backfill_embedded_metadata_apply() -> dict:
    """Run the full backfill as a background job (probes every eligible video via ffprobe, then
    reverse-geocodes any newly-found GPS) — pass through /api/jobs/{id} to track progress."""
    if manager.is_running("video-metadata-backfill"):
        raise HTTPException(409, "A metadata backfill job is already running")
    job = manager.submit("video-metadata-backfill", lambda h: video_metadata_backfill.run(
        progress=lambda p, m: h.update(p, m), should_cancel=lambda: h.cancelled))
    return job.to_dict()


@router.get("/needs-metadata")
def needs_metadata(limit: int = 60, offset: int = 0, db: Session = Depends(db_dependency)) -> dict:
    """Videos that highlights skips because they have no capture date AND no location. Assign a date
    and/or place here so a later 'Build highlights' run can group them into an event."""
    base = db.query(Media).filter(
        Media.media_type == "video",
        Media.taken_at.is_(None),
        Media.gps_lat.is_(None),
        Media.place_name.is_(None),
    )
    total = base.count()
    rows = base.order_by(Media.rel_name).offset(offset).limit(limit).all()
    items = [{
        "id": m.id,
        "name": m.rel_name,
        "thumb": f"/media/thumbs/{m.thumb_path}" if m.thumb_path else None,
        "file": f"/api/media/{m.id}/file",
        "duration": m.duration,
        "bytes": m.bytes,
    } for m in rows]
    return {"total": total, "items": items}


class AssignMetaBody(BaseModel):
    ids: list[int]
    date: str | None = None          # "YYYY-MM-DD" (from a date picker) or a full ISO datetime
    place_name: str | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None


@router.post("/assign-metadata")
def assign_metadata(body: AssignMetaBody, db: Session = Depends(db_dependency)) -> dict:
    """Write a date and/or location onto the chosen videos. Catalog-only — the original files on
    disk are never modified (a highlight reel built later carries the assigned date in its container)."""
    if not body.ids:
        raise HTTPException(400, "no videos selected")

    taken: datetime | None = None
    if body.date:
        raw = body.date.strip()
        try:
            taken = datetime.fromisoformat(raw)          # accepts "2020-05-01" and full ISO on 3.11+
        except ValueError:
            try:
                taken = datetime.strptime(raw, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, f"unrecognized date {body.date!r} — use YYYY-MM-DD")
        # A date-only value (the date picker) → noon, not midnight, so Google Photos' UTC reading of
        # the embedded creation_time can't slip the clip onto an adjacent day. Explicit times are kept.
        if "T" not in raw and ":" not in raw and taken.time() == datetime.min.time():
            taken = taken.replace(hour=12)

    has_gps = body.gps_lat is not None and body.gps_lng is not None
    if taken is None and not body.place_name and not has_gps:
        raise HTTPException(400, "provide a date and/or a place to assign")

    updated = 0
    for m in db.query(Media).filter(Media.id.in_(body.ids), Media.media_type == "video").all():
        if taken is not None:
            m.taken_at = taken
        if body.place_name:
            m.place_name = body.place_name.strip()
        if has_gps:
            m.gps_lat, m.gps_lng = body.gps_lat, body.gps_lng
        updated += 1
    db.commit()
    return {"updated": updated}


def _src_brief(m: Media) -> dict:
    return {
        "id": m.id,
        "name": m.rel_name,
        "thumb": f"/media/thumbs/{m.thumb_path}" if m.thumb_path else None,
        "file": f"/api/media/{m.id}/file",   # original, served read-only for before/after compare
        "taken_at": m.taken_at.isoformat() if m.taken_at else None,
        "duration": m.duration,
        "bytes": m.bytes,
    }


@router.get("/results")
def results(status: str | None = None, db: Session = Depends(db_dependency)) -> list[dict]:
    """Derived clips awaiting review, or any status when asked.

    The extension needs `approved`/`uploaded` rows too — retiring a source video is only permitted
    once its replacement has actually reached Google Photos, so the UI has to see that state.
    """
    q = db.query(DerivedMedia).filter(DerivedMedia.kind.in_(["compressed", "highlight"]))
    q = q.filter(DerivedMedia.status == (status or "pending"))

    # One linkage lookup for the whole page rather than one per source clip.
    links: dict[int, GpItem] = {}
    rows = q.all()
    src_ids = [d.source_media_id for d in rows if d.source_media_id]
    for d in rows:
        meta = json.loads(d.meta) if d.meta else {}
        src_ids.extend(meta.get("source_ids") or [])
    if src_ids:
        links = {g.media_id: g for g in db.query(GpItem).filter(
            GpItem.media_id.in_(set(src_ids)), GpItem.trashed.is_(False)).all() if g.media_id}

    def with_gp(brief: dict) -> dict:
        g = links.get(brief["id"])
        brief["gp"] = ({"media_key": g.media_key, "dedup_key": g.dedup_key,
                        "product_url": g.product_url, "account": g.account} if g else None)
        brief["live"] = bool(g and g.dedup_key)
        return brief

    out = []
    for d in rows:
        meta = json.loads(d.meta) if d.meta else {}
        sources: list[dict] = []
        if d.kind == "compressed" and d.source_media_id:
            m = db.get(Media, d.source_media_id)
            if m:
                sources = [with_gp(_src_brief(m))]
        elif d.kind == "highlight":
            ids = meta.get("source_ids") or []
            if ids:
                by_id = {m.id: m for m in db.query(Media).filter(Media.id.in_(ids)).all()}
                sources = [with_gp(_src_brief(by_id[i])) for i in ids if i in by_id]  # reel order
        # ?v=<mtime> busts the browser cache: reel filenames are deterministic per event, so a
        # regenerated reel reuses the URL — without this the browser shows the stale old video. Keyed
        # on the file's own mtime rather than d.id: SQLite reuses rowids when a row is deleted and the
        # table has no AUTOINCREMENT, so a manually-deleted-then-rebuilt reel can land back on the
        # exact same id — d.id alone silently fails to bust the cache in that case (hit for real:
        # 2025-07-26's reel regenerated at 71.7s but the extension kept showing the old cached 29s
        # cut because both rows happened to get id=2).
        try:
            cache_v = int((settings.derived_dir / d.path).stat().st_mtime)
        except OSError:
            cache_v = d.id
        out.append({
            "derived_id": d.id,
            "kind": d.kind,
            "status": d.status,
            "url": f"/media/derived/{d.path}?v={cache_v}",
            "sources": sources,
            "source_bytes": sum(s.get("bytes") or 0 for s in sources),
            "live_sources": sum(1 for s in sources if s["live"]),
            "uploads_enabled": uploader.enabled(),
            "meta": meta,
        })
    return out


class RetireSourcesBody(BaseModel):
    media_ids: list[int]          # explicit opt-in: the caller names every clip to retire
    dry_run: bool = False


@router.post("/{derived_id}/retire-sources")
def retire_sources(derived_id: int, body: RetireSourcesBody,
                   db: Session = Depends(db_dependency)) -> dict:
    """Trash the source videos of a derived clip, once that clip is safely in Google Photos.

    Two guards, and the second is the one that matters for reels:

    * the derived clip must be ``uploaded`` — retiring a source before its replacement exists in
      Google would leave neither;
    * every clip to retire is named explicitly. A **compressed** file replaces its source 1:1, but a
      **highlight reel is a montage** — a 30-second cut of forty clips is not a substitute for them.
      So there is no "retire all sources of this reel" shortcut; the caller lists what it means.
    """
    d = db.get(DerivedMedia, derived_id)
    if not d:
        raise HTTPException(404, "result not found")
    if d.status != "uploaded":
        raise HTTPException(409,
                            f"'{d.kind}' is '{d.status}', not 'uploaded' — upload it before "
                            "retiring anything it replaces")
    if not body.media_ids:
        raise HTTPException(400, "name the source clips to retire")

    meta = json.loads(d.meta) if d.meta else {}
    valid = set(meta.get("source_ids") or [])
    if d.source_media_id:
        valid.add(d.source_media_id)
    stray = [i for i in body.media_ids if i not in valid]
    if stray:
        raise HTTPException(400, f"media {stray} are not sources of derived {derived_id}")

    links = {g.media_id: g for g in db.query(GpItem).filter(
        GpItem.media_id.in_(body.media_ids),
        GpItem.dedup_key.isnot(None),
        GpItem.trashed.is_(False)).all() if g.media_id}

    ready, blocked = [], []
    for mid in body.media_ids:
        g, m = links.get(mid), db.get(Media, mid)
        if not g:
            blocked.append({"media_id": mid, "reason": "not live in Google Photos"})
        elif m:
            ready.append((m, g))

    if not ready:
        return {"scheduled": 0, "blocked": blocked, "operation_id": None}

    accounts = {g.account for _, g in ready}
    if len(accounts) > 1:
        raise HTTPException(409, f"items span multiple accounts {sorted(accounts)}")

    action = ReviewAction(
        kind="delete",
        status="approved",
        payload=json.dumps({
            "reason": f"source of uploaded {d.kind} (derived {d.id})",
            "keeper_media_id": None,
            "items": [{"media_id": m.id, "name": m.rel_name, "abs_path": m.abs_path,
                       "taken_at": m.taken_at.isoformat() if m.taken_at else None,
                       "place_name": m.place_name} for m, _ in ready],
        }),
        note=f"Retire {len(ready)} source clip(s) after uploading {d.kind} {d.id}.",
    )
    db.add(action)
    db.flush()

    from .gp import dedupe_keys
    keys = dedupe_keys([g.dedup_key for _, g in ready])
    op = GpOperation(
        op="trash", review_action_id=action.id, account=next(iter(accounts)),
        payload=json.dumps(keys),
        total=len(keys), status="pending", dry_run=body.dry_run,
        results=json.dumps({"succeeded": [], "failed": []}),
        note=f"Sources of {d.kind} {d.id}",
    )
    db.add(op)
    db.commit()

    return {"scheduled": len(ready), "blocked": blocked,
            "operation_id": op.id, "review_action_id": action.id,
            "freed_bytes": sum(m.bytes or 0 for m, _ in ready)}


@router.post("/{derived_id}/approve")
def approve(derived_id: int, db: Session = Depends(db_dependency)) -> dict:
    d = db.get(DerivedMedia, derived_id)
    if not d:
        raise HTTPException(404, "result not found")
    d.status = "approved"
    meta = json.loads(d.meta) if d.meta else {}
    db.add(ReviewAction(
        kind="upload",
        payload=json.dumps({"derived_id": d.id, "kind": d.kind, "path": d.path,
                            "source_media_id": d.source_media_id, "taken_at": meta.get("taken_at")}),
    ))
    db.commit()
    return {"queued": True}
