"""Enhancement endpoints. Approving an enhanced result queues an `upload` review-action (new album).
Originals are never modified."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import db_dependency
from ..jobs import manager
from ..models import DerivedMedia, GpItem, GpOperation, Media, ReviewAction
from ..pipeline import enhance as enhance_pipeline
from ..services import uploader

router = APIRouter(prefix="/api/enhance", tags=["enhance"])


class EnhanceBody(BaseModel):
    ids: list[int] | None = None
    blurry: bool = True


@router.post("/run")
def run(body: EnhanceBody) -> dict:
    if manager.is_running("enhance"):
        raise HTTPException(409, "Enhance already running")
    job = manager.submit("enhance", lambda h: enhance_pipeline.run(
        ids=body.ids, blurry=body.blurry, progress=lambda p, m: h.update(p, m)))
    return job.to_dict()


@router.get("/results")
def results(db: Session = Depends(db_dependency)) -> list[dict]:
    out = []
    for d in db.query(DerivedMedia).filter(DerivedMedia.kind == "enhanced",
                                            DerivedMedia.status == "pending").all():
        src = db.get(Media, d.source_media_id) if d.source_media_id else None
        out.append({
            "derived_id": d.id,
            "before": f"/media/thumbs/{src.thumb_path}" if src and src.thumb_path else None,
            "after": f"/media/derived/{d.path}",
            "source_name": src.rel_name if src else None,
            "meta": json.loads(d.meta) if d.meta else {},
        })
    return out


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


# ────────────────────────────────────────────────────────────────────────────
# Replace-in-Google-Photos flow
#
# Enhancement itself has to stay local: Real-ESRGAN needs the original pixels, and Google only ever
# serves a downscaled thumbnail. So the loop is
#     enhance locally -> upload the improved copy -> retire the blurry original
# and the extension owns only that last step. `candidates` drives the picking, `retire` schedules
# the trash once the replacement is confirmed present.
# ────────────────────────────────────────────────────────────────────────────
@router.get("/candidates")
def candidates(limit: int = 40, offset: int = 0, max_blur: float | None = None,
               db: Session = Depends(db_dependency)) -> dict:
    """Blurry photos that are still live in Google Photos, worst first.

    Restricted to live items because a blurry photo that is no longer in Google has nothing to
    replace — enhancing it is a local-only exercise and belongs in the web UI, not here.
    """
    threshold = max_blur if max_blur is not None else settings.blur_threshold

    live = db.query(GpItem.media_id).filter(
        GpItem.media_id.isnot(None), GpItem.dedup_key.isnot(None), GpItem.trashed.is_(False))

    q = (db.query(Media)
         .filter(Media.media_type == "photo",
                 Media.blur_score.isnot(None),
                 Media.blur_score < threshold,
                 Media.archived_at.is_(None),
                 Media.id.in_(live))
         .order_by(Media.blur_score.asc()))

    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    links = {g.media_id: g for g in db.query(GpItem).filter(
        GpItem.media_id.in_([m.id for m in rows])).all()} if rows else {}

    # An existing derived row means this one has already been enhanced — surface its state rather
    # than inviting the user to enhance it twice.
    derived = {d.source_media_id: d for d in db.query(DerivedMedia).filter(
        DerivedMedia.kind == "enhanced",
        DerivedMedia.source_media_id.in_([m.id for m in rows])).all()} if rows else {}

    items = []
    for m in rows:
        g = links.get(m.id)
        d = derived.get(m.id)
        items.append({
            "media_id": m.id,
            "name": m.rel_name,
            "thumb": f"/media/thumbs/{m.thumb_path}" if m.thumb_path else None,
            "blur_score": round(m.blur_score, 1) if m.blur_score is not None else None,
            "width": m.width, "height": m.height,
            "taken_at": m.taken_at.isoformat() if m.taken_at else None,
            "gp": {"media_key": g.media_key, "dedup_key": g.dedup_key,
                   "product_url": g.product_url, "thumb_url": g.thumb_url,
                   "account": g.account} if g else None,
            "derived": {"derived_id": d.id, "status": d.status,
                        "after": f"/media/derived/{d.path}"} if d else None,
        })

    return {
        "total": total, "offset": offset, "threshold": threshold,
        "uploads_enabled": uploader.enabled(),
        "items": items,
    }


class RetireBody(BaseModel):
    media_ids: list[int]
    dry_run: bool = False


@router.post("/retire-originals")
def retire_originals(body: RetireBody, db: Session = Depends(db_dependency)) -> dict:
    """Trash the original photos whose enhanced replacements are already uploaded.

    Refuses any item whose enhanced version has not reached ``uploaded`` — trashing the original
    before its replacement is safely in Google would leave you with neither.
    """
    if not body.media_ids:
        raise HTTPException(400, "no media_ids given")

    derived = {d.source_media_id: d for d in db.query(DerivedMedia).filter(
        DerivedMedia.kind == "enhanced",
        DerivedMedia.source_media_id.in_(body.media_ids)).all()}
    links = {g.media_id: g for g in db.query(GpItem).filter(
        GpItem.media_id.in_(body.media_ids),
        GpItem.dedup_key.isnot(None),
        GpItem.trashed.is_(False)).all()}

    ready, blocked = [], []
    for mid in body.media_ids:
        d, g = derived.get(mid), links.get(mid)
        m = db.get(Media, mid)
        if not d:
            blocked.append({"media_id": mid, "reason": "no enhanced version"})
        elif d.status != "uploaded":
            blocked.append({"media_id": mid, "reason": f"replacement not uploaded (status: {d.status})"})
        elif not g:
            blocked.append({"media_id": mid, "reason": "not live in Google Photos"})
        else:
            ready.append((m, g))

    if not ready:
        return {"scheduled": 0, "blocked": blocked, "operation_id": None}

    accounts = {g.account for _, g in ready}
    if len(accounts) > 1:
        raise HTTPException(409, f"items span multiple accounts {sorted(accounts)}")

    action = ReviewAction(
        kind="delete",
        payload=json.dumps({
            "reason": "replaced by enhanced version",
            "keeper_media_id": None,
            "items": [{"media_id": m.id, "name": m.rel_name, "abs_path": m.abs_path,
                       "taken_at": m.taken_at.isoformat() if m.taken_at else None,
                       "place_name": m.place_name} for m, _ in ready],
        }),
        status="approved",
        note="Originals superseded by locally enhanced uploads.",
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
        note=f"Retire {len(ready)} original(s) replaced by enhanced uploads",
    )
    db.add(op)
    db.commit()

    return {"scheduled": len(ready), "blocked": blocked,
            "operation_id": op.id, "review_action_id": action.id}
