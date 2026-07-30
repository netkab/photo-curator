---
name: photo-gp-sync
description: Sync the live Google Photos library into the catalog via the Chrome extension and match it to local Takeout rows, so the app can act on real photos (trash duplicates, retire originals, build albums) instead of only producing checklists. Run this before any Duplicates, Enhance, Storage or Maps album work. Reports link coverage and diagnoses unmatched items.
---

Build the bridge between the local Takeout catalog and the live Google Photos library.

**Nothing in the app can act on Google Photos until this has run.** A Takeout export contains no
Google identifier, so until an item is synced and matched there is no `dedupKey` for it and every
downstream flow degrades to "here's a checklist, go find it by date".

## Prerequisites

1. Backend running: `.\start.ps1`
2. A **photos.google.com** tab open and signed in — every Google call rides that tab's session
3. The extension loaded (`chrome://extensions` → Load unpacked → `gp-extension/`)

## Steps

1. Open the extension (toolbar icon) → **Library Sync** → **Scan library**.

   Walks the library newest-first in pages of 500, pushing each page to the backend as it arrives, so
   an interrupted scan keeps everything fetched so far. ~60 pages for a 30k library.

2. Matching runs automatically afterwards. Check coverage from the terminal at any time:

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli gp-status
   ```

3. If coverage looks low, retry the rows that failed (no re-scan needed):

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli gp-link --relink
   ```

## Reading the coverage numbers

| Row | Meaning | Normal? |
|---|---|---|
| **Linked** | Actionable. The only number that matters. | Aim >95% |
| **Google-only** | Live in Google, absent from the catalog | Yes — uploaded after your Takeout export |
| **Catalog-only** | In the catalog, not live | Yes — already deleted from Google |
| **Ambiguous** | >1 plausible catalog row; left unlinked on purpose | Should be small |

Diagnose either side before trusting a large destructive run:

```
GET /api/gp/unmatched?side=gp        # in Google, no catalog row
GET /api/gp/unmatched?side=catalog   # in the catalog, not in Google
```

## Notes

- **The matcher never guesses.** Four passes, strongest first: filename+capture-time → filename+dims
  (survives timezone skew) → filename unique on both sides → capture-time+dims (survives Google
  truncating long Takeout filenames). More than one candidate at any pass ⇒ `ambiguous`, left
  unlinked. A wrong link means trashing the wrong photo.
- **Undated rows match worst.** Catalog rows with no `taken_at` lose the two strongest passes; that
  is the single biggest cause of low coverage.
- **`dedup_key` vs `media_key`**: mutations key on `dedup_key` (content identity); albums and URLs
  use `media_key`. Shared-album copies share a `dedup_key`.
- Re-syncing is idempotent and never breaks an existing link.
- Google Photos also exposes GPS (`geoLocation`) here — the "GPS only comes from Takeout/EXIF"
  constraint applies to the *official* API only.

## Rules

- Read-only. This skill never mutates Google Photos.
- Never suggest deleting local files based on "unlinked" status — an unlinked catalog row is often
  the **last copy in existence**. See `photo-reclaim`.
