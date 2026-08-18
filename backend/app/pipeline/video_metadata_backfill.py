"""Backfill capture date + GPS for videos whose Takeout JSON sidecar never carried them, reading the
video file's own embedded container metadata instead (ffprobe ``creation_time`` / ISO 6709 location).

Common cause: iPhone Live Photo ``.MP4`` companions, which Takeout frequently ships without a matched
sidecar even though the file itself has the real camera-original date and GPS baked in — these land
undated and unplaced at ingest, which excludes them from highlight clustering entirely (an event needs
a day/place to group by). This never overwrites a value already set from Takeout or a manual edit; it
only fills in videos that currently have neither a date nor a place.

Any GPS recovered this way is reverse-geocoded to a place name via the same Places API path used for
photos (``pipeline.geo.reverse_geocode``), so these videos get proper place names too, not just raw
coordinates.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..db import session_scope
from ..models import Media
from . import geo
from . import metadata as md


def run(progress: Callable[[float, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None) -> dict:
    checked = dated = geotagged = 0
    cancelled = False
    with session_scope() as s:
        # Never probed at all — the normal case.
        fresh = s.query(Media).filter(
            Media.media_type == "video",
            Media.taken_at.is_(None), Media.place_name.is_(None), Media.gps_lat.is_(None),
        ).all()
        total = len(fresh) or 1
        for i, m in enumerate(fresh):
            if should_cancel and should_cancel():
                cancelled = True
                break
            if progress:
                progress(i / total * 0.8, f"Reading embedded metadata {i + 1}/{total}")
            taken_at, lat, lng = md.read_embedded_datetime_gps(Path(m.abs_path))
            checked += 1
            if taken_at:
                m.taken_at = taken_at
                dated += 1
            if lat is not None and lng is not None:
                m.gps_lat, m.gps_lng = lat, lng
                geotagged += 1
            if i % 100 == 0:
                s.commit()  # incremental, resume-friendly — a stop/crash loses at most one batch

        # Has GPS but no place yet — either just backfilled above, or left over from a previous run
        # that got interrupted mid-geocode (e.g. cancelled, or the process was stopped). Re-running
        # this job picks those up too instead of leaving them stuck.
        target_ids = [m.id for m in s.query(Media).filter(
            Media.media_type == "video", Media.gps_lat.isnot(None), Media.place_name.is_(None),
        ).all()]

    geocoded = {"placed": 0}
    if target_ids and not cancelled:
        if progress:
            progress(0.8, f"Reverse-geocoding {len(target_ids)} point(s)...")
        # Scoped to exactly these rows — reverse_geocode()'s default sweeps every media item still
        # missing a place name, which is a much larger, unrelated backlog (photos included) and would
        # burn through the Places API's monthly quota for work nobody asked for.
        geocoded = geo.reverse_geocode(media_ids=target_ids, should_cancel=should_cancel,
                                       progress=lambda p, m: progress(0.8 + p * 0.2, m) if progress else None)
        cancelled = cancelled or bool(geocoded.get("cancelled"))

    if progress:
        progress(1.0, "Backfill cancelled" if cancelled else "Backfill complete")
    return {"checked": checked, "dated": dated, "geotagged": geotagged,
            "reverse_geocoded": geocoded.get("placed", 0), "cancelled": cancelled}
