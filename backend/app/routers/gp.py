"""Google Photos bridge — library sync, catalog linking, and resumable batch operations.

The Chrome extension is the only thing that can reach Google Photos (via its internal
``batchexecute`` API, riding the user's own signed-in session). This router is the backend half:

* ``/sync``       the extension posts pages of the live library; we upsert ``gp_items``
* ``/link``       match those rows to the local Takeout catalog
* ``/operations`` durable work queues the extension drains

**The server owns the cursor.** The extension asks for the next slice, executes it, and reports back;
we advance ``cursor`` and persist the result. Killing the browser mid-run therefore loses at most one
batch. That is deliberate — the reference implementation (mtalcott/google-photos-deduper) keeps
progress only in memory and its users lose thousands of items when a run stalls.

Nothing here authorises a deletion. A ``trash`` operation carries the ``review_action_id`` that was
approved in the Review Queue; this router only schedules and tracks the execution of it.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import db_dependency
from ..jobs import manager
from ..models import GpItem, GpOperation, Media, ReviewAction
from ..pipeline import gp_match

router = APIRouter(prefix="/api/gp", tags=["gp"])

# Operations that mutate the library. Anything not listed is rejected outright — this is the
# allow-list that keeps "permanent delete" and "locked folder" off the table by construction.
ALLOWED_OPS = {"trash", "restore", "set_description", "set_timestamp", "add_to_album"}
# Ops that must be able to point back at the approved ReviewAction that authorised them.
GATED_OPS = {"trash"}

MAX_BATCH = 250
DEFAULT_BATCH = 100


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
    account: str = "/u/0"
    items: list[GpItemIn]


@router.post("/sync")
def sync(body: SyncBody, db: Session = Depends(db_dependency)) -> dict:
    """Upsert a page of live-library items. Idempotent — safe to replay a page after a failure."""
    if not body.items:
        return {"upserted": 0, "inserted": 0, "total": db.query(GpItem).count()}

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
        # Only overwrite with values we actually received. A later page fetched through a leaner
        # RPC can omit fields, and nulling out a dedup_key we already hold would silently make the
        # item unactionable. `trashed` is exempt — False is a real state, not a missing one.
        for field, value in item.model_dump(exclude={"media_key", "trashed"}).items():
            if value is not None:
                setattr(row, field, value)
        row.trashed = item.trashed
        row.account = body.account
        row.synced_at = now
        # media_id / match_method are deliberately untouched: re-syncing must never break a link.

    db.commit()
    return {"upserted": len(body.items), "inserted": inserted, "total": db.query(GpItem).count()}


class LinkBody(BaseModel):
    relink: bool = False


@router.post("/link")
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


@router.post("/items/{media_key}/link")
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
    account: str = "/u/0"
    dry_run: bool = False
    note: str | None = None


@router.post("/operations")
def create_operation(body: CreateOpBody, db: Session = Depends(db_dependency)) -> dict:
    if body.op not in ALLOWED_OPS:
        raise HTTPException(400, f"op must be one of {sorted(ALLOWED_OPS)}")

    # A dry run is exempt from the review gate: the extension returns before issuing the RPC, so it
    # provably cannot change anything, and requiring an approval would mean consuming real review
    # state just to test the plumbing.
    if body.op in GATED_OPS and not body.dry_run:
        if body.review_action_id is None:
            raise HTTPException(400, f"'{body.op}' requires an approved review_action_id")
        ra = db.get(ReviewAction, body.review_action_id)
        if not ra:
            raise HTTPException(404, "review action not found")
        if ra.status not in ("approved", "done"):
            raise HTTPException(409, f"review action {ra.id} is '{ra.status}', not approved")

    payload = _dedupe_payload(body)
    if not payload:
        raise HTTPException(400, "nothing to do")

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
def next_batch(op_id: int, size: int = DEFAULT_BATCH, account: str | None = None,
               db: Session = Depends(db_dependency)) -> dict:
    """Hand the extension the next slice to execute, starting from the persisted cursor."""
    o = db.get(GpOperation, op_id)
    if not o:
        raise HTTPException(404, "operation not found")
    if o.status in ("done", "cancelled", "failed"):
        return {"op": o.op, "status": o.status, "cursor": o.cursor, "batch": [], "done": True}

    # Refuse to act against a different signed-in account than the one the op was built for.
    if account and account != o.account:
        raise HTTPException(409, f"operation targets account {o.account}, extension is on {account}")

    size = max(1, min(size, MAX_BATCH))
    payload = json.loads(o.payload)
    batch = payload[o.cursor:o.cursor + size]

    if not batch:
        o.status = "done"
        o.updated_at = datetime.utcnow()
        db.commit()
        return {"op": o.op, "status": "done", "cursor": o.cursor, "batch": [], "done": True}

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
def report_result(op_id: int, body: ResultBody, db: Session = Depends(db_dependency)) -> dict:
    o = db.get(GpOperation, op_id)
    if not o:
        raise HTTPException(404, "operation not found")

    # A retry that arrives after the cursor already moved must not double-count.
    if body.cursor != o.cursor:
        return {**_op_dict(o), "ignored": "stale cursor"}

    if body.error:
        # Batch-level failure. Leave the cursor where it is so the retry re-runs the same slice —
        # every op here is idempotent (trashing an already-trashed key is a no-op).
        o.status = "paused"
        o.error = body.error
        o.updated_at = datetime.utcnow()
        db.commit()
        return _op_dict(o)

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
    o.status = "done" if o.cursor >= o.total else "running"
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
    for row in db.query(GpItem).filter(GpItem.dedup_key.in_(keys)).all():
        row.trashed = value


def _finish_review_action(db: Session, o: GpOperation) -> None:
    """Close out the ReviewAction once its trash operation actually completed."""
    if o.op != "trash" or not o.review_action_id or o.dry_run:
        return
    ra = db.get(ReviewAction, o.review_action_id)
    if ra and ra.status != "done":
        ra.status = "done"
        ra.applied_at = datetime.utcnow()
        ra.note = f"Trashed {o.done_count} item(s) in Google Photos via extension (op {o.id})."


@router.post("/operations/{op_id}/cancel")
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
def undo_operation(op_id: int, db: Session = Depends(db_dependency)) -> dict:
    """Build the inverse operation from what actually succeeded.

    Only ``trash`` is reversible, and only because Google's bin retains items for 60 days. This is
    the safety net that makes one-click trashing acceptable at all.
    """
    o = db.get(GpOperation, op_id)
    if not o:
        raise HTTPException(404, "operation not found")
    if o.op != "trash":
        raise HTTPException(400, f"cannot undo '{o.op}' — only trash is reversible")
    if o.dry_run:
        raise HTTPException(400, "dry run made no changes to undo")

    succeeded = (json.loads(o.results) if o.results else {}).get("succeeded", [])
    if not succeeded:
        raise HTTPException(400, "nothing succeeded, nothing to undo")

    succeeded = dedupe_keys(succeeded)
    inverse = GpOperation(
        op="restore", account=o.account, payload=json.dumps(succeeded),
        total=len(succeeded), status="pending",
        results=json.dumps({"succeeded": [], "failed": []}),
        note=f"Undo of operation {o.id}",
    )
    db.add(inverse)

    if o.review_action_id:
        ra = db.get(ReviewAction, o.review_action_id)
        if ra:
            ra.status = "approved"
            ra.applied_at = None
            ra.note = f"Undone — restore queued as operation for {len(succeeded)} item(s)."

    db.commit()
    return _op_dict(inverse)
