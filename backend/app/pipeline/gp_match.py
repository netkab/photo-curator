"""Link live Google Photos items (``gp_items``) to local catalog rows (``media``).

The catalog comes from a Takeout export and stores no Google identifier; the extension enumerates
the live library and stores no local hash. This is the join.

Passes run strongest-first and are greedy: once a ``media`` row is claimed by a pass, no weaker pass
may take it. Anything with more than one candidate at its strongest pass is recorded as
``ambiguous`` and left unlinked for the user to resolve — a wrong link here would let a delete
operation trash the wrong photo, so guessing is never acceptable.

Passes
    1. ``name+ts``      filename + capture time to the second      1.0
    2. ``name+dims``    filename + pixel dimensions                0.9   (survives timezone shifts)
    3. ``name-unique``  filename, unique on both sides             0.8
    4. ``ts+dims``      capture time + dimensions                  0.7   (survives Google truncating
                                                                          long filenames in Takeout)

``bytes`` is only ever a tiebreaker. Items Google stored in "storage saver" quality
(``is_original_quality=False``) were recompressed server-side and will never match the Takeout byte
count, so requiring it would silently drop exactly the items most worth de-duplicating.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import GpItem, Media

Progress = Callable[[float, str], None]


def _noop(_p: float, _m: str) -> None:
    pass


def _norm_name(name: str | None) -> str | None:
    """Filenames round-trip through Takeout with case and path noise. Compare on the bare stem."""
    if not name:
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    return base or None


def _sec(dt: datetime | None) -> datetime | None:
    """Truncate to whole seconds — GP reports ms, EXIF does not."""
    return dt.replace(microsecond=0) if dt else None


def _dims(w: int | None, h: int | None) -> tuple[int, int] | None:
    return (w, h) if w and h else None


def _bytes_close(a: int | None, b: int | None) -> bool:
    """Within 1%. Used only to break ties, never to reject a match."""
    if not a or not b:
        return False
    return abs(a - b) <= max(a, b) * 0.01


def _pick(candidates: list[Media], gp: GpItem) -> Media | None:
    """Break a multi-candidate tie on byte size. Returns None if still ambiguous."""
    if len(candidates) == 1:
        return candidates[0]
    by_bytes = [m for m in candidates if _bytes_close(m.bytes, gp.bytes)]
    return by_bytes[0] if len(by_bytes) == 1 else None


def link(progress: Progress | None = None, relink: bool = False) -> dict:
    """Match unlinked ``gp_items`` against ``media``.

    Args:
        relink: also reconsider rows that previously failed to match (``ambiguous``/``none``).
            Manual links (``match_method='manual'``) are never touched.
    """
    on = progress or _noop
    stats: dict[str, int] = defaultdict(int)

    with session_scope() as db:
        gp_rows = _unlinked(db, relink)
        if not gp_rows:
            return {"considered": 0, "linked": 0, "ambiguous": 0, "unmatched": 0, "by_method": {}}

        on(0.05, f"Indexing {db.query(Media).count()} catalog rows…")
        idx = _CatalogIndex(db)

        total = len(gp_rows)
        for i, gp in enumerate(gp_rows):
            if i % 200 == 0:
                on(0.05 + 0.9 * i / total, f"Matching {i}/{total}…")

            media, method, score = idx.best(gp)
            if media is not None:
                gp.media_id = media.id
                gp.match_method, gp.match_score = method, score
                idx.claim(media.id)
                stats["linked"] += 1
                stats[f"m:{method}"] += 1
            else:
                gp.media_id = None
                gp.match_method, gp.match_score = method, None  # "ambiguous" | "none"
                stats[method] += 1

        on(0.98, "Committing…")

    by_method = {k[2:]: v for k, v in stats.items() if k.startswith("m:")}
    return {
        "considered": total,
        "linked": stats["linked"],
        "ambiguous": stats["ambiguous"],
        "unmatched": stats["none"],
        "by_method": by_method,
    }


def _unlinked(db: Session, relink: bool) -> list[GpItem]:
    q = db.query(GpItem).filter(GpItem.media_id.is_(None))
    if not relink:
        q = q.filter(GpItem.match_method.is_(None))
    else:
        q = q.filter(GpItem.match_method.notin_(("manual",)) | GpItem.match_method.is_(None))
    return q.all()


class _CatalogIndex:
    """In-memory lookup over ``media``, keyed for each matching pass.

    The catalog is ~31.5k rows, so holding it in RAM beats 4 indexed queries per GP item.
    """

    def __init__(self, db: Session) -> None:
        self.by_name_ts: dict[tuple[str, datetime], list[Media]] = defaultdict(list)
        self.by_name_dims: dict[tuple[str, tuple[int, int]], list[Media]] = defaultdict(list)
        self.by_name: dict[str, list[Media]] = defaultdict(list)
        self.by_ts_dims: dict[tuple[datetime, tuple[int, int]], list[Media]] = defaultdict(list)
        # media_ids already taken by a stronger pass (or a pre-existing link).
        self.claimed: set[int] = set(
            r[0] for r in db.query(GpItem.media_id).filter(GpItem.media_id.isnot(None)).all() if r[0]
        )

        for m in db.query(Media).all():
            name, ts, dims = _norm_name(m.rel_name), _sec(m.taken_at), _dims(m.width, m.height)
            if name and ts:
                self.by_name_ts[(name, ts)].append(m)
            if name and dims:
                self.by_name_dims[(name, dims)].append(m)
            if name:
                self.by_name[name].append(m)
            if ts and dims:
                self.by_ts_dims[(ts, dims)].append(m)

    def claim(self, media_id: int) -> None:
        self.claimed.add(media_id)

    def _free(self, rows: Iterable[Media], gp: GpItem) -> list[Media]:
        """Drop already-claimed rows and anything of the wrong media type.

        Google only reports ``duration`` for videos, so its presence is the type discriminator.
        """
        want = "video" if (gp.duration or 0) > 0 else "photo"
        return [m for m in rows if m.id not in self.claimed and m.media_type == want]

    def best(self, gp: GpItem) -> tuple[Media | None, str, float | None]:
        """Return (media, method, score). ``media`` is None for 'ambiguous' and 'none'."""
        name, ts, dims = _norm_name(gp.file_name), _sec(gp.taken_at), _dims(gp.width, gp.height)

        passes: list[tuple[str, float, list[Media]]] = []
        if name and ts:
            passes.append(("name+ts", 1.0, self._free(self.by_name_ts.get((name, ts), []), gp)))
        if name and dims:
            passes.append(("name+dims", 0.9, self._free(self.by_name_dims.get((name, dims), []), gp)))
        if name:
            cands = self._free(self.by_name.get(name, []), gp)
            # "unique on both sides" — one catalog row with this name, and this is the only GP item
            # carrying it (enforced by the greedy claim: a second GP item finds it already taken).
            passes.append(("name-unique", 0.8, cands))
        if ts and dims:
            passes.append(("ts+dims", 0.7, self._free(self.by_ts_dims.get((ts, dims), []), gp)))

        saw_candidates = False
        for method, score, cands in passes:
            if not cands:
                continue
            saw_candidates = True
            hit = _pick(cands, gp)
            if hit is not None:
                return hit, method, score
            # More than one plausible target at this strength. Falling through to a *weaker* pass
            # would be worse, not better — stop and flag it.
            return None, "ambiguous", None

        return None, ("ambiguous" if saw_candidates else "none"), None
