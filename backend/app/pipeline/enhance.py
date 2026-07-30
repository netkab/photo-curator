"""Photo enhancement: Real-ESRGAN upscaling (tiled for 8 GB) + GFPGAN face restoration.

Outputs NEW files under data/derived/ and records a DerivedMedia row (status="pending"). Originals
are never modified. A matching ``upload`` review-action is created by the router/CLI layer.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Callable

from ..config import settings
from ..db import session_scope
from ..models import DerivedMedia, Media
from . import metadata as md


@lru_cache(maxsize=1)
def _load_enhancers():
    """Return (upsampler, face_restorer). Tiled Real-ESRGAN keeps VRAM within 8 GB."""
    import torch
    from basicsr.archs.rrdbnet_arch import RRDBNet
    from realesrgan import RealESRGANer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
    upsampler = RealESRGANer(
        scale=4, model_path="weights/RealESRGAN_x4plus.pth", model=model,
        tile=256, tile_pad=10, pre_pad=0, half=(device == "cuda"), device=device,
    )
    face_restorer = None
    try:
        from gfpgan import GFPGANer

        face_restorer = GFPGANer(
            model_path="weights/GFPGANv1.4.pth", upscale=4, arch="clean",
            channel_multiplier=2, bg_upsampler=upsampler,
        )
    except Exception:
        pass
    return upsampler, face_restorer


def enhance_one(media: Media) -> Path | None:
    import cv2

    upsampler, face_restorer = _load_enhancers()
    img = cv2.imread(media.abs_path)
    if img is None:
        return None
    if face_restorer is not None and media.is_portrait:
        _, _, output = face_restorer.enhance(img, has_aligned=False, only_center_face=False, paste_back=True)
    else:
        output, _ = upsampler.enhance(img, outscale=4)
    out_path = settings.derived_dir / f"enhanced_{media.id}.jpg"
    cv2.imwrite(str(out_path), output)
    # cv2 strips EXIF — copy the original's date/GPS/orientation back so a manual upload lands at the
    # same spot in the Google Photos timeline as the original.
    md.copy_image_timestamp(Path(media.abs_path), out_path)
    return out_path


def run(ids: list[int] | None = None, blurry: bool = False,
        progress: Callable[[float, str], None] | None = None) -> dict:
    made = 0
    with session_scope() as s:
        q = s.query(Media).filter(Media.media_type == "photo")
        if ids:
            q = q.filter(Media.id.in_(ids))
        elif blurry:
            q = q.filter(Media.blur_score.isnot(None), Media.blur_score < settings.blur_threshold)
        rows = q.all()
        total = len(rows) or 1
        for i, m in enumerate(rows):
            if progress:
                progress(i / total, f"Enhancing {m.rel_name}")
            out = enhance_one(m)
            if not out:
                continue
            before = m.bytes or 0
            after = out.stat().st_size
            s.add(DerivedMedia(
                source_media_id=m.id, kind="enhanced", path=out.name,
                meta=json.dumps({"bytes_before": before, "bytes_after": after,
                                 "blur_score": m.blur_score,
                                 "taken_at": m.taken_at.isoformat() if m.taken_at else None}),
            ))
            made += 1
    if progress:
        progress(1.0, "Enhancement complete")
    return {"enhanced": made}
