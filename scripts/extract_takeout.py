"""
Extract Google Takeout .tgz archives on Windows.

Fixes:
- Trailing-space album names (Google quirk) stripped from all paths
- Streaming mode (r|gz) — starts immediately, no upfront full-file scan
- .done marker files — restarts skip completed archives instantly
- Pure ASCII output — no Unicode symbols that break Windows cp1252 redirection
- Sleep prevention via ctypes (no external deps)
"""
import sys
import shutil
import tarfile
from pathlib import Path
from datetime import datetime

TAKEOUT_DIR = Path(r"D:\Takeout")
DEST_DIR    = TAKEOUT_DIR
ENV_FILE    = Path(r"C:\photo-curator\backend\.env")


def prevent_sleep():
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    except Exception:
        pass


def allow_sleep():
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    except Exception:
        pass


def clean_name(name: str) -> str:
    """Strip trailing whitespace from every path component (Google Takeout quirk)."""
    parts = name.replace("\\", "/").split("/")
    return "/".join(p.rstrip() for p in parts)


def done_marker(arc: Path) -> Path:
    """Returns e.g. D:/Takeout/takeout-001.tgz.done (avoids Path.with_suffix multi-dot bug)."""
    return arc.parent / (arc.name + ".done")


def extract_archive(arc_path: Path, dest: Path) -> dict:
    """Stream-extract — starts immediately, skips existing correct-size files."""
    stats = {"extracted": 0, "skipped": 0, "errors": 0}
    print(f"  Streaming {arc_path.name} ...", flush=True)

    with tarfile.open(arc_path, "r|gz") as tf:
        for i, member in enumerate(tf, 1):
            if i % 500 == 0:
                print(f"  {i:,} entries  extracted={stats['extracted']:,}  "
                      f"skipped={stats['skipped']:,}  errors={stats['errors']}", flush=True)

            member.name = clean_name(member.name)
            if not member.name:
                continue

            dest_path = dest / member.name
            if member.isfile() and dest_path.exists():
                if dest_path.stat().st_size == member.size:
                    stats["skipped"] += 1
                    continue

            try:
                tf.extract(member, path=dest, set_attrs=False)
                if member.isfile():
                    stats["extracted"] += 1
            except Exception as e:
                stats["errors"] += 1
                if stats["errors"] <= 5:
                    print(f"  ERROR: {member.name}: {e}", flush=True)
                elif stats["errors"] == 6:
                    print("  (further errors suppressed)", flush=True)

    return stats


def update_env(photos_dir: Path) -> None:
    if not ENV_FILE.exists():
        print(f"WARN: {ENV_FILE} not found. Set TAKEOUT_DIR={photos_dir} manually.")
        return
    content = ENV_FILE.read_text(encoding="utf-8")
    lines = [
        f"TAKEOUT_DIR={photos_dir}" if l.startswith("TAKEOUT_DIR=") else l
        for l in content.splitlines()
    ]
    if not any(l.startswith("TAKEOUT_DIR=") for l in content.splitlines()):
        lines.insert(0, f"TAKEOUT_DIR={photos_dir}")
    ENV_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Updated .env: TAKEOUT_DIR={photos_dir}")


def main():
    archives = sorted(TAKEOUT_DIR.glob("*.tgz"))
    if not archives:
        print(f"No .tgz files found in {TAKEOUT_DIR}")
        sys.exit(1)

    total_gb = sum(a.stat().st_size for a in archives) / 1e9
    free_gb  = shutil.disk_usage(TAKEOUT_DIR).free / 1e9

    print("=" * 60, flush=True)
    print(f"Google Takeout Extraction  ({len(archives)} archives, {total_gb:.1f} GB)", flush=True)
    print(f"Destination : {DEST_DIR}", flush=True)
    print(f"Free on D   : {free_gb:.1f} GB", flush=True)
    print("=" * 60, flush=True)

    prevent_sleep()
    start  = datetime.now()
    totals = {"extracted": 0, "skipped": 0, "errors": 0}

    for idx, arc in enumerate(archives, 1):
        marker = done_marker(arc)

        if marker.exists():
            print(f"\n[{idx}/{len(archives)}] {arc.name} -- SKIPPED (already done)", flush=True)
            continue

        size_gb = arc.stat().st_size / 1e9
        print(f"\n[{idx}/{len(archives)}] {arc.name}  ({size_gb:.1f} GB)", flush=True)
        t0    = datetime.now()
        stats = extract_archive(arc, DEST_DIR)
        secs  = int((datetime.now() - t0).total_seconds())
        free  = shutil.disk_usage(TAKEOUT_DIR).free / 1e9
        for k in totals:
            totals[k] += stats[k]
        print(f"  Done in {secs}s | free={free:.1f}GB | "
              f"extracted={stats['extracted']:,} skipped={stats['skipped']:,} errors={stats['errors']}",
              flush=True)

        # Mark complete so next restart skips this archive instantly
        marker.write_text(
            f"extracted={stats['extracted']} skipped={stats['skipped']} errors={stats['errors']}",
            encoding="utf-8"
        )

    elapsed = (datetime.now() - start).total_seconds() / 60
    print("\n" + "=" * 60, flush=True)
    print(f"Extraction complete in {elapsed:.1f} min", flush=True)
    print(f"Total extracted : {totals['extracted']:,}", flush=True)
    print(f"Total skipped   : {totals['skipped']:,}", flush=True)
    print(f"Total errors    : {totals['errors']}", flush=True)

    photos_dir = DEST_DIR / "Takeout"
    if not (photos_dir / "Google Photos").exists():
        sub = next(iter(photos_dir.iterdir()), None) if photos_dir.exists() else None
        if sub:
            photos_dir = sub

    allow_sleep()
    update_env(photos_dir)

    print("\nNext steps:", flush=True)
    print("  1. Run: cd photo-curator ; .\\start.ps1", flush=True)
    print("  2. Click 'Ingest Takeout' on the Catalog page", flush=True)


if __name__ == "__main__":
    main()
