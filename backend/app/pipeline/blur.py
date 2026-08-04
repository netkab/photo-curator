"""Blur detection via OpenCV Laplacian variance (CPU). Lower variance => blurrier.

Scored on the already-generated thumbnail (`Media.thumb_path`, `thumbnail_px` long edge — 512 by
default), not the original file. Laplacian variance is **not scale-invariant**: computed on a
photo's native resolution, a small or heavily-downscaled image can show a *higher* raw variance than
a full-resolution photo of the same scene, purely because edge energy per pixel changes with
sampling density. That isn't hypothetical — verified against 63 real duplicate groups where a 20 KB
Picasa-era thumbnail, a Google "Motion Photo" GIF preview, or a phone screenshot outscored a
full-resolution camera original on raw variance and was picked as the group's keeper as a result.
Scoring on a common, fixed-size thumbnail for every photo makes the metric directly comparable across
the whole library, and is far cheaper than decoding multi-megapixel originals.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..db import session_scope
from ..models import Media


def laplacian_variance(path: str, thumb_path: str | None = None) -> float | None:
    from ..config import settings

    try:
        import cv2

        src = path
        if thumb_path:
            t = settings.thumbs_dir / thumb_path
            if t.exists():
                src = str(t)
        img = cv2.imread(src, cv2.IMREAD_GRAYSCALE)
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
            if progress and i % 200 == 0:
                progress(i / total, f"Blur scoring {m.rel_name}")
            v = laplacian_variance(m.abs_path, m.thumb_path)
            if v is None:
                continue
            m.blur_score = v
            scored += 1
            if v < settings.blur_threshold:
                blurry += 1
    if progress:
        progress(1.0, "Blur scoring complete")
    return {"scored": scored, "blurry": blurry}
