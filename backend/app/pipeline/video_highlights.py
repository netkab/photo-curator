"""Build highlight reels from many clips — or trim one long clip down to its best moments.

Pipeline: cluster videos by date/place -> for each cluster, PySceneDetect splits scenes -> sample a
frame per scene -> score "interest" with the local vision model -> concatenate the top scenes into a
single reel. A single clip longer than ``highlight_solo_min_seconds`` is also eligible on its own:
its low-scoring (boring) scenes are dropped and the best ones stitched back in order. Output is a NEW
file under data/derived/ (kind="highlight"), reviewed before upload.

Two guards keep the run bounded (scoring each clip is slow — one vision call per scene): clips with
neither a date nor GPS are never stitched into one giant reel, and each event cluster samples at most
``highlight_max_clips_per_cluster`` clips spread across its timeline.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import settings
from ..db import session_scope
from ..models import DerivedMedia, Media
from . import metadata as md

# Videos with neither a capture date nor GPS land here. They are NOT one event, so we never stitch
# the whole pile into a single reel (it would also never finish — it can be thousands of clips).
_DEGENERATE_KEY = "undated|nowhere"

# Google-generated montages (year recaps etc.) — already-edited content with GP watermarks; recycling
# them into a "highlight" produces junk. Takeout also ships near-identical copies across folders.
_GENERATED_STEMS = {"RECAP"}


def _is_generated(name: str) -> bool:
    return Path(name).stem.upper() in _GENERATED_STEMS


def _dedupe_members(members: list[Media]) -> list[Media]:
    """Drop same-name ~same-duration copies within an event (Takeout split-archive duplicates that
    differ in bytes, so sha256 dedup at ingest missed them). Keeps the lowest id."""
    seen: set[tuple[str, int]] = set()
    out = []
    for m in sorted(members, key=lambda m: m.id):
        k = (m.rel_name.lower(), round(m.duration or 0))
        if k in seen:
            continue
        seen.add(k)
        out.append(m)
    return out


def _slug(s: str) -> str:
    """Filesystem-safe, space-free token: keep letters/digits/dots, collapse the rest to hyphens."""
    return re.sub(r"[^A-Za-z0-9.]+", "-", s).strip("-.")


def _reel_basename(key: str, ist_date: str) -> str:
    """Space-free reel name -> becomes the Google Photos title on upload. e.g.
    ``Highlights_Aquarium-Paradise_2025-07-26``. Unique per event (the place token, when present,
    plus the date; one undated/unplaced event can't share a date key)."""
    _, place = key.split("|", 1)
    bits = ["Highlights"]
    if place and place != "nowhere":
        bits.append(_slug(place))   # named place, or rounded "lat-lng" for un-geocoded spots
    bits.append(ist_date)
    return "_".join(bits)


def _sample_evenly(members: list[Media], k: int) -> list[Media]:
    """Pick at most ``k`` clips spread across the event's timeline (so a big day is still covered)."""
    if len(members) <= k:
        return members
    ordered = sorted(members, key=lambda m: m.taken_at or datetime.min)
    step = len(ordered) / k
    return [ordered[int(i * step)] for i in range(k)]


def _cluster_videos(videos: list[Media], solo_min_seconds: float, max_clips: int) -> dict[str, list[Media]]:
    buckets: dict[str, list[Media]] = defaultdict(list)
    for v in videos:
        if _is_generated(v.rel_name):
            continue
        day = v.taken_at.date().isoformat() if v.taken_at else "undated"
        place = v.place_name or (f"{round(v.gps_lat, 2)},{round(v.gps_lng, 2)}" if v.gps_lat else "nowhere")
        buckets[f"{day}|{place}"].append(v)

    out: dict[str, list[Media]] = {}
    for key, members in buckets.items():
        members = _dedupe_members(members)
        if len(members) >= 2:
            if key == _DEGENERATE_KEY:
                continue  # no date AND no place → not a real event; skip the whole pile
            out[key] = _sample_evenly(members, max_clips)
        elif len(members) == 1 and (members[0].duration or 0) >= solo_min_seconds:
            out[key] = members  # a single long clip is still worth trimming down to its best scenes
    return out


def _segments(path: str, duration: float | None) -> list[tuple[float, float]]:
    """Candidate moments in a clip as (start, end) seconds.

    ContentDetector only finds *cuts within* a video, so a single continuous shot (most phone clips)
    yields nothing. In that case we fall back to one whole-clip segment so every clip still becomes a
    scoring candidate — otherwise single-shot clips are silently dropped from the reel."""
    try:
        from scenedetect import ContentDetector, detect

        scenes = detect(path, ContentDetector())
    except Exception:
        scenes = []
    segs = [(sc[0].get_seconds(), sc[1].get_seconds()) for sc in scenes]
    if not segs and duration:
        segs = [(0.0, float(duration))]
    return segs


def _score_frame(image_path: Path) -> float:
    """Score a sampled frame 0..1 via moondream (people/action/quality). Falls back to 0.5."""
    import base64

    import requests

    try:
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        resp = requests.post(
            f"{settings.ollama_host}/api/generate",
            json={
                "model": settings.caption_model,
                "prompt": "Rate how interesting this video frame is for a highlights reel from 0 to 10 "
                          "(people, action, scenery, sharpness). Reply with only the number.",
                "images": [b64], "stream": False,
            },
            timeout=60,
        )
        txt = "".join(c for c in resp.json().get("response", "") if c.isdigit() or c == ".")
        return min(1.0, float(txt) / 10.0) if txt else 0.5
    except Exception:
        return 0.5


def _phash_close(a: str | None, b: str | None, thresh: int = 8) -> bool:
    """True when two sampled frames look near-identical (perceptual hash within `thresh` bits).
    Used to keep repeated shots of the same subject from crowding out variety in a reel."""
    if not a or not b:
        return False
    try:
        import imagehash

        return (imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)) <= thresh
    except Exception:
        return False


def _extract_clip(src: str, start: float, end: float, out: Path,
                  canvas_w: int = 1080, canvas_h: int = 1920) -> bool:
    # -ss before -i = fast seek; -t (output DURATION) after -i = exact length.
    # Crucially, normalize EVERY clip to one canvas / fps / timebase / pixel-format / audio layout. The
    # concat demuxer assumes uniform streams; mixing portrait+landscape (or differing timebases) other-
    # wise corrupts timestamps and roughly doubles the reel's duration.
    # `highlight_fit_mode` decides how an off-orientation clip meets the canvas:
    #   blur — whole clip centred over a blurred, zoomed copy of itself (no bars, nothing cropped)
    #   crop — scale up and centre-crop to fill (off-orientation clips lose their edges)
    #   pad  — letterbox with black bars (whole clip, but ugly bars)
    dur = max(0.1, end - start)
    w, h = canvas_w, canvas_h
    mode = settings.highlight_fit_mode
    if mode == "pad":
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p")
    elif mode == "crop":
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{h},setsar=1,fps=30,format=yuv420p")
    else:  # "blur" (default)
        vf = (f"split=2[bg][fg];"
              f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=20[bgb];"
              f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
              f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start), "-i", src, "-t", str(dur),
             "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-video_track_timescale", "30000",
             "-c:a", "aac", "-ar", "48000", "-ac", "2", str(out)],
            check=True, capture_output=True,
        )
        return out.exists()
    except Exception:
        return False


def run(top_scenes_per_cluster: int = 6, solo_min_seconds: float | None = None,
        max_clips_per_cluster: int | None = None, max_reels: int | None = None,
        progress: Callable[[float, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None) -> dict:
    if not shutil.which("ffmpeg"):
        return {"error": "ffmpeg not found on PATH"}
    if solo_min_seconds is None:
        solo_min_seconds = settings.highlight_solo_min_seconds
    if max_clips_per_cluster is None:
        max_clips_per_cluster = settings.highlight_max_clips_per_cluster
    clip_seconds = settings.highlight_clip_seconds

    reels = 0
    with session_scope() as s:
        videos = s.query(Media).filter(Media.media_type == "video").all()
        clusters = _cluster_videos(videos, solo_min_seconds, max_clips_per_cluster)

        # Resume-friendly: collect events that already have a reel and drop any duplicate rows (one per
        # event — they share the deterministic filename). A re-run after a crash/stop then fills in only
        # the missing reels instead of redoing the whole library or piling up duplicates. (A full
        # rebuild is "Reset → Build": Reset clears the reels, so nothing is skipped.)
        done_keys: set[str] = set()
        for d in s.query(DerivedMedia).filter(DerivedMedia.kind == "highlight").order_by(DerivedMedia.id).all():
            try:
                ck = json.loads(d.meta).get("cluster") if d.meta else None
            except Exception:
                ck = None
            if ck and ck in done_keys:
                s.delete(d)            # duplicate row for an already-kept event
            elif ck:
                done_keys.add(ck)
        s.commit()
        clusters = {k: v for k, v in clusters.items() if k not in done_keys}

        # Progress is per source clip (not per cluster): scoring each clip is the slow part, so this
        # keeps the bar moving instead of freezing at 0% inside one big cluster.
        total_clips = sum(len(m) for m in clusters.values()) or 1
        done_clips = 0

        for key, members in clusters.items():
            if should_cancel and should_cancel():
                break
            scored: list[tuple[float, str, float, float, str | None]] = []  # (score, src, start, end, phash)
            with tempfile.TemporaryDirectory() as td:
                for v in members:
                    if should_cancel and should_cancel():
                        break
                    if progress:
                        progress(done_clips / total_clips,
                                 f"Highlight {key}: scoring clip {done_clips + 1}/{total_clips}")
                    for start, end in _segments(v.abs_path, v.duration):
                        if end - start < 1.0:
                            continue
                        mid = (start + end) / 2
                        # Trim the picked moment to clip_seconds around the sampled frame so a long
                        # single-shot clip contributes a short highlight, not its whole length.
                        win = min(end - start, clip_seconds)
                        hs, he = max(start, mid - win / 2), min(end, mid + win / 2)
                        frame = Path(td) / f"f_{v.id}_{int(mid)}.jpg"
                        subprocess.run(["ffmpeg", "-y", "-ss", str(mid), "-i", v.abs_path,
                                        "-frames:v", "1", str(frame)], capture_output=True)
                        score = _score_frame(frame) if frame.exists() else 0.4
                        ph = md.compute_phash(frame) if frame.exists() else None
                        scored.append((score, v.abs_path, hs, he, ph))
                    done_clips += 1

                if not scored:
                    continue
                # A solo clip is only worth a reel if there are lower-scoring scenes to drop;
                # otherwise we'd just re-stitch the whole video with no trimming.
                if len(members) == 1 and len(scored) <= top_scenes_per_cluster:
                    continue
                # Pick best-first with diversity: at most 2 moments per source clip (a solo reel is
                # exempt — all its moments come from the one clip), and skip moments that look
                # near-identical to one already picked (repeated shots of the same subject).
                clip_cap = top_scenes_per_cluster if len(members) == 1 else 2
                picks: list[tuple[float, str, float, float, str | None]] = []
                per_clip: dict[str, int] = {}
                for cand in sorted(scored, key=lambda c: c[0], reverse=True):
                    if len(picks) >= top_scenes_per_cluster:
                        break
                    if per_clip.get(cand[1], 0) >= clip_cap:
                        continue
                    if any(_phash_close(cand[4], p[4]) for p in picks):
                        continue
                    picks.append(cand)
                    per_clip[cand[1]] = per_clip.get(cand[1], 0) + 1
                if not picks:
                    continue
                # chronological order within the reel
                picks.sort(key=lambda x: (x[1], x[2]))

                # Reel aspect (config). Landscape by default so it fills a landscape screen; the fit
                # mode then decides how the off-orientation clips meet the canvas. "auto" matches each
                # event's majority orientation.
                if settings.highlight_canvas == "portrait":
                    canvas_w, canvas_h = 1080, 1920
                elif settings.highlight_canvas == "auto":
                    dims = {m.abs_path: (m.width or 0, m.height or 0) for m in members}
                    portrait = sum(1 for _, src, _, _, _ in picks if dims.get(src, (0, 0))[1] >= dims.get(src, (0, 0))[0])
                    canvas_w, canvas_h = (1080, 1920) if portrait * 2 >= len(picks) else (1920, 1080)
                else:  # "landscape" (default)
                    canvas_w, canvas_h = 1920, 1080

                clip_paths = []
                for idx, (_, src, start, end, _ph) in enumerate(picks):
                    cp = Path(td) / f"clip_{idx}.mp4"
                    if _extract_clip(src, start, end, cp, canvas_w, canvas_h):
                        clip_paths.append(cp)
                if not clip_paths:
                    continue

                listfile = Path(td) / "list.txt"
                listfile.write_text("".join(f"file '{c.as_posix()}'\n" for c in clip_paths))
                # Date the reel by its earliest source clip so it sits with that event in the timeline.
                rep_dt = min((m.taken_at for m in members if m.taken_at), default=None)
                creation = md.video_creation_time(rep_dt, Path(clip_paths[0]))  # IST ISO string
                clips_used = len({src for _, src, _, _, _ in picks})
                _, place = key.split("|", 1)
                where = place if place and place != "nowhere" else "an unknown place"
                title = _reel_basename(key, creation[:10])
                caption = f"Highlights from {where} on {creation[:10]} ({len(picks)} moments from {clips_used} clips)"
                out_path = settings.derived_dir / f"{title}.mp4"
                meta_args = ["-metadata", f"creation_time={creation}",
                             "-metadata", f"title={title}", "-metadata", f"comment={caption}"]
                try:
                    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
                                    "-c", "copy", *meta_args, str(out_path)],
                                   check=True, capture_output=True)
                except Exception:
                    continue
                md.set_mtime(out_path, rep_dt)

            s.add(DerivedMedia(
                source_media_id=None, kind="highlight", path=out_path.name,
                meta=json.dumps({"cluster": key, "source_count": len(members),
                                 "scenes": len(picks), "clips_used": clips_used,
                                 "title": title, "caption": caption,
                                 "source_ids": [m.id for m in members],  # to compare reel vs originals
                                 "taken_at": rep_dt.isoformat() if rep_dt else None}),
            ))
            s.commit()  # persist each reel as it's made: shows up live, and a cancel can't orphan files
            reels += 1
            if max_reels and reels >= max_reels:
                break  # small test batch: stop after the requested number of reels
    if progress:
        progress(1.0, "Highlights complete")
    return {"reels": reels}
