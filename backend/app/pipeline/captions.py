"""Image captioning + tags via moondream served by Ollama (local).

Ollama must be running (``OLLAMA_HOST``). Falls back to no-op if unreachable so the rest of the
analyze pass can still proceed.
"""
from __future__ import annotations

import base64
import io
import re
from typing import Callable

import requests

from ..config import settings
from ..db import session_scope
from ..models import Caption, Media

_PROMPT = (
    "Describe this photo in one concise sentence for a personal photo library. "
    "Then list 3-6 short tags (objects, scene, mood) after the line 'TAGS:'."
)


def _encode_image(path: str, max_px: int = 768) -> str | None:
    try:
        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _ollama_caption(b64: str) -> str | None:
    try:
        resp = requests.post(
            f"{settings.ollama_host}/api/generate",
            json={"model": settings.caption_model, "prompt": _PROMPT, "images": [b64], "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception:
        return None


def _split_caption_tags(text: str) -> tuple[str, str | None]:
    if "TAGS:" in text:
        cap, _, tags = text.partition("TAGS:")
        return cap.strip(), ",".join(t.strip() for t in tags.replace("\n", ",").split(",") if t.strip())
    return text.strip(), None


def is_caption_garbage(text: str | None) -> bool:
    """moondream occasionally drops into detection mode and returns coordinate lists like
    ``ids [0.39, 0.44, 0.61, 0.64]`` instead of a description. Treat those (and other non-prose) as
    garbage so we neither store nor draft reviews from them."""
    t = (text or "").strip()
    if not t:
        return True
    if re.match(r"^\s*ids?\s*[\[\(]", t, re.I):        # "ids [0.39, ...]"
        return True
    if re.search(r"[\[\(]\s*\d+\.\d+(\s*,\s*\d+\.\d+)+", t):  # bracketed float/coord lists
        return True
    if sum(c.isalpha() for c in t) < 8:                # essentially no words
        return True
    return False


def run(limit: int | None = None, force: bool = False, progress: Callable[[float, str], None] | None = None) -> dict:
    done = 0
    with session_scope() as s:
        q = s.query(Media).filter(Media.media_type == "photo")
        if not force:
            q = q.outerjoin(Caption).filter(Caption.id.is_(None))
        if limit:
            q = q.limit(limit)
        rows = q.all()
        total = len(rows) or 1
        for i, m in enumerate(rows):
            if progress:
                progress(i / total, f"Captioning {m.rel_name}")
            b64 = _encode_image(m.abs_path)
            if not b64:
                continue
            text = _ollama_caption(b64)
            if not text:
                continue
            cap, tags = _split_caption_tags(text)
            if is_caption_garbage(cap):
                continue  # model returned detection coords / non-prose — don't store garbage
            s.add(Caption(media_id=m.id, text=cap, tags=tags, model=settings.caption_model))
            done += 1
    if progress:
        progress(1.0, "Captioning complete")
    return {"captioned": done}
