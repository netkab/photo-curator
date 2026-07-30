"""Face detection + embeddings (InsightFace) and clustering (DBSCAN over cosine distance).

Also sets ``media.is_portrait`` from the largest face-area ratio — Maps Studio uses this to exclude
portraits from place reviews.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Callable

import numpy as np

from ..config import settings
from ..db import session_scope
from ..models import Face, FaceCluster, Media


@lru_cache(maxsize=1)
def _load_insightface():
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    return app


def detect(limit: int | None = None, force: bool = False, progress: Callable[[float, str], None] | None = None) -> dict:
    """Detect faces, store embeddings, and set is_portrait."""
    import cv2

    app = _load_insightface()
    found = 0
    with session_scope() as s:
        q = s.query(Media).filter(Media.media_type == "photo")
        if not force:
            q = q.filter(Media.is_portrait.is_(None))
        if limit:
            q = q.limit(limit)
        rows = q.all()
        total = len(rows) or 1
        for i, m in enumerate(rows):
            if progress:
                progress(0.7 * i / total, f"Faces {m.rel_name}")
            img = cv2.imread(m.abs_path)
            if img is None:
                m.is_portrait = False
                m.face_area_ratio = 0.0
                continue
            h, w = img.shape[:2]
            faces = app.get(img)
            max_ratio = 0.0
            total_ratio = 0.0
            for f in faces:
                x1, y1, x2, y2 = f.bbox.astype(int)
                area = max(0, (x2 - x1)) * max(0, (y2 - y1))
                ratio = area / float(w * h or 1)
                max_ratio = max(max_ratio, ratio)
                total_ratio += ratio
                s.add(Face(
                    media_id=m.id,
                    bbox=f"{x1},{y1},{x2 - x1},{y2 - y1}",
                    embedding=np.asarray(f.normed_embedding, dtype=np.float32).tobytes(),
                    det_score=float(f.det_score),
                ))
                found += 1
            m.is_portrait = max_ratio >= settings.portrait_face_ratio
            m.face_area_ratio = total_ratio
    return {"faces": found}


def is_person_in_focus(media: Media) -> bool:
    """True if a person or group is the clear subject of the photo — a single prominent face
    (``is_portrait``) OR several faces that jointly cover enough of the frame to be the subject
    (``face_area_ratio``). The single source of truth for this decision — used by clustering
    (keeps such photos out of place clusters), Maps Studio ranking, and the one-time cleanup of
    already-clustered photos."""
    return bool(media.is_portrait) or (media.face_area_ratio or 0.0) >= settings.person_focus_area_ratio


def backfill_face_area_ratio(force: bool = False) -> dict:
    """Recompute ``Media.face_area_ratio`` for already-analyzed photos from their stored ``Face.bbox``
    rows — no image reads, no GPU, no re-running InsightFace. Lets an existing library pick up the
    improved (sum-of-all-faces) signal cheaply instead of re-running face detection on ~28k photos."""
    updated = 0
    with session_scope() as s:
        q = s.query(Media).filter(Media.media_type == "photo", Media.is_portrait.isnot(None))
        if not force:
            q = q.filter(Media.face_area_ratio.is_(None))
        rows = q.all()
        for m in rows:
            if not m.width or not m.height:
                continue
            frame_area = float(m.width * m.height)
            total_ratio = 0.0
            for f in s.query(Face).filter(Face.media_id == m.id):
                try:
                    _x, _y, bw, bh = (float(v) for v in f.bbox.split(","))
                except (ValueError, AttributeError):
                    continue
                total_ratio += (bw * bh) / frame_area
            m.face_area_ratio = total_ratio
            updated += 1
    return {"updated": updated}


def cluster(progress: Callable[[float, str], None] | None = None) -> dict:
    """Cluster face embeddings into people via DBSCAN (cosine)."""
    from sklearn.cluster import DBSCAN

    with session_scope() as s:
        faces = s.query(Face).filter(Face.embedding.isnot(None)).all()
        if not faces:
            return {"clusters": 0, "faces": 0}
        if progress:
            progress(0.85, "Clustering faces")
        mat = np.stack([np.frombuffer(f.embedding, dtype=np.float32) for f in faces])
        labels = DBSCAN(eps=0.5, min_samples=3, metric="cosine").fit_predict(mat)

        # Reset existing clusters
        for c in s.query(FaceCluster).all():
            s.delete(c)
        s.flush()

        clusters: dict[int, FaceCluster] = {}
        for face, label in zip(faces, labels):
            if label == -1:  # noise / unique face
                face.cluster_id = None
                continue
            if label not in clusters:
                fc = FaceCluster(cover_media_id=face.media_id)
                s.add(fc)
                s.flush()
                clusters[label] = fc
            face.cluster_id = clusters[label].id
    if progress:
        progress(1.0, "Face clustering complete")
    return {"clusters": len(clusters), "faces": len(faces)}


def run(limit: int | None = None, force: bool = False, progress=None) -> dict:
    d = detect(limit=limit, force=force, progress=progress)
    c = cluster(progress=progress)
    return {**d, **c}
