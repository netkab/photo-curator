"""FastAPI application entrypoint."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .jobs import manager
from .routers import catalog, dedup, enhance, gp, ingest, maps, reclaim, review, videos
from ._path_fix import ensure_tool_paths

app = FastAPI(title="Photo Curator", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    # Allow the React dev server + any Chrome extension (origin is chrome-extension://<id>)
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Private Network Access header — lets Chrome extensions and HTTPS pages
# fetch from http://localhost without being blocked by PNA restrictions.
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

class PrivateNetworkMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

app.add_middleware(PrivateNetworkMiddleware)


@app.on_event("startup")
def _startup() -> None:
    ensure_tool_paths()   # inject known tool locations into PATH before anything else runs
    settings.ensure_dirs()
    init_db()


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "version": app.version,
        "uploads_enabled": settings.uploads_enabled,
        "places_enabled": bool(settings.places_api_key),
        "data_dir": str(settings.data_dir),
    }


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return [j.to_dict() for j in manager.list()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = manager.get(job_id)
    return job.to_dict() if job else {"error": "not found"}


app.include_router(ingest.router)
app.include_router(catalog.router)
app.include_router(dedup.router)
app.include_router(enhance.router)
app.include_router(maps.router)
app.include_router(videos.router)
app.include_router(review.router)
app.include_router(gp.router)
app.include_router(reclaim.router)

# Serve thumbnails + derived media so the UI can display them.
app.mount("/media/thumbs", StaticFiles(directory=str(settings.thumbs_dir)), name="thumbs")
app.mount("/media/derived", StaticFiles(directory=str(settings.derived_dir)), name="derived")
