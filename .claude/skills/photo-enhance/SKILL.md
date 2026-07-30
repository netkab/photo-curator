---
name: photo-enhance
description: Detect blurry photos (OpenCV Laplacian variance) and enhance them locally — upscale/restore with Real-ESRGAN, and restore faces with GFPGAN/CodeFormer. Outputs new derived files (originals untouched), uploads the improved copy, then retires the blurry original in Google Photos via the extension. Enhancement must stay local — it needs the original pixels.
---

Detect blurry photos and replace them with restored versions. Enhanced images are written as **new**
files under `data/derived/` — local originals are never modified.

The full loop is:

```
enhance on the GPU  ->  upload the improved copy  ->  retire the blurry original in Google Photos
```

Enhancement **cannot** move into the extension: Real-ESRGAN needs the original pixels and Google only
ever serves a downscaled thumbnail. The extension owns the last step only.

## Steps

1. Enhance all flagged-blurry photos (or pass specific ids / a selection from the UI):

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli enhance --blurry
   ```

   Enhance specific catalog ids:

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli enhance --ids 1432,1581,1607
   ```

2. Review before/after and approve — extension **Enhance** tab, or the web UI's Enhance page.
   Approval queues an `upload` review-action; applying it uploads to a dedicated album (appendonly)
   or exports to a folder if uploads aren't configured.

3. **Retire the originals** once the replacements are uploaded — extension Enhance tab →
   *Retire original*, or `POST /api/enhance/retire-originals`. Uses the same dry-run / progress /
   undo machinery as duplicate trashing.

## Picking targets

`GET /api/enhance/candidates` lists blurry photos **that are still live in Google Photos**, worst
first. A blurry photo no longer in Google has nothing to replace — enhancing it is a local-only
exercise, so it's excluded here. Requires `photo-gp-sync` to have run.

## Notes

- Blur detection: Laplacian variance < `BLUR_THRESHOLD` (tunable in `app/config.py`). Run
  `photo-analyze` first so `blur_score` is populated. Blur must be scored on the **original** —
  Laplacian variance on a 512 px thumbnail is meaningless, which is another reason Takeout is still
  required.
- Real-ESRGAN runs **tiled** (tile 192–256) to fit 8 GB VRAM; faces additionally restored with
  GFPGAN/CodeFormer when detected.
- Enhancement is GPU-heavy — process in small batches to avoid thermal throttling.
- **Uploads need OAuth.** Without `PHOTOS_OAUTH_CLIENT_ID` / `_SECRET`, approved files are exported
  to a folder for manual upload and retiring stays blocked. Setup steps are in
  `backend/.env.example`; the scope is `photoslibrary.appendonly` only, which by Google's design
  cannot read, edit or delete anything already in the library.
- Derived files inherit the original's EXIF timestamp and GPS, so the replacement lands at the right
  point in the Google Photos timeline despite being uploaded today.

## Rules

- Always write to `data/derived/`; never overwrite a local original.
- **Never retire an original whose replacement isn't `uploaded`.** The endpoint enforces this —
  trashing before the replacement is safely in Google leaves the user with neither.
- Retiring means Google's bin (60 days, undoable), never permanent deletion.
