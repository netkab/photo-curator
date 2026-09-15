"""Loopback-only cleanup API. Unrelated legacy routes are intentionally not mounted."""
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from .config import settings
from .db import init_db
from .jobs import manager
from .routers import catalog, dedup, gp, review, direct, accidents
from .security import LocalSecurityMiddleware, allowed_origins, local_token, router as auth_router

@asynccontextmanager
async def lifespan(app):
    settings.ensure_dirs()
    local_token()
    init_db()
    yield

app = FastAPI(title="Photo Curator", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=sorted(allowed_origins()),
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Authorization"])
app.add_middleware(LocalSecurityMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

@app.get("/api/health")
def health():
    return {"ok": True, "version": app.version, "cleanup_only": True,
            "live_trash_enabled": settings.live_trash_enabled}

@app.get("/api/jobs")
def jobs():
    return [j.to_dict() for j in manager.list()]

@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = manager.get(job_id)
    if not j:
        raise HTTPException(404, "Job not found; restart analysis after a server restart")
    return j.to_dict()

@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    return {"cancelled": manager.cancel(job_id)}

for router in (auth_router, catalog.router, dedup.router, gp.router, review.router, direct.router, accidents.router):
    app.include_router(router)
app.mount("/media/thumbs", StaticFiles(directory=str(settings.thumbs_dir)), name="thumbs")
