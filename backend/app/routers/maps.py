"""Maps Review Studio endpoints.

Cluster geo-tagged non-portrait photos by place, match places (read-only Places API), draft a star
rating + review text from the photos' captions/OCR, and stage a review package the user posts
MANUALLY. No API can submit a Maps review — this only prepares.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..db import db_dependency
from ..jobs import manager
from ..models import (
    Caption, Face, GpItem, IgnoredPlace, Media, OcrText, PlaceCluster, ReviewAction,
)
from ..pipeline import captions as captions_pipe
from ..pipeline import faces as faces_pipe
from ..pipeline import geo
from ..services import places
from ..services import uploader

router = APIRouter(prefix="/api/maps", tags=["maps"])

# Cap how many tiles the detail gallery returns — enough to review/split without rendering thousands.
_GALLERY_MAX = 60


def _cluster_media_ids(pc: PlaceCluster) -> list[int]:
    return json.loads(pc.media_ids) if pc.media_ids else []


def _rank_review_images(db: Session, media_ids: list[int]) -> tuple[list[dict], bool]:
    """Rank a cluster's photos for a Maps review and flag each. Returns (images, needs_analysis).

    Excludes from the default pick:
      - a person or group in focus (``faces.is_person_in_focus`` — a prominent single face OR several
        faces that jointly cover enough of the frame);
      - documents/screenshots (has OCR text and no faces).
    Eligible photos are ranked sharpest-first and the top ~6 are pre-recommended. When the cluster is
    largely un-analyzed, flags are unreliable so we just recommend the first few and signal the UI.
    """
    if not media_ids:
        return [], False
    media = db.query(Media).filter(Media.id.in_(media_ids)).all()
    face_counts = dict(
        db.query(Face.media_id, func.count(Face.id)).filter(Face.media_id.in_(media_ids))
        .group_by(Face.media_id).all()
    )
    ocr_ids = {r[0] for r in db.query(OcrText.media_id).filter(OcrText.media_id.in_(media_ids)).distinct()}
    captions: dict[int, str] = {}
    for mid, txt in db.query(Caption.media_id, Caption.text).filter(Caption.media_id.in_(media_ids)):
        if not captions_pipe.is_caption_garbage(txt):   # hide detection-coord junk from the UI
            captions.setdefault(mid, txt)

    unanalyzed = 0
    items: list[dict] = []
    for m in media:
        faces = int(face_counts.get(m.id, 0))
        if m.is_portrait is None and m.blur_score is None and faces == 0:
            unanalyzed += 1
        is_portrait = bool(m.is_portrait)
        is_document = (m.id in ocr_ids) and faces == 0
        person_in_focus = faces_pipe.is_person_in_focus(m)
        eligible = not person_in_focus and not is_document
        items.append({
            "id": m.id,
            "media_type": m.media_type,                  # "photo" | "video"
            "thumb": f"/media/thumbs/{m.thumb_path}" if m.thumb_path else None,
            "file": f"/api/media/{m.id}/file" if m.media_type == "video" else None,
            "caption": captions.get(m.id),
            "is_portrait": is_portrait,
            "person_in_focus": person_in_focus,
            "faces": faces,
            "is_document": is_document,
            "_eligible": eligible,
            "_blur": m.blur_score or 0.0,
        })

    needs_analysis = bool(media) and unanalyzed > len(media) / 2
    # Recommend up to 6 eligible PHOTOS (sharpest first); never auto-select a video.
    photos = sorted((d for d in items if d["_eligible"] and d["media_type"] == "photo"),
                    key=lambda d: d["_blur"], reverse=True)
    rec_ids = {d["id"] for d in photos[:6]} or {d["id"] for d in items[:6]}  # fallback if none eligible
    for d in items:
        d["recommended"] = d["id"] in rec_ids
    # Display order: recommended first, then any videos, then other eligible (sharpest), then the rest —
    # so videos and the recommended picks always survive the _GALLERY_MAX window.
    items.sort(key=lambda d: (d["recommended"], d["media_type"] == "video", d["_eligible"], d["_blur"]),
               reverse=True)
    for d in items:
        d.pop("_eligible"); d.pop("_blur")
    return items, needs_analysis


class ClusterOpts(BaseModel):
    # Sample mode: cap reverse-geocoding to this many photos (Places API calls) for a quick test
    # before committing to the whole library — like the highlights "test 5 reels" button.
    limit: int | None = None


@router.post("/cluster")
def cluster(opts: ClusterOpts | None = None) -> dict:
    if manager.is_running("maps"):
        raise HTTPException(409, "Maps clustering already running")
    sample = opts.limit if opts else None

    def _job(h):
        cc = lambda: h.cancelled
        # Naming is now manual/on-demand (Match place), so a full run does NOT reverse-geocode the
        # whole library (avoids thousands of paid lookups). Only the small "Test: N" sample does a
        # cheap Places-API smoke-test to pre-name a few before clustering.
        base = 0.1
        if sample:
            h.update(0.1, f"Reverse-geocoding sample of {sample}")
            geo.reverse_geocode(limit=sample, should_cancel=cc,
                                progress=lambda p, m: h.update(0.1 + 0.4 * p, m))
            if h.cancelled:
                return {"cancelled": True, "sample": sample}
            base = 0.5
        h.update(base, "Clustering places")
        return geo.cluster_places(limit=sample, should_cancel=cc,
                                  progress=lambda p, m: h.update(base + (1 - base) * p, m))

    return manager.submit("maps", _job).to_dict()


@router.post("/reprocess-person-focus")
def reprocess_person_focus() -> dict:
    """Recompute the person/group-in-focus signal for the whole analyzed library (cheap — reads
    already-stored Face rows, no GPU, no Places API calls) then re-check existing place clusters
    against it. Clusters are never deleted, only their photo membership is trimmed. Reviewed clusters
    are skipped entirely; ignored places are never PlaceCluster rows so they're untouched too."""
    if manager.is_running("maps"):
        raise HTTPException(409, "Maps clustering already running")

    def _job(h):
        h.update(0.05, "Recomputing face metrics")
        backfill = faces_pipe.backfill_face_area_ratio(force=True)
        h.update(0.4, "Re-checking existing clusters")
        prune = geo.prune_person_in_focus(progress=lambda p, m: h.update(0.4 + 0.6 * p, m))
        return {"backfill": backfill, "prune": prune}

    return manager.submit("maps", _job).to_dict()


@router.post("/cancel")
def cancel() -> dict:
    """Flag the running maps clustering job to stop (cooperative — stops at the next photo)."""
    return {"cancelled": manager.cancel_name("maps")}


@router.get("/clusters")
def clusters(db: Session = Depends(db_dependency)) -> list[dict]:
    """List place clusters (≥2 photos), biggest first. Ignored places are filtered out as
    defense-in-depth (clustering already skips them)."""
    ignored = geo.load_ignored(db)
    out = []
    for pc in db.query(PlaceCluster).all():
        ids = _cluster_media_ids(pc)
        if len(ids) < 2:
            continue  # a lone photo isn't a reviewable place yet — hidden until a 2nd joins
        if pc.lat is not None and geo.is_ignored(pc.lat, pc.lng, pc.place_id, ignored):
            continue
        media = db.query(Media).filter(Media.id.in_(ids)).all()
        out.append({
            "id": pc.id, "place_id": pc.place_id, "name": pc.name,
            "address": pc.address, "lat": pc.lat, "lng": pc.lng, "count": len(ids),
            "matched": bool(pc.place_id), "manual": bool(pc.manual), "reviewed": bool(pc.reviewed),
            "thumbs": [{"id": m.id, "thumb": f"/media/thumbs/{m.thumb_path}" if m.thumb_path else None}
                       for m in media[:6]],
        })
    return sorted(out, key=lambda x: x["count"], reverse=True)


@router.get("/clusters/{cluster_id}")
def cluster_detail(
    cluster_id: int, limit: int = _GALLERY_MAX, offset: int = 0, db: Session = Depends(db_dependency)
) -> dict:
    """Full cluster detail with ranked, flagged review images — loaded when a tile is opened. Images are
    paginated (``limit``/``offset``) over the full ranking so ``recommended`` stays correct regardless
    of which page is requested; the UI fetches more pages via a "Load more" button."""
    pc = db.get(PlaceCluster, cluster_id)
    if not pc:
        raise HTTPException(404, "cluster not found")
    ids = _cluster_media_ids(pc)
    images, needs_analysis = _rank_review_images(db, ids)
    page = images[offset:offset + limit]
    return {
        "id": pc.id, "place_id": pc.place_id, "name": pc.name, "address": pc.address,
        "lat": pc.lat, "lng": pc.lng, "count": len(ids), "matched": bool(pc.place_id),
        "manual": bool(pc.manual), "reviewed": bool(pc.reviewed), "needs_analysis": needs_analysis,
        "offset": offset, "shown": offset + len(page), "images": page,
    }


@router.get("/clusters/{cluster_id}/album-package")
def album_package(cluster_id: int, db: Session = Depends(db_dependency)) -> dict:
    """Everything the extension needs to build a Google Photos album for this place.

    Album membership is keyed on ``media_key`` (per-item id), not ``dedup_key`` — albums are the one
    Google Photos operation where the content-identity key is the wrong one.

    This is deliberately the extent of the automation. Posting the review itself, or contributing
    photos to the public place listing, stays a manual click: that is public content about someone
    else's business, and automating it is what Google's anti-abuse systems exist to stop.
    """
    pc = db.get(PlaceCluster, cluster_id)
    if not pc:
        raise HTTPException(404, "cluster not found")

    ids = _cluster_media_ids(pc)
    media = {m.id: m for m in db.query(Media).filter(Media.id.in_(ids)).all()} if ids else {}
    links = {g.media_id: g for g in db.query(GpItem).filter(
        GpItem.media_id.in_(ids), GpItem.trashed.is_(False)).all()} if ids else {}

    items = []
    for mid in ids:
        m = media.get(mid)
        if not m:
            continue
        g = links.get(mid)
        items.append({
            "media_id": m.id,
            "name": m.rel_name,
            "media_type": m.media_type,
            "thumb": f"/media/thumbs/{m.thumb_path}" if m.thumb_path else None,
            "taken_at": m.taken_at.isoformat() if m.taken_at else None,
            "media_key": g.media_key if g else None,
            "product_url": g.product_url if g else None,
            "account": g.account if g else None,
            "live": bool(g),
        })

    live = [i for i in items if i["live"]]
    title = pc.name or f"Place {pc.id}"
    return {
        "cluster_id": pc.id,
        "title": title,
        "album_title": f"{title} — Photo Curator",
        "place_id": pc.place_id,
        "address": pc.address,
        "review_url": places.review_url(pc.place_id, pc.name),
        "total": len(items),
        "live_count": len(live),
        "photos": sum(1 for i in live if i["media_type"] == "photo"),
        "videos": sum(1 for i in live if i["media_type"] == "video"),
        "accounts": sorted({i["account"] for i in live if i["account"]}),
        "items": items,
    }


@router.get("/clusters/{cluster_id}/place-suggestions")
def place_suggestions(cluster_id: int, db: Session = Depends(db_dependency)) -> list[dict]:
    """On-demand nearby-place candidates for matching (one paid Places-API call)."""
    pc = db.get(PlaceCluster, cluster_id)
    if not pc or pc.lat is None:
        raise HTTPException(404, "cluster not found or has no coordinates")
    return places.nearby_places(pc.lat, pc.lng)


class MatchBody(BaseModel):
    place_id: str


@router.post("/clusters/{cluster_id}/match")
def match_place(cluster_id: int, body: MatchBody, db: Session = Depends(db_dependency)) -> dict:
    """Assign a Google place to a cluster. Persists on the cluster (manual=True) AND writes the name
    back onto member photos so highlights/catalog keep it and re-clusters skip them."""
    pc = db.get(PlaceCluster, cluster_id)
    if not pc:
        raise HTTPException(404, "cluster not found")
    detail = places.get_place(body.place_id)
    if not detail:
        raise HTTPException(404, "place not found or Places API not configured")
    pc.place_id = detail.get("place_id")
    pc.name = detail.get("name")
    pc.address = detail.get("address")
    pc.manual = True
    for m in db.query(Media).filter(Media.id.in_(_cluster_media_ids(pc))):
        m.place_id, m.place_name = pc.place_id, pc.name
    db.commit()
    return {"id": pc.id, "place_id": pc.place_id, "name": pc.name, "address": pc.address,
            "matched": True, "place_type": _guess_type(detail.get("primary_type"), detail.get("types"))}


class ReviewedBody(BaseModel):
    reviewed: bool = True


@router.post("/clusters/{cluster_id}/reviewed")
def set_reviewed(cluster_id: int, body: ReviewedBody, db: Session = Depends(db_dependency)) -> dict:
    """Mark (or unmark) a cluster as already reviewed on Google Maps. Reviewed places are hidden from
    the list by default so you can focus on the ones still to do."""
    pc = db.get(PlaceCluster, cluster_id)
    if not pc:
        raise HTTPException(404, "cluster not found")
    pc.reviewed = body.reviewed
    db.commit()
    return {"id": pc.id, "reviewed": pc.reviewed}


class IgnoreBody(BaseModel):
    label: str


@router.post("/clusters/{cluster_id}/ignore")
def ignore_cluster(cluster_id: int, body: IgnoreBody, db: Session = Depends(db_dependency)) -> dict:
    """Mark a cluster's place as personal/ignored (with a label) and delete the cluster. Future
    clustering skips photos within the ignored place, so it never re-clusters."""
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(400, "a label is required (e.g. 'Home')")
    pc = db.get(PlaceCluster, cluster_id)
    if not pc:
        raise HTTPException(404, "cluster not found")
    if pc.lat is not None and pc.lng is not None:
        key = pc.place_id or f"{round(pc.lat, 3)},{round(pc.lng, 3)}"
    else:
        key = pc.place_id or ""
    # Size the ignore radius to the cluster's actual spread (+buffer) so the WHOLE place stays skipped
    # on future runs — not just photos near the centroid.
    radius = 120.0
    if pc.lat is not None:
        members = db.query(Media).filter(Media.id.in_(_cluster_media_ids(pc))).all()
        spread = max((geo._meters(pc.lat, pc.lng, m.gps_lat, m.gps_lng)
                      for m in members if m.gps_lat is not None), default=0.0)
        radius = max(120.0, spread + 30.0)
    db.add(IgnoredPlace(place_id=pc.place_id, lat=pc.lat, lng=pc.lng, key=key, label=label, radius_m=radius))
    db.delete(pc)
    db.commit()
    return {"ignored": True, "label": label}


@router.get("/ignored")
def list_ignored(db: Session = Depends(db_dependency)) -> list[dict]:
    return [{"id": ip.id, "label": ip.label, "place_id": ip.place_id,
             "lat": ip.lat, "lng": ip.lng, "radius_m": ip.radius_m}
            for ip in db.query(IgnoredPlace).order_by(IgnoredPlace.created_at.desc())]


@router.post("/ignored/{ignored_id}/unignore")
def unignore(ignored_id: int, db: Session = Depends(db_dependency)) -> dict:
    ip = db.get(IgnoredPlace, ignored_id)
    if not ip:
        raise HTTPException(404, "ignored place not found")
    db.delete(ip)
    db.commit()
    return {"removed": True}


class SplitBody(BaseModel):
    image_ids: list[int]


@router.post("/clusters/{cluster_id}/split")
def split_cluster(cluster_id: int, body: SplitBody, db: Session = Depends(db_dependency)) -> dict:
    """Move the given photos out of a cluster into a brand-new cluster (for photos that were grouped
    into the wrong place). Both clusters are marked manual so auto-clustering won't undo it."""
    pc = db.get(PlaceCluster, cluster_id)
    if not pc:
        raise HTTPException(404, "cluster not found")
    move = set(body.image_ids)
    current = _cluster_media_ids(pc)
    moved = [i for i in current if i in move]
    if not moved or len(moved) >= len(current):
        raise HTTPException(400, "select at least one photo, but not the whole cluster")
    remaining = [i for i in current if i not in move]

    moved_media = db.query(Media).filter(Media.id.in_(moved)).all()
    new = PlaceCluster(
        lat=sum(m.gps_lat for m in moved_media) / len(moved_media),
        lng=sum(m.gps_lng for m in moved_media) / len(moved_media),
        media_ids=json.dumps(moved), manual=True,
    )
    pc.media_ids = json.dumps(remaining)
    pc.manual = True
    db.add(new)
    db.commit()
    return {"new_cluster_id": new.id, "moved": len(moved), "remaining": len(remaining)}


@router.post("/reset")
def reset() -> dict:
    """Delete all clusters so the next run rebuilds from scratch (ignore-list kept). Guarded in the UI."""
    if manager.is_running("maps"):
        raise HTTPException(409, "Stop the running clustering job first.")
    return geo.reset_clusters()


class OpenFolderBody(BaseModel):
    path: str


@router.post("/open-folder")
def open_folder(body: OpenFolderBody) -> dict:
    """Open an exported review-package folder in the OS file explorer (local-first app). Restricted to
    paths under the exports directory so it can't open arbitrary locations."""
    exports = settings.exports_dir.resolve()
    p = Path(body.path).resolve()
    if p != exports and exports not in p.parents:
        raise HTTPException(400, "path must be inside the exports directory")
    if not p.exists():
        raise HTTPException(404, "folder not found")
    try:
        if sys.platform == "win32":
            os.startfile(str(p))            # type: ignore[attr-defined]  # noqa: S606 — local app
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return {"opened": True}
    except Exception as e:
        raise HTTPException(500, f"could not open folder: {e}")


def _open_explorer_multiselect(paths: list[Path]) -> None:
    """Open one Explorer window with every path highlighted (Windows only). Falls back to just opening
    the parent folder (no highlight) on any failure — e.g. non-Windows, or shell32 quirks."""
    folder = paths[0].parent
    if sys.platform != "win32":
        raise RuntimeError("multi-select highlighting is Windows-only")
    import ctypes

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    pidls: list[ctypes.c_void_p] = []
    folder_pidl: ctypes.c_void_p | None = None
    try:
        folder_pidl = ctypes.c_void_p()
        if shell32.SHParseDisplayName(str(folder), None, ctypes.byref(folder_pidl), 0, None) != 0:
            raise RuntimeError(f"SHParseDisplayName failed for {folder}")
        for p in paths:
            pidl = ctypes.c_void_p()
            if shell32.SHParseDisplayName(str(p), None, ctypes.byref(pidl), 0, None) == 0 and pidl:
                pidls.append(pidl)
        if not pidls:
            raise RuntimeError("no paths resolved to a PIDL")
        arr = (ctypes.c_void_p * len(pidls))(*pidls)
        hr = shell32.SHOpenFolderAndSelectItems(folder_pidl, len(pidls), arr, 0)
        if hr != 0:
            raise RuntimeError(f"SHOpenFolderAndSelectItems failed (hr={hr})")
    finally:
        for pidl in pidls:
            ole32.CoTaskMemFree(pidl)
        if folder_pidl is not None:
            ole32.CoTaskMemFree(folder_pidl)
        ole32.CoUninitialize()


class OpenSelectedBody(BaseModel):
    media_ids: list[int]


@router.post("/clusters/{cluster_id}/open-selected")
def open_selected(cluster_id: int, body: OpenSelectedBody, db: Session = Depends(db_dependency)) -> dict:
    """Open the original files behind a gallery selection in Explorer, highlighted — for inspecting
    originals before staging (the export-folder button only covers already-staged copies)."""
    pc = db.get(PlaceCluster, cluster_id)
    if not pc:
        raise HTTPException(404, "cluster not found")
    member_ids = set(_cluster_media_ids(pc))
    ids = [i for i in body.media_ids if i in member_ids]
    if not ids:
        raise HTTPException(400, "no valid media ids for this cluster")
    media = db.query(Media).filter(Media.id.in_(ids)).all()
    paths = [Path(m.abs_path) for m in media if Path(m.abs_path).exists()]
    if not paths:
        raise HTTPException(404, "none of the selected files exist on disk")
    try:
        _open_explorer_multiselect(paths)
        return {"opened": True, "count": len(paths)}
    except Exception:
        # Best-effort fallback: just open the folder containing the first file, no highlight.
        try:
            if sys.platform == "win32":
                os.startfile(str(paths[0].parent))           # type: ignore[attr-defined]  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(paths[0].parent)])
            else:
                subprocess.Popen(["xdg-open", str(paths[0].parent)])
            return {"opened": True, "count": len(paths), "highlighted": False}
        except Exception as e:
            raise HTTPException(500, f"could not open Explorer: {e}")


@router.get("/search")
def search(q: str, lat: float | None = None, lng: float | None = None) -> list[dict]:
    return places.search_text(q, lat, lng)


@router.get("/place/{place_id}")
def place_details(place_id: str) -> dict:
    result = places.get_place(place_id)
    if not result:
        raise HTTPException(404, "place not found or Places API not configured")
    return result


def _describe_image(path: str) -> str | None:
    """Fresh one-line description of an image via moondream (a vision model). Used to ground a draft
    when the stored captions are missing or garbage. Returns None on failure/garbage."""
    b64 = captions_pipe._encode_image(path)
    if not b64:
        return None
    try:
        r = requests.post(
            f"{settings.ollama_host}/api/generate",
            json={"model": settings.caption_model,
                  "prompt": "Describe what is in this photo in one sentence.",
                  "images": [b64], "stream": False, "options": {"num_predict": 80}},
            timeout=60,
        )
        if r.ok:
            txt = r.json().get("response", "").strip()
            return None if captions_pipe.is_caption_garbage(txt) else (txt or None)
    except Exception:
        pass
    return None


# The local vision model (moondream) can accurately *describe* a photo but cannot write a *review* — it
# just returns descriptions or "!!!Amazing!!!". So we use it only to extract visual facts, then compose
# the review deterministically here, injecting sentiment from the chosen star rating. This reads like a
# real review and stays grounded in what the photos actually show.
_LEAD_INS = ("there is ", "there are ", "this is ", "this photo shows ", "the photo shows ",
             "a photo of ", "an image of ", "the image shows ", "it is ", "it's ")
_OPENERS = {
    5: "{p} is a fantastic {t} — we had a wonderful time!",
    4: "Really enjoyed our visit to {p}, a lovely {t}.",
    3: "{p} is a decent {t}.",
    2: "{p} was a bit of an underwhelming {t}.",
    1: "Unfortunately, {p} didn't live up to expectations as a {t}.",
}
_CLOSERS = {
    5: "Highly recommend — we'll definitely be back!",
    4: "Well worth a visit if you're in the area.",
    3: "Worth a look if you happen to be nearby.",
    2: "It may suit some visitors, but temper your expectations.",
    1: "I probably wouldn't go out of my way to return.",
}

# ---- Guided-review options: place type + experience aspects the user selects in the UI ----
_TYPE_LABELS = {
    "restaurant": "Restaurant", "cafe": "Café", "bar": "Bar / Pub", "hotel": "Hotel / Stay",
    "park": "Park / Outdoors", "museum": "Museum / Gallery", "attraction": "Attraction / Sightseeing",
    "shop": "Shop / Store", "beach": "Beach", "place": "Other / General",
}
_TYPE_NOUN = {  # noun used inside a sentence
    "restaurant": "restaurant", "cafe": "café", "bar": "bar", "hotel": "stay", "park": "park",
    "museum": "museum", "attraction": "place", "shop": "shop", "beach": "beach", "place": "place",
}
_ASPECTS_BY_TYPE = {
    "restaurant": ["food", "service", "ambiance", "value", "drinks", "cleanliness"],
    "cafe": ["coffee", "food", "ambiance", "service", "value", "wifi"],
    "bar": ["drinks", "ambiance", "service", "value", "music", "crowd"],
    "hotel": ["rooms", "service", "cleanliness", "location", "amenities", "value"],
    "park": ["scenery", "cleanliness", "facilities", "kids", "peaceful", "photospots"],
    "museum": ["exhibits", "staff", "informative", "value", "kids", "layout"],
    "attraction": ["experience", "scenery", "staff", "value", "kids", "photospots"],
    "shop": ["selection", "quality", "staff", "value", "layout"],
    "beach": ["scenery", "cleanliness", "facilities", "peaceful", "photospots"],
    "place": ["experience", "scenery", "staff", "cleanliness", "value", "photospots"],
}
_ASPECT_LABELS = {
    "food": "Food", "service": "Service", "ambiance": "Ambiance", "value": "Value for money",
    "drinks": "Drinks", "cleanliness": "Cleanliness", "coffee": "Coffee", "wifi": "Wi-Fi",
    "music": "Music", "crowd": "Crowd", "rooms": "Rooms", "location": "Location",
    "amenities": "Amenities", "scenery": "Scenery / views", "facilities": "Facilities",
    "kids": "Kid-friendly", "peaceful": "Peaceful", "photospots": "Photo spots",
    "exhibits": "Exhibits", "staff": "Staff", "informative": "Informative", "layout": "Layout",
    "experience": "Overall experience", "selection": "Selection", "quality": "Quality",
}
# (positive phrasing, negative phrasing) — rating chooses which
_ASPECT_PHRASES = {
    "food": ("the food was delicious", "the food was disappointing"),
    "service": ("the service was friendly and attentive", "the service was slow"),
    "ambiance": ("the ambiance was lovely", "the ambiance fell flat"),
    "value": ("it was great value for money", "it felt overpriced"),
    "drinks": ("the drinks were great", "the drinks were underwhelming"),
    "cleanliness": ("everything was clean and well kept", "it could have been cleaner"),
    "coffee": ("the coffee was excellent", "the coffee was mediocre"),
    "wifi": ("the wi-fi was reliable", ""),
    "music": ("the music set a great vibe", "the music was too loud"),
    "crowd": ("the crowd was lively", "it was overly crowded"),
    "rooms": ("the rooms were comfortable", "the rooms were underwhelming"),
    "location": ("the location was convenient", "the location was inconvenient"),
    "amenities": ("the amenities were great", "the amenities were lacking"),
    "scenery": ("the scenery was beautiful", "the views were nothing special"),
    "facilities": ("the facilities were good", "the facilities were lacking"),
    "kids": ("it was great for kids", "it wasn't very kid-friendly"),
    "peaceful": ("it was peaceful and relaxing", "it was noisier than expected"),
    "photospots": ("there were great spots for photos", ""),
    "exhibits": ("the exhibits were fascinating", "the exhibits were underwhelming"),
    "staff": ("the staff were friendly and helpful", "the staff were unhelpful"),
    "informative": ("it was very informative", ""),
    "layout": ("it was well laid out", "the layout was confusing"),
    "experience": ("the whole experience was wonderful", "the experience was underwhelming"),
    "selection": ("the selection was great", "the selection was limited"),
    "quality": ("the quality was excellent", "the quality was poor"),
}
# Map Google Places types onto our coarse categories so the UI can default the place-type dropdown.
_GOOGLE_TYPE_MAP = {
    "restaurant": "restaurant", "meal_takeaway": "restaurant", "meal_delivery": "restaurant",
    "cafe": "cafe", "coffee_shop": "cafe", "bakery": "cafe", "bar": "bar", "night_club": "bar",
    "lodging": "hotel", "hotel": "hotel", "resort_hotel": "hotel", "park": "park",
    "national_park": "park", "hiking_area": "park", "museum": "museum", "art_gallery": "museum",
    "tourist_attraction": "attraction", "amusement_park": "attraction", "aquarium": "attraction",
    "zoo": "attraction", "place_of_worship": "attraction", "hindu_temple": "attraction",
    "store": "shop", "shopping_mall": "shop", "supermarket": "shop", "beach": "beach",
}


def _guess_type(primary_type: str | None, types: list[str] | None) -> str:
    """Best coarse category for a matched place, for defaulting the UI's place-type dropdown."""
    for t in [primary_type] + list(types or []):
        if t and t in _GOOGLE_TYPE_MAP:
            return _GOOGLE_TYPE_MAP[t]
    return "place"


def _cap(s: str) -> str:
    return (s[0].upper() + s[1:]) if s else s


def _humanize_list(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return items[0] if items else ""
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _clean_detail(desc: str) -> str:
    """Turn a model photo-description into a short noun phrase that reads naturally inside a sentence:
    drop lead-ins, keep the main subject (before the first comma), cap length, ensure an article."""
    t = (desc or "").strip().rstrip(".")
    low = t.lower()
    for lead in _LEAD_INS:
        if low.startswith(lead):
            t = t[len(lead):]
            break
    t = t.split(",")[0].strip()                 # main subject only — avoids run-on sentences
    words = t.split()
    if len(words) > 12:
        t = " ".join(words[:12])
    if not t:
        return t
    t = t[0].lower() + t[1:]
    if t.split()[0] not in ("a", "an", "the", "some", "several", "many", "two", "three"):
        t = "the " + t
    return t


def _compose_review(place_name: str, descriptions: list[str], rating: int,
                    place_type: str = "place", aspects: list[str] | None = None,
                    note: str | None = None) -> str:
    """Assemble a review from the guided inputs: star rating (sentiment), place type, selected
    experience aspects, an optional custom note, plus concrete details from the photos."""
    p = place_name or "this place"
    rating = max(1, min(5, rating or 5))
    t = _TYPE_NOUN.get(place_type, "place")
    negative = rating <= 2

    parts = [_OPENERS[rating].format(p=p, t=t)]

    # Aspect sentence from the user's selections (positive or negative phrasing per rating).
    phrases = []
    for a in (aspects or []):
        pair = _ASPECT_PHRASES.get(a)
        if pair:
            ph = pair[1] if negative else pair[0]
            if ph:
                phrases.append(ph)
    if phrases:
        parts.append(_cap(_humanize_list(phrases)) + ".")

    # One concrete detail straight from the photos, deduped.
    cleaned, seen = [], set()
    for d in descriptions:
        c = _clean_detail(d)
        key = c.lower()[:25]
        if c and key not in seen:
            seen.add(key)
            cleaned.append(c)
    if cleaned:
        parts.append((f"We especially enjoyed {cleaned[0]}." if not negative
                      else f"That said, {cleaned[0]} stood out."))

    # The user's own note, verbatim.
    if note and note.strip():
        parts.append(_cap(note.strip().rstrip(".")) + ".")

    parts.append(_CLOSERS[rating])
    return " ".join(parts)


@router.get("/review-options")
def review_options() -> dict:
    """Catalog the UI uses to render the guided-review form: selectable place types and the experience
    aspects offered per type."""
    return {
        "types": [{"value": v, "label": _TYPE_LABELS[v]} for v in _TYPE_LABELS],
        "aspects_by_type": {
            t: [{"key": a, "label": _ASPECT_LABELS.get(a, a.title())} for a in aspects]
            for t, aspects in _ASPECTS_BY_TYPE.items()
        },
        "uploads_enabled": uploader.enabled(),
    }


class UploadVideoBody(BaseModel):
    media_id: int


@router.post("/clusters/{cluster_id}/upload-video")
def upload_video(cluster_id: int, body: UploadVideoBody, db: Session = Depends(db_dependency)) -> dict:
    """Push a video into the curated Google Photos album so it's pickable from Maps' photo picker —
    Maps' review-attachment UI only accepts videos via the Google Photos picker, not a direct file
    upload. Runs as a job since video files can be large."""
    if not uploader.enabled():
        raise HTTPException(400, "Google Photos uploads disabled — set PHOTOS_OAUTH_CLIENT_ID/SECRET in .env")
    pc = db.get(PlaceCluster, cluster_id)
    if not pc:
        raise HTTPException(404, "cluster not found")
    m = db.get(Media, body.media_id)
    if not m or m.id not in _cluster_media_ids(pc):
        raise HTTPException(404, "media not found in this cluster")
    if m.media_type != "video":
        raise HTTPException(400, "only videos need this — photos can be dragged in directly")
    abs_path = m.abs_path

    def _job(h):
        h.update(0.1, "Uploading to Google Photos")
        result = uploader.upload_files([Path(abs_path)])
        item = result["uploaded"][0]
        if "error" in item:
            raise RuntimeError(item["error"])
        return {"product_url": item["product_url"], "album_title": result["album_title"]}

    return manager.submit(f"maps-upload-{cluster_id}-{body.media_id}", _job).to_dict()


class DraftBody(BaseModel):
    rating: int = 5
    image_ids: list[int] | None = None
    place_type: str = "place"
    aspects: list[str] | None = None
    note: str | None = None


@router.post("/clusters/{cluster_id}/draft")
def draft(cluster_id: int, body: DraftBody, db: Session = Depends(db_dependency)) -> dict:
    pc = db.get(PlaceCluster, cluster_id)
    if not pc:
        raise HTTPException(404, "cluster not found")
    # Pick the scenic photos to ground the review: explicit selection, else the recommended ones.
    if body.image_ids:
        ids = body.image_ids
    else:
        ranked, _ = _rank_review_images(db, _cluster_media_ids(pc))
        ids = [im["id"] for im in ranked if im["recommended"]]

    # Prefer clean stored captions; ignore garbage (detection-coord junk).
    descriptions = [c.text for c in db.query(Caption).filter(Caption.media_id.in_(ids))
                    if c.text and not captions_pipe.is_caption_garbage(c.text)]
    grounded_by = "captions"
    # If none are usable, freshly describe a few of the selected images with the vision model.
    if not descriptions:
        media = {m.id: m for m in db.query(Media).filter(Media.id.in_(ids))}
        for mid in ids[:4]:
            m = media.get(mid)
            if m and Path(m.abs_path).exists():
                d = _describe_image(m.abs_path)
                if d:
                    descriptions.append(d)
        grounded_by = "images"

    text = _compose_review(pc.name or "", descriptions, body.rating,
                           place_type=body.place_type, aspects=body.aspects, note=body.note)
    return {"rating": body.rating, "text": text, "captions_used": len(descriptions),
            "grounded_by": grounded_by, "no_captions": len(descriptions) == 0}


class ReviewBody(BaseModel):
    place_id: str | None = None
    name: str | None = None
    rating: int = 5
    text: str
    image_ids: list[int] = []
    rotations: dict[int, int] | None = None   # media_id -> clockwise degrees (90/180/270) for posting
    captions: dict[int, str] | None = None


@router.post("/clusters/{cluster_id}/review")
def stage_review(cluster_id: int, body: ReviewBody, db: Session = Depends(db_dependency)) -> dict:
    pc = db.get(PlaceCluster, cluster_id)
    if not pc:
        raise HTTPException(404, "cluster not found")

    # Export shortlisted images (copies — originals untouched) into exports/<place>/. A requested
    # rotation is baked into the EXPORTED copy only, so the upright image is ready to post.
    safe = "".join(ch for ch in (body.name or pc.name or f"place_{cluster_id}") if ch.isalnum() or ch in " -_")[:60]
    export_dir = settings.exports_dir / safe.strip()
    export_dir.mkdir(parents=True, exist_ok=True)
    rotations = body.rotations or {}
    exported = []
    for mid in body.image_ids:
        m = db.get(Media, mid)
        if not (m and Path(m.abs_path).exists()):
            continue
        if m.media_type == "video":
            # Maps accepts video; export the original clip as-is (rotation/format unchanged).
            try:
                dest = export_dir / m.rel_name
                shutil.copy2(m.abs_path, dest)
                exported.append(str(dest))
            except Exception:
                pass
            continue
        deg = int(rotations.get(mid, 0)) % 360
        # Export photos as JPEG so the package is directly postable (HEIC/iPhone photos aren't accepted).
        # Apply EXIF orientation, then the user's rotation. Originals are untouched.
        dest = export_dir / f"{Path(m.rel_name).stem}.jpg"
        try:
            from PIL import Image, ImageOps
            with Image.open(m.abs_path) as im:
                im = ImageOps.exif_transpose(im)
                if deg:
                    im = im.rotate(-deg, expand=True)
                im.convert("RGB").save(dest, "JPEG", quality=95)
            exported.append(str(dest))
        except Exception:
            try:  # last resort: copy the original as-is (correct extension)
                fallback = export_dir / m.rel_name
                shutil.copy2(m.abs_path, fallback)
                exported.append(str(fallback))
            except Exception:
                pass
    (export_dir / "review.txt").write_text(
        f"{body.name or pc.name}\nRating: {body.rating}/5\n\n{body.text}\n", encoding="utf-8")

    # Fetch the live maps_uri for the most reliable review URL
    pid = body.place_id or pc.place_id
    maps_uri = None
    if pid:
        detail = places.get_place(pid)
        maps_uri = (detail or {}).get("maps_uri")
    review_url = places.review_url(pid, body.name or pc.name, maps_uri)

    action = ReviewAction(
        kind="review",
        payload=json.dumps({
            "place_id": pid, "name": body.name or pc.name,
            "rating": body.rating, "text": body.text, "images": exported,
            "review_url": review_url, "export_dir": str(export_dir),
        }),
    )
    db.add(action)
    db.commit()
    return {"queued": True, "export_dir": str(export_dir), "exported_images": len(exported),
            "review_url": review_url}
