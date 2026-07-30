"""OCR via Florence-2 (transformers). Lazy-loaded; PaddleOCR is an easy drop-in fallback.

Florence-2 is small (~2.5 GB) and strong at reading text in photos (signs, menus, screenshots).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Callable

from ..config import settings
from ..db import session_scope
from ..models import Media, OcrText


@lru_cache(maxsize=1)
def _load_florence():
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        settings.ocr_model, trust_remote_code=True, torch_dtype=dtype
    ).to(device).eval()
    processor = AutoProcessor.from_pretrained(settings.ocr_model, trust_remote_code=True)
    return model, processor, device, dtype


def _ocr_image(path: str) -> str | None:
    try:
        from PIL import Image

        model, processor, device, dtype = _load_florence()
        image = Image.open(path).convert("RGB")
        inputs = processor(text="<OCR>", images=image, return_tensors="pt").to(device, dtype)
        ids = model.generate(
            input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
            max_new_tokens=512, num_beams=3,
        )
        text = processor.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(text, task="<OCR>", image_size=(image.width, image.height))
        return (parsed.get("<OCR>") or "").strip() or None
    except Exception:
        return None


def run(limit: int | None = None, force: bool = False, progress: Callable[[float, str], None] | None = None) -> dict:
    done = found = 0
    with session_scope() as s:
        q = s.query(Media).filter(Media.media_type == "photo")
        if not force:
            q = q.outerjoin(OcrText).filter(OcrText.id.is_(None))
        if limit:
            q = q.limit(limit)
        rows = q.all()
        total = len(rows) or 1
        for i, m in enumerate(rows):
            if progress:
                progress(i / total, f"OCR {m.rel_name}")
            text = _ocr_image(m.abs_path)
            done += 1
            if text:
                s.add(OcrText(media_id=m.id, text=text, model="florence-2"))
                found += 1
    if progress:
        progress(1.0, "OCR complete")
    return {"processed": done, "with_text": found}
