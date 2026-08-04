"""The Review Queue — the single gate for any change.

Every proposed action is `pending` until the user approves it, and only `apply` performs anything:
  * delete   -> schedules a GpOperation the extension executes (moves to Google's bin, undoable for
                60 days). Items with no Google Photos link fall back to a manual checklist.
  * upload   -> uploads the approved NEW file to a dedicated album (if OAuth set) else exports it.
  * review   -> the package was exported at creation; apply just returns the Maps review URL.
  * caption  -> marks the caption approved.
Nothing in this app deletes originals on disk, permanently deletes anything, or posts to Google
automatically. `apply` on a delete only *schedules* the trash — the extension performs it, and
`/api/gp/operations/{id}/undo` reverses it.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..db import db_dependency
from ..models import Caption, DerivedMedia, GpItem, GpOperation, Media, ReviewAction
from ..services import uploader

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


@router.post("/{action_id}/approve")
def approve(action_id: int, db: Session = Depends(db_dependency)) -> dict:
    a = _get(db, action_id)
    a.status = "approved"
    db.commit()
    return {"id": a.id, "status": a.status}


@router.post("/{action_id}/dismiss")
def dismiss(action_id: int, db: Session = Depends(db_dependency)) -> dict:
    a = _get(db, action_id)
    a.status = "dismissed"
    db.commit()
    return {"id": a.id, "status": a.status}


@router.post("/{action_id}/apply")
def apply(action_id: int, db: Session = Depends(db_dependency)) -> dict:
    a = _get(db, action_id)
    if a.status not in ("approved", "pending"):
        raise HTTPException(409, f"action is {a.status}")
    payload = json.loads(a.payload) if a.payload else {}
    result: dict = {}

    if a.kind == "delete":
        result = _apply_delete(db, a, payload)

    elif a.kind == "upload":
        path = settings.derived_dir / payload["path"]
        if uploader.enabled():
            try:
                result = uploader.upload_files([path])
            except Exception as exc:
                # Surface the real Google API error instead of letting it fall through as a bare,
                # non-JSON 500 — that previously made a real failure (e.g. a scope problem) look to
                # the client like the backend was unreachable.
                raise HTTPException(502, f"Google Photos upload failed: {exc}") from exc
            d = db.get(DerivedMedia, payload.get("derived_id"))
            if d:
                d.status = "uploaded"
        else:
            dest = settings.exports_dir / "to_upload" / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                import shutil

                shutil.copy2(path, dest)
            result = {"mode": "manual", "exported_to": str(dest),
                      "note": "Set PHOTOS_OAUTH_* to enable direct upload to a new album."}

    elif a.kind == "review":
        result = {"review_url": payload.get("review_url"), "export_dir": payload.get("export_dir"),
                  "note": "Open the URL and paste the text + attach the exported images manually."}

    elif a.kind == "caption":
        cap = db.get(Caption, payload.get("caption_id"))
        if cap:
            cap.approved = True
        result = {"caption_id": payload.get("caption_id"), "approved": True}

    # A scheduled trash isn't finished until the extension actually drains it. Leave the action
    # `approved` so it stays visible and undoable; gp.report_result flips it to `done`.
    if result.pop("_defer_done", False):
        a.status = "approved"
    else:
        a.status = "done"
        a.applied_at = datetime.utcnow()
    db.commit()
    return {"id": a.id, "status": a.status, "result": result}


def _apply_delete(db: Session, a: ReviewAction, payload: dict) -> dict:
    """Schedule a trash operation for every item we can identify in Google Photos.

    Items linked to a live ``gp_items`` row are trashed by the extension over Google's internal API
    (recoverable from the bin for 60 days, and undoable in one click). Anything unlinked — typically
    already deleted, or never uploaded — still gets the date-sorted manual checklist, so no item is
    silently dropped from the flow.
    """
    items = sorted(payload.get("items", []), key=lambda it: it.get("taken_at") or "")
    media_ids = [it["media_id"] for it in items if it.get("media_id")]

    links: dict[int, GpItem] = {}
    if media_ids:
        for g in (db.query(GpItem)
                  .filter(GpItem.media_id.in_(media_ids),
                          GpItem.dedup_key.isnot(None),
                          GpItem.trashed.is_(False))
                  .all()):
            links[g.media_id] = g

    # Never trash a keeper, even if a caller wrongly included one in the payload. A bulk action
    # spans many groups, so the keeper is carried per item; a single-group action has one at the
    # top level. Honour both.
    action_keeper = payload.get("keeper_media_id")

    def is_keeper(it: dict) -> bool:
        return it["media_id"] in (action_keeper, it.get("keeper_media_id"))

    linked = [(it, links[it["media_id"]]) for it in items
              if it.get("media_id") in links and not is_keeper(it)]
    unlinked = [it for it in items if it.get("media_id") not in links and not is_keeper(it)]

    result: dict = {"linked": len(linked), "unlinked": len(unlinked)}

    if linked:
        accounts = {g.account for _, g in linked}
        if len(accounts) > 1:
            raise HTTPException(409, f"items span multiple accounts {sorted(accounts)}")
        # Several media rows can share one dedup_key; collapse so the counts mean something.
        from .gp import dedupe_keys
        keys = dedupe_keys([g.dedup_key for _, g in linked])
        op = GpOperation(
            op="trash",
            review_action_id=a.id,
            account=next(iter(accounts)),
            payload=json.dumps(keys),
            total=len(keys),
            status="pending",
            results=json.dumps({"succeeded": [], "failed": []}),
            note=f"{payload.get('reason', 'duplicate')} — review action {a.id}",
        )
        db.add(op)
        db.flush()  # need op.id in the response
        result["operation_id"] = op.id
        result["mode"] = "extension"
        result["_defer_done"] = True

    if unlinked:
        checklist = settings.exports_dir / f"delete_checklist_{a.id}.txt"
        lines = [
            "These items are not linked to a live Google Photos entry, so the extension cannot act",
            "on them. Run a library sync from the extension first; if they still appear here they",
            "were most likely already deleted, or never uploaded.",
            f"Reason: {payload.get('reason', 'duplicate')}",
            "",
        ]
        for it in unlinked:
            when = (it.get("taken_at") or "unknown date").replace("T", " ")[:19]
            place = f"  @ {it['place_name']}" if it.get("place_name") else ""
            lines.append(f"[{when}]{place}  {it['name']}")
            lines.append(f"           {it['abs_path']}")
        checklist.write_text("\n".join(lines), encoding="utf-8")
        result["checklist"] = str(checklist)
        result.setdefault("mode", "manual")

    return result


@router.post("/apply-approved")
def apply_all_approved(db: Session = Depends(db_dependency)) -> dict:
    ids = [a.id for a in db.query(ReviewAction).filter(ReviewAction.status == "approved").all()]
    results = [apply(i, db) for i in ids]
    return {"applied": len(results), "results": results}


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
