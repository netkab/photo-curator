"""Google Photos direct catalog sync, durable page cursors, and reviewed trash/restore queues.

The extension reads the signed-in web integration. Metadata and its next cursor commit together.
Only an exact approved selection can create live trash work. Each issued batch has a durable lease;
results must cover those keys exactly. Interrupted/uncertain work requires explicit user resumption.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import db_dependency, serialized
from ..jobs import manager
from ..models import GpItem, GpOperation, Media, ReviewAction, ScanCursor
from ..pipeline import gp_match
from ..pipeline.direct import catalog_item
from ..config import settings

router = APIRouter(prefix="/api/gp", tags=["gp"])

# Operations that mutate the library. Anything not listed is rejected outright — this is the
# allow-list that keeps "permanent delete" and "locked folder" off the table by construction.
ALLOWED_OPS = {"trash", "restore"}
# Ops that must be able to point back at the approved ReviewAction that authorised them.
GATED_OPS = {"trash"}

MAX_BATCH = 25
DEFAULT_BATCH = 25


# ────────────────────────────────────────────────────────────────────────────
# Sync
# ────────────────────────────────────────────────────────────────────────────
class GpItemIn(BaseModel):
    media_key: str
    dedup_key: str | None = None
    file_name: str | None = None
    taken_at: datetime | None = None
    uploaded_at: datetime | None = None
    width: int | None = None
    height: int | None = None
    bytes: int | None = None
    duration: float | None = None
    is_owned: bool | None = None
    is_original_quality: bool | None = None
    trashed: bool = False
    product_url: str | None = None
    thumb_url: str | None = None


class SyncBody(BaseModel):
    account: str = Field(min_length=3, max_length=320)
    items: list[GpItemIn] = Field(max_length=500)
    page_id: str | None = None
    next_page_id: str | None = None

    @field_validator("account")
    @classmethod
    def stable_account(cls, value):
        if value.startswith("/u/"):
            raise ValueError("Stable Google account identity required; reload the extension")
        return value



@router.post("/sync")
@serialized
def sync(body: SyncBody, db: Session = Depends(db_dependency)) -> dict:
    """Upsert a page of live-library items. Idempotent — safe to replay a page after a failure."""
    accounts = {a for (a,) in db.query(GpItem.account).distinct().all()}
    if accounts and accounts != {body.account}:
        raise HTTPException(409, "This catalog belongs to another account. Use a separate DATA_DIR.")
    cursor = db.get(ScanCursor, body.account)
    if cursor and cursor.page_id != body.page_id:
        # A page can be replayed after a lost response, but not silently skip a checkpoint.
        if cursor.page_id == body.next_page_id:
            return {"upserted": 0, "inserted": 0, "total": db.query(GpItem).count()}
        raise HTTPException(409, "Scan checkpoint changed; resume the scan")
    keys = [i.media_key for i in body.items]
    existing = {g.media_key: g for g in db.query(GpItem).filter(GpItem.media_key.in_(keys)).all()}

    now = datetime.utcnow()
    inserted = 0
    for item in body.items:
        row = existing.get(item.media_key)
        if row is None:
            row = GpItem(media_key=item.media_key)
            db.add(row)
            inserted += 1
            existing[item.media_key] = row
        # Only overwrite with values we actually received. A later page fetched through a leaner
        # RPC can omit fields, and nulling out a dedup_key we already hold would silently make the
        # item unactionable. `trashed` is exempt — False is a real state, not a missing one.
        for field, value in item.model_dump(exclude={"media_key", "trashed"}).items():
            if value is not None:
                setattr(row, field, value)
        row.trashed = item.trashed
        row.account = body.account
        row.synced_at = now
        catalog_item(db, row)
        media = db.get(Media, row.media_id)
        if media and media.source == "google-photos-thumbnail":
            for name in ("width", "height", "bytes", "taken_at", "duration"):
                if getattr(row, name) is not None:
                    setattr(media, name, getattr(row, name))
            media.rel_name = row.file_name or row.media_key
            media.media_type = "video" if row.duration is not None else "photo"
    if cursor is None:
        cursor = ScanCursor(account=body.account, pages=0, items=0)
        db.add(cursor)
    cursor.page_id = body.next_page_id
    cursor.complete = body.next_page_id is None
    cursor.pages += 1
    cursor.items += len(body.items)

    db.commit()
    return {"upserted": len(body.items), "inserted": inserted, "total": db.query(GpItem).count()}


class LinkBody(BaseModel):
    relink: bool = False


# Legacy Takeout matcher is not exposed by cleanup mode.
def link(body: LinkBody | None = None) -> dict:
    """Run the catalog matcher as a background job (poll via /api/jobs/{id})."""
    if manager.is_running("gp-link"):
        raise HTTPException(409, "Link already running")
    relink = bool(body and body.relink)
    job = manager.submit("gp-link", lambda h: gp_match.link(
        progress=lambda p, m: h.update(p, m), relink=relink))
    return job.to_dict()


@router.get("/status")
def status(db: Session = Depends(db_dependency)) -> dict:
    """Coverage report: how much of the live library we can actually act on."""
    gp_total = db.query(GpItem).filter(GpItem.trashed.is_(False)).count()
    # Both filtered to trashed=False: "linked" is a subset of "currently live", and once real trash
    # runs started completing, an unfiltered `linked` (which kept counting items trashed via this
    # tool) could exceed `gp_total`, producing a negative gp_only and >100% coverage.
    linked = db.query(GpItem).filter(GpItem.media_id.isnot(None), GpItem.trashed.is_(False)).count()
    catalog_total = db.query(Media).count()

    by_method = dict(
        db.query(GpItem.match_method, func.count(GpItem.id))
        .group_by(GpItem.match_method).all()
    )
    accounts = [a for (a,) in db.query(GpItem.account).distinct().all()]
    last_sync = db.query(func.max(GpItem.synced_at)).scalar()

    return {
        "gp_total": gp_total,
        "linked": linked,
        # Live in Google Photos but absent from the Takeout catalog — typically uploaded after the
        # export. Visible and actionable for metadata/albums, but no local hash means no dedup.
        "gp_only": gp_total - linked,
        "catalog_total": catalog_total,
        "catalog_only": max(catalog_total - linked, 0),
        "coverage": round(linked / gp_total, 4) if gp_total else 0.0,
        "ambiguous": by_method.get("ambiguous", 0),
        "unmatched": by_method.get("none", 0),
        "by_method": {k or "pending": v for k, v in by_method.items()},
        "accounts": accounts,
        "last_sync": last_sync.isoformat() if last_sync else None,
    }


@router.get("/unmatched")
def unmatched(side: str = "gp", limit: int = 50, db: Session = Depends(db_dependency)) -> dict:
    """Diagnostics for tuning the matcher before trusting a full scan."""
    if side == "gp":
        q = db.query(GpItem).filter(GpItem.media_id.is_(None), GpItem.trashed.is_(False))
        total = q.count()
        rows = q.order_by(GpItem.taken_at.desc()).limit(limit).all()
        items = [{
            "media_key": g.media_key, "file_name": g.file_name,
            "taken_at": g.taken_at.isoformat() if g.taken_at else None,
            "width": g.width, "height": g.height, "bytes": g.bytes,
            "match_method": g.match_method, "thumb_url": g.thumb_url,
            "product_url": g.product_url,
        } for g in rows]
    elif side == "catalog":
        linked_ids = db.query(GpItem.media_id).filter(GpItem.media_id.isnot(None))
        q = db.query(Media).filter(Media.id.notin_(linked_ids))
        total = q.count()
        rows = q.order_by(Media.taken_at.desc()).limit(limit).all()
        items = [{
            "media_id": m.id, "name": m.rel_name, "media_type": m.media_type,
            "taken_at": m.taken_at.isoformat() if m.taken_at else None,
            "width": m.width, "height": m.height, "bytes": m.bytes,
            "thumb": f"/media/thumbs/{m.thumb_path}" if m.thumb_path else None,
        } for m in rows]
    else:
        raise HTTPException(400, "side must be 'gp' or 'catalog'")

    return {"side": side, "total": total, "items": items}


class ManualLinkBody(BaseModel):
    media_id: int | None  # null clears the link


# Manual relinking is not exposed by cleanup mode.
def manual_link(media_key: str, body: ManualLinkBody, db: Session = Depends(db_dependency)) -> dict:
    """Resolve an ambiguous row by hand. Manual links are never overwritten by the matcher."""
    g = db.query(GpItem).filter(GpItem.media_key == media_key).one_or_none()
    if not g:
        raise HTTPException(404, "gp item not found")

    if body.media_id is None:
        g.media_id, g.match_method, g.match_score = None, "none", None
    else:
        if not db.get(Media, body.media_id):
            raise HTTPException(404, "media not found")
        clash = (db.query(GpItem)
                 .filter(GpItem.media_id == body.media_id, GpItem.media_key != media_key)
                 .first())
        if clash:
            raise HTTPException(409, f"media {body.media_id} already linked to {clash.media_key}")
        g.media_id, g.match_method, g.match_score = body.media_id, "manual", 1.0

    db.commit()
    return {"media_key": g.media_key, "media_id": g.media_id, "match_method": g.match_method}


# ────────────────────────────────────────────────────────────────────────────
# Operations
# ────────────────────────────────────────────────────────────────────────────
def _op_dict(o: GpOperation) -> dict:
    results = json.loads(o.results) if o.results else {"succeeded": [], "failed": []}
    return {
        "id": o.id, "op": o.op, "status": o.status, "account": o.account,
        "review_action_id": o.review_action_id, "dry_run": o.dry_run,
        "total": o.total, "done_count": o.done_count, "failed_count": o.failed_count,
        "cursor": o.cursor, "error": o.error, "note": o.note,
        "failed": results.get("failed", [])[:50],
        "succeeded_count": len(results.get("succeeded", [])),
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "updated_at": o.updated_at.isoformat() if o.updated_at else None,
    }


class CreateOpBody(BaseModel):
    op: str
    keys: list[str] | None = None          # key-only ops (trash, restore)
    entries: list[dict] | None = None      # parameterised ops (set_description, add_to_album, …)
    args: dict | None = None               # applies to every batch, e.g. {"title": "Goa"}
    review_action_id: int | None = None
    account: str = Field(min_length=3, max_length=320)
    dry_run: bool = True
    note: str | None = None


def validate_trash(db: Session, action_id: int | None, account: str, keys: list[str]):
    if not settings.live_trash_enabled:
        raise HTTPException(403, "Live trash is disabled. Set PC_ENABLE_LIVE_TRASH=1 and restart after reviewing dry runs.")
    ra = db.get(ReviewAction, action_id) if action_id else None
    if not ra or ra.kind != "delete" or ra.status != "approved":
        raise HTTPException(409, "An approved trash review is required")
    snapshot = json.loads(ra.payload)
    if snapshot.get("approved_account") != account or account.startswith("/u/"):
        raise HTTPException(409, "Approval account does not match; rescan and review again")
    if not keys or not set(keys).issubset(snapshot.get("approved_keys", [])):
        raise HTTPException(409, "Operation contains keys outside the reviewed selection")
    protected = set(snapshot.get("protected_keys", []))
    if not protected or set(keys) & protected:
        raise HTTPException(409, "Operation conflicts with a protected keeper")
    live_keepers = {k for (k,) in db.query(GpItem.dedup_key).filter(GpItem.account == account,
                       GpItem.dedup_key.in_(protected), GpItem.trashed.is_(False)).all()}
    if live_keepers != protected:
        raise HTTPException(409, "A keeper is no longer live; review again")
    rows = db.query(GpItem).filter(GpItem.account == account, GpItem.dedup_key.in_(keys)).all()
    if {g.dedup_key for g in rows} != set(keys) or any(g.is_owned is not True for g in rows):
        raise HTTPException(409, "Selected keys are missing or not owned by this account")


@router.post("/operations")
@serialized
def create_operation(body: CreateOpBody, db: Session = Depends(db_dependency)) -> dict:
    if body.op not in ALLOWED_OPS:
        raise HTTPException(400, f"op must be one of {sorted(ALLOWED_OPS)}")

    payload = _dedupe_payload(body)
    if not payload or any(not isinstance(k, str) for k in payload):
        raise HTTPException(400, "Provide a nonempty list of content keys")
    if body.op == "restore" and not body.dry_run:
        raise HTTPException(400, "Create restores through the Undo endpoint")
    if body.op == "trash" and not body.dry_run:
        validate_trash(db, body.review_action_id, body.account, payload)
    if body.review_action_id:
        existing = db.query(GpOperation).filter(GpOperation.review_action_id == body.review_action_id,
                     GpOperation.dry_run == body.dry_run, GpOperation.op == body.op).first()
        if existing:
            return _op_dict(existing)

    o = GpOperation(
        op=body.op, review_action_id=body.review_action_id, account=body.account,
        payload=json.dumps(payload), total=len(payload), status="pending",
        dry_run=body.dry_run, note=body.note,
        args=json.dumps(body.args) if body.args else None,
        results=json.dumps({"succeeded": [], "failed": []}),
    )
    db.add(o)
    db.commit()
    return _op_dict(o)


def dedupe_keys(keys: list[str]) -> list[str]:
    """Collapse to one entry per dedup_key, preserving order.

    Shared with every route that builds a GpOperation directly. Mutations key on content identity,
    so two rows can carry the same dedup_key — sending both fires a redundant (idempotent) call and,
    worse, inflates `total`/`done_count` so the progress bar and the audit trail disagree with what
    actually happened.
    """
    return list(dict.fromkeys(k for k in keys if k))


def _dedupe_payload(body: CreateOpBody) -> list:
    """Collapse to one entry per dedup_key, preserving order.

    Google's mutations key on ``dedupKey``, which is *content* identity — the same bytes surfaced
    through a shared album carry a different ``mediaKey`` but the same ``dedupKey``. Submitting both
    fires a redundant op and, worse, makes the success count disagree with reality.
    """
    seen: set[str] = set()
    out: list = []
    if body.keys:
        for k in body.keys:
            if k and k not in seen:
                seen.add(k)
                out.append(k)
    elif body.entries:
        for e in body.entries:
            k = e.get("key")
            if k and k not in seen:
                seen.add(k)
                out.append(e)
    return out


@router.get("/operations")
def list_operations(status: str | None = None, limit: int = 50,
                    db: Session = Depends(db_dependency)) -> dict:
    q = db.query(GpOperation)
    if status:
        q = q.filter(GpOperation.status == status)
    rows = q.order_by(GpOperation.id.desc()).limit(limit).all()
    return {"items": [_op_dict(o) for o in rows]}


@router.get("/operations/{op_id}")
def get_operation(op_id: int, db: Session = Depends(db_dependency)) -> dict:
    o = db.get(GpOperation, op_id)
    if not o:
        raise HTTPException(404, "operation not found")
    return _op_dict(o)


@router.get("/operations/{op_id}/next")
@serialized
def next_batch(op_id: int, size: int = DEFAULT_BATCH, account: str | None = None,
               db: Session = Depends(db_dependency)) -> dict:
    """Hand the extension the next slice to execute, starting from the persisted cursor."""
    o = db.get(GpOperation, op_id)
    if not o:
        raise HTTPException(404, "operation not found")
    if o.status in ("done", "cancelled", "failed", "paused"):
        return {"op": o.op, "status": o.status, "cursor": o.cursor, "batch": [], "done": True}

    # Refuse to act against a different signed-in account than the one the op was built for.
    if not account or account != o.account:
        raise HTTPException(409, f"operation targets account {o.account}, extension is on {account}")

    if o.op not in ALLOWED_OPS:
        raise HTTPException(409, "Unsupported legacy operation")
    if o.op == "trash" and not o.dry_run:
        validate_trash(db, o.review_action_id, o.account, json.loads(o.payload))
    size = max(1, min(size, MAX_BATCH))
    payload = json.loads(o.payload)
    batch = payload[o.cursor:o.cursor + size]

    if not batch:
        o.status = "done"
        o.updated_at = datetime.utcnow()
        db.commit()
        return {"op": o.op, "status": "done", "cursor": o.cursor, "batch": [], "done": True}

    import time
    lease = json.loads(o.args or "{}")
    if lease.get("lease_until", 0) > time.time():
        raise HTTPException(409, "A batch is already in flight; wait before resuming")
    o.args = json.dumps({"issued": batch, "issued_cursor": o.cursor, "lease_until": time.time() + 150})
    db.commit()
    if o.status == "pending":
        o.status = "running"
        o.updated_at = datetime.utcnow()
        db.commit()

    return {
        "op": o.op, "status": o.status, "account": o.account, "dry_run": o.dry_run,
        "cursor": o.cursor, "total": o.total, "batch": batch, "done": False,
        "args": json.loads(o.args) if o.args else {},
    }


class ResultBody(BaseModel):
    cursor: int                       # cursor the batch was issued at — stale reports are ignored
    succeeded: list[str] = []
    failed: list[dict] = []           # [{"key": …, "error": …}]
    error: str | None = None          # batch-level failure => pause, do not advance


@router.post("/operations/{op_id}/result")
@serialized
def report_result(op_id: int, body: ResultBody, db: Session = Depends(db_dependency)) -> dict:
    o = db.get(GpOperation, op_id)
    if not o:
        raise HTTPException(404, "operation not found")

    # A retry that arrives after the cursor already moved must not double-count.
    if body.cursor != o.cursor:
        return {**_op_dict(o), "ignored": "stale cursor"}

    if o.status in ("done", "failed"):
        raise HTTPException(409, "Operation has finished")
    issued = json.loads(o.args or "{}")
    if issued.get("issued_cursor") != body.cursor or not issued.get("issued"):
        raise HTTPException(409, "No matching issued batch")
    if body.error:
        # Batch-level failure. Leave the cursor where it is so the retry re-runs the same slice —
        # every op here is idempotent (trashing an already-trashed key is a no-op).
        o.status = "paused"
        o.error = body.error
        o.updated_at = datetime.utcnow()
        db.commit()
        return _op_dict(o)

    reported = body.succeeded + [f.get("key") for f in body.failed]
    if len(set(reported)) != len(reported) or set(reported) != set(issued["issued"]):
        raise HTTPException(400, "Result must cover exactly the issued keys once each")
    was_cancelled = o.status == "cancelled"
    o.args = None
    results = json.loads(o.results) if o.results else {"succeeded": [], "failed": []}
    results["succeeded"].extend(body.succeeded)
    results["failed"].extend(body.failed)

    # Reflect the mutation back into the catalog. Without this the local view still calls a trashed
    # item live, so it reappears as actionable in the dedup queue and the coverage numbers overcount
    # — the only way to correct it would be a full re-scan of the library.
    _mark_trashed(db, o, body.succeeded)

    advanced = len(body.succeeded) + len(body.failed)
    o.cursor += advanced
    o.done_count = len(results["succeeded"])
    o.failed_count = len(results["failed"])
    o.results = json.dumps(results)
    o.error = None
    o.status = "cancelled" if was_cancelled else "done" if o.cursor >= o.total else "running"
    o.updated_at = datetime.utcnow()

    if o.status == "done":
        _finish_review_action(db, o)

    db.commit()
    return _op_dict(o)


def _mark_trashed(db: Session, o: GpOperation, keys: list[str]) -> None:
    """Keep ``gp_items.trashed`` in step with what an operation actually did.

    Only trash/restore change an item's presence in the library, and a dry run changes nothing.
    """
    if o.dry_run or not keys or o.op not in ("trash", "restore"):
        return
    value = o.op == "trash"
    # Content identity, so one dedup_key can cover several rows (e.g. shared-album copies).
    for row in db.query(GpItem).filter(GpItem.dedup_key.in_(keys), GpItem.account == o.account).all():
        row.trashed = value


def _finish_review_action(db: Session, o: GpOperation) -> None:
    """Close out the ReviewAction once its trash operation actually completed."""
    if o.op != "trash" or not o.review_action_id or o.dry_run:
        return
    ra = db.get(ReviewAction, o.review_action_id)
    if ra and ra.status != "done" and not o.failed_count:
        ra.status = "done"
        ra.applied_at = datetime.utcnow()
        ra.note = f"Trashed {o.done_count} item(s) in Google Photos via extension (op {o.id})."


@router.post("/operations/{op_id}/cancel")
@serialized
def cancel_operation(op_id: int, db: Session = Depends(db_dependency)) -> dict:
    o = db.get(GpOperation, op_id)
    if not o:
        raise HTTPException(404, "operation not found")
    if o.status not in ("done", "failed"):
        o.status = "cancelled"
        o.updated_at = datetime.utcnow()
        db.commit()
    return _op_dict(o)


@router.post("/operations/{op_id}/resume")
@serialized
def resume_operation(op_id: int, db: Session = Depends(db_dependency)) -> dict:
    o = db.get(GpOperation, op_id)
    if not o:
        raise HTTPException(404, "operation not found")
    if o.status in ("paused", "cancelled"):
        o.status = "running" if o.cursor else "pending"
        o.error = None
        o.updated_at = datetime.utcnow()
        db.commit()
    return _op_dict(o)


@router.post("/operations/{op_id}/undo")
@serialized
def undo_operation(op_id: int, db: Session = Depends(db_dependency)) -> dict:
    """Build the inverse operation from what actually succeeded.

    Only ``trash`` is reversible, and only while Google retains the items in its bin. This is
    the safety net that makes one-click trashing acceptable at all.
    """
    o = db.get(GpOperation, op_id)
    if not o:
        raise HTTPException(404, "operation not found")
    if o.status not in ("done", "cancelled", "failed") or json.loads(o.args or "{}").get("issued"):
        raise HTTPException(409, "Stop and resolve any in-flight batch before Undo")
    if o.op != "trash":
        raise HTTPException(400, f"cannot undo '{o.op}' — only trash is reversible")
    if o.dry_run:
        raise HTTPException(400, "dry run made no changes to undo")

    succeeded = (json.loads(o.results) if o.results else {}).get("succeeded", [])
    if not succeeded:
        raise HTTPException(400, "nothing succeeded, nothing to undo")

    existing = db.query(GpOperation).filter(GpOperation.op == "restore", GpOperation.note == f"Undo of operation {o.id}").first()
    if existing:
        return _op_dict(existing)
    succeeded = dedupe_keys(succeeded)
    inverse = GpOperation(
        op="restore", account=o.account, payload=json.dumps(succeeded), dry_run=False,
        total=len(succeeded), status="pending",
        results=json.dumps({"succeeded": [], "failed": []}),
        note=f"Undo of operation {o.id}",
    )
    db.add(inverse)

    if o.review_action_id:
        ra = db.get(ReviewAction, o.review_action_id)
        if ra:
            ra.status = "dismissed"
            ra.applied_at = None
            ra.note = f"Restore requested — restore queued as operation for {len(succeeded)} item(s)."

    db.commit()
    return _op_dict(inverse)


@router.get("/scan-state")
def scan_state(account: str, db: Session = Depends(db_dependency)):
    row = db.get(ScanCursor, account)
    return {"page_id": row.page_id if row else None, "complete": row.complete if row else False,
            "pages": row.pages if row else 0, "items": row.items if row else 0}

class StartScan(BaseModel):
    account: str
    restart: bool = False

@router.post("/scan-start")
@serialized
def scan_start(body: StartScan, db: Session = Depends(db_dependency)):
    accounts = {a for (a,) in db.query(GpItem.account).distinct().all()}
    if body.account.startswith("/u/") or not body.account:
        raise HTTPException(400, "Stable account identity required")
    if accounts and accounts != {body.account}:
        raise HTTPException(409, "Use a separate DATA_DIR for a different Google account")
    row = db.get(ScanCursor, body.account)
    if row and (body.restart or row.complete):
        row.page_id, row.complete, row.pages, row.items = None, False, 0, 0
        db.commit()
    return scan_state(body.account, db)
