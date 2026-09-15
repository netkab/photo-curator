"""Approval snapshots exact account/content keys. Applying defaults to a queued dry run.
Only the extension can execute reviewed trash; no upload or unrelated legacy apply paths exist.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..db import db_dependency, serialized
from ..models import AccidentGroup, Caption, DerivedMedia, GpItem, GpOperation, Media, ReviewAction


router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("")
def list_actions(status: str | None = None, db: Session = Depends(db_dependency)) -> list[dict]:
    q = db.query(ReviewAction)
    if status:
        q = q.filter(ReviewAction.status == status)
    out = []
    for a in q.order_by(ReviewAction.created_at.desc()).all():
        out.append({
            "id": a.id, "kind": a.kind, "status": a.status,
            "payload": json.loads(a.payload) if a.payload else {},
            "note": a.note, "created_at": a.created_at.isoformat(),
            "applied_at": a.applied_at.isoformat() if a.applied_at else None,
        })
    return out


def _get(db: Session, action_id: int) -> ReviewAction:
    a = db.get(ReviewAction, action_id)
    if not a:
        raise HTTPException(404, "action not found")
    return a


def reviewed_targets(db: Session, a: ReviewAction) -> tuple[str, list[str], list[str]]:
    """Resolve only reviewed members, protecting every keeper by content key across groups."""
    if a.kind != "delete":
        raise HTTPException(400, "Only cleanup trash reviews are enabled")
    payload = json.loads(a.payload)
    items = payload.get("items", [])
    keeper_ids = {payload.get("keeper_media_id")} | {i.get("keeper_media_id") for i in items}
    keeper_ids.update(payload.get("kept_media_ids", []))
    keeper_ids.discard(None)
    if not keeper_ids:
        # Standalone temporary references have no duplicate keeper. Only a catalog-backed
        # temporary group can use this path; a payload flag cannot disable keeper protection.
        group = db.get(AccidentGroup, payload.get('accident_group_id')) if payload.get('accident_group_id') else None
        members = {x['media_id'] for x in json.loads(group.payload)['members']} if group else set()
        if not (group and group.category == 'temporary' and payload.get('cleanup_category') == 'temporary'
                and {i['media_id'] for i in items} == members and members):
            raise HTTPException(409, "Cleanup requires a surviving keeper")
    ids = {i["media_id"] for i in items} - keeper_ids
    rows = db.query(GpItem).filter(GpItem.media_id.in_(ids | keeper_ids)).all()
    accounts = {g.account for g in rows}
    if len(accounts) != 1 or next(iter(accounts)).startswith("/u/"):
        raise HTTPException(409, "Rescan with the current extension; a stable single account is required")
    account = next(iter(accounts))
    keepers = [g for g in rows if g.media_id in keeper_ids and not g.trashed and g.dedup_key]
    if {g.media_id for g in keepers} != keeper_ids:
        raise HTTPException(409, "A keeper is missing; review the group again")
    protected = {g.dedup_key for g in keepers}
    targets = [g for g in rows if g.media_id in ids and g.dedup_key and not g.trashed]
    if any(g.is_owned is not True for g in targets):
        raise HTTPException(409, "Ownership is unknown or shared; inspect these photos manually")
    keys = list(dict.fromkeys(g.dedup_key for g in targets if g.dedup_key not in protected))
    if not keys:
        raise HTTPException(409, "No distinct owned duplicates remain; keepers are protected by content identity")
    # Every content alias of a target must be reviewed too. Prevent an unselected alias from going.
    aliases = db.query(GpItem).filter(GpItem.account == account, GpItem.dedup_key.in_(keys),
                                      GpItem.trashed.is_(False)).all()
    if any(g.media_id not in ids for g in aliases):
        raise HTTPException(409, "A selected content key also identifies an unselected photo")
    return account, keys, sorted(protected)


@router.post("/{action_id}/keep-unowned")
@serialized
def keep_unowned(action_id: int, db: Session = Depends(db_dependency)) -> dict:
    """Narrow a pending selection; never approve it or create an operation."""
    a = _get(db, action_id)
    if a.status != "pending" or a.kind != "delete":
        raise HTTPException(409, "Only pending cleanup reviews can be edited")
    if db.query(GpOperation).filter(GpOperation.review_action_id == a.id).first():
        raise HTTPException(409, "This review already has an operation; create a new review")
    payload = json.loads(a.payload)
    items = payload.get("items", [])
    ids = {i["media_id"] for i in items}
    owned = {g.media_id for g in db.query(GpItem).filter(GpItem.media_id.in_(ids),
             GpItem.is_owned.is_(True), GpItem.trashed.is_(False))}
    excluded = ids - owned
    kept = set(payload.get("kept_media_ids", [])) | excluded
    kept.update(i.get("keeper_media_id") for i in items)
    kept.add(payload.get("keeper_media_id"))
    kept.discard(None)
    payload["items"] = [i for i in items if i["media_id"] in owned and i["media_id"] not in kept]
    payload["kept_media_ids"] = sorted(kept)
    payload["ownership_excluded_media_ids"] = sorted(set(payload.get("ownership_excluded_media_ids", [])) | excluded)
    for field in ("approved_account", "approved_keys", "protected_keys"):
        payload.pop(field, None)
    a.payload = json.dumps(payload)
    db.commit()
    return {"id": a.id, "status": a.status, "selected": len(payload["items"]), "excluded": len(excluded)}


@router.post("/{action_id}/approve")
@serialized
def approve(action_id: int, db: Session = Depends(db_dependency)) -> dict:
    a = _get(db, action_id)
    if a.status != "pending":
        raise HTTPException(409, f"Action is {a.status}; approval requires a pending review")
    account, keys, protected = reviewed_targets(db, a)
    payload = json.loads(a.payload)
    payload.update(approved_account=account, approved_keys=keys, protected_keys=protected)
    a.payload = json.dumps(payload)
    a.status = "approved"
    db.commit()
    return {"id": a.id, "status": a.status, "count": len(keys)}


@router.post("/{action_id}/dismiss")
@serialized
def dismiss(action_id: int, db: Session = Depends(db_dependency)) -> dict:
    a = _get(db, action_id)
    if db.query(GpOperation).filter(GpOperation.review_action_id == a.id,
                                   GpOperation.status.in_(["running", "pending", "paused"])).first():
        raise HTTPException(409, "Stop the scheduled operation before dismissing")
    a.status = "dismissed"
    db.commit()
    return {"id": a.id, "status": a.status}


from pydantic import BaseModel
class ApplyBody(BaseModel):
    dry_run: bool = True


@router.post("/{action_id}/apply")
@serialized
def apply(action_id: int, body: ApplyBody | None = None, db: Session = Depends(db_dependency)) -> dict:
    a = _get(db, action_id)
    if a.status != "approved":
        raise HTTPException(409, f"Action is {a.status}; explicitly approve it first")
    body = body or ApplyBody()
    payload = json.loads(a.payload)
    from .gp import CreateOpBody, create_operation
    op = create_operation(CreateOpBody(op="trash", account=payload.get("approved_account", ""),
                          keys=payload.get("approved_keys", []), review_action_id=a.id,
                          dry_run=body.dry_run, note=f"{ {'accidents': 'Accident cleanup', 'temporary': 'Temporary photos', 'attempts': 'Repeated attempts'}.get(payload.get('cleanup_category'), 'Accident cleanup' if payload.get('accident_group_id') else 'Reviewed photos')} — action {a.id}"), db)
    return {"id": a.id, "status": a.status,
            "result": {"operation_id": op["id"], "mode": "extension", "dry_run": body.dry_run}}


@router.get("/delete-items")
def delete_items(db: Session = Depends(db_dependency)) -> dict:
    """Flatten every pending/approved delete action into a single list of photos to delete,
    enriched with thumbs and dates. Used by the Delete Helper page."""
    actions = (db.query(ReviewAction)
               .filter(ReviewAction.kind == "delete",
                       ReviewAction.status.in_(["pending", "approved"]))
               .all())

    items: list[dict] = []
    total_bytes = 0

    for a in actions:
        payload = json.loads(a.payload) if a.payload else {}
        # Fetch the keeper once per action so we can show it alongside each duplicate
        keeper_id = payload.get("keeper_media_id")
        keeper = db.get(Media, keeper_id) if keeper_id else None
        keeper_info = None
        if keeper:
            keeper_info = {
                "name": keeper.rel_name,
                "thumb": f"/media/thumbs/{keeper.thumb_path}" if keeper.thumb_path else None,
                "taken_at": keeper.taken_at.isoformat() if keeper.taken_at else None,
                "width": keeper.width,
                "height": keeper.height,
                "path": keeper.abs_path,
            }

        for it in payload.get("items", []):
            mid = it.get("media_id")
            m = db.get(Media, mid) if mid else None
            if not m:
                continue
            total_bytes += m.bytes or 0
            items.append({
                "media_id": m.id,
                "action_id": a.id,
                "name": m.rel_name,
                "thumb": f"/media/thumbs/{m.thumb_path}" if m.thumb_path else None,
                "taken_at": m.taken_at.isoformat() if m.taken_at else None,
                "date_key": m.taken_at.date().isoformat() if m.taken_at else "no-date",
                "place_name": m.place_name,
                "width": m.width,
                "height": m.height,
                "bytes": m.bytes,
                "path": m.abs_path,
                "reason": payload.get("reason"),
                "keeper": keeper_info,
            })

    # Sort newest first, then by name
    items.sort(key=lambda x: (x["date_key"] or "", x["name"]), reverse=True)

    return {
        "total": len(items),
        "estimated_mb": round(total_bytes / 1_000_000, 1),
        "items": items,
    }
