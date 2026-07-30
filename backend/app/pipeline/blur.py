"""Blur detection via OpenCV Laplacian variance (CPU). Lower variance => blurrier."""
from __future__ import annotations

from typing import Callable

from ..db import session_scope
from ..models import Media


def laplacian_variance(path: str) -> float | None:
    try:
        import cv2

        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        return None


def compute_blur_scores(force: bool = False, progress: Callable[[float, str], None] | None = None) -> dict:
    """Populate ``media.blur_score`` for photos. Returns {scored, blurry}."""
    from ..config import settings

    scored = blurry = 0
    with session_scope() as s:
        q = s.query(Media).filter(Media.media_type == "photo")
        if not force:
            q = q.filter(Media.blur_score.is_(None))
        rows = q.all()
        total = len(rows) or 1
        for i, m in enumerate(rows):
            if progress and i % 25 == 0:
                progress(i / total, f"Blur scoring {m.rel_name}")
            v = laplacian_variance(m.abs_path)
            if v is None:
                continue
            m.blur_score = v
            scored += 1
            if v < settings.blur_threshold:
                blurry += 1
    if progress:
        progress(1.0, "Blur scoring complete")
    return {"scored": scored, "blurry": blurry}
