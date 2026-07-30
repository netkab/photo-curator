# Using the Google Photos Bridge

A walkthrough of what the extension does, why it works the way it does, and how to run a first
session safely. For installation and error messages see [INSTALL.md](INSTALL.md).

---

## 1. The one idea you need

Photo Curator's catalog is built from a **Google Takeout export**. Takeout gives you the original
files, GPS and timestamps — but it gives you **no Google Photos identifier**. Nothing in the export
tells you which item in the live library a given file corresponds to.

That is why the old version could only draw red boxes and tell you to go delete things by hand.

The extension fixes this by reading your live library through the open Photos tab and storing, for
every item, two Google identifiers:

| Identifier | What it is | Used for |
|---|---|---|
| `mediaKey` | per-item id | URLs, album membership, matching grid tiles |
| `dedupKey` | **content** identity | **every mutation — trash, restore, everything** |

The distinction matters. If the same photo appears in a shared album it gets a *different*
`mediaKey` but the *same* `dedupKey`. Acting per-`mediaKey` would fire the same operation twice and
make the counts lie. Everything here keys on `dedupKey`.

**Consequence:** until an item has been synced *and* matched to a catalog row, the extension has no
`dedupKey` for it and literally cannot act on it. That's why Library Sync comes first, always.

---

## 2. Starting up

Three things must be true, in this order:

```powershell
# 1. Backend running
cd "C:\photo-curator" ; .\start.ps1
```

**2.** A **photos.google.com** tab, open and signed in. Leave it open — every Google call is made
*through that tab*, using your own session. There is no API key and no OAuth; close the tab and the
extension has no way in.

**3.** Click the extension's toolbar icon. The app page opens.

Check the status dot, top-right:

| It says | Meaning |
|---|---|
| **Connected · /u/0** | Ready. `/u/0` is your primary account; a second account shows `/u/1`. |
| Backend offline | `start.ps1` isn't running |
| No Google Photos tab | Open photos.google.com |
| Toolkit not loaded | The tab was open *before* you installed/reloaded the extension → **reload the tab** |
| Photos tab not signed in | Sign in again |

> That last one catches people every time. Reloading the extension does **not** update tabs that are
> already open — they keep running the old content script, or none at all.

---

## 3. Library Sync — always first

Open **Library Sync** → **Scan library**.

It walks your library newest-first in pages of 500. Each page is pushed to the backend *as it
arrives*, then the matcher runs. For a ~31,000-item library that's around 60 pages; expect a few
minutes.

Streaming the pages matters: if the scan dies at page 40, the first 40 pages are already saved. Hit
**Scan library** again and it re-syncs (upserts are idempotent) rather than starting from nothing.

You can hit **Stop** at any time. It finishes the page in flight and keeps everything fetched so far.

### Reading the Coverage panel

| Row | What it means | What's normal |
|---|---|---|
| **In Google Photos** | Live items found | Should roughly match your library size |
| **Linked to catalog** | **Actionable.** The number that matters. | Aim for >95% |
| **Google-only** | Live, but absent from the catalog | Anything uploaded *after* your Takeout export. Expected, and grows over time. |
| **Catalog-only** | In the catalog, not live | Usually already deleted. Also expected. |
| **Ambiguous** | More than one plausible catalog row | Should be small. These are left unlinked **on purpose**. |

The matcher never guesses. It runs four passes, strongest first:

1. filename + capture time to the second
2. filename + pixel dimensions *(survives timezone skew)*
3. filename, unique on both sides
4. capture time + dimensions *(survives Google truncating long filenames in Takeout)*

If a pass finds two equally good candidates, it stops and records `ambiguous` rather than falling
through to a weaker guess. A wrong link here would mean trashing the wrong photo, so a gap is always
preferable to a mistake.

### If coverage looks low

Scroll to the **Unmatched** panel and actually read the two lists before running anything
destructive. Then:

- **Retry unmatched** — re-runs only rows that came out ambiguous/unmatched.
- **Re-run matching** — re-runs the matcher over everything not yet linked.

Same thing from the terminal, useful when you want the raw numbers:

```powershell
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli gp-status
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli gp-link --relink
```

---

## 4. Duplicates

The groups themselves come from Photo Curator's dedup pipeline, run earlier against the **original
files on disk**:

- **sha256** — byte-identical copies
- **perceptual hash** — resized or recompressed copies
- **CLIP** — near-duplicates and burst shots

That's better evidence than the browser can ever have, since the web UI only sees compressed
thumbnails. (The best-known tool in this space works from thumbnail embeddings alone, and has a
three-year-old open bug about missing byte-identical duplicates as a result.)

If you have no groups, run the pipeline first:

```powershell
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli dedup
```

### What a group looks like

Green-bordered tile = **KEEP**. Red-bordered = **TRASH**. Each shows filename, dimensions and
capture time, with a link to open it in Google Photos.

The keeper was auto-picked by resolution and sharpness. To override, click **Keep this one instead**
under a different tile — they swap.

### The badge on each group

| Badge | Meaning | What to do |
|---|---|---|
| **N to trash** | The keeper survives and N duplicates are still in Google Photos | Act on it |
| **already done** | 0 or 1 copies left — the duplicates were removed previously | **Mark done**, or use the bulk clean-up |
| **keeper gone** | Copies survive, but the one marked KEEP isn't one of them | Clean-up promotes a survivor, or click *Make keeper* |
| **too large — check by hand** | Far more members than a duplicate set can plausibly have | Excluded from every bulk action; see below |

### If you see the same group twice, or an enormous one

Both come from the old detection and are fixed at source; existing groups need one repair pass:

```powershell
cd "…\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli dedup --cleanup           # preview
cd "…\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli dedup --cleanup --apply
```

**Same photos, two groups** — one `phash`, one `clip`. The two layers used to run independently and
each persist its findings, so a pair caught by both produced two identical groups. The cleanup drops
the redundant one (keeping the `phash` group, whose signal is tighter). Detection now runs the layers
strongest-first over whatever is still unassigned, so each photo lands in at most one group.

**A group with hundreds of members** — clustering used to be transitive: A≈B and B≈C put A and C
together even when they look nothing alike, and over 28k photos that chained into groups of several
hundred. Trashing "the duplicates" there would bin hundreds of distinct photos to keep one, so these
are now flagged and **excluded from bulk trashing and from Trash all**. The cleanup only reports
them; a full `python -m app.cli dedup` re-run (GPU, slow) rebuilds them properly with seeded
clustering, where membership means "close to *this* photo" rather than "reachable from it".

A tile drawn dashed and grey, captioned **"not in Google Photos"**, is a photo that no longer exists
there. **This is not a matching failure and manual linking can't fix it** — there is nothing on the
Google side to link to. It's almost always something you already deleted.

The keeper must survive for a group to be actionable. Duplicates that are already gone are simply
skipped; they aren't a reason to block the group.

**Not a duplicate** / **Mark done** marks the group reviewed without queuing anything.

### Keeping more than one

CLIP finds *near*-duplicates, so a group is frequently several genuinely different shots taken
seconds apart rather than copies. Keeping several is the normal case.

- **Keep this too** — toggles a duplicate out of the trash list. It turns green and reads
  *✓ Keeping*; the group header count drops. Click again to put it back.
- **Make keeper** — swaps that photo in as the keeper. The old keeper becomes a duplicate. Offered
  on every live duplicate, including in two-photo groups where it's the whole decision.

Each card spells out the outcome above the tiles — *"Keeping X · trashing Y"* — and the confirmation
lists the filenames for small selections, so you can check the assignment is the one you meant
before anything moves.

**Two-photo groups.** The green tile is kept, the red one is trashed, and **Trash 1** does exactly
that. To swap which is which, click **Make keeper** under the red one.

Kept items are excluded from every trash path, including dry runs and bulk runs. If you keep *all*
of a group's duplicates, the group is skipped and **left unreviewed** — deciding to keep everything
this time isn't the same as saying the group is resolved, so it'll still be there later.

### Throwing away a whole group

Sometimes the group isn't "one good photo plus copies" — it's all junk. **Trash all N** (the
outlined red button) trashes every member *including the keeper*.

It's a separate button, one group at a time, and always confirmed, because every other path in this
tool guarantees the keeper survives — that guarantee shouldn't be reachable by a checkbox. There is
no bulk "trash all groups entirely", deliberately.

Anything marked **Keep this too** still wins over it, so you can throw away a group *except* one
frame. Same 60-day bin and same undo as everything else.

> **Note on ordering after *Make keeper*.** Groups are sorted by the keeper's capture date, so
> promoting an older photo moves the group down the list — sometimes a long way. It is not deleted.
> The page updates the group in place rather than reloading, so it stays where you're looking until
> the next refresh.

### Clean up the backlog first

A Takeout export is a snapshot. Everything you've deleted from Google Photos since then still has a
catalog row, so the queue accumulates groups that were resolved long ago — often most of it.

The **Clean up whole backlog** button (in the amber panel) reconciles the queue against what actually
still exists:

- groups with 0 or 1 surviving copies → marked reviewed, nothing to deduplicate
- keeper gone but ≥2 copies survive → the best survivor is promoted to keeper

**Preview clean-up** shows the numbers first and changes nothing. Neither button deletes anything —
worst case it marks groups reviewed, and `python -m app.cli dedup` rebuilds groups from scratch if
you ever want them back.

Do this before working through the list, or you'll page through thousands of groups that are already
finished.

### Your first real run — do it in this order

The **Dry run** box is ticked by default and stays ticked until one completes.

**Step 1 — Dry run.** Click *Trash duplicates* on a single group. It walks the entire pipeline: tab
connection, toolkit, account guard, batching, cursor advance. It returns *before* issuing any
mutation, so it cannot change anything even if something else is wrong. It also bypasses the review
queue completely — nothing is approved and the group isn't consumed, so it's still waiting for you
afterwards.

**Step 2 — One group for real.** Untick Dry run, click *Trash duplicates* on one group, confirm.

**Step 3 — Verify in Google Photos.** Open the bin (Photos → Bin). The duplicate should be there.
Check the keeper is still in your library.

**Step 4 — Undo.** In the Operations panel, click *Undo (restore N)*. Confirm it comes back.

You now know the whole loop works end to end on your account. Only then go bulk.

### Going bulk

**Trash duplicates in N groups** takes everything actionable currently loaded. Use **Load 20 more**
to pull in more first.

All selected groups become **one** review action and **one** operation. That's deliberate — with
~8,500 groups, one operation per group would mean thousands of separate runs each paying the pacing
delay, and thousands of separate things to undo.

Rough timing: items are sent 100 at a time with a 5-second gap, so ~1,000 items is about a minute.

---

## 5. Operations — progress, pausing, undo

Every run appears in the **Operations** panel with a progress bar.

**Progress is stored in Photo Curator, not the browser.** The extension asks the backend for the next
slice, runs it, and reports back; the cursor advances server-side. So:

- Close the app page → fine.
- Quit Chrome mid-run → fine. Reopen and it resumes from where it stopped.
- Worst case is one in-flight batch retried, which is harmless because trashing an already-trashed
  item is a no-op.

*(The tool this borrows its API approach from keeps progress in memory. Its users report trash runs
stalling at exactly 2,500 items — 10 × their hardcoded batch size, with no delay and no rate-limit
handling — and losing all of it. That specific failure is what the server-side cursor exists to
prevent.)*

### When an operation pauses

A pause is a **good** outcome, not a crash. It means a batch failed and the run stopped at that exact
slice rather than skipping it. The error is shown in red.

Fix the cause (usually: the Photos tab was closed, or Google rate-limited you), then click
**Resume**. It restarts at the same slice — nothing is skipped, nothing is double-counted.

**Stop** cancels a running operation. Whatever already succeeded stays done and is still undoable.

### Undo

**Undo (restore N)** builds the inverse operation from exactly what succeeded — not from what was
requested. Items sit in Google's bin for **60 days**, so undo works any time within that window.

Undo is unavailable for dry runs (nothing happened) and for restores (nothing to reverse).

---

## 5a. Storage — reclaiming disk space

Your Takeout folder is large (157 GB here), and the obvious instinct — "delete the duplicates I've
already handled" — is the one that loses photos. Read this table before touching anything:

| | Meaning |
|---|---|
| **Backed by Google** | The file exists on disk *and* in Google Photos. Two copies. |
| **Sole copies** | On disk only. Already deleted from Google, so **this is the last copy in existence.** |
| **Already quarantined** | Moved by a previous reclaim run. |

Sole copies are the ones that *feel* most finished. They are the ones never to delete, and the tool
excludes them by construction.

**Reclaim duplicates** moves a local duplicate only when its keeper exists **on disk** *and* is
**live in Google Photos** — so two independent copies of the content survive every move. Files are
moved to a quarantine folder, never deleted; the catalog follows them, and every run writes a
manifest with a one-click **Undo**.

Videos are excluded by default (biggest files, hardest to re-acquire, and their "duplicates" are
more often re-encodes than true copies).

Same thing from the terminal:

```powershell
cd "…\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli reclaim              # preview
cd "…\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli reclaim --apply      # do it
cd "…\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli reclaim --list
cd "…\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli reclaim --undo <RUN_ID>
```

Set `QUARANTINE_DIR` in `backend/.env` to choose the destination. **Keep it on the same drive** — a
same-volume move is instant; crossing drives copies every byte and can half-finish.

## 5b. Enhance — replacing blurry photos

Restoration has to run locally: Real-ESRGAN needs the original pixels, and Google only ever serves a
downscaled thumbnail. So the loop is

```
enhance on the GPU  ->  upload the improved copy  ->  retire the blurry original
```

The Enhance tab lists blurry photos **that are still in Google Photos** (anything else has nothing to
replace), worst first. Select some, enhance, approve the result, and once the replacement is
confirmed uploaded, **Retire original** trashes the blurry one through the same dry-run/undo
machinery as duplicates.

**Retiring is blocked until the replacement's status is `uploaded`** — trashing an original before
its replacement is safely in Google would leave you with neither.

That middle step needs upload credentials. Without them, approved files are exported to a folder for
you to upload by hand and retiring stays blocked. To automate it, follow the setup notes at
`PHOTOS_OAUTH_CLIENT_ID` in [backend/.env.example](../backend/.env.example) — about five minutes in
the Google Cloud console, free, and the token requests `photoslibrary.appendonly` **only**, which by
Google's design cannot read, edit or delete anything already in your library.

## 5c. Maps — album per place, review staged

Pick a place and hit **Prepare**. You get three steps:

1. **Album in Google Photos** — creates `<Place> — Photo Curator` and adds that place's live photos
   and videos. Non-destructive: album membership doesn't move or copy anything. (Albums are the one
   Google Photos operation keyed on `mediaKey` rather than `dedupKey`.)
2. **Review text** — drafted on your GPU from the photos' own captions and OCR. One click to copy.
3. **Post it yourself** — opens the write-review page and can open Explorer with the shortlisted
   files selected, ready to drag in.

**Step 3 stays manual deliberately.** A Maps review is public content about someone else's business,
and automated review posting is exactly what Google's anti-abuse systems are built to catch —
unlike managing your own library, it would put your account at risk. Everything is staged; you click
Post.

## 5d. Video — reels, uploads, retiring sources

Compression and highlight reels are GPU jobs on the original files (Google only serves downscaled
thumbnails), so they run locally. The extension owns what happens afterwards: **upload the result,
then retire what it replaces.**

Each card shows the derived clip, its status, and every source clip it came from:

1. **Approve & upload** — queues the upload and pushes it to Google Photos. Takes minutes for a reel.
2. **Retire this** (per source clip) — only enabled once the replacement's status is `uploaded`.
3. **Retire N** — trashes exactly the clips you ticked.

### The reel/compressed asymmetry — read before retiring anything

| Kind | Relationship | Retiring sources |
|---|---|---|
| **compressed** | 1:1 replacement of one clip | Sensible — same content, smaller file |
| **highlight** | A montage cut from *many* clips | **The reel is not a substitute.** A 30-second cut of forty clips does not contain those clips |

So **nothing is pre-selected, and there is no "retire all sources" button.** You tick each clip you
are willing to lose, which is also how you skip the ones you want to keep. The backend rejects an
empty selection, rejects clips that aren't sources of that reel, and refuses outright until the
replacement is `uploaded` — retiring first would leave you with neither.

Trashing is the usual Google bin: 60 days, undoable from the Duplicates tab's Operations panel. Your
local originals are never touched.

### Uploads need one-time setup

Without OAuth credentials the extension cannot upload, so **Retire stays disabled** — approved files
are exported to a folder for you to upload by hand instead. To enable it, set
`PHOTOS_OAUTH_CLIENT_ID` and `PHOTOS_OAUTH_CLIENT_SECRET` in
[backend/.env](../backend/.env.example) (the walkthrough is inline in `.env.example`; about five
minutes in the Google Cloud console, free). The scope requested is `photoslibrary.appendonly` only,
which by Google's design cannot read, edit or delete anything already in your library. The first
upload opens a browser consent window once.

## 6. The in-grid overlay

Separate from the app page, the extension also marks duplicates inside Google Photos itself while you
browse. A **Photo Curator** button sits bottom-left; the count next to it is how many are visible on
screen right now.

This is **read-only**. Outlined photos are duplicates of something Photo Curator is keeping, shown
for context. All trashing happens from the app page, which has the confirmation, dry run, progress
and undo around it.

Tiles are identified by `mediaKey` read from the tile's link, so it works regardless of your Google
Photos UI language or timezone.

---

## 7. What it will and won't do

| | |
|---|---|
| ✅ | Move items to the Google Photos bin (recoverable 60 days) |
| ✅ | Restore them — one click, from exactly what succeeded |
| ✅ | Read your library and link it to the local catalog |
| ❌ | **Permanently delete anything.** Not implemented; excluded by an allow-list in the backend |
| ❌ | **Touch your local originals.** Files in your Takeout folder are only ever read |
| ❌ | Trash without an approved review action (dry runs excepted — they can't mutate) |
| ❌ | Act against a different Google account than the operation was built for |

Two guards worth knowing about because they'll block you one day and you should know why:

- **Account pinning.** Every operation records the `/u/N` it was built for. Sign into a different
  account and it refuses rather than acting on whatever happens to share those keys.
- **All-or-nothing groups.** A group with any unlinked member can't be acted on at all.

---

## 8. Maps and Video

Both tabs are placeholders. They need Google Photos operations that aren't built yet — album
create/add, set-description, set-timestamp. The tabs list exactly what's missing.

Clustering, review drafting, compression and highlight reels all still work today in the Photo
Curator web UI at **http://localhost:5177**.

---

## 9. A typical session, condensed

```
start.ps1                          backend up
open photos.google.com             signed in, leave it open
click toolbar icon                 status: Connected · /u/0

Library Sync → Scan library        a few minutes; check Coverage
  Linked < 95%?  → read Unmatched, then Retry unmatched

Duplicates
  first time only: dry run one group
  untick Dry run
  Trash duplicates in N groups
  watch Operations; walk away if you like — it resumes

paused?  → read the error, fix, Resume
regret?  → Undo (restore N), any time within 60 days
```

---

## 10. Under the hood

```
app/app.html ──chrome.runtime──> background/worker.js ──chrome.tabs──> content/bridge.js
  (this page)                      (batching, pacing,                   (isolated world)
                                    retries, backoff)                          │ postMessage
                                          │                                    ▼
                                          ▼                        main/commands.js (page world)
                                Photo Curator :8077                            │
                                (owns the cursor,                        window.gptkApi
                                 owns the approval gate)                       │
                                                                 photos.google.com/…/batchexecute
```

`main/commands.js` has to run in the page's own JavaScript context because that's the only place
Google's session tokens exist. Everything that could go wrong at scale — batching, pacing, retries,
accounting — lives outside it, in the worker and the backend.

**Google's official API cannot delete your photos.** It never could; the Library API was cut back to
app-created data only in March 2025. The only mechanism that works is Google Photos' own internal
`batchexecute` endpoint, driven from your signed-in session. It is undocumented, and Google can
change it without notice. That's why the third-party layer that encodes its request shapes is
vendored and pinned to a specific release rather than tracked, and why `healthCheck` exists as a
cheap canary.

**After upgrading that layer, re-run a dry run before trusting a real one.** See
[vendor/gptk/VERSION.md](vendor/gptk/VERSION.md).
