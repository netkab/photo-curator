"""Compress videos with ffmpeg h264_nvenc, keeping a quality floor (>= target height, good bitrate).

Clips already at/under both the target resolution AND bitrate are skipped up front — there's no real
saving to gain and re-encoding could grow them. As a final safety net, any output that ends up >= its
source is discarded, so compression never increases a file's size.

Outputs NEW files under data/derived/ and records DerivedMedia(kind="compressed"). The original is
never deleted — replacing it in Google Photos stays a manual step.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from ..config import settings
from ..db import session_scope
from ..models import DerivedMedia, Media
from . import metadata as md


def _has_nvenc() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
        return "h264_nvenc" in out.stdout
    except Exception:
        return False


def _parse_bitrate(b: str) -> int:
    """ffmpeg-style bitrate string -> bits/s. '8M' -> 8_000_000, '800k' -> 800_000. 0 if unparseable."""
    s = b.strip().lower()
    mult = 1
    if s.endswith("k"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def _worth_compressing(media: Media, target_h: int, target_bitrate_bps: int) -> bool:
    """Skip clips already at/under both the target height AND bitrate: re-encoding them to the same
    target would burn GPU time for little/no saving (and risks growing the file). Prefer the values
    already in the catalog; only probe the file if those are missing. When height/bitrate stay
    unknown, attempt it anyway — the post-encode size check still guards against growth."""
    height = media.height
    bitrate = int(media.bytes * 8 / media.duration) if media.bytes and media.duration else None
    if not height or not bitrate:
        info = md.probe_video(Path(media.abs_path))
        height = height or info.get("height")
        bitrate = bitrate or info.get("bitrate")
    if not height or not bitrate:
        return True
    return height > target_h or bitrate > target_bitrate_bps


def compress_one(media: Media, target_h: int, bitrate: str) -> Path | None:
    if not shutil.which("ffmpeg"):
        return None
    out_path = settings.derived_dir / f"compressed_{media.id}.mp4"
    # Don't upscale: scale down to target height only if larger; keep aspect; even dims for h264.
    vf = f"scale='-2:min({target_h},ih)'"
    if _has_nvenc():
        vcodec = ["-c:v", "h264_nvenc", "-preset", "p5", "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", "16M"]
    else:  # CPU fallback — higher quality, slower
        vcodec = ["-c:v", "libx264", "-preset", "slow", "-crf", "20"]
    # Stamp the original's capture time into the container so a manual upload keeps its timeline slot.
    creation = md.video_creation_time(media.taken_at, Path(media.abs_path))
    cmd = ["ffmpeg", "-y", "-i", str(media.abs_path), "-vf", vf, *vcodec,
           "-c:a", "aac", "-b:a", "128k", "-metadata", f"creation_time={creation}",
           "-movflags", "+faststart", str(out_path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        md.copy_file_times(Path(media.abs_path), out_path)
        return out_path
    except Exception:
        return None


def run(target_h: int | None = None, bitrate: str | None = None,
        progress: Callable[[float, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None) -> dict:
    target_h = target_h or settings.video_target_height
    bitrate = bitrate or settings.video_bitrate
    target_bitrate_bps = _parse_bitrate(bitrate)
    made = saved_bytes = skipped = 0
    with session_scope() as s:
        rows = s.query(Media).filter(Media.media_type == "video").all()
        # Skip ones already compressed
        done_ids = {d.source_media_id for d in s.query(DerivedMedia).filter(DerivedMedia.kind == "compressed").all()}
        rows = [m for m in rows if m.id not in done_ids]
        total = len(rows) or 1
        for i, m in enumerate(rows):
            if should_cancel and should_cancel():
                break
            # Don't transcode clips already at/under the target res & bitrate — no real saving to gain.
            if target_bitrate_bps and not _worth_compressing(m, target_h, target_bitrate_bps):
                skipped += 1
                continue
            if progress:
                progress(i / total, f"Compressing {m.rel_name}")
            out = compress_one(m, target_h, bitrate)
            if not out or not out.exists():
                continue
            before, after = (m.bytes or 0), out.stat().st_size
            if after >= before and before:  # no savings — drop the derived file
                out.unlink(missing_ok=True)
                continue
            saved_bytes += max(0, before - after)
            s.add(DerivedMedia(
                source_media_id=m.id, kind="compressed", path=out.name,
                meta=json.dumps({"bytes_before": before, "bytes_after": after}),
            ))
            s.commit()  # persist each clip as it's made: shows up live, and a cancel can't orphan files
            made += 1
    if progress:
        progress(1.0, "Compression complete")
    return {"compressed": made, "skipped": skipped, "saved_mb": round(saved_bytes / 1e6, 1)}
