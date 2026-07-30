---
name: maps-review-studio
description: Build Google Maps review packages from geo-tagged photos — cluster non-portrait photos by location, match each to a place via the Places API (read-only), draft a star rating + review text from captions/OCR, and gather each place's photos and videos into a Google Photos album via the extension. The review itself is staged for you to post MANUALLY — auto-posting is never attempted.
---

Turn your location-tagged, non-portrait photos into ready-to-post Google Maps reviews, and collect
each place's media into its own Google Photos album.

**Posting is always manual.** There is no API for it, and automating review posting is what Google's
anti-abuse systems are built to catch — unlike managing the user's own library, it is public content
about someone else's business and would put the account at risk. Prepare everything; the user clicks
Post.

## Steps

1. Cluster geo-tagged photos by place and match to Places API entries:

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli maps-cluster
   ```

2. **Extension → Maps tab → Prepare** on a place (or the web UI's Maps Studio page for detailed
   shortlisting/rotation). The Prepare panel has three steps:

   1. **Album in Google Photos** — creates `<Place> — Photo Curator` and adds that place's live
      photos and videos. Non-destructive; album membership doesn't move or copy anything. Dry-run
      available. Needs `photo-gp-sync` to have run.
   2. **Review text** — drafted locally from the photos' captions and OCR; one click to copy.
   3. **Post it yourself** — opens the write-review page, and can open Explorer with the shortlisted
      files selected for dragging in.

3. The web UI's Approve flow still creates a `review` review-action, exporting text + selected images
   to `data/exports/<place>/`.

## Notes

- Requires `GOOGLE_PLACES_API_KEY` for place matching; without it, clusters show raw coordinates and
  you match places manually. Field-masking keeps Places cost within the free tier.
- Photos where a person or group is the subject are excluded automatically — a single prominent face
  (`is_portrait`) OR several faces that jointly cover enough of the frame (`face_area_ratio`, catches
  posed group shots that no single face would flag). Run `photo-analyze` first. Videos aren't covered
  by this filter yet.
- **One-time reprocessing for an existing library** (only needed once, after this filter improved to
  catch group shots): backfill the new signal from already-detected faces (no GPU), then prune it out
  of clusters built before the fix. Clusters you've already marked reviewed are left untouched.
  ```powershell
  cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli backfill-face-metrics
  cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli prune-person-focus
  ```
  Both are standalone and make no Places API calls — `prune-person-focus` only rewrites cluster
  membership in the local DB (unlike `maps-cluster`, which reverse-geocodes with no cap when run
  without `--sample`).

- **Album ops key on `media_key`**, not `dedup_key` — albums are the one Google Photos operation
  where the content-identity key is the wrong one. `GET /api/maps/clusters/{id}/album-package`
  returns exactly what the extension needs, plus a per-item `live` flag.
- Items in a cluster that aren't live in Google Photos are reported as `not in Google Photos` and
  simply omitted from the album — usually already deleted.

## Rules

- **Never auto-submit a review, and never automate contributing photos to a public place listing.**
  Both are public content about someone else's business; there is no official API, and automating
  them risks the user's account. Prepare and stage only.
- Album creation *is* automated — that's the user's own library, and membership changes nothing about
  the underlying media.
