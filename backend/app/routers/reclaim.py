"""Disk-space reclaim — move redundant local duplicates to quarantine.

Preview is free and read-only; ``/run`` is a background job because moving tens of thousands of
files takes a while. Every run writes a manifest so ``/undo`` can put everything back.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..db import db_dependency
from ..jobs import manager
from ..models import GpItem, Media
from ..pipeline import reclaim as reclaim_pipeline

router = APIRouter(prefix="/api/reclaim", tags=["reclaim"])


@router.get("/summary")
def summary(db: Session = Depends(db_dependency)) -> dict:
    """Where the library's bytes actually are, split by how recoverable each file is."""
    total_n, total_b = db.query(func.count(Media.id), func.sum(Media.bytes)).one()

    live = db.query(GpItem.media_id).filter(
        GpItem.media_id.isnot(None), GpItem.dedup_key.isnot(None), GpItem.trashed.is_(False))

    backed_n, backed_b = (db.query(func.count(Media.id), func.sum(Media.bytes))
                          .filter(Media.id.in_(live)).one())
    sole_n, sole_b = (db.query(func.count(Media.id), func.sum(Media.bytes))
                      .filter(Media.id.notin_(live)).one())
    arch_n, arch_b = (db.query(func.count(Media.id), func.sum(Media.bytes))
                      .filter(Media.archived_at.isnot(None)).one())

    def block(n, b, note):
        return {"files": n or 0, "bytes": b or 0,
                "gb": round((b or 0) / 1024 ** 3, 2), "note": note}

    return {
        "total": block(total_n, total_b, "everything in the catalog"),
        "backed_by_google": block(backed_n, backed_b, "also live in Google Photos"),
        # The counter-intuitive one: these look the most 'done' but are the least safe to delete.
        "sole_copies": block(sole_n, sole_b,
                             "on disk only — no longer in Google Photos, so this is the last copy"),
        "archived": block(arch_n, arch_b, "already moved to quarantine"),
        "quarantine_dir": str(settings.quarantine_path),
    }


class PlanBody(BaseModel):
    include_videos: bool = False
    limit: int | None = None


@router.post("/preview")
def preview(body: PlanBody | None = None) -> dict:
    body = body or PlanBody()
    return reclaim_pipeline.plan(include_videos=body.include_videos, limit=body.limit)


@router.post("/run")
def run(body: PlanBody | None = None) -> dict:
    if manager.is_running("reclaim"):
        raise HTTPException(409, "Reclaim already running")
    body = body or PlanBody()
    job = manager.submit("reclaim", lambda h: reclaim_pipeline.run(
        include_videos=body.include_videos, limit=body.limit,
        progress=lambda p, m: h.update(p, m)))
    return job.to_dict()


@router.get("/runs")
def runs() -> dict:
    return {"items": reclaim_pipeline.runs()}


@router.post("/runs/{run_id}/undo")
def undo(run_id: str) -> dict:
    if manager.is_running("reclaim"):
        raise HTTPException(409, "Reclaim already running")
    job = manager.submit("reclaim", lambda h: reclaim_pipeline.undo(
        run_id, progress=lambda p, m: h.update(p, m)))
    return job.to_dict()
