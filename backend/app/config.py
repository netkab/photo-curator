"""Configuration + tunable thresholds.

Reads ``backend/.env`` (via python-dotenv) and exposes a singleton ``settings``. Kept dependency-light
(plain dataclass + os.environ) so importing the app never fails when optional GPU deps are absent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # dotenv optional at import time
    pass

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _path(env: str, default: Path) -> Path:
    raw = os.getenv(env, "").strip()
    return Path(raw) if raw else default


@dataclass
class Settings:
    # --- Paths ---
    data_dir: Path = field(default_factory=lambda: _path("DATA_DIR", BACKEND_DIR / "data"))
    takeout_dir: Path = field(default_factory=lambda: _path("TAKEOUT_DIR", BACKEND_DIR / "data" / "takeout"))
    library_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["LIBRARY_DIR"]) if os.getenv("LIBRARY_DIR") else None
    )
    # Where reclaimed local duplicates are MOVED (never deleted). Defaults to a sibling of the
    # Takeout folder so it lands on the same drive — a same-volume move is instant and atomic,
    # whereas crossing drives would copy 27 GB and could half-finish.
    quarantine_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["QUARANTINE_DIR"]) if os.getenv("QUARANTINE_DIR") else None
    )

    # --- Local AI ---
    ollama_host: str = field(default_factory=lambda: os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"))
    caption_model: str = field(default_factory=lambda: os.getenv("CAPTION_MODEL", "moondream"))
    ocr_model: str = field(default_factory=lambda: os.getenv("OCR_MODEL", "microsoft/Florence-2-large"))

    # --- Google ---
    places_api_key: str = field(default_factory=lambda: os.getenv("GOOGLE_PLACES_API_KEY", ""))
    photos_client_id: str = field(default_factory=lambda: os.getenv("PHOTOS_OAUTH_CLIENT_ID", ""))
    photos_client_secret: str = field(default_factory=lambda: os.getenv("PHOTOS_OAUTH_CLIENT_SECRET", ""))
    photos_album: str = field(default_factory=lambda: os.getenv("PHOTOS_UPLOAD_ALBUM", "Photo Curator (curated)"))

    # --- Tunable thresholds ---
    blur_threshold: float = 100.0          # Laplacian variance below this => "blurry"
    phash_max_distance: int = 6            # Hamming distance <= this => near-duplicate (resize/recompress)
    clip_similarity: float = 0.92          # cosine sim >= this => semantic near-duplicate
    # Groups larger than this are excluded from bulk trashing and flagged in the UI. Real duplicate
    # sets are small; a huge one is almost always a clustering artifact, and sweeping it up in a
    # bulk run would bin hundreds of distinct photos to keep one.
    dedup_max_group_size: int = field(
        default_factory=lambda: int(os.getenv("DEDUP_MAX_GROUP_SIZE", "25")))
    # A duplicate-group member below this fraction of the group's largest resolution is disqualified
    # from being chosen as keeper, no matter how it scores on sharpness or anything else. Verified
    # against 63 real groups: junk copies (Picasa-era thumbnails, Google Motion-Photo GIF previews,
    # phone screenshots of a photo) top out around 16% of the real photo's resolution, while
    # legitimately-smaller-but-real photos start around 29% — 25% sits cleanly in that gap. Without
    # this floor, a 20 KB 364x273 thumbnail out-scored a 2.2 MB 10 MP camera original on raw
    # sharpness and was picked as the keeper.
    dedup_keeper_min_resolution_ratio: float = field(
        default_factory=lambda: float(os.getenv("DEDUP_KEEPER_MIN_RESOLUTION_RATIO", "0.25")))
    portrait_face_ratio: float = 0.10      # face area / image area >= this => "portrait"
    # Sum of ALL faces' area / image area >= this => a person/group is the subject (Maps Studio).
    # Higher than portrait_face_ratio since it's a sum across possibly several faces, not just one.
    person_focus_area_ratio: float = 0.15
    thumbnail_px: int = 512

    # --- Maps Studio ---
    # Photos within this many metres of a place cluster's centroid are grouped into it (user wanted a
    # ~50–100 m approximation rather than exact rounded coordinates).
    place_cluster_radius_m: float = field(
        default_factory=lambda: float(os.getenv("PLACE_CLUSTER_RADIUS_M", "75")))

    # --- Video ---
    video_target_height: int = field(default_factory=lambda: int(os.getenv("VIDEO_TARGET_HEIGHT", "1080")))
    video_bitrate: str = field(default_factory=lambda: os.getenv("VIDEO_BITRATE", "8M"))
    # A single clip this long (s) or more is eligible for a solo highlight — trim its boring scenes.
    highlight_solo_min_seconds: float = field(
        default_factory=lambda: float(os.getenv("HIGHLIGHT_SOLO_MIN_SECONDS", "60")))
    # Cap source clips sampled per event cluster — scene-scoring each clip is slow (one vision call
    # per scene), so a huge bucket would never finish. Clips are sampled evenly across the event.
    highlight_max_clips_per_cluster: int = field(
        default_factory=lambda: int(os.getenv("HIGHLIGHT_MAX_CLIPS_PER_CLUSTER", "40")))
    # Ceiling (s) on any one picked moment in a reel — a small event still gets a generous slice per
    # clip. For a multi-clip event this shrinks toward highlight_min_clip_seconds as clip count grows
    # (see highlight_target_seconds), so a big day still fits every clip in without hitting this cap.
    highlight_clip_seconds: float = field(
        default_factory=lambda: float(os.getenv("HIGHLIGHT_CLIP_SECONDS", "5")))
    # Floor (s) on a picked moment — every source clip in a multi-clip event is guaranteed at least
    # one moment of at least this length, no matter how many clips the event has.
    highlight_min_clip_seconds: float = field(
        default_factory=lambda: float(os.getenv("HIGHLIGHT_MIN_CLIP_SECONDS", "2")))
    # Soft total reel duration (s) a multi-clip event aims for: per-clip moment length is
    # target/clip_count (clamped to [min, ceiling]) so the reel scales gracefully with event size
    # instead of a fixed moment-count cap silently dropping clips from big events. It's a soft target,
    # not a hard cap — coverage (every clip gets ≥1 moment) always wins, so a very large event can
    # still run longer than this once every clip is at the floor length.
    highlight_target_seconds: float = field(
        default_factory=lambda: float(os.getenv("HIGHLIGHT_TARGET_SECONDS", "90")))
    # How off-orientation clips fit the reel canvas: "blur" (whole clip over a blurred fill — no bars,
    # nothing cropped), "crop" (fill by centre-cropping), or "pad" (letterbox with black bars).
    highlight_fit_mode: str = field(default_factory=lambda: os.getenv("HIGHLIGHT_FIT_MODE", "blur"))
    # Reel aspect: "landscape" (1920x1080 — fills a landscape screen; portrait clips get the side fill),
    # "portrait" (1080x1920), or "auto" (match each event's majority orientation).
    highlight_canvas: str = field(default_factory=lambda: os.getenv("HIGHLIGHT_CANVAS", "landscape"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "catalog.db"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbnails"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def uploads_enabled(self) -> bool:
        return bool(self.photos_client_id and self.photos_client_secret)

    @property
    def quarantine_path(self) -> Path:
        """Resolved quarantine location — sibling of the Takeout folder unless overridden."""
        if self.quarantine_dir:
            return self.quarantine_dir
        return self.takeout_dir.parent / f"{self.takeout_dir.name}_quarantine"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.thumbs_dir, self.derived_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
