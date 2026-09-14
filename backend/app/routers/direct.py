"""Direct catalog analysis controls; scanning lives in the extension."""
import base64
import binascii
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from ..jobs import manager
from ..db import serialized, session_scope
from ..models import Media, GpItem
from ..config import settings
from ..pipeline import direct
router = APIRouter(prefix="/api/direct", tags=["direct cleanup"])
class AnalyzeBody(BaseModel):
    use_clip: bool = False
    cached_only: bool = False
@router.post("/analyze")
@serialized
def analyze(body: AnalyzeBody | None = None):
    if manager.is_running("direct-analysis"):
        raise HTTPException(409, "Thumbnail analysis is already running")
    body = body or AnalyzeBody()
    return manager.submit("direct-analysis", lambda h: direct.analyze(h, body.use_clip, body.cached_only)).to_dict()

@router.get("/thumbnail-queue")
def thumbnail_queue(after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    with session_scope() as s:
        rows = (s.query(Media, GpItem).join(GpItem, GpItem.media_id == Media.id)
                .filter(Media.source == "google-photos-thumbnail", Media.media_type == "photo",
                        GpItem.trashed.is_(False), Media.id > after).order_by(Media.id))
        items = []
        for m, g in rows.yield_per(100):
            if m.thumb_path and (settings.thumbs_dir / m.thumb_path).is_file():
                continue
            items.append({"media_id": m.id, "account": g.account, "url": g.thumb_url})
            if len(items) == limit:
                break
        return {"items": items}

class PreviewBody(BaseModel):
    account: str = Field(min_length=1, max_length=320)
    data: str = Field(max_length=4 * ((direct.MAX_BYTES + 2) // 3))

@router.post("/thumbnails/{media_id}")
@serialized
def cache_preview(media_id: int, body: PreviewBody):
    with session_scope() as s:
        g = s.query(GpItem).filter(GpItem.media_id == media_id, GpItem.trashed.is_(False)).first()
        m = s.get(Media, media_id)
        if not g or not m or m.source != "google-photos-thumbnail" or m.media_type != "photo":
            raise HTTPException(404, "Live catalog photo not found")
        if g.account != body.account:
            raise HTTPException(409, "Google account does not match the catalog")
        if m.thumb_path and (settings.thumbs_dir / m.thumb_path).is_file():
            return {"cached": False}
        try:
            raw = base64.b64decode(body.data, validate=True)
            direct.save_thumbnail(m, raw)
        except (ValueError, binascii.Error, OSError):
            raise HTTPException(400, "Invalid or oversized preview image") from None
        return {"cached": True}
