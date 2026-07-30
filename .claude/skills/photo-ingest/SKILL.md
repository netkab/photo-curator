---
name: photo-ingest
description: Ingest a Google Takeout export into the Photo Curator catalog — walk the export folder, pair each photo/video with its JSON sidecar (GPS, timestamp, description), de-duplicate split-archive copies by hash, extract EXIF/codec metadata, and generate thumbnails. Pass the Takeout folder path as the argument.
---

Ingest a Google Takeout export into the catalog. Pass the unzipped Takeout folder as the argument
(falls back to `TAKEOUT_DIR` in `.env`).

## Steps

1. Run ingestion (incremental — already-cataloged items are skipped by hash):

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli ingest --takeout "<TAKEOUT_FOLDER>"
   ```

   Example: `--takeout "D:\Takeout"`.

2. Review the printed summary: new media indexed, sidecars matched, GPS merged, split-archive
   duplicates collapsed, and any files without sidecars.

3. Confirm in the UI: open the **Catalog** page (`photo-dev`) and filter by date/place/type.

## Notes

- Handles Google's sidecar quirks: `IMG_1234.jpg.json` and newer `*.supplemental-metadata.json`,
  truncated long names, and `(1)`/`(2)` collision suffixes.
- GPS comes from the sidecar JSON (`geoData`) or embedded EXIF — never from any Photos API.
- Images use exiftool; videos use ffprobe (codec, bitrate, resolution, duration).
- Ingestion is read-only with respect to the Takeout files; it indexes paths and copies thumbnails
  into `backend/data/thumbnails/`. It never moves or deletes your originals.
- **Commits every 100 new files** — if the process is killed or the PC restarts, the next run
  skips already-committed files (by sha256) and continues from where it left off.
- **Takeout extraction**: if archives are in `.tgz` format, use
  `scripts/extract_takeout.py` first (handles Windows trailing-space folder name bug, writes
  `.done` markers per archive for safe resume). See `docs/USAGE.md` for details.
- Library ingested: 27,859 photos + 3,696 videos (D:\Takeout\Takeout).

## Rules

- Never modify or delete files inside the Takeout export.
