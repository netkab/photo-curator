"""Orchestrates the local AI analysis pass. Stages run sequentially and release VRAM between them
(via the lru_cache loaders living in separate modules) to avoid thermal throttling on the GTX 1070.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from ..db import session_scope
from ..models import Media
from . import blur, captions, faces, geo, ocr

STAGES = ("blur", "captions", "ocr", "faces", "geo")


def run(only: str | None = None, limit: int | None = None, force: bool = False,
        progress: Callable[[float, str], None] | None = None) -> dict:
    stages = [only] if only else list(STAGES)
    results: dict[str, dict] = {}
    n = len(stages)

    def stage_progress(idx: int):
        def _p(p: float, msg: str) -> None:
            if progress:
                progress((idx + p) / n, msg)
        return _p

    for idx, stage in enumerate(stages):
        if stage == "blur":
            results["blur"] = blur.compute_blur_scores(force=force, progress=stage_progress(idx))
        elif stage == "captions":
            results["captions"] = captions.run(limit=limit, force=force, progress=stage_progress(idx))
        elif stage == "ocr":
            results["ocr"] = ocr.run(limit=limit, force=force, progress=stage_progress(idx))
        elif stage == "faces":
            results["faces"] = faces.run(limit=limit, force=force, progress=stage_progress(idx))
        elif stage == "geo":
            results["geo"] = geo.reverse_geocode(limit=limit, force=force, progress=stage_progress(idx))

    # Mark analyzed
    if not only:
        with session_scope() as s:
            for m in s.query(Media).filter(Media.analyzed_at.is_(None)).all():
                m.analyzed_at = datetime.utcnow()

    if progress:
        progress(1.0, "Analysis complete")
    return results
