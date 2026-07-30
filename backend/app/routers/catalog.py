"""Catalog browse + people endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..db import db_dependency
from ..models import Caption, Face, FaceCluster, Media, OcrText

router = APIRouter(prefix="/api", tags=["catalog"])


def _thumb(m: Media) -> str | None:
    return f"/media/thumbs/{m.thumb_path}" if m.thumb_path else None


def _media_brief(m: Media, caption: str | None = None) -> dict:
    return {
        "id": m.id, "type": m.media_type, "name": m.rel_name,
        "thumb": _thumb(m), "width": m.width, "height": m.height,
        "taken_at": m.taken_at.isoformat() if m.taken_at else None,
        "place_name": m.place_name, "is_portrait": m.is_portrait,
        "blur_score": m.blur_score, "caption": caption,
    }


@router.get("/stats")
def stats(db: Session = Depends(db_dependency)) -> dict:
    return {
        "photos": db.query(func.count(Media.id)).filter(Media.media_type == "photo").scalar(),
        "videos": db.query(func.count(Media.id)).filter(Media.media_type == "video").scalar(),
        "with_gps": db.query(func.count(Media.id)).filter(Media.gps_lat.isnot(None)).scalar(),
        "captioned": db.query(func.count(func.distinct(Caption.media_id))).scalar(),
        "with_text": db.query(func.count(func.distinct(OcrText.media_id))).scalar(),
        "faces": db.query(func.count(Face.id)).scalar(),
        "people": db.query(func.count(FaceCluster.id)).scalar(),
        "analyzed": db.query(func.count(Media.id)).filter(Media.analyzed_at.isnot(None)).scalar(),
    }


@router.get("/media")
def list_media(
    type: str | None = None,
    q: str | None = None,
    place: str | None = None,
    limit: int = 60,
    offset: int = 0,
    db: Session = Depends(db_dependency),
) -> dict:
    query = db.query(Media)
    if type:
        query = query.filter(Media.media_type == type)
    if place:
        query = query.filter(Media.place_name.ilike(f"%{place}%"))
    if q:
        like = f"%{q}%"
        query = (query.outerjoin(Caption).outerjoin(OcrText)
                 .filter(or_(Media.rel_name.ilike(like), Media.description.ilike(like),
                             Caption.text.ilike(like), OcrText.text.ilike(like)))
                 .distinct())
    total = query.count()
    # SQLite sorts NULLs first; DESC pushes them last, which is what we want (undated at the end).
    rows = query.order_by(Media.taken_at.desc()).offset(offset).limit(limit).all()
    caps = {c.media_id: c.text for c in db.query(Caption).filter(
        Caption.media_id.in_([r.id for r in rows])).all()} if rows else {}
    return {"total": total, "items": [_media_brief(m, caps.get(m.id)) for m in rows]}


@router.get("/media/{media_id}")
def media_detail(media_id: int, db: Session = Depends(db_dependency)) -> dict:
    m = db.get(Media, media_id)
    if not m:
        raise HTTPException(404, "media not found")
    return {
        **_media_brief(m),
        "abs_path": m.abs_path, "gps": [m.gps_lat, m.gps_lng] if m.gps_lat else None,
        "camera": m.camera, "codec": m.codec, "bitrate": m.bitrate, "duration": m.duration,
        "description": m.description,
        "captions": [{"id": c.id, "text": c.text, "tags": c.tags, "approved": c.approved} for c in m.captions],
        "ocr": [o.text for o in m.ocr],
        "faces": [{"bbox": f.bbox, "cluster_id": f.cluster_id} for f in m.faces],
    }


class NameBody(BaseModel):
    name: str


@router.get("/people")
def people(db: Session = Depends(db_dependency)) -> list[dict]:
    out = []
    for c in db.query(FaceCluster).all():
        count = db.query(func.count(Face.id)).filter(Face.cluster_id == c.id).scalar()
        cover = db.get(Media, c.cover_media_id) if c.cover_media_id else None
        out.append({"id": c.id, "name": c.name, "count": count, "cover": _thumb(cover) if cover else None})
    return sorted(out, key=lambda x: x["count"], reverse=True)


@router.get("/media/{media_id}/file")
def media_file(media_id: int, db: Session = Depends(db_dependency)):
    """Serve the original file (read-only) so the UI can show full-resolution previews."""
    m = db.get(Media, media_id)
    if not m:
        raise HTTPException(404, "media not found")
    p = Path(m.abs_path)
    if not p.exists():
        raise HTTPException(404, "file missing on disk")
    return FileResponse(str(p), filename=m.rel_name)


@router.post("/people/{cluster_id}/name")
def name_person(cluster_id: int, body: NameBody, db: Session = Depends(db_dependency)) -> dict:
    c = db.get(FaceCluster, cluster_id)
    if not c:
        raise HTTPException(404, "cluster not found")
    c.name = body.name
    db.commit()
    return {"id": c.id, "name": c.name}
