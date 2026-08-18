"""Reverse-geocode GPS coordinates to place names, and cluster geo-tagged photos by place.

Clustering is **persistent and incremental**: photos are grouped by spatial proximity (within
``settings.place_cluster_radius_m``) into PlaceClusters that survive across runs. A re-run only folds
*new* photos into existing clusters (or starts new ones) — it never wipes or reassigns members, so the
user's manual matches/splits and the ignore-list are preserved. Naming uses the Google Places service
when a key is configured; otherwise the user matches places manually in Maps Studio.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from typing import Callable

from sqlalchemy.orm import Session

from ..config import settings
from ..db import session_scope
from ..models import IgnoredPlace, Media, PlaceCluster
from ..services import places
from . import faces as faces_pipe

_EARTH_R = 6_371_000.0  # metres


def _meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Equirectangular distance approximation — accurate enough under ~1 km."""
    mlat = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lng2 - lng1) * math.cos(mlat) * _EARTH_R
    dy = math.radians(lat2 - lat1) * _EARTH_R
    return math.hypot(dx, dy)


def _cell(lat: float, lng: float) -> tuple[float, float]:
    """Coarse grid cell (~110 m) used to index cluster centroids for fast nearest lookup."""
    return (round(lat, 3), round(lng, 3))


def _neighbor_cells(lat: float, lng: float) -> list[tuple[float, float]]:
    cx, cy = round(lat, 3), round(lng, 3)
    return [(round(cx + dx * 0.001, 3), round(cy + dy * 0.001, 3))
            for dx in (-1, 0, 1) for dy in (-1, 0, 1)]


def load_ignored(s: Session) -> list[IgnoredPlace]:
    return s.query(IgnoredPlace).all()


def is_ignored(lat: float, lng: float, place_id: str | None, ignored: list[IgnoredPlace]) -> bool:
    """True if (lat, lng)/place_id falls inside any user-ignored place."""
    for ip in ignored:
        if place_id and ip.place_id and place_id == ip.place_id:
            return True
        if ip.lat is not None and ip.lng is not None and _meters(lat, lng, ip.lat, ip.lng) <= (ip.radius_m or 120):
            return True
    return False


def reset_clusters() -> dict:
    """Delete every PlaceCluster (including manual ones) so the next run rebuilds from scratch.
    The ignore-list is left intact. Backs the guarded 'Rebuild from scratch' button."""
    with session_scope() as s:
        n = s.query(PlaceCluster).delete()
    return {"deleted": n}


def reverse_geocode(limit: int | None = None, force: bool = False, media_ids: list[int] | None = None,
                    should_cancel: Callable[[], bool] | None = None,
                    progress: Callable[[float, str], None] | None = None) -> dict:
    """Reverse-geocode GPS to place names. ``limit`` caps how many photos are looked up — pass a small
    value (e.g. 5–10) for a quick Places-API sample before committing to the whole library. ``media_ids``
    scopes the run to specific rows instead of the whole library — each call costs a real Places API
    request against a quota, so a caller that just touched a known, bounded set of rows (e.g. a
    metadata backfill) should pass those ids rather than sweeping everything with ``place_name`` still
    unset, which could be a much larger, unrelated backlog. Stops cooperatively when ``should_cancel``
    returns True."""
    tagged = 0
    with session_scope() as s:
        q = s.query(Media).filter(Media.gps_lat.isnot(None), Media.gps_lng.isnot(None))
        if not force:
            q = q.filter(Media.place_name.is_(None))
        if media_ids is not None:
            q = q.filter(Media.id.in_(media_ids))
        if limit:
            q = q.limit(limit)
        rows = q.all()
        total = len(rows) or 1
        for i, m in enumerate(rows):
            if should_cancel and should_cancel():
                if progress:
                    progress(i / total, f"Stopped after {tagged} place(s)")
                return {"placed": tagged, "cancelled": True}
            if progress:
                progress(i / total, f"Geocoding {m.rel_name}")
            place = places.nearest_place(m.gps_lat, m.gps_lng)
            if place:
                m.place_id = place.get("place_id")
                m.place_name = place.get("name")
                tagged += 1
            if i % 50 == 0:
                s.commit()  # incremental, resume-friendly — an abrupt kill loses at most one batch,
                            # not every Places API call made so far in this run
    if progress:
        progress(1.0, "Reverse-geocoding complete")
    return {"placed": tagged}


def cluster_places(non_portrait_only: bool = True, limit: int | None = None,
                   should_cancel: Callable[[], bool] | None = None,
                   progress: Callable[[float, str], None] | None = None) -> dict:
    """Fold geo-tagged photos into persistent PlaceClusters by spatial proximity.

    Incremental: only photos not already in a cluster are considered. Each is added to the nearest
    existing cluster within ``settings.place_cluster_radius_m`` (centroid updated as a running mean) or
    seeds a new cluster. Existing clusters and their members are never destroyed or reassigned, so
    manual matches/splits persist. Photos inside an ignored place are skipped. ``limit`` caps how many
    *new* clusters this run may create (the "Test: N" sample); ``should_cancel`` stops cooperatively.
    """
    radius = settings.place_cluster_radius_m
    with session_scope() as s:
        ignored = load_ignored(s)

        # Load existing clusters into working state + a grid index of their centroids.
        states: list[dict] = []
        grid: dict[tuple[float, float], list[int]] = defaultdict(list)
        assigned: set[int] = set()
        for pc in s.query(PlaceCluster).all():
            ids = json.loads(pc.media_ids) if pc.media_ids else []
            assigned.update(ids)
            idx = len(states)
            st = {"pc": pc, "ids": ids, "lat": pc.lat, "lng": pc.lng, "cell": None}
            states.append(st)
            if pc.lat is not None and pc.lng is not None:
                cell = _cell(pc.lat, pc.lng)
                grid[cell].append(idx)
                st["cell"] = cell

        q = s.query(Media).filter(Media.gps_lat.isnot(None), Media.gps_lng.isnot(None))
        if non_portrait_only:
            q = q.filter((Media.is_portrait.is_(False)) | (Media.is_portrait.is_(None)))
            q = q.filter((Media.face_area_ratio.is_(None))
                        | (Media.face_area_ratio < settings.person_focus_area_ratio))
        rows = [m for m in q.all() if m.id not in assigned]
        # Process the busiest locations first so a capped ("Test: N") run creates the biggest, most
        # review-worthy NEW clusters rather than whatever random spots come first in id order.
        cell_size = Counter(_cell(m.gps_lat, m.gps_lng) for m in rows)
        rows.sort(key=lambda m: cell_size[_cell(m.gps_lat, m.gps_lng)], reverse=True)
        total = len(rows) or 1
        new_made = 0

        for i, m in enumerate(rows):
            if should_cancel and should_cancel():
                break
            if progress:
                progress(i / total, f"Clustering ({new_made} new place(s))")
            if is_ignored(m.gps_lat, m.gps_lng, m.place_id, ignored):
                continue

            # Nearest existing cluster within radius (check the photo's grid cell + 8 neighbours).
            best_idx, best_d = None, radius
            for cell in _neighbor_cells(m.gps_lat, m.gps_lng):
                for idx in grid.get(cell, ()):  # may visit an idx twice — harmless
                    st = states[idx]
                    if st["lat"] is None:
                        continue
                    d = _meters(m.gps_lat, m.gps_lng, st["lat"], st["lng"])
                    if d <= best_d:
                        best_d, best_idx = d, idx

            if best_idx is not None:
                st = states[best_idx]
                n = len(st["ids"])
                st["lat"] = (st["lat"] * n + m.gps_lat) / (n + 1)
                st["lng"] = (st["lng"] * n + m.gps_lng) / (n + 1)
                st["ids"].append(m.id)
                # Re-index if the centroid drifted into a new grid cell.
                nc = _cell(st["lat"], st["lng"])
                if nc != st["cell"]:
                    grid[nc].append(best_idx)
                    st["cell"] = nc
                # Adopt a reverse-geocoded name if the cluster has none yet (never overwrite a match).
                if not st["pc"].name and m.place_name:
                    st["pc"].name, st["pc"].place_id = m.place_name, m.place_id
            else:
                if limit is not None and new_made >= limit:
                    continue  # sample cap: don't create more new clusters this run
                pc = PlaceCluster(place_id=m.place_id, name=m.place_name,
                                  lat=m.gps_lat, lng=m.gps_lng, media_ids=json.dumps([m.id]))
                s.add(pc)
                s.flush()
                idx = len(states)
                cell = _cell(m.gps_lat, m.gps_lng)
                states.append({"pc": pc, "ids": [m.id], "lat": m.gps_lat, "lng": m.gps_lng, "cell": cell})
                grid[cell].append(idx)
                new_made += 1
            assigned.add(m.id)

        # Persist updated membership + centroids.
        for st in states:
            st["pc"].media_ids = json.dumps(st["ids"])
            st["pc"].lat, st["pc"].lng = st["lat"], st["lng"]

    if progress:
        progress(1.0, "Place clustering complete")
    return {"place_clusters": len(states), "new_clusters": new_made}


def prune_person_in_focus(progress: Callable[[float, str], None] | None = None) -> dict:
    """One-time cleanup for clusters built before the face_area_ratio signal existed: drop any member
    photo that now fails ``faces.is_person_in_focus`` from clusters the user hasn't already reviewed.

    Clusters marked ``reviewed`` are skipped entirely — those are done, don't reopen them. Ignored
    places need no handling: they're deleted from PlaceCluster the moment they're ignored, so they're
    never visited here. A cluster that drops below 2 members just stops appearing in the list (the
    /clusters endpoint already hides len(ids) < 2) — nothing is deleted.
    """
    with session_scope() as s:
        clusters = s.query(PlaceCluster).filter(PlaceCluster.reviewed.is_(False)).all()
        total = len(clusters) or 1
        pruned_clusters = 0
        pruned_photos = 0
        for i, pc in enumerate(clusters):
            if progress:
                progress(i / total, f"Pruning cluster {pc.id}")
            ids = json.loads(pc.media_ids) if pc.media_ids else []
            if not ids:
                continue
            media = {m.id: m for m in s.query(Media).filter(Media.id.in_(ids))}
            # A missing row (deleted media) is left alone here — nothing to evaluate, not our call to drop.
            keep = [mid for mid in ids if mid not in media or not faces_pipe.is_person_in_focus(media[mid])]
            if len(keep) != len(ids):
                pc.media_ids = json.dumps(keep)
                pruned_clusters += 1
                pruned_photos += len(ids) - len(keep)
    if progress:
        progress(1.0, "Pruning complete")
    return {"clusters_changed": pruned_clusters, "photos_removed": pruned_photos}
