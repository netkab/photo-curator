"""Command-line entrypoint mirroring the skills. Run as ``python -m app.cli <command>``.

Each command calls a pipeline directly (no HTTP) with a simple stderr progress printer.
All heavy commands are resume-safe — they skip already-processed items. You can Ctrl+C at
any time and re-run to continue where you left off.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .config import settings
from .db import init_db
from ._path_fix import ensure_tool_paths


def _progress(p: float, msg: str) -> None:
    sys.stderr.write(f"\r[{int(p * 100):3d}%] {msg[:70]:<70}")
    sys.stderr.flush()
    if p >= 1.0:
        sys.stderr.write("\n")


def _status() -> dict:
    """Return counts of total, processed, and remaining items per pipeline."""
    from sqlalchemy import func
    from .db import get_session
    from .models import Caption, DerivedMedia, Face, Media, OcrText

    s = get_session()
    try:
        total_photos = s.query(func.count(Media.id)).filter(Media.media_type == "photo").scalar() or 0
        total_videos = s.query(func.count(Media.id)).filter(Media.media_type == "video").scalar() or 0
        total = total_photos + total_videos

        analyzed   = s.query(func.count(Media.id)).filter(Media.analyzed_at.isnot(None)).scalar() or 0
        blur_done  = s.query(func.count(Media.id)).filter(Media.blur_score.isnot(None), Media.media_type == "photo").scalar() or 0
        captioned  = s.query(func.count(func.distinct(Caption.media_id))).scalar() or 0
        ocr_done   = s.query(func.count(func.distinct(OcrText.media_id))).scalar() or 0
        faces_done = s.query(func.count(Media.id)).filter(Media.is_portrait.isnot(None)).scalar() or 0
        geo_done   = s.query(func.count(Media.id)).filter(Media.place_name.isnot(None)).scalar() or 0
        with_gps   = s.query(func.count(Media.id)).filter(Media.gps_lat.isnot(None)).scalar() or 0

        compressed = s.query(func.count(DerivedMedia.id)).filter(DerivedMedia.kind == "compressed").scalar() or 0
        enhanced   = s.query(func.count(DerivedMedia.id)).filter(DerivedMedia.kind == "enhanced").scalar() or 0

        return {
            "catalog": {"photos": total_photos, "videos": total_videos, "total": total},
            "analyze": {
                "blur":     {"done": blur_done,  "remaining": total_photos - blur_done},
                "captions": {"done": captioned,  "remaining": total_photos - captioned},
                "ocr":      {"done": ocr_done,   "remaining": total_photos - ocr_done},
                "faces":    {"done": faces_done, "remaining": total_photos - faces_done},
                "geo":      {"done": geo_done,   "remaining": with_gps - geo_done, "with_gps": with_gps},
                "fully_analyzed": analyzed,
                "remaining": total_photos - analyzed,
            },
            "derived": {"compressed": compressed, "enhanced": enhanced},
        }
    finally:
        s.close()


def _print_status(st: dict) -> None:
    cat = st["catalog"]
    an  = st["analyze"]
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"  Catalog : {cat['photos']:,} photos, {cat['videos']:,} videos ({cat['total']:,} total)", file=sys.stderr)
    print(f"  Analyzed: {an['fully_analyzed']:,} / {cat['photos']:,} photos  ({an['remaining']:,} remaining)", file=sys.stderr)
    print(f"    blur     {an['blur']['done']:,} done, {an['blur']['remaining']:,} left", file=sys.stderr)
    print(f"    captions {an['captions']['done']:,} done, {an['captions']['remaining']:,} left", file=sys.stderr)
    print(f"    ocr      {an['ocr']['done']:,} done, {an['ocr']['remaining']:,} left", file=sys.stderr)
    print(f"    faces    {an['faces']['done']:,} done, {an['faces']['remaining']:,} left", file=sys.stderr)
    print(f"    geo      {an['geo']['done']:,} done, {an['geo']['remaining']:,} left (of {an['geo']['with_gps']:,} with GPS)", file=sys.stderr)
    print(f"  Derived : {st['derived']['compressed']:,} compressed, {st['derived']['enhanced']:,} enhanced", file=sys.stderr)
    print(f"{'='*50}\n", file=sys.stderr)


def _gp_status() -> dict:
    """Coverage of the Google Photos link table. Mirrors GET /api/gp/status."""
    from sqlalchemy import func
    from .db import get_session
    from .models import GpItem, Media

    s = get_session()
    try:
        gp_total = s.query(func.count(GpItem.id)).filter(GpItem.trashed.is_(False)).scalar() or 0
        linked = s.query(func.count(GpItem.id)).filter(GpItem.media_id.isnot(None)).scalar() or 0
        catalog_total = s.query(func.count(Media.id)).scalar() or 0
        by_method = dict(s.query(GpItem.match_method, func.count(GpItem.id))
                         .group_by(GpItem.match_method).all())
        last_sync = s.query(func.max(GpItem.synced_at)).scalar()
        return {
            "gp_total": gp_total, "linked": linked, "gp_only": gp_total - linked,
            "catalog_total": catalog_total, "catalog_only": max(catalog_total - linked, 0),
            "coverage": round(linked / gp_total, 4) if gp_total else 0.0,
            "by_method": {k or "pending": v for k, v in by_method.items()},
            "last_sync": last_sync.isoformat() if last_sync else None,
        }
    finally:
        s.close()


def _print_gp_status(st: dict) -> None:
    print(f"\n{'='*50}", file=sys.stderr)
    if not st["gp_total"]:
        print("  No Google Photos items synced yet.", file=sys.stderr)
        print("  Open the Chrome extension and run a library sync first.", file=sys.stderr)
        print(f"{'='*50}\n", file=sys.stderr)
        return
    print(f"  Google Photos : {st['gp_total']:,} live items (last sync {st['last_sync']})", file=sys.stderr)
    print(f"  Linked        : {st['linked']:,}  ({st['coverage'] * 100:.1f}% coverage)", file=sys.stderr)
    print(f"  GP-only       : {st['gp_only']:,}  (uploaded after the Takeout export)", file=sys.stderr)
    print(f"  Catalog-only  : {st['catalog_only']:,}  (of {st['catalog_total']:,} local rows)", file=sys.stderr)
    for method, n in sorted(st["by_method"].items(), key=lambda kv: -kv[1]):
        print(f"    {method:<14} {n:,}", file=sys.stderr)
    print(f"{'='*50}\n", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ensure_tool_paths()
    init_db()
    parser = argparse.ArgumentParser(prog="photo-curator", description="Local photo & video curation")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("env",    help="Report GPU / tool availability")
    sub.add_parser("status", help="Show catalog counts and pipeline progress")

    p_ing = sub.add_parser("ingest", help="Ingest a Google Takeout export")
    p_ing.add_argument("--takeout", default=str(settings.takeout_dir))

    p_an = sub.add_parser("analyze", help="Run local AI analysis")
    p_an.add_argument("--only", choices=["blur", "captions", "ocr", "faces", "geo"], default=None)
    p_an.add_argument("--limit", type=int, default=200, help="Items per batch (default: 200)")
    p_an.add_argument("--batch", action="store_true", help="Keep running batches until all done (Ctrl+C safe)")
    p_an.add_argument("--cooldown", type=int, default=30, help="Seconds to wait between batches for GPU cooling (default: 30)")
    p_an.add_argument("--force", action="store_true")

    p_dd = sub.add_parser("dedup", help="Detect duplicates / similar")
    p_dd.add_argument("--no-clip", action="store_true")
    p_dd.add_argument("--cleanup", action="store_true",
                      help="Don't re-detect; repair existing groups (drop layer-duplicate groups, "
                           "report oversized ones). Previews unless --apply is given.")
    p_dd.add_argument("--apply", action="store_true", help="With --cleanup: actually remove them")
    p_dd.add_argument("--rescore", action="store_true",
                      help="Don't re-detect; re-pick the keeper in every unreviewed group using "
                           "the current quality signals (fast, reads no images)")

    p_en = sub.add_parser("enhance", help="Enhance blurry / selected photos")
    p_en.add_argument("--blurry", action="store_true")
    p_en.add_argument("--ids", default=None, help="comma-separated media ids")

    p_maps = sub.add_parser("maps-cluster", help="Reverse-geocode + cluster places")
    p_maps.add_argument("--sample", type=int, default=None,
                        help="Reverse-geocode only N photos (quick Places-API test) before a full run")

    sub.add_parser("backfill-face-metrics",
                    help="One-time, GPU-free backfill of face_area_ratio for already-analyzed photos "
                         "from their stored Face rows")

    # Standalone (not folded into maps-cluster): that command unconditionally reverse-geocodes
    # un-tagged photos with no cap when --sample is omitted, which would burn Places API quota just
    # to run a prune. This touches media_ids only — no geocoding, no Places API calls.
    sub.add_parser("prune-person-focus",
                    help="One-time cleanup: drop person/group-in-focus photos from existing "
                         "(non-reviewed) place clusters using the current face_area_ratio signal")

    p_vid = sub.add_parser("video", help="Compress and/or build highlights")
    p_vid.add_argument("--compress", action="store_true")
    p_vid.add_argument("--highlights", action="store_true")

    sub.add_parser("gp-status", help="Google Photos link coverage (run a sync from the extension first)")

    p_gpl = sub.add_parser("gp-link", help="Match synced Google Photos items to the local catalog")
    p_gpl.add_argument("--relink", action="store_true",
                       help="Also retry rows that previously came out ambiguous/unmatched")

    p_rc = sub.add_parser("reclaim",
                          help="Move redundant local duplicates to quarantine (never deletes). "
                               "Previews unless --apply is given.")
    p_rc.add_argument("--apply", action="store_true", help="Actually move the files")
    p_rc.add_argument("--include-videos", action="store_true",
                      help="Also move video duplicates (skipped by default — hardest to re-acquire)")
    p_rc.add_argument("--limit", type=int, default=None, help="Cap how many files to move")
    p_rc.add_argument("--undo", default=None, metavar="RUN_ID",
                      help="Restore a previous run (see 'reclaim --list')")
    p_rc.add_argument("--list", action="store_true", help="List past reclaim runs")

    args = parser.parse_args(argv)
    result: dict

    if args.cmd == "env":
        from .services import ai_models
        result = ai_models.environment_report()

    elif args.cmd == "status":
        st = _status()
        _print_status(st)
        result = st

    elif args.cmd == "ingest":
        from .pipeline import ingest_takeout
        result = ingest_takeout.ingest(args.takeout, progress=_progress)

    elif args.cmd == "analyze":
        from .pipeline import analyze

        if args.batch:
            # Batch loop mode: run until all done, with cooldown between batches.
            # Ctrl+C stops after the current batch — already-processed items are saved.
            batch_num = 0
            total_results: dict = {}
            try:
                while True:
                    st = _status()
                    # With --only, track THAT stage. The overall figure counts `analyzed_at`, which
                    # is already set library-wide, so it reads <= 0 and the loop exited immediately
                    # — `analyze --only faces --batch` silently did nothing.
                    remaining = (st["analyze"][args.only]["remaining"] if args.only
                                 else st["analyze"]["remaining"])
                    if remaining <= 0:
                        print(f"\nAll {args.only or 'photos'} done!", file=sys.stderr)
                        break

                    batch_num += 1
                    print(f"\n--- Batch {batch_num} ({remaining:,} remaining) ---", file=sys.stderr)

                    batch_result = analyze.run(
                        only=args.only, limit=args.limit, force=args.force, progress=_progress
                    )

                    # Merge results
                    for k, v in batch_result.items():
                        if k not in total_results:
                            total_results[k] = {}
                        if isinstance(v, dict):
                            for k2, v2 in v.items():
                                total_results[k][k2] = total_results[k].get(k2, 0) + (v2 if isinstance(v2, int) else 0)
                        else:
                            total_results[k] = v

                    # Status after batch
                    _print_status(_status())

                    # Cooldown to let GPU breathe
                    if remaining > args.limit:
                        print(f"Cooling down {args.cooldown}s (Ctrl+C to stop safely)...", file=sys.stderr)
                        time.sleep(args.cooldown)

            except KeyboardInterrupt:
                print("\n\nStopped by user. Progress is saved -- re-run to continue.", file=sys.stderr)
                _print_status(_status())

            result = total_results
        else:
            result = analyze.run(only=args.only, limit=args.limit, force=args.force, progress=_progress)
            _print_status(_status())

    elif args.cmd == "dedup":
        from .pipeline import dedup

        if args.rescore:
            result = dedup.rescore(progress=_progress)
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"  Groups re-scored : {result['groups']:,}", file=sys.stderr)
            print(f"  Keeper changed   : {result['keeper_changed']:,}", file=sys.stderr)
            print(f"  Keeper unchanged : {result['keeper_unchanged']:,}", file=sys.stderr)
            if result["reasons"]:
                print("  Why the keeper won:", file=sys.stderr)
                for why, n in result["reasons"].items():
                    print(f"    {why:<28} {n:,}", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
        elif args.cleanup:
            result = dedup.cleanup(apply=args.apply)
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"  Groups examined       : {result['groups_examined']:,}", file=sys.stderr)
            print(f"  Layer-duplicate groups: {result['duplicate_groups_removed']:,}"
                  f"{'  (removed)' if args.apply else '  (would remove)'}", file=sys.stderr)
            print(f"  Remaining             : {result['remaining']:,}", file=sys.stderr)
            if result["oversized_groups"]:
                print(f"\n  Oversized (> {result['oversized_limit']} members): "
                      f"{result['oversized_groups']:,} groups, "
                      f"{result['oversized_photos']:,} photos", file=sys.stderr)
                print("  These are chaining artifacts from the old detection. They are excluded",
                      file=sys.stderr)
                print("  from bulk trashing. Re-run 'dedup' (GPU) to rebuild them properly.",
                      file=sys.stderr)
            if not args.apply:
                print("\n  Nothing changed. Re-run with --apply.", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
        else:
            result = dedup.run(use_clip=not args.no_clip, progress=_progress)

    elif args.cmd == "enhance":
        from .pipeline import enhance
        ids = [int(x) for x in args.ids.split(",")] if args.ids else None
        result = enhance.run(ids=ids, blurry=args.blurry, progress=_progress)

    elif args.cmd == "maps-cluster":
        from .pipeline import geo
        geo.reverse_geocode(limit=args.sample, progress=_progress)
        result = geo.cluster_places(limit=args.sample, progress=_progress)

    elif args.cmd == "backfill-face-metrics":
        from .pipeline import faces
        result = faces.backfill_face_area_ratio()

    elif args.cmd == "prune-person-focus":
        from .pipeline import geo
        result = geo.prune_person_in_focus(progress=_progress)

    elif args.cmd == "video":
        from .pipeline import video_compress, video_highlights
        result = {}
        if args.compress or not args.highlights:
            result["compress"] = video_compress.run(progress=_progress)
        if args.highlights:
            result["highlights"] = video_highlights.run(progress=_progress)

    elif args.cmd == "gp-status":
        result = _gp_status()
        _print_gp_status(result)

    elif args.cmd == "gp-link":
        from .pipeline import gp_match
        result = gp_match.link(progress=_progress, relink=args.relink)
        _print_gp_status(_gp_status())

    elif args.cmd == "reclaim":
        from .pipeline import reclaim

        if args.list:
            result = {"runs": reclaim.runs()}
        elif args.undo:
            result = reclaim.undo(args.undo, progress=_progress)
        elif args.apply:
            result = reclaim.run(include_videos=args.include_videos, limit=args.limit,
                                 progress=_progress)
            print(f"\nMoved {result['moved']:,} files ({result['gb']} GB) to "
                  f"{result['quarantine_dir']}", file=sys.stderr)
            print(f"Undo with:  python -m app.cli reclaim --undo {result['run_id']}\n",
                  file=sys.stderr)
        else:
            result = reclaim.plan(include_videos=args.include_videos, limit=args.limit)
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"  Would move : {result['candidates']:,} files  ({result['gb']} GB)", file=sys.stderr)
            print(f"  Destination: {result['quarantine_dir']}", file=sys.stderr)
            print("  Skipped:", file=sys.stderr)
            for reason, n in sorted(result["skipped"].items(), key=lambda kv: -kv[1]):
                if n:
                    print(f"    {reason:<22} {n:,}", file=sys.stderr)
            print("\n  Nothing moved. Re-run with --apply to do it.", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)

    else:
        parser.error("unknown command")

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
