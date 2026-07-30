"""Duplicate review endpoints. Approving a group queues its non-keepers as `delete` review-actions.
Nothing is ever deleted here."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, aliased

from ..config import settings
from ..db import db_dependency
from ..jobs import manager
from ..models import DupGroup, DupMember, GpItem, Media, ReviewAction
from ..pipeline import dedup as dedup_pipeline

router = APIRouter(prefix="/api/dedup", tags=["dedup"])


def _gp_links(db: Session, media_ids: list[int]) -> dict[int, dict]:
    """Map media_id -> its live Google Photos identifiers, for the ids we have them for.

    ``dedup_key`` is what every mutation keys on; ``media_key`` is only good for URLs and albums.
    """
    if not media_ids:
        return {}
    rows = (db.query(GpItem)
            .filter(GpItem.media_id.in_(media_ids), GpItem.trashed.is_(False))
            .all())
    return {g.media_id: {"media_key": g.media_key, "dedup_key": g.dedup_key,
                         "product_url": g.product_url, "thumb_url": g.thumb_url,
                         "account": g.account}
            for g in rows if g.media_id}


def _is_live(gp: dict | None) -> bool:
    """Live = present in Google Photos AND carrying the key every mutation needs."""
    return bool(gp and gp.get("dedup_key"))


def _keeper_rank(db: Session, media_id: int, score: float | None) -> tuple:
    """Ordering used when the original keeper is gone and we must pick from what survives.

    Mirrors the dedup pipeline's own preference: similarity score, then pixel count, then sharpness.
    """
    m = db.get(Media, media_id)
    return (
        score or 0.0,
        (m.width or 0) * (m.height or 0) if m else 0,
        m.blur_score or 0.0 if m else 0.0,
    )


class DedupBody(BaseModel):
    use_clip: bool = True


@router.post("/run")
def run(body: DedupBody) -> dict:
    if manager.is_running("dedup"):
        raise HTTPException(409, "Dedup already running")
    job = manager.submit("dedup", lambda h: dedup_pipeline.run(
        use_clip=body.use_clip, progress=lambda p, m: h.update(p, m)))
    return job.to_dict()


@router.get("/groups")
def groups(limit: int = 20, offset: int = 0, db: Session = Depends(db_dependency)) -> dict:
    base = db.query(DupGroup).filter(DupGroup.reviewed.is_(False))
    total = base.count()

    # Order groups by the keeper photo's capture date, newest first (like Google Photos).
    # SQLite sorts NULLs last on DESC, so undated groups fall to the end.
    keeper = aliased(Media)
    rows = (base.outerjoin(keeper, keeper.id == DupGroup.keeper_media_id)
            .order_by(keeper.taken_at.desc())
            .offset(offset).limit(limit).all())

    items = []
    for g in rows:
        members = []
        keeper_dt = None
        member_dts = []
        for mem in g.members:
            m = db.get(Media, mem.media_id)
            if not m:
                continue
            if m.taken_at:
                member_dts.append(m.taken_at)
                if m.id == g.keeper_media_id:
                    keeper_dt = m.taken_at
            members.append({
                "media_id": m.id, "thumb": f"/media/thumbs/{m.thumb_path}" if m.thumb_path else None,
                "name": m.rel_name, "score": mem.score,
                "width": m.width, "height": m.height, "blur_score": m.blur_score,
                "taken_at": m.taken_at.isoformat() if m.taken_at else None,
                "is_keeper": m.id == g.keeper_media_id,
            })
        members.sort(key=lambda x: x["score"] or 0, reverse=True)
        # Group date = keeper's date, else newest member date, else undated
        dt = keeper_dt or (max(member_dts) if member_dts else None)
        date_key = dt.date().isoformat() if dt else "no-date"
        items.append({"id": g.id, "method": g.method, "keeper_media_id": g.keeper_media_id,
                      "date_key": date_key, "members": members})

    total_dupes = db.query(DupMember).join(DupGroup).filter(DupGroup.reviewed.is_(False)).count()
    return {"total": total, "total_dupes": total_dupes, "offset": offset, "items": items}


class KeeperBody(BaseModel):
    media_id: int


@router.post("/groups/{group_id}/keeper")
def set_keeper(group_id: int, body: KeeperBody, db: Session = Depends(db_dependency)) -> dict:
    g = db.get(DupGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    g.keeper_media_id = body.media_id
    db.commit()
    return {"id": g.id, "keeper_media_id": g.keeper_media_id}


@router.get("/extension-queue")
def extension_queue(limit: int = 20, db: Session = Depends(db_dependency)) -> dict:
    """Return top N unreviewed dup groups ready for the Chrome extension.
    No approval step needed — the extension lets users act directly."""
    base = db.query(DupGroup).filter(DupGroup.reviewed.is_(False))
    total = base.count()

    # Newest-first using keeper date (same order as UI)
    keeper = aliased(Media)
    rows = (base.outerjoin(keeper, keeper.id == DupGroup.keeper_media_id)
            .order_by(keeper.taken_at.desc())
            .limit(limit).all())

    groups = []
    for g in rows:
        keeper_m = db.get(Media, g.keeper_media_id) if g.keeper_media_id else None
        dupes = [m for mem in g.members
                 if (m := db.get(Media, mem.media_id)) and m.id != g.keeper_media_id]
        if not dupes:
            continue
        groups.append((g, keeper_m, dupes))

    # One query for every media id on the page rather than one per member.
    all_ids = [m.id for _, k, ds in groups for m in ([k] if k else []) + ds]
    gp = _gp_links(db, all_ids)

    def brief(m: Media) -> dict:
        link = gp.get(m.id)
        return {
            "media_id": m.id,
            "name":     m.rel_name,
            "thumb":    f"/media/thumbs/{m.thumb_path}" if m.thumb_path else None,
            "width":    m.width,
            "height":   m.height,
            "taken_at": m.taken_at.isoformat() if m.taken_at else None,
            "gp":       link,
            # False = not in Google Photos. Usually already deleted, so there is nothing to act on
            # for this member — not a failure, and not something manual linking can fix.
            "live":     _is_live(link),
        }

    items = []
    for g, keeper_m, dupes in groups:
        del_m = dupes[0]  # primary duplicate (lowest quality)
        live_dupes = [d for d in dupes if _is_live(gp.get(d.id))]
        keeper_live = bool(keeper_m) and _is_live(gp.get(keeper_m.id))

        # Actionable when the keeper survives AND there is at least one live duplicate to remove.
        # Duplicates that are already gone from Google Photos are simply skipped — requiring *every*
        # member to be live conflated "already deleted" with "unsafe" and blocked ~700 groups that
        # were perfectly safe to act on. The invariant that matters is that the keeper remains.
        actionable = keeper_live and bool(live_dupes)

        live_total = len(live_dupes) + (1 if keeper_live else 0)
        member_count = len(dupes) + 1
        oversized = member_count > settings.dedup_max_group_size

        if oversized:
            # Not a duplicate set — a chaining artifact from the old detection. Flagged rather than
            # hidden, because real duplicates are buried in there.
            reason = "oversized"
        elif live_total <= 1:
            reason = "already-deduplicated"   # 0 or 1 copies left in Google Photos: nothing to do
        elif not keeper_live:
            reason = "keeper-missing"         # survivors exist, but the chosen keeper isn't one
        else:
            reason = None

        items.append({
            "group_id":   g.id,
            "method":     g.method,
            "date_key":   (keeper_m.taken_at.date().isoformat() if keeper_m and keeper_m.taken_at
                           else (del_m.taken_at.date().isoformat() if del_m.taken_at else "no-date")),
            "keeper_reason": g.keeper_reason,
            "actionable": actionable and not oversized,
            "blocked_reason": reason,
            "oversized": oversized,
            "member_count": member_count,
            "live_count": live_total,
            "keeper":     brief(keeper_m) if keeper_m else None,
            "deletes":    [brief(d) for d in dupes],
        })

    return {"total_remaining": total, "items": items}


@router.post("/groups/{group_id}/ignore")
def ignore(group_id: int, db: Session = Depends(db_dependency)) -> dict:
    """Mark a group as reviewed without queuing anything for deletion (not a real duplicate)."""
    g = db.get(DupGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    g.reviewed = True
    db.commit()
    return {"ignored": True}


class ApproveBody(BaseModel):
    exclude_ids: list[int] | None = None  # media_ids the user wants to keep too (not delete)


def _group_deletes(db: Session, g: DupGroup, excluded: set[int]) -> list[dict]:
    out = []
    for mem in g.members:
        if mem.media_id == g.keeper_media_id or mem.media_id in excluded:
            continue
        m = db.get(Media, mem.media_id)
        if m:
            out.append({"media_id": m.id, "name": m.rel_name, "abs_path": m.abs_path,
                        "taken_at": m.taken_at.isoformat() if m.taken_at else None,
                        "place_name": m.place_name})
    return out


@router.post("/groups/{group_id}/approve")
def approve(group_id: int, body: ApproveBody | None = None, db: Session = Depends(db_dependency)) -> dict:
    g = db.get(DupGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    excluded = set((body.exclude_ids if body else None) or [])
    to_delete = _group_deletes(db, g, excluded)

    action_id = None
    if to_delete:
        a = ReviewAction(
            kind="delete",
            payload=json.dumps({"reason": f"duplicate ({g.method})",
                                "keeper_media_id": g.keeper_media_id, "items": to_delete}),
        )
        db.add(a)
        db.flush()
        action_id = a.id
    g.reviewed = True
    db.commit()
    return {"queued_for_deletion": len(to_delete), "excluded": len(excluded),
            "action_id": action_id}


@router.post("/rescore")
def rescore() -> dict:
    """Re-pick the keeper in every unreviewed group using the current quality rules.

    Cheap — reads no image files. Run it after the face-analysis pass to unlock the face-aware
    signals (clearest face, subject size, centering) without re-detecting duplicates.
    """
    if manager.is_running("dedup"):
        raise HTTPException(409, "Dedup already running")
    job = manager.submit("dedup", lambda h: dedup_pipeline.rescore(
        progress=lambda p, m: h.update(p, m)))
    return job.to_dict()


class CleanupBody(BaseModel):
    apply: bool = False


@router.post("/cleanup")
def cleanup(body: CleanupBody | None = None) -> dict:
    """Remove duplicate *groups* (same photos found by two layers) and report chaining artifacts.

    Distinct from `/reconcile`, which is about what still exists in Google Photos. This one is about
    the detection output itself being malformed. Preview by default.
    """
    body = body or CleanupBody()
    return dedup_pipeline.cleanup(apply=body.apply)


class TrashAllBody(BaseModel):
    exclude_media_ids: list[int] | None = None   # "keep this too" still wins
    live_only: bool = True


@router.post("/groups/{group_id}/trash-all")
def trash_all(group_id: int, body: TrashAllBody | None = None,
              db: Session = Depends(db_dependency)) -> dict:
    """Queue **every** member of a group for trashing, keeper included.

    Separate from `approve` / `approve-bulk` on purpose. Those enforce "the keeper survives", which
    is the right default and must not be reachable by a stray flag. This endpoint is the explicit
    opposite — for a group that is simply junk — so it takes one group at a time, chosen by looking
    at it. There is deliberately no bulk equivalent.

    Still only trash: recoverable from Google's bin for 60 days and undoable like any other
    operation. Local originals are untouched.
    """
    body = body or TrashAllBody()
    g = db.get(DupGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")

    excluded = set(body.exclude_media_ids or ())
    member_ids = [mem.media_id for mem in g.members if mem.media_id not in excluded]
    gp = _gp_links(db, member_ids)

    items = []
    skipped_not_live = 0
    for mid in member_ids:
        if body.live_only and not _is_live(gp.get(mid)):
            skipped_not_live += 1
            continue
        m = db.get(Media, mid)
        if m:
            items.append({"media_id": m.id, "name": m.rel_name, "abs_path": m.abs_path,
                          "taken_at": m.taken_at.isoformat() if m.taken_at else None,
                          "place_name": m.place_name})

    if not items:
        raise HTTPException(400, "nothing in this group is live in Google Photos")

    # No keeper recorded anywhere in the payload — that absence is what tells the apply step every
    # item may go. Keepers are protected by *presence* of a keeper id, not by a flag.
    action = ReviewAction(
        kind="delete",
        status="approved",
        payload=json.dumps({"reason": f"entire group ({g.method})",
                            "keeper_media_id": None, "group_ids": [g.id], "items": items}),
        note=f"Whole group {g.id} trashed — no copy kept.",
    )
    db.add(action)
    g.reviewed = True
    db.flush()
    action_id = action.id
    db.commit()

    return {"action_id": action_id, "queued_for_deletion": len(items),
            "excluded": len(excluded), "skipped_not_live": skipped_not_live}


class ReconcileBody(BaseModel):
    apply: bool = False          # False = preview only, change nothing
    repick_keeper: bool = True
    close_resolved: bool = True


@router.post("/reconcile")
def reconcile(body: ReconcileBody | None = None, db: Session = Depends(db_dependency)) -> dict:
    """Reconcile duplicate groups against what actually still exists in Google Photos.

    A Takeout export is a snapshot. Anything deleted from Google Photos since then still has a
    catalog row, so groups accumulate that were long ago resolved by hand. Two fixes:

    * **close_resolved** — a group with 0 or 1 surviving copies has nothing left to deduplicate.
      Mark it reviewed. This is the bulk of the backlog.
    * **repick_keeper** — if the chosen keeper is gone but two or more copies survive, promote the
      best survivor to keeper so the group becomes actionable again.

    Defaults to a preview (`apply=false`) because it touches a lot of rows at once.
    """
    body = body or ReconcileBody()

    groups = db.query(DupGroup).filter(DupGroup.reviewed.is_(False)).all()
    all_media_ids = [mem.media_id for g in groups for mem in g.members]
    gp = _gp_links(db, all_media_ids)

    closed = repicked = actionable = untouched = 0
    samples: list[dict] = []

    for g in groups:
        live = [mem for mem in g.members if _is_live(gp.get(mem.media_id))]

        if len(live) <= 1:
            if body.close_resolved:
                if body.apply:
                    g.reviewed = True
                closed += 1
                if len(samples) < 10:
                    m = db.get(Media, g.keeper_media_id) if g.keeper_media_id else None
                    samples.append({"group_id": g.id, "action": "closed",
                                    "live_copies": len(live),
                                    "keeper": m.rel_name if m else None})
            else:
                untouched += 1
            continue

        keeper_live = any(mem.media_id == g.keeper_media_id for mem in live)
        if not keeper_live and body.repick_keeper:
            best = max(live, key=lambda mem: _keeper_rank(db, mem.media_id, mem.score))
            if body.apply:
                g.keeper_media_id = best.media_id
            repicked += 1
            if len(samples) < 10:
                m = db.get(Media, best.media_id)
                samples.append({"group_id": g.id, "action": "repicked-keeper",
                                "new_keeper": m.rel_name if m else None,
                                "live_copies": len(live)})
            actionable += 1
        elif keeper_live:
            actionable += 1
        else:
            untouched += 1

    if body.apply:
        db.commit()

    return {
        "applied": body.apply,
        "groups_examined": len(groups),
        "closed_already_deduplicated": closed,
        "keepers_repicked": repicked,
        "actionable_after": actionable,
        "untouched": untouched,
        "samples": samples,
    }


class BulkApproveBody(BaseModel):
    group_ids: list[int] | None = None   # omit to take every actionable unreviewed group
    limit: int = 500                     # cap when group_ids is omitted
    actionable_only: bool = True         # skip groups not fully linked to Google Photos
    # media_ids the user marked "keep this too". CLIP groups near-duplicates, so a group is often
    # several genuinely different shots rather than copies — keeping more than one is normal.
    exclude_media_ids: list[int] | None = None


@router.post("/approve-bulk")
def approve_bulk(body: BulkApproveBody | None = None,
                 db: Session = Depends(db_dependency)) -> dict:
    """Approve many duplicate groups into ONE delete action.

    With thousands of unreviewed groups, approving them individually would produce thousands of
    review actions and thousands of one-item operations — each paying the executor's per-batch
    pacing. A single action collapses the whole run into one resumable operation, and stays a
    single thing to approve, undo, or walk away from.
    """
    body = body or BulkApproveBody()

    q = db.query(DupGroup).filter(DupGroup.reviewed.is_(False))
    if body.group_ids:
        q = q.filter(DupGroup.id.in_(body.group_ids))
        groups = q.all()
    else:
        keeper = aliased(Media)
        groups = (q.outerjoin(keeper, keeper.id == DupGroup.keeper_media_id)
                  .order_by(keeper.taken_at.desc())
                  .limit(max(1, body.limit)).all())

    items: list[dict] = []
    used_groups: list[int] = []
    skipped_unlinked = 0
    skipped_all_kept = 0
    excluded = set(body.exclude_media_ids or ())

    # Resolve linkage in one pass rather than per group. Only a row carrying a dedup_key is
    # actually actionable — that is the key every mutation takes.
    all_media_ids = [mem.media_id for g in groups for mem in g.members]
    live = {mid for mid, link in _gp_links(db, all_media_ids).items() if _is_live(link)}

    skipped_oversized = 0
    for g in groups:
        # An enormous group is a clustering artifact, not a duplicate set. Sweeping one up in a bulk
        # run would bin hundreds of distinct photos to keep one, so bulk never touches them — they
        # stay available for deliberate, individual review.
        if len(g.members) > settings.dedup_max_group_size:
            skipped_oversized += 1
            continue

        # The keeper must survive; duplicates already gone from Google Photos are simply nothing to
        # do, not a reason to refuse the whole group.
        if body.actionable_only and g.keeper_media_id not in live:
            skipped_unlinked += 1
            continue

        deletes = [d for d in _group_deletes(db, g, excluded)
                   if not body.actionable_only or d["media_id"] in live]
        if not deletes:
            # Distinguish "nothing survives to trash" from "the user chose to keep all of them" —
            # the latter must NOT be marked reviewed, or a deliberate keep-everything silently
            # consumes the group.
            if excluded & {mem.media_id for mem in g.members}:
                skipped_all_kept += 1
            else:
                skipped_unlinked += 1
            continue
        # Carry the keeper per item so a mixed-group action can still name what it kept.
        for d in deletes:
            d["keeper_media_id"] = g.keeper_media_id
            d["group_id"] = g.id
        items.extend(deletes)
        used_groups.append(g.id)
        g.reviewed = True

    action_id = None
    if items:
        a = ReviewAction(
            kind="delete",
            payload=json.dumps({
                "reason": f"duplicates ({len(used_groups)} groups)",
                "keeper_media_id": None,
                "group_ids": used_groups,
                "items": items,
            }),
        )
        db.add(a)
        db.flush()
        action_id = a.id

    db.commit()
    return {"action_id": action_id, "groups": len(used_groups),
            "queued_for_deletion": len(items), "skipped_unlinked": skipped_unlinked,
            "skipped_all_kept": skipped_all_kept, "skipped_oversized": skipped_oversized,
            "excluded": len(excluded)}
