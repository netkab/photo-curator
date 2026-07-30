"""Ingest + environment endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..jobs import manager
from ..pipeline import analyze, ingest_takeout
from ..services import ai_models

router = APIRouter(prefix="/api", tags=["ingest"])


class IngestBody(BaseModel):
    takeout_dir: str | None = None


class AnalyzeBody(BaseModel):
    only: str | None = None
    limit: int | None = 200
    force: bool = False


@router.get("/env")
def env() -> dict:
    return ai_models.environment_report()


@router.post("/ingest")
def start_ingest(body: IngestBody) -> dict:
    if manager.is_running("ingest"):
        raise HTTPException(409, "Ingest already running")
    target_dir = body.takeout_dir or str(settings.takeout_dir)
    job = manager.submit(
        "ingest",
        lambda h: ingest_takeout.ingest(target_dir, progress=lambda p, m: h.update(p, m)),
    )
    return job.to_dict()


@router.post("/analyze")
def start_analyze(body: AnalyzeBody) -> dict:
    if manager.is_running("analyze"):
        raise HTTPException(409, "Analyze already running")
    job = manager.submit(
        "analyze",
        lambda h: analyze.run(only=body.only, limit=body.limit, force=body.force,
                              progress=lambda p, m: h.update(p, m)),
    )
    return job.to_dict()
