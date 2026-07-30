"""Pick the best photo in a duplicate group, and say why.

The old score was ``megapixels + sharpness/100 + 0.5 * face_count``, which has two problems. The
terms aren't comparable — a 12 MP photo scores 12 on resolution but a typically-sharp one scores 2.6
on sharpness, so resolution silently decided almost every group — and a raw sum can't explain itself.

This module scores **relatively, within the group**. When choosing between near-identical shots the
question is never "is this sharp?" but "is this the sharpest of these five?", so each signal is
normalised across the group's own members before weighting. That also makes the result stable: a
library of dark indoor photos and a library of bright outdoor ones both get sensible keepers.

Every signal degrades to neutral when its data is missing, so the scorer works on a catalog where
only blur has been computed, and gets sharper as more analysis lands — no re-detection needed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Weights when the group has usable face data (someone is the subject).
_W_FACES = {
    "face_quality": 0.30,   # detector confidence — clean, frontal, unobstructed
    "sharpness": 0.25,
    "face_size": 0.15,      # subject fills more of the frame
    "centering": 0.15,      # subject nearer the middle
    "resolution": 0.10,
    "detail": 0.05,         # bytes per pixel — less compression at equal size
}
# Weights when nobody is detected: fall back to plain image quality.
_W_PLAIN = {
    "sharpness": 0.55,
    "resolution": 0.30,
    "detail": 0.15,
}


@dataclass
class Signals:
    media_id: int
    raw: dict[str, float | None] = field(default_factory=dict)
    norm: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    reason: str = ""


def _mp(m) -> float | None:
    return ((m.width or 0) * (m.height or 0)) / 1e6 or None


def _detail(m) -> float | None:
    """Bytes per megapixel — at equal dimensions, the bigger file kept more detail."""
    mp = _mp(m)
    if not mp or not m.bytes:
        return None
    return m.bytes / mp


def _face_stats(faces: list) -> tuple[float | None, float | None, float | None]:
    """(best detector confidence, largest face area fraction, centering 0..1) from stored faces.

    ``det_score`` is InsightFace's detection confidence. It is *not* a gaze estimate, but it drops
    for profiles, motion blur and partial occlusion, so it is a usable stand-in for "is the face
    cleanly presented to the camera". True eyes-open/gaze needs landmarks we do not persist.
    """
    if not faces:
        return None, None, None

    best = max((f.det_score or 0.0) for f in faces)

    largest_area = 0.0
    centering = None
    for f in faces:
        try:
            x, y, w, h = (float(v) for v in (f.bbox or "").split(","))
        except (ValueError, AttributeError):
            continue
        area = w * h
        if area > largest_area:
            largest_area = area
            # bbox is in pixels; normalise against the frame via the face's own media row later.
            centering = (x + w / 2.0, y + h / 2.0, w, h)
    return best, (largest_area or None), centering


def _centering_score(centering, m) -> float | None:
    """1.0 when the subject sits dead centre, falling to 0 at the corner."""
    if not centering or not m.width or not m.height:
        return None
    cx, cy, _, _ = centering
    dx = (cx / m.width) - 0.5
    dy = (cy / m.height) - 0.5
    dist = math.hypot(dx, dy) / math.hypot(0.5, 0.5)   # 0 centre .. 1 corner
    return max(0.0, 1.0 - dist)


def _normalise(values: dict[int, float | None]) -> dict[int, float]:
    """Scale a signal to 0..1 across the group. Missing or uniform values become neutral 0.5."""
    present = {k: v for k, v in values.items() if v is not None}
    if not present:
        return {k: 0.5 for k in values}
    lo, hi = min(present.values()), max(present.values())
    if hi - lo < 1e-9:
        return {k: 0.5 for k in values}
    return {k: ((present[k] - lo) / (hi - lo)) if k in present else 0.5 for k in values}


def score_group(members: list, faces_by_media: dict[int, list],
                gp_by_media: dict[int, object] | None = None) -> dict[int, Signals]:
    """Score every member of one duplicate group relative to the others."""
    gp_by_media = gp_by_media or {}
    out = {m.id: Signals(media_id=m.id) for m in members}

    raw: dict[str, dict[int, float | None]] = {
        "sharpness": {}, "resolution": {}, "detail": {},
        "face_quality": {}, "face_size": {}, "centering": {},
    }

    any_faces = False
    for m in members:
        faces = faces_by_media.get(m.id) or []
        det, area, centering = _face_stats(faces)
        if det is not None:
            any_faces = True

        # Sharpness is heavy-tailed (0 .. ~10,000), so compare on a log scale — the difference
        # between 50 and 200 matters far more than between 5,000 and 5,150.
        blur = m.blur_score
        raw["sharpness"][m.id] = math.log1p(blur) if blur else None
        raw["resolution"][m.id] = _mp(m)
        raw["detail"][m.id] = _detail(m)
        raw["face_quality"][m.id] = det
        raw["face_size"][m.id] = (area / ((m.width or 1) * (m.height or 1))) if area else None
        raw["centering"][m.id] = _centering_score(centering, m)

        out[m.id].raw = {k: raw[k][m.id] for k in raw}

    weights = _W_FACES if any_faces else _W_PLAIN
    norms = {k: _normalise(raw[k]) for k in weights}

    for m in members:
        s = out[m.id]
        s.norm = {k: norms[k][m.id] for k in weights}
        s.score = sum(weights[k] * s.norm[k] for k in weights)

        # Google's own "original quality" flag is decisive when present: a storage-saver copy is a
        # recompressed version of the same shot, never the one to keep.
        gp = gp_by_media.get(m.id)
        if gp is not None and getattr(gp, "is_original_quality", None) is True:
            s.score += 0.15
            s.raw["original_quality"] = True

    _explain(out, weights, any_faces)
    return out


_LABEL = {
    "sharpness": "sharpest",
    "resolution": "most pixels",
    "detail": "least compressed",
    "face_quality": "clearest face",
    "face_size": "subject largest",
    "centering": "best centred",
}


def _explain(out: dict[int, Signals], weights: dict[str, float], any_faces: bool) -> None:
    """Give the winner a short human reason, so a user can accept or override at a glance."""
    if not out:
        return
    winner = max(out.values(), key=lambda s: s.score)
    wins = [k for k in weights if s_leads(out, k, winner.media_id)]
    wins.sort(key=lambda k: -weights[k])

    parts = [_LABEL[k] for k in wins[:2] if k in _LABEL]
    if winner.raw.get("original_quality"):
        parts.insert(0, "original quality")
    if not parts:
        parts = ["best overall"]
    winner.reason = " · ".join(parts)

    for s in out.values():
        if s.media_id != winner.media_id and not s.reason:
            s.reason = ""
    if not any_faces and winner.reason == "best overall":
        winner.reason = "sharpest"


def s_leads(out: dict[int, Signals], key: str, media_id: int) -> bool:
    """True when this member is the strict best on that signal."""
    vals = {mid: s.norm.get(key, 0.5) for mid, s in out.items()}
    best = max(vals.values())
    return vals[media_id] >= best - 1e-9 and sum(1 for v in vals.values() if v >= best - 1e-9) == 1
