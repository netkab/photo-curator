# CLAUDE.md — Photo Curator

Guidance for Claude Code when working inside `photo-curator/`.

A **local-first** photo & video curation studio. It ingests a Google Photos library (via Google
Takeout), analyzes every item with **local AI** on the user's GTX 1070 (nothing leaves the machine
for analysis), and helps curate: de-duplicate, enhance blurry photos, caption, draft Google Maps
reviews from geo-tagged photos, and compress videos + build highlight reels. A **React web UI gates
every change** — nothing is deleted from Google Photos or posted to Maps automatically.

## Terminal Command Rules

**CRITICAL**: Always combine directory change and command in a single line, with absolute Windows
paths. Shell working directory does not persist between tool calls.

```powershell
# ✅ Correct
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m uvicorn app.main:app --reload

# ❌ Wrong — second line may run in the wrong directory
cd "C:\photo-curator\backend"
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

## ⚠️ Non-negotiable safety rules (encoded throughout the code)

1. **Local originals are never modified or deleted by this app.** Every output (enhanced photo,
   compressed video, highlight reel) is a **new** file under `backend/data/derived/`. The files in
   your Takeout folder are only ever read.
2. **Deletion means "move to the Google Photos bin", never permanent delete.** The Chrome extension
   can trash items (recoverable for 60 days) through Google's internal `batchexecute` API, and every
   trash run has a one-click **undo** that restores exactly what succeeded. `ALLOWED_OPS` in
   `routers/gp.py` is the allow-list that keeps permanent delete and locked-folder moves off the
   table by construction. Items with no live Google Photos link still fall back to a manual checklist
   so nothing is silently dropped.
3. **Nothing is uploaded, trashed, or posted automatically.** The Review Queue (`review_actions`
   table) remains the single gate — `POST /api/gp/operations` refuses a `trash` without an approved
   `review_action_id`. The extension *executes* approved actions; it never authorises them. The one
   exemption is a **dry run**, which returns before issuing any RPC and so provably cannot mutate.
4. **Uploads add NEW media to a dedicated album** via the `photoslibrary.appendonly` scope only. The
   official API is never used to edit or delete existing Google Photos — it cannot, which is the
   whole reason the extension exists.
5. **No Maps review auto-posting.** Maps Studio prepares star rating + text + shortlisted captioned
   images; the user posts manually (Places API is read-only for reviews).
6. **Operations are pinned to one Google account.** Every `GpOperation` records the `/u/N` it was
   built for, and both the backend and the extension refuse to run it against a different signed-in
   account.
7. **Reclaim MOVES, never deletes, and never touches a sole copy.** `pipeline/reclaim.py` only
   relocates a local duplicate when its keeper exists **on disk** AND is **live in Google Photos**,
   so two independent copies survive. A file no longer in Google Photos is the last copy in
   existence — those look the most "already handled", which is exactly why the naive version of this
   feature loses photos. Every run writes a manifest and is undoable.
8. **A blurry original is only retired after its replacement is `uploaded`.** `/api/enhance/
   retire-originals` refuses anything else — trashing before the replacement lands leaves neither.
9. **Oversized duplicate groups are never bulk-actionable.** Groups above
   `DEDUP_MAX_GROUP_SIZE` (25) are clustering artifacts, not duplicate sets; `approve-bulk` and
   `trash-all` both skip them. See the dedup note below.
10. **A highlight reel is not a replacement for its sources.** `/api/videos/{id}/retire-sources`
    demands an explicit `media_ids` list — no "retire all" shortcut exists, and nothing is
    pre-selected in the UI. Retiring is refused until the derived file's status is `uploaded`.
    (`compressed` output *is* a 1:1 replacement; `highlight` is a montage of many clips.)

## Layout

| Part | Path | Stack |
|------|------|-------|
| Backend (API + pipelines) | `backend/` | Python 3.11, FastAPI, SQLAlchemy + SQLite |
| Frontend (review UI) | `frontend/` | React + Vite + TypeScript |
| Setup / ops scripts | `scripts/` | PowerShell |
| Working data (gitignored) | `backend/data/` | catalog.db, thumbnails/, derived/, exports/ |

## Claude Code skills (`.claude/skills/`)

| Skill | Purpose |
|-------|---------|
| `photo-setup` | One-time: create venv, install deps, pull Ollama model (`moondream`), verify GPU/exiftool/ffmpeg, load the extension. |
| `photo-dev` | Run backend (uvicorn port 8077) + frontend (Vite port 5177); extension load/reload + debugging. |
| `photo-ingest` | Ingest a Takeout export folder → catalog. Commits every 100 files; safe to restart. |
| `photo-analyze` | Local AI pass (blur, captions, OCR, faces, geo). Use `--batch` flag for unattended run. |
| **`photo-gp-sync`** | **Sync the live Google Photos library into `gp_items` + match it to the catalog. Prerequisite for every acting skill below.** |
| `photo-dedup` | 3-layer detection (sha256→pHash→CLIP), reconcile the backlog, then trash duplicates in Google Photos. |
| **`photo-reclaim`** | **Move redundant local duplicates to quarantine to free disk. Never deletes; never touches a sole copy.** |
| `photo-enhance` | Blur detection + Real-ESRGAN → upload the improved copy → retire the blurry original. |
| `maps-review-studio` | Cluster geo photos by place, build a Google Photos album per place, stage the review (posting stays manual). |
| `video-process` | Compress videos (h264_nvenc) + assemble highlight reels. GP-side retire not wired yet. |

**Ordering that matters:** `photo-gp-sync` must run before `photo-dedup` acting, `photo-reclaim`,
`photo-enhance` retiring, or Maps albums — without it nothing has a `dedup_key`/`media_key`.

## Commands

```powershell
# Setup (one-time)
cd "C:\photo-curator" ; .\scripts\setup-models.ps1

# Run backend
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8077

# Run frontend
cd "C:\photo-curator\frontend" ; npm run dev

# Pipelines via CLI (each maps to a skill; --help on each)
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli ingest  --takeout "D:\Takeout"
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli analyze --limit 200
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli dedup
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli enhance --blurry
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli maps-cluster
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli video --compress --highlights
```

## Architecture

- **Catalog**: SQLite via SQLAlchemy (`app/models.py`). `media` is the spine; `captions`, `ocr_text`,
  `faces`/`face_clusters`, `dup_groups`/`dup_members`, `place_clusters`, `derived_media`, and
  `review_actions` hang off it. `review_actions` is the **single gate** for every mutating action.
- **Pipelines** (`app/pipeline/`): pure functions that read/write the catalog. Each is invokable from
  the CLI (`app/cli.py`) and from a FastAPI background job (`app/jobs.py`).
- **Services** (`app/services/`): `ai_models` (lazy model loading/runtime), `places` (Google Places
  read-only), `uploader` (Photos `appendonly`, new album only).
- **Routers** (`app/routers/`): thin REST layer the React UI calls. Long work is dispatched to the
  in-process job runner and polled via `/jobs/{id}`.
- **Frontend pages**: Catalog, Duplicates (stacked tiles, date-grouped newest-first), Enhance,
  MapsStudio, Videos, **VideoMetadata** (`/video-metadata` — backfill date/place on undated clips),
  ReviewQueue, **DeleteHelper** (`/delete-helper`). API base in `src/lib/api.ts`.
- **Google Photos bridge** (`gp_items` + `gp_operations`, `pipeline/gp_match.py`, `routers/gp.py`):
  the catalog is built from Takeout and stores no Google identifier, so nothing could act on the live
  library. The extension enumerates it and posts the results to `POST /api/gp/sync`; `gp_match` links
  each row to a `media` row; `dedup_key` (content identity, **not** `media_key`) is then what every
  mutation takes. `gp_operations` is a resumable work queue whose **cursor lives server-side** — the
  extension asks for a slice, runs it, reports back, and a browser crash costs at most one batch.
- **Chrome Extension** (`gp-extension/`, v2): MV3, no build step. A full app page
  (`app/app.html`, opened from the toolbar icon) with **Library Sync · Duplicates · Storage ·
  Enhance · Maps · Video** tabs, plus an in-grid overlay on `photos.google.com`. Actions run through
  Google's internal `batchexecute` API via a vendored, pinned copy of Google-Photos-Toolkit (MIT)
  injected into the page's MAIN world. See `gp-extension/GUIDE.md` (usage),
  `gp-extension/INSTALL.md` (install/troubleshooting) and `gp-extension/vendor/gptk/VERSION.md`.
- **Reclaim** (`pipeline/reclaim.py`, `routers/reclaim.py`, `media.archived_at/archived_from`):
  moves redundant local duplicates to `QUARANTINE_DIR` (default: a sibling of `TAKEOUT_DIR` — keep it
  on the same drive so the move is atomic rather than a 10 GB copy). Manifests in `exports/` make
  every run undoable.

## Key API endpoints (non-obvious)

| Endpoint | What it does |
|----------|-------------|
| `GET /api/stats` | Catalog counts incl. `analyzed` for progress bar |
| `GET /api/dedup/groups?limit=&offset=` | Paginated dup groups, newest-first with `date_key` |
| `GET /api/dedup/extension-queue?limit=` | Top N unreviewed groups + their Google Photos linkage (`gp.dedup_key`) and an `actionable` flag |
| `POST /api/dedup/approve-bulk` | Approve many groups into **one** delete action (thousands of one-item operations would be unusable) |
| `POST /api/dedup/groups/{id}/ignore` | Mark a group reviewed without queuing deletion |
| `GET /api/gp/status` | Link coverage: linked / Google-only / catalog-only / ambiguous |
| `POST /api/gp/sync` · `/link` | Extension posts a page of the live library · run the matcher as a job |
| `GET /api/gp/unmatched?side=gp\|catalog` | Diagnose coverage before trusting a big run |
| `GET /api/gp/operations/{id}/next?size=` | Next slice to execute, from the persisted cursor |
| `POST /api/gp/operations/{id}/result` | Report a slice; advances the cursor (stale cursors ignored) |
| `POST /api/gp/operations/{id}/undo` | Build the inverse `restore` op from what actually succeeded |
| `GET /api/reclaim/summary` | Bytes split by backed-by-Google / sole-copy / already-quarantined |
| `POST /api/reclaim/preview` · `/run` · `/runs/{id}/undo` | Dry run · move · restore a whole run |
| `GET /api/enhance/candidates` | Blurry photos **still live in Google Photos**, worst first |
| `POST /api/enhance/retire-originals` | Trash originals whose enhanced replacement is `uploaded` |
| `GET /api/maps/clusters/{id}/album-package` | `media_key`s for a place, for the album op |
| `GET /api/review/delete-items` | Flat list of all pending delete actions with keeper info |
| `GET /api/media/{id}/file` | Serve the original file (read-only, for full-res preview) |
| `GET /api/media/{id}` | Full media detail including captions, OCR, faces |
| `GET /api/videos/needs-metadata` | Videos highlights skips (no date AND no GPS/place) — paginated |
| `GET\|POST /api/videos/infer-dates` | Preview / apply filename-inferred capture dates (e.g. `PXL_20220108_…`) onto undated videos |
| `POST /api/videos/assign-metadata` | Backfill date/place/GPS onto chosen videos (catalog-only; originals untouched) |
| `POST /api/videos/cancel` · `/reset` | Stop the running video job · clear not-yet-uploaded results |

## Local AI / GPU notes (GTX 1070, 8 GB, Pascal CC 6.1)

- fp16 / int4-GGUF only — **no bf16, no flash-attention, no int8 tensor cores**. Process in **batches**
  and release VRAM between stages to avoid thermal throttling.
- Captions: **moondream** via **Ollama** (`OLLAMA_HOST`, default `http://127.0.0.1:11434`).
- OCR/detection: **Florence-2-large** (transformers). Fallback: **PaddleOCR**.
- Faces: **InsightFace** (`onnxruntime-gpu`) → embeddings → clustering.
- Enhance: **Real-ESRGAN** (tiled, tile 192–256) + **GFPGAN/CodeFormer** for faces.
- Dedup: `imagehash` (pHash/dHash) for exact/resize; **CLIP ViT-B/32** for near-dupes (offline batch).
- Blur: OpenCV Laplacian variance (CPU). Metadata: **exiftool** (images) + **ffprobe** (videos).
- Video compress: ffmpeg `h264_nvenc`, target 1080p ~8 Mbps; `libx264 -crf 18..22` CPU fallback.
  HEVC is skipped (Pascal NVENC HEVC is 8-bit only / poor quality).

## Gotchas

- **Takeout sidecars**: each media file has a `.json` or `.supplemental-metadata.json` sidecar. Google
  truncates long names and splits libraries across multiple `Takeout`/archive folders, producing
  duplicate copies — `ingest_takeout.py` pairs sidecars by stem and de-dupes by sha256.
  Folder names with **trailing spaces** (`Portraits `) fail on Windows — `extract_takeout.py` strips them.
- **GPS only comes from Takeout/EXIF**, never from any Photos API. Reverse-geocoding uses the Places
  API and is cached in `place_clusters`.
- **Derived files inherit the original's timestamp + GPS.** `metadata.copy_image_timestamp`
  re-injects EXIF DateTimeOriginal/CreateDate/Orientation/GPS into enhanced JPEGs, and video pipelines
  pass `-metadata creation_time=` to ffmpeg. Filesystem mtimes are mirrored too. Requires exiftool.
- **Dedup clustering is seeded, not chained** (`pipeline/dedup.py`). It used transitive union-find,
  so A≈B and B≈C grouped A with C — over 28k photos that chained into groups of 687. It now takes the
  best remaining photo as a seed and claims only what is similar *to that seed*. Layers also run
  strongest-first over the still-unassigned photos, because running them independently produced
  2,280 identical `phash`/`clip` group pairs. Repair old data with `cli dedup --cleanup --apply`;
  only a full `cli dedup` re-run rebuilds oversized groups properly.
- **Dates are surfaced for manual cleanup.** Duplicates UI, Delete Helper, and
  `delete_checklist_<id>.txt` all show capture date sorted, so originals are easy to find in GP by date.
- **`backend/data/` is gitignored** and can grow large. Originals live in the Takeout folder;
  catalog stores absolute paths.
- **PATH fix**: `app/_path_fix.py` injects known tool dirs at startup so exiftool/ffmpeg/Ollama are
  found even when uvicorn inherits a stale PATH from the launch terminal.
- **CORS**: `allow_origins=["*"]` (local-only app; needed for Chrome extension's HTTPS→HTTP fetch).
  `Access-Control-Allow-Private-Network: true` added for Chrome PNA restrictions.
- **Ollama model name**: `moondream` (not `moondream2` — renamed). Set `CAPTION_MODEL=moondream`.
- **Node version**: must be ≥ 20.19 for `chrome-devtools-mcp`. Use `nvm use 24.13.0`.
- **Vite proxy**: uses `127.0.0.1:8077` not `localhost` (IPv6/IPv4 mismatch on Windows).
- **React useEffect**: `useEffect(load, [])` is a React bug when `load` returns a Promise.
  Always use `useEffect(() => { load(); }, [])`.
- **exiftool location**: `%USERPROFILE%\Documents\tools\exiftool.exe` (added to user PATH).
- **Sleep during long jobs**: `jobs.py` calls `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)`
  inside each worker thread so Windows won't sleep mid-job (display may still turn off). Per-thread, so
  it ref-counts across concurrent jobs and auto-releases when the last one ends. No-op on non-Windows.
- **Library stats** (June 2026): 27,859 photos · 3,696 videos · 11,579 geo-tagged · 8,530 dup groups.

## Chrome Extension (`gp-extension/`) — v2

How to use it: [gp-extension/GUIDE.md](gp-extension/GUIDE.md). Install + troubleshooting:
[gp-extension/INSTALL.md](gp-extension/INSTALL.md). Highlights and traps only here.

- **Install**: `chrome://extensions` → Developer mode → Load unpacked → `gp-extension/`.
  Requires the backend on `http://localhost:8077` **and** an open, signed-in `photos.google.com` tab —
  every Google call rides that tab's own session.
- **Layout**: `app/` tab page (Sync + Duplicates tabs) · `background/worker.js` executor ·
  `content/` bridge + injector · `main/` MAIN-world command handler · `panel/` in-grid overlay ·
  `vendor/gptk/` pinned third-party API layer. No build step; plain ES modules.
- **Why MAIN-world injection**: `gptkApi` and the batchexecute tokens (`WIZ_global_data.SNlM0e`) only
  exist in the page's own JS context. Content scripts run in an isolated world and cannot see them,
  so `content/inject.js` injects real `<script>` tags. **Injection order is load-bearing** — the shim
  must define `unsafeWindow` before GPTK, and GPTK (~190 KB) must finish parsing before
  `main/commands.js`, so they are chained on `onload`, not injected together.
- **`dedupKey` vs `mediaKey`**: every mutation takes `dedupKey` (content identity). `mediaKey` is only
  good for URLs and album membership. The same bytes surfaced through a shared album have different
  `mediaKey`s but one `dedupKey`, so payloads are collapsed by `dedup_key` before anything is sent.
- **Scanning is two-stage and both stages are mandatory**: `getItemsByTakenDate` returns no
  `fileName` and no `size` — only `getBatchMediaInfo` does. Without `fileName` the matcher loses its
  three strongest passes.
- **Tile identification is by `mediaKey` from the tile `href`** (`/photo/<mediaKey>`), not the
  `aria-label`. The v1 extension parsed `"Photo - Portrait - Jun 3, 2026, 8:14:07 AM"` and matched it
  against a timestamp regex-extracted from the filename, which broke on any non-English UI, on any
  timezone skew, and silently skipped files whose names lacked seconds.
- **Never use inline `onclick`** in injected markup: the attribute evaluates in the *page's* main
  world (where content-script functions don't exist) and GP's CSP blocks it anyway. Four buttons in
  v1 were dead for exactly this reason. Always `addEventListener`.
- **The overlay's MutationObserver must be disconnected while highlighting** — the badges it appends
  are themselves mutations under `document.body`, so it otherwise re-arms forever on a page as
  mutation-heavy as Google Photos.
- **Images**: local thumbnails go through the service worker (`GET_IMAGE`) to dodge the HTTPS→HTTP
  mixed-content block; Google's own HTTPS `thumb_url` is preferred where available (append
  `=w256-h256-k-no` to size it and drop the auth requirement).
- **After updates**: reload via `chrome://extensions` → ↻, **and reload any open photos.google.com
  tab** — existing tabs keep running the old content script.
- **`batchexecute` is undocumented and Google can change it without notice.** GPTK is pinned to a
  release for that reason, and `healthCheck` (reads `WIZ_global_data`) is the cheap canary. After any
  GPTK upgrade, re-run a dry run before trusting a real one.

## Env (`backend/.env`, see `.env.example`)

`TAKEOUT_DIR`, `LIBRARY_DIR`, `DATA_DIR`, `OLLAMA_HOST`, `CAPTION_MODEL` (=`moondream`),
`GOOGLE_PLACES_API_KEY` (read-only place search — see `docs/USAGE.md` §15 for setup),
and optional `PHOTOS_OAUTH_CLIENT_*` (only for `appendonly` uploads to a new album).
Absent keys degrade gracefully (Maps Studio drafts offline; upload disabled).
