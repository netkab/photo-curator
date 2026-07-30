"""Runtime helpers for the local AI stack — GPU/tool availability checks surfaced to the UI.

Heavy model loading lives in the individual pipeline modules (lazy + lru_cache) so this module stays
import-safe even when torch/onnxruntime aren't installed.
"""
from __future__ import annotations

import shutil

import requests

from ..config import settings


def gpu_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def gpu_name() -> str | None:
    try:
        import torch

        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception:
        return None


def ollama_up() -> bool:
    try:
        r = requests.get(f"{settings.ollama_host}/api/tags", timeout=3)
        return r.ok
    except Exception:
        return False


def environment_report() -> dict:
    """Summarize what's installed — used by `photo-setup` verification and the UI health panel."""
    return {
        "gpu": gpu_name(),
        "gpu_available": gpu_available(),
        "ollama": ollama_up(),
        "exiftool": bool(shutil.which("exiftool")),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "places_enabled": bool(settings.places_api_key),
        "uploads_enabled": settings.uploads_enabled,
    }
