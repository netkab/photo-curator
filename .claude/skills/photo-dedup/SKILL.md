---
name: photo-dedup
description: Detect duplicate and visually-similar photos in the Photo Curator catalog — exact (file hash), resized/recompressed (perceptual hash), and near-duplicate/burst shots (CLIP embeddings). Groups them newest-date-first, auto-picks the best keeper, then trashes the duplicates in Google Photos through the Chrome extension (recoverable 60 days, undoable). Run photo-gp-sync first. Nothing is deleted locally.
---

Detect duplicate and similar photos, then act on them in Google Photos. Results appear in the
**Duplicates page** of the web UI and in the extension's **Duplicates** tab, which can actually trash
them.

## Steps

1. Run detection (fast = pHash only, no GPU needed; with CLIP = also catches burst shots):

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli dedup
   ```

   Fast mode (skip CLIP):

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli dedup --no-clip
   ```

2. **Review in the web UI** at `/duplicates` — stacked tiles sorted newest-first (like Google Photos),
   with sticky date headers. Click any stack to open the KEEP/DELETE comparison modal.

3. **Or use the Chrome extension**, which can actually act on the results — see below.

## Acting on results

**Chrome extension (recommended)** — see [gp-extension/GUIDE.md](../../../gp-extension/GUIDE.md):
- **Run `photo-gp-sync` first.** Until an item is synced and matched there is no `dedupKey` for it,
  so nothing can act on it.
- **Then clean up the backlog** (Duplicates tab → *Clean up whole backlog*, or
  `POST /api/dedup/reconcile`). A Takeout export is a snapshot, so every duplicate you removed from
  Google Photos previously still has a stale catalog row. On a long-running library this is usually
  **most of the queue** — reconcile closes groups with 0 or 1 surviving copies and promotes a
  survivor to keeper where the chosen keeper is gone. Preview with `{"apply": false}` first; it
  deletes nothing.
- Duplicates tab → **Trash duplicates**. Items move to the Google Photos bin (recoverable 60 days),
  with progress, resume, and a one-click **Undo**.
- A group is actionable when the **keeper survives** and at least one duplicate is still live.
  Duplicates already gone from Google Photos are skipped, not treated as a blocker.
- The **Dry run** box is ticked by default until one completes; it walks the whole pipeline without
  issuing a single mutation and without consuming any review state.
- The in-grid overlay still outlines duplicates while you browse Google Photos (read-only).

Group badges and what each means:

| Badge | Meaning | Action |
|---|---|---|
| `N to trash` | keeper survives, N live duplicates | act on it |
| `already done` | 0 or 1 copies left in Google Photos | *Mark done*, or bulk clean-up |
| `keeper gone` | copies survive but the keeper isn't one | clean-up promotes a survivor, or re-pick by hand |

A tile drawn dashed/grey captioned **"not in Google Photos"** is *not* a matching failure — the photo
isn't there any more, so there's nothing to link it to. Don't send the user chasing it.

**Keeping several from one group is the normal case, not an edge case.** CLIP matches *near*-
duplicates, so a group is often distinct shots taken seconds apart. Per-tile **Keep this too**
excludes an item from trashing (`exclude_media_ids` on `POST /api/dedup/approve-bulk`); **Make
keeper** swaps it in as the keeper. If every duplicate is kept, the group is skipped and deliberately
left **not reviewed** — that's a decision about this run, not a verdict that the group is resolved.

**Groups are ordered by the keeper's capture date.** Changing the keeper therefore moves the group in
the list, sometimes far. It is not deleted. The extension updates the group in place instead of
reloading, precisely because a reload made it look like the row had been consumed.

**Whole-group disposal** is `POST /api/dedup/groups/{id}/trash-all` — the only path that trashes the
keeper too, for a group that is simply junk. It is a separate endpoint, one group per call, with no
bulk form, because every other path guarantees the keeper survives and that guarantee must not be
reachable by a flag. `exclude_media_ids` still wins over it. Keepers are protected by the *presence*
of a keeper id in the action payload, so `trash-all` simply omits one.

**Web UI (Duplicates page):**
- Click any stack → modal shows KEEP (green, large) and DELETE photos (red, smaller)
- Clicking a duplicate swaps it as keeper
- Approve → queues items as `delete` review-actions in Review Queue
- Review Queue → Apply → schedules a `GpOperation` the extension drains; anything not linked to
  Google Photos still falls back to a `delete_checklist_*.txt`

**Delete Helper page (`/delete-helper`):** date-grouped cards for fully manual work. Superseded by
the extension for anything linked.

## Repairing groups from the old detection

Two defects existed before 2026-07-28; both are fixed at source, but groups already in the DB need
one repair pass (no GPU needed):

```powershell
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli dedup --cleanup
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli dedup --cleanup --apply
```

1. **Layer-duplicate groups.** pHash and CLIP each persisted their findings independently, so a pair
   caught by both became two identical groups. Cleanup drops the redundant one (keeps `phash`).
   Detection now runs layers strongest-first over the still-unassigned photos.
2. **Chained mega-groups.** Clustering was transitive union-find (single linkage): A≈B and B≈C put A
   and C together regardless of how unalike they are. On 28k photos this produced groups of up to
   **687 members** — "trash the duplicates" there means binning 686 distinct photos. Detection now
   uses **seeded clustering**: the best remaining photo anchors a group and claims only what is
   similar *to it*, so membership can never be transitive.

Oversized groups (> `DEDUP_MAX_GROUP_SIZE`, default 25) are flagged `blocked_reason: "oversized"`,
never `actionable`, and excluded from both bulk approve and `trash-all`. Cleanup only *reports* them
— a full `dedup` re-run rebuilds them correctly.

## Notes

- Groups are sorted by **keeper photo date, newest first** (like Google Photos timeline).
- Keeper is auto-picked: highest `resolution × sharpness × face_count` score.
- Thresholds configurable in `app/config.py`: `PHASH_MAX_DISTANCE` (default 6), `CLIP_SIMILARITY` (default 0.92).
- Current library: ~8,500 dup groups — but after reconcile expect **roughly half to close as already
  resolved**. Report the reconciled number, not the raw one; the raw count badly overstates the work.
- Grid tiles are identified by **`mediaKey` read from the tile's `href`**, not the filename timestamp
  the v1 extension parsed out of `aria-label`. Coverage now depends on link coverage
  (`GET /api/gp/status`), not on whether a filename happens to embed seconds.
- At this scale, approve in bulk (`POST /api/dedup/approve-bulk`): one review action and one
  resumable operation, rather than thousands of single-item ones.

## Rules

- **Never delete local files.** Originals in the Takeout folder are only ever read.
- Trash means Google's bin (recoverable 60 days) and is always undoable. Permanent deletion is not
  implemented and is excluded by the `ALLOWED_OPS` allow-list in `routers/gp.py`.
- Every real trash run goes through an approved `review_action`. Dry runs are exempt only because
  they return before issuing any RPC.
- Ignore marks a group `reviewed=True` with no delete action — it never reappears.
