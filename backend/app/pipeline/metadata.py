"""Low-level media helpers: hashing, dimensions, thumbnails, EXIF (exiftool), video probe (ffprobe).

CPU-only and dependency-tolerant: if exiftool/ffprobe aren't on PATH, the richer fields are simply
left empty rather than raising.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import settings

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".webm", ".mpg", ".mpeg"}


def media_type(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def compute_phash(path: Path) -> str | None:
    try:
        import imagehash
        from PIL import Image

        with Image.open(path) as im:
            return str(imagehash.phash(im.convert("RGB")))
    except Exception:
        return None


def image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.width, im.height
    except Exception:
        return None, None


def make_thumbnail(path: Path, media_id: int, mtype: str) -> str | None:
    """Write a thumbnail into data/thumbnails/<id>.jpg; return its relative name or None."""
    out = settings.thumbs_dir / f"{media_id}.jpg"
    try:
        if mtype == "photo":
            from PIL import Image

            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((settings.thumbnail_px, settings.thumbnail_px))
                im.save(out, "JPEG", quality=82)
        else:  # video: grab a frame at ~1s via ffmpeg
            if not shutil.which("ffmpeg"):
                return None
            subprocess.run(
                ["ffmpeg", "-y", "-ss", "1", "-i", str(path), "-frames:v", "1",
                 "-vf", f"scale={settings.thumbnail_px}:-1", str(out)],
                check=True, capture_output=True,
            )
        return out.name
    except Exception:
        return None


def read_exif(path: Path) -> dict[str, Any]:
    """Use exiftool (if present) for GPS / timestamp / camera. Numeric output (-n)."""
    if not shutil.which("exiftool"):
        return {}
    try:
        proc = subprocess.run(
            ["exiftool", "-json", "-n", "-GPSLatitude", "-GPSLongitude",
             "-DateTimeOriginal", "-CreateDate", "-Model", "-Make", str(path)],
            check=True, capture_output=True, text=True,
        )
        data = json.loads(proc.stdout)
        return data[0] if data else {}
    except Exception:
        return {}


def probe_video(path: Path) -> dict[str, Any]:
    """Return {codec, bitrate, width, height, duration} via ffprobe, or {} if unavailable."""
    if not shutil.which("ffprobe"):
        return {}
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            check=True, capture_output=True, text=True,
        )
        info = json.loads(proc.stdout)
        vstream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
        fmt = info.get("format", {})
        return {
            "codec": vstream.get("codec_name"),
            "width": vstream.get("width"),
            "height": vstream.get("height"),
            "bitrate": int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
            "duration": float(fmt["duration"]) if fmt.get("duration") else None,
        }
    except Exception:
        return {}


# --- Timestamp / location preservation for derived files -----------------------------------------
# A derived photo/video that you upload manually should land at the SAME point in the Google Photos
# timeline as its original. Google Photos reads the embedded date (EXIF DateTimeOriginal for images,
# container creation_time for videos), so we copy those across. GPS + orientation are copied too so
# the upload matches the original exactly.

def copy_image_timestamp(src: Path, dst: Path) -> bool:
    """Copy date / orientation / GPS EXIF from ``src`` into ``dst`` (in place). Returns True on
    success. Always also copies filesystem times as a fallback. Requires exiftool for EXIF."""
    copy_file_times(src, dst)
    if not shutil.which("exiftool"):
        return False
    try:
        subprocess.run(
            ["exiftool", "-overwrite_original", "-TagsFromFile", str(src),
             "-EXIF:DateTimeOriginal", "-EXIF:CreateDate", "-EXIF:ModifyDate",
             "-EXIF:Orientation", "-GPS:all", str(dst)],
            check=True, capture_output=True,
        )
        copy_file_times(src, dst)  # exiftool touches mtime; restore it
        return True
    except Exception:
        return False


def copy_file_times(src: Path, dst: Path) -> None:
    """Mirror ``src``'s access/modified filesystem times onto ``dst``."""
    try:
        st = src.stat()
        os.utime(dst, (st.st_atime, st.st_mtime))
    except Exception:
        pass


def set_mtime(path: Path, dt: datetime | None) -> None:
    """Set ``path``'s filesystem times to ``dt`` (no-op if dt is None)."""
    if dt is None:
        return
    try:
        ts = dt.timestamp()
        os.utime(path, (ts, ts))
    except Exception:
        pass


_IST = timezone(timedelta(hours=5, minutes=30))


def video_creation_time(taken_at: datetime | None, src: Path) -> str:
    """ISO-8601 string for ffmpeg ``-metadata creation_time=`` in IST wall-clock.

    ``taken_at`` is stored as naive UTC (Takeout's ``photoTakenTime``), so we shift it to IST before
    stamping. Google Photos shows a video's embedded creation time as-is (no tz conversion), so writing
    the IST wall-clock makes the reel land on the correct India date — including late-night captures
    whose UTC date is the previous day. The filesystem-time fallback is already local (IST machine)."""
    if taken_at is not None:
        dt = taken_at.replace(tzinfo=timezone.utc).astimezone(_IST).replace(tzinfo=None)
    else:
        dt = datetime.fromtimestamp(src.stat().st_mtime)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# --- Filename date inference -----------------------------------------------------------------------
# Capture date embedded in common phone/camera/app filenames, used to backfill `taken_at` for clips
# Takeout left undated. Date is required; time is optional (defaults to midnight).
#   VID_20190406_123456.mp4 · PXL_20190406_123456789.mp4 · VID-20190406-WA0001.mp4 · 20190406123456.mp4
_RE_DASHED = re.compile(
    r"(?<!\d)(\d{4})[-_.](\d{2})[-_.](\d{2})"               # YYYY-MM-DD (any of - _ .)
    r"(?:[ _T-]+(\d{2})[-_.:](\d{2})(?:[-_.:](\d{2}))?)?"   # optional HH:MM[:SS]
)
_RE_COMPACT = re.compile(
    r"(?<!\d)(\d{4})(\d{2})(\d{2})"                          # YYYYMMDD
    r"(?:[ _T-]?(\d{2})(\d{2})(\d{2})?)?"                    # optional HHMM[SS]
)


def parse_datetime_from_name(name: str) -> datetime | None:
    """Best-effort capture datetime parsed from a filename. Returns None if nothing plausible found.
    Years are bounded to 2000..next-year to avoid matching IDs / resolutions / epoch numbers.
    When the name has no time, noon is used (not midnight) so Google Photos' UTC interpretation of the
    container creation_time can't shift the clip onto an adjacent calendar date."""
    for rx in (_RE_DASHED, _RE_COMPACT):
        for m in rx.finditer(name):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if m.group(4):  # filename carried an explicit time
                h, mi, s = int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)
            else:           # date-only → noon (timezone-safe for the GP timeline)
                h, mi, s = 12, 0, 0
            if not (2000 <= y <= datetime.now().year + 1):
                continue
            try:
                return datetime(y, mo, d, h, mi, s)
            except ValueError:
                continue
    return None
