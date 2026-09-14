"""Local-only authentication. No tokens in URLs, logs, or Google page context."""
from __future__ import annotations
import os
import re
import secrets
import time
from functools import lru_cache
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
from .config import settings

UI_ORIGINS = {"http://localhost:5177", "http://127.0.0.1:5177"}

def allowed_origins() -> set[str]:
    ids = os.getenv("PC_EXTENSION_IDS", "").split(",")
    return UI_ORIGINS | {f"chrome-extension://{i.strip()}" for i in ids
                         if re.fullmatch(r"[a-p]{32}", i.strip())}

@lru_cache(maxsize=1)
def local_token() -> str:
    settings.ensure_dirs()
    path = settings.data_dir / ".local-token"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32))
    if os.name != "nt":
        path.chmod(0o600)
    token = path.read_text().strip()
    if len(token) < 40:
        raise RuntimeError("Invalid local token file; remove it and restart to generate a new token")
    return token

# Sessions expire after 12 hours or backend restart, independently of the pairing token.
sessions: dict[str, float] = {}
router = APIRouter(prefix="/api/auth", tags=["local authentication"])
class Login(BaseModel):
    token: str = Field(min_length=1, max_length=200)

@router.post("/login")
def login(body: Login, request: Request, response: Response):
    if request.headers.get("origin") not in UI_ORIGINS:
        raise HTTPException(403, "Open the local UI to sign in")
    if not secrets.compare_digest(body.token.encode(), local_token().encode()):
        raise HTTPException(401, "Incorrect local token")
    now = time.time()
    for key in list(sessions):
        if sessions[key] < now:
            del sessions[key]
    session = secrets.token_urlsafe(32)
    sessions[session] = now + 43200
    response.set_cookie("pc_session", session, httponly=True, samesite="strict", max_age=43200)
    return {"ok": True}

@router.post("/logout")
def logout(request: Request, response: Response):
    sessions.pop(request.cookies.get("pc_session", ""), None)
    response.delete_cookie("pc_session")
    return {"ok": True}

class LocalSecurityMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins():
            return await JSONResponse({"detail": "Origin not allowed"}, 403)(scope, receive, send)
        if request.method != "OPTIONS" and request.url.path != "/api/auth/login":
            auth = request.headers.get("authorization", "")
            bearer = auth.startswith("Bearer ") and secrets.compare_digest(auth[7:].encode(), local_token().encode())
            cookie = sessions.get(request.cookies.get("pc_session", ""), 0) > time.time()
            if not bearer and not cookie:
                return await JSONResponse({"detail": "Pair with your local token"}, 401)(scope, receive, send)
            if not bearer and request.method not in {"GET", "HEAD"} and origin not in UI_ORIGINS:
                return await JSONResponse({"detail": "Same-origin request required"}, 403)(scope, receive, send)
        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] += [(b"cache-control", b"no-store"),
                                       (b"x-content-type-options", b"nosniff"),
                                       (b"referrer-policy", b"no-referrer")]
            await send(message)
        await self.app(scope, receive, secured_send)
