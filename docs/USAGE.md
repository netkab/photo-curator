# Photo Curator — Complete Usage Guide

This guide walks you through every step: getting your photos out of Google, setting up the app,
using each feature, and understanding what happens when you "apply" an action.

---

## Table of Contents

1. [How the app works — the big picture](#1-how-the-app-works)
2. [Creating a Google Takeout export](#2-creating-a-google-takeout-export)
3. [One-time setup](#3-one-time-setup)
4. [Running the app](#4-running-the-app)
5. [Step 1 — Ingest your Takeout](#5-step-1--ingest-your-takeout)
6. [Step 2 — Analyze (local AI pass)](#6-step-2--analyze-local-ai-pass)
7. [Catalog page — browse your library](#7-catalog-page--browse-your-library)
8. [Duplicates page — find and remove clutter](#8-duplicates-page--find-and-remove-clutter)
9. [Enhance page — fix blurry photos](#9-enhance-page--fix-blurry-photos)
10. [Maps Studio — draft place reviews](#10-maps-studio--draft-place-reviews)
11. [Videos page — compress and highlights](#11-videos-page--compress-and-highlights)
12. [Review Queue — the final gate](#12-review-queue--the-final-gate)
13. [Environment variables reference](#13-environment-variables-reference)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. How the app works

```
Google Photos (your library)
         │
         │  manual export (once every ~2 months)
         ▼
  Google Takeout ZIP/folder
         │
         │  photo-ingest  (reads files + JSON sidecars, extracts GPS, indexes)
         ▼
   SQLite Catalog  ◄──── photo-analyze (captions, OCR, faces, geo via local AI on GTX 1070)
         │
         ├─ Duplicates page  →  "delete" review-action  (YOU delete manually in Google Photos)
         ├─ Enhance page     →  "upload" review-action  (new file uploaded to a new album)
         ├─ Maps Studio      →  "review" review-action  (YOU paste + post in Google Maps)
         └─ Videos page      →  "upload" review-action  (compressed/highlight uploaded to album)
                                          │
                                          ▼
                                   Review Queue
                                 approve → apply
```

**Key rule:** Nothing happens automatically. Every proposed change sits in the Review Queue as
"pending" until you explicitly approve AND apply it.

**What the app CAN do automatically (after you approve):**
- Upload new derived files (enhanced photos, highlight reels) to a **dedicated new album** in
  Google Photos via the official API.

**What the app CANNOT do (by Google's API rules — not a design choice):**
- Delete your existing photos from Google Photos (the API simply won't allow it).
- Post a review to Google Maps (no such API exists).

For these two, the app prepares everything and you do the final action manually.

**Timestamps are preserved for manual uploads.** Because you'll upload enhanced photos, compressed
videos, and highlight reels yourself, every derived file carries the **same capture date, GPS, and
orientation as its original** (re-injected into the file's EXIF / video metadata). When you drag it
into Google Photos, it lands at the exact same spot in your timeline as the original — not at
"today". And for photos you need to delete, the app shows you each one's **exact date** so you can
jump to it in the Google Photos app and remove the original by hand.

---

## 2. Creating a Google Takeout export

Google Takeout is the only official way to get your full library including GPS coordinates.

### Step-by-step

1. Go to **https://takeout.google.com** (sign in with your Google account).

2. Click **"Deselect all"** at the top — you only want Google Photos, not your entire Google
   account.

3. Scroll down to **"Google Photos"** and check the box next to it.
   - Click **"All photo albums included"** to choose specific albums, or leave it as-is for
     everything.

4. Scroll to the bottom and click **"Next step"**.

5. Choose your delivery options:
   - **Delivery method:** "Send download link via email" is easiest.
   - **Frequency:** "Export once" (you can repeat this every ~2 months to pick up new photos).
   - **File type:** `.zip`
   - **File size:** `10 GB` or `50 GB` — larger = fewer files to download. Your library may split
     into multiple parts (e.g. `takeout-20250101-001.zip`, `takeout-20250101-002.zip`).

6. Click **"Create export"**. Google will email you a download link, usually within a few hours
   (large libraries can take 1–2 days).

7. Download all the ZIP files. Extract them all **into the same folder**, e.g. `D:\Takeout`.
   When you extract multiple parts they will all merge into one `Takeout/` folder structure.

### What's inside the Takeout folder

```
D:\Takeout\
  Google Photos\
    Photos from 2023\
      IMG_1234.jpg
      IMG_1234.jpg.json          ← GPS + timestamp + description for this photo
      IMG_5678.mp4
      IMG_5678.mp4.json
    Photos from 2024\
      ...
    Vacation Album\
      ...
```

Each media file has a companion `.json` sidecar file. That sidecar contains:
- **GPS coordinates** (latitude, longitude, altitude) — this is the only place Google stores them
  for API access.
- The **original timestamp** the photo was taken.
- Any **description** or title you added in Google Photos.

The ingest step reads these sidecars and merges everything into the catalog.

---

## 3. One-time setup

### Prerequisites (install before running setup)

| Tool | What for | Where to get it |
|------|----------|-----------------|
| **Python 3.11+** | Backend runtime | https://www.python.org/downloads/ |
| **Node 18+** | Frontend (Vite/React) | https://nodejs.org |
| **Ollama** | Runs the local vision model | https://ollama.com |
| **exiftool** | Reads EXIF/GPS from images | https://exiftool.org — download the Windows `.zip`, rename `exiftool(-k).exe` → `exiftool.exe`, put it in `C:\Windows` or any folder on PATH |
| **ffmpeg + ffprobe** | Video metadata, compression, highlight assembly | https://ffmpeg.org/download.html — download a Windows build (e.g. from gyan.dev), extract, add the `bin\` folder to PATH |
| **NVIDIA driver** | GPU acceleration for the AI models | https://www.nvidia.com/Download/index.aspx |

> **PATH check:** Open PowerShell and run:
> ```powershell
> exiftool -ver ; ffmpeg -version ; ffprobe -version ; ollama list
> ```
> All four should print version info. If any says "not recognized", it's not on PATH yet.

### Run the setup script

```powershell
cd "C:\photo-curator"
.\scripts\setup-models.ps1
```

This script:
1. Creates `backend\.venv` (Python virtual environment).
2. Installs Python dependencies — including `torch` with CUDA 11.8 for the GTX 1070.
3. Downloads `moondream` via Ollama (~3 GB — the vision/captioning model).
4. Installs the React frontend (`npm install`).
5. Copies `backend\.env.example` → `backend\.env`.
6. Runs a final tool check and prints an environment report.

It takes 10–20 minutes mainly due to the model download.

### Configure your paths

Open `photo-curator\backend\.env` in a text editor and set at minimum:

```
TAKEOUT_DIR=D:\Takeout
```

Optional but useful — Google Places for matching photo locations to real place names:
```
GOOGLE_PLACES_API_KEY=AIza...
```
*(Without this, Maps Studio still works — it shows raw coordinates instead of place names.)*

Optional — to enable direct upload to a Google Photos album:
```
PHOTOS_OAUTH_CLIENT_ID=...
PHOTOS_OAUTH_CLIENT_SECRET=...
```
*(Without these, "Apply upload" exports files to a folder instead. You can upload manually.)*

---

## 4. Running the app

Open **two PowerShell windows** (or two tabs in Windows Terminal):

**Window 1 — backend:**
```powershell
cd "C:\photo-curator\backend"
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8077
```

You should see:
```
INFO:     Started server process [...]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8077
```

**Window 2 — frontend:**
```powershell
cd "C:\photo-curator\frontend"
npm run dev
```

You should see:
```
  VITE v5.x  ready in ... ms
  ➜  Local:   http://localhost:5177/
```

Open **http://localhost:5177** in your browser.

### What you see on first load

The sidebar shows the tool status (bottom-left corner):
- **GPU:** should show "NVIDIA GeForce GTX 1070"
- **Ollama:** ✓ (means the Ollama server is running and moondream is available)
- **ffmpeg / exiftool:** ✓

If Ollama shows ✗, start it separately: open a third terminal and run `ollama serve`.

---

## 5. Step 1 — Ingest your Takeout

Go to the **Catalog** page. Click **"Ingest Takeout"**.

The button starts a background job. You can watch progress in the top-right of the button (the
percentage updates live). For a large library (10,000+ photos) this takes 5–30 minutes — most of
the time is computing file hashes and generating thumbnails.

**What happens during ingest:**
1. Walks every file in `TAKEOUT_DIR` and finds photos/videos.
2. For each file, finds its companion `.json` sidecar and reads GPS + timestamp + description.
3. Falls back to embedded EXIF if the sidecar has no GPS (older exports).
4. Runs `exiftool` to get camera make/model.
5. Runs `ffprobe` on videos to get codec, resolution, bitrate, duration.
6. Generates a 512×512 thumbnail (used by the UI throughout).
7. Computes sha256 (skips any file already in the catalog — ingest is incremental).
8. Writes a row to the SQLite catalog.

**After ingest**, the Catalog page will show your photo grid with dates and (if GPS is present)
place names.

> **Re-ingesting after a new Takeout export:** Just click "Ingest Takeout" again. Files already in
> the catalog (same sha256) are skipped. Only new/changed photos are added.

---

## 6. Step 2 — Analyze (local AI pass)

Still on the Catalog page, click **"Analyze (local AI)"**.

This is the heavy GPU work. It runs all AI stages in sequence:

| Stage | What it does | Model used | Notes |
|-------|-------------|------------|-------|
| **blur** | Scores every photo's sharpness (Laplacian variance) | OpenCV (CPU) | Fast; no GPU |
| **captions** | Writes a 1-sentence description + 3–6 tags | moondream (Ollama) | ~3–4 GB VRAM |
| **ocr** | Extracts any text visible in the photo | Florence-2 (GPU) | Signs, menus, screenshots |
| **faces** | Detects faces, embeds them, clusters into "people" | InsightFace (GPU) | Powers portrait detection |
| **geo** | Reverse-geocodes GPS → place names | Google Places API | Needs `GOOGLE_PLACES_API_KEY`; skips if absent |

For 200 photos on the GTX 1070, this takes roughly 15–45 minutes depending on GPU temperature.
The default limit is 200 photos per run to avoid thermal throttling — just click it again to
continue processing the rest.

**After analyze:**
- Every photo in the Catalog grid shows a caption below its thumbnail.
- Photos with faces are identified; you can name people on the People page (Catalog → People tab).
- Photos with GPS show a place name.
- Blurry photos are flagged (visible in the Enhance page).

---

## 7. Catalog page — browse your library

**Search bar:** Type any word to search across captions, OCR text, file names, and descriptions.
For example: `beach`, `birthday`, `Taj Mahal`, or any text visible in a sign in a photo.

**Filter dropdown:** Switch between "all", "photos", "videos".

**Stats row:** Shows total counts — photos, videos, geo-tagged, captioned, with text, people.

**Photo grid:** Click any photo to see its full detail:
- Full-size preview
- GPS coordinates (and place name if geocoded)
- Caption + tags
- OCR text (if any text was found)
- Face clusters (which "person" each detected face belongs to)
- Camera make/model, for videos: codec + bitrate + duration

**People panel** (sidebar link): Lists all detected face clusters. Click a cluster's name field to
type a person's name (e.g. "Ash", "Mom") — this name then appears throughout the app.

---

## 8. Duplicates page — find and remove clutter

Click **"Detect (with CLIP)"** (or "Detect (fast)" to skip the semantic layer).

Detection runs three layers:
1. **Exact** (sha256) — caught at ingest; not shown here.
2. **Perceptual hash** (pHash/dHash) — catches the same photo resized, recompressed, or slightly
   cropped. Fast.
3. **CLIP embeddings** — catches "burst" shots, slightly different angles of the same thing, nearly
   identical scenes. Slower, uses GPU.

**Using the Duplicates page:**

Each card shows a group of similar photos. The **green-outlined one is the auto-picked keeper**
(chosen by: highest resolution × sharpest × most faces). To change the keeper, click a different
photo in the group — it becomes the keeper.

When you're happy with the keeper choice, click **"Approve — keep best, queue N for deletion"**.

This creates a `delete` **review-action** in the Review Queue for the non-keepers. The photos are
**not deleted yet** — that happens only when you apply the action in the Review Queue.

Each photo tile in a group shows its **exact capture date** (📅) so you can identify it later.

**How deletion actually works** (when you apply):
- The app produces a plain-text **deletion checklist** saved in `backend/data/exports/`, sorted by
  date, listing each photo's **capture date/time**, place, and file name.
- In Google Photos (web or phone), navigate to each date shown and delete the photo manually.
- There is no API to do this automatically.

---

## 9. Enhance page — fix blurry photos

Click **"Enhance all blurry"**. The app:
1. Filters photos where `blur_score < 100` (the Laplacian threshold in `.env`).
2. Runs **Real-ESRGAN** (4× upscale, tiled at 256px to fit 8 GB VRAM) on each.
3. For photos with detected faces, additionally runs **GFPGAN** face restoration.
4. Writes a new `.jpg` to `backend/data/derived/enhanced_<id>.jpg`.
5. Presents a **before / after** comparison for each.
6. Copies the original's EXIF date, GPS, and orientation into the enhanced file.

The originals are never modified. The enhanced copy keeps the original's capture date and location,
so when you upload it to Google Photos it appears right next to the original in your timeline. The
Review Queue shows you the date it will keep ("keeps original date …").

**Review the results:** Each card shows the original thumbnail alongside the enhanced version. If
the enhancement looks good, click **"Approve enhanced version"** — this creates an `upload`
review-action. The enhanced photo will be uploaded to a dedicated Google Photos album when you apply
it in the Review Queue.

If an enhancement looks worse (sometimes happens with already-good photos that were just dim), just
ignore it — don't approve.

> Enhancement is GPU-heavy. Processing 10 photos at once is a reasonable batch to avoid thermal
> throttling. The "Enhance" button processes up to the blurry threshold; you can also select specific
> photos by ID from the CLI if you want more control.

---

## 10. Maps Studio — draft place reviews

**This is the separate review-drafting interface you asked for.**

### Step 1: Cluster your places

Click **"Cluster places"**. This:
- Reverse-geocodes every GPS-tagged, non-portrait photo against the Places API (or groups by raw
  coordinates if no API key).
- Groups photos into `PlaceClusters` — one cluster per distinct place.

### Step 2: Pick a place cluster

The grid shows all discovered places with a photo count and a preview strip. Click any place to
open the review drafting UI for that location.

### Step 3: Edit the place name (optional)

The text field at the top shows the matched place name from Google Places. You can edit it if it
was matched incorrectly.

### Step 4: Set your star rating

Choose 1–5 stars from the dropdown.

### Step 5: Draft the review

Click **"Draft review with AI"**. The app:
1. Gathers the captions of all photos taken at this place.
2. Gathers any OCR text from those photos (menus, signs, shop names).
3. Sends a prompt to the local moondream model asking it to write a 2–3 sentence review in your
   voice, grounded only in what the photos show.
4. Fills the text box with the draft.

You can edit the text freely — it's just a starting point.

### Step 6: Pick images to attach

The photo grid shows all images from this cluster. Click photos to toggle them selected
(green outline = selected). Select the 2–5 best shots that show the place well.

### Step 7: Stage the review package

Click **"Stage review package (N images)"**. This:
- Copies the selected photos to `backend/data/exports/<place-name>/`
- Writes a `review.txt` there with the place name, rating, and your review text.
- Creates a `review` action in the Review Queue.
- Shows you the export folder path.

### Step 8: Apply and post (Review Queue)

Go to the Review Queue page. Find the review action, click **"Open in Maps"** — this opens the
Google Maps review page for that place in your browser. You then:
1. Set the star rating manually.
2. Paste the review text from the `review.txt` file in the export folder.
3. Attach the exported images.
4. Submit in Google Maps.

Then click **"Apply"** in the Review Queue to mark it done.

---

## 11. Videos page — compress and highlights

### Compress

Click **"Compress all"**. For each video:
- Runs `ffmpeg` with `h264_nvenc` (NVIDIA GPU encoder) at 1080p and ~8 Mbps.
- If `h264_nvenc` is unavailable, falls back to CPU `libx264 -crf 20` (slower but higher quality).
- HEVC/h265 is intentionally skipped — Pascal NVENC HEVC is 8-bit only and produces poor quality.
- If the compressed file is **not smaller** than the original (e.g. already-compressed short clip),
  the output is discarded — no point keeping a bigger version.
- Stamps the original's capture time into the compressed file (`creation_time`), so a manual upload
  keeps its timeline position.
- Writes `compressed_<id>.mp4` to `data/derived/`.

The card shows you the original vs compressed size before you approve.

### Build highlights

Click **"Build highlights"**. For each cluster of videos (grouped by date + place):
1. **PySceneDetect** splits each clip into scenes (by content change).
2. A frame is sampled from the middle of each scene.
3. The frame is scored 0–10 by moondream ("how interesting is this frame for a highlights reel?").
4. The top 6 scenes (across all clips in the cluster) are kept, sorted into chronological order.
5. `ffmpeg` concatenates them into a single highlight reel MP4.
6. The reel is written to `data/derived/highlight_<hash>.mp4`.

The card shows you a video player so you can preview the reel before approving.

### Approving a video result

Click **"Approve"** on a compressed video or highlight reel → creates an `upload` review-action.
When applied, the file is uploaded to the dedicated Google Photos album (or exported to a folder if
OAuth isn't configured).

The original video is never deleted automatically.

---

## 12. Review Queue — the final gate

**Every proposed change in the app ends up here.** Nothing touches Google Photos until you act.

### Action types

| Kind | What "Apply" does |
|------|-------------------|
| **delete** | Writes a manual deletion checklist to `backend/data/exports/delete_checklist_<id>.txt`. You delete those photos yourself in Google Photos. |
| **upload** | If OAuth is configured: uploads the file to a dedicated album in Google Photos. Otherwise: copies the file to `backend/data/exports/to_upload/` for manual upload. |
| **review** | Returns the Maps review URL + export folder path. You open the URL and post manually. |
| **caption** | Marks the caption as "approved" in the catalog (used internally). |

### Workflow

1. Actions arrive as **"pending"** — this is the default from any page.
2. Review the details (the payload column shows what will happen).
3. Click **"Approve"** to mark it ready to apply — or **"Dismiss"** to cancel.
4. Click **"Apply"** (per item) or **"Apply all approved"** (batch) to execute.

### Filtering

Use the status buttons to filter: **pending** (needs review), **approved** (ready to apply),
**done** (already applied), **dismissed** (cancelled), **all** (everything).

### The "delete checklist" format

When you apply a `delete` action, the file written looks like:

```
Manually delete these from Google Photos (the API cannot delete your existing photos).
In the Google Photos app/web, navigate to the date shown to find each photo.
Reason: duplicate (phash)

[2023-07-14 09:21:03]  @ Goa Beach  IMG_1234.jpg
           D:\Takeout\Google Photos\Photos from 2023\IMG_1234.jpg
[2023-07-14 09:21:05]  @ Goa Beach  IMG_5678.jpg
           D:\Takeout\Google Photos\Photos from 2023\IMG_5678.jpg
```

Sorted by date, so you can scroll your Google Photos timeline to that day and delete each original.
The same per-photo date list is also shown in the Review Queue (expand "Show N photos to delete").

---

## 13. Environment variables reference

All in `photo-curator/backend/.env`:

| Variable | Required | Default | What it does |
|----------|----------|---------|-------------|
| `TAKEOUT_DIR` | Yes | `backend/data/takeout` | Path to your extracted Google Takeout folder |
| `LIBRARY_DIR` | No | same as TAKEOUT_DIR | If you copy originals to a separate working folder |
| `DATA_DIR` | No | `backend/data` | Where the catalog DB, thumbnails, derived files, exports live |
| `OLLAMA_HOST` | No | `http://127.0.0.1:11434` | Ollama server URL |
| `CAPTION_MODEL` | No | `moondream` | Which Ollama model to use for captions |
| `OCR_MODEL` | No | `microsoft/Florence-2-large` | HuggingFace model id for OCR |
| `GOOGLE_PLACES_API_KEY` | No | (blank) | Read-only place search for Maps Studio. See section 15 for setup. |
| `PHOTOS_OAUTH_CLIENT_ID` | No | (blank) | For direct upload to a new Google Photos album |
| `PHOTOS_OAUTH_CLIENT_SECRET` | No | (blank) | Same OAuth app as above |
| `PHOTOS_UPLOAD_ALBUM` | No | `Photo Curator (curated)` | Album name to upload new media into |
| `BLUR_THRESHOLD` | No | `100` | Laplacian variance below this = "blurry" (lower = stricter) |
| `VIDEO_TARGET_HEIGHT` | No | `1080` | Target height for video compression |
| `VIDEO_BITRATE` | No | `8M` | Target bitrate for video compression |

---

## 14. Troubleshooting

### "Ollama shows ✗ in the sidebar"

Ollama must be running separately. Open a terminal and run:
```powershell
ollama serve
```
Leave it running. Then refresh the browser.

If `moondream` isn't downloaded yet:
```powershell
ollama pull moondream
```

### "Ingest runs but shows 0 photos indexed"

- Confirm `TAKEOUT_DIR` in `.env` points to the right folder.
- The folder should contain a `Google Photos\` subfolder with year-folders or album folders inside.
- Check that the files inside are `.jpg`, `.mp4`, etc. (not still inside a `.zip`).

### "Analyze is very slow / GPU not being used"

Check:
```powershell
cd "C:\photo-curator\backend"
.\.venv\Scripts\python -m app.cli env
```

Look for `"gpu_available": true`. If it shows `false`:
- CUDA runtime may not be installed. Run: `.\.venv\Scripts\python -c "import torch; print(torch.cuda.is_available())"`
- If `False`, re-run `setup-models.ps1` — it installs the CUDA 11.8 torch build.
- If the GPU is detected but analyze is still slow, you may be hitting thermal throttling — 
  reduce `--limit` to 50 or 100 and let the GPU cool between runs.

### "Error: h264_nvenc not found" during video compression

The app automatically falls back to CPU `libx264`. Nothing breaks — compression just takes longer.
This happens if your `ffmpeg` build doesn't include NVIDIA support. Download the `full_build` from
gyan.dev which includes NVENC: https://www.gyan.dev/ffmpeg/builds/

### "Places shows raw coordinates instead of place names"

Set `GOOGLE_PLACES_API_KEY` in `.env`. Get a key:
1. Go to https://console.cloud.google.com
2. Create a project (or use an existing one).
3. Enable the **"Places API (New)"**.
4. Create an API key under "Credentials". Restrict it to "Places API" for safety.
5. Paste into `.env`.

The free tier gives 10,000 requests/month — more than enough for personal use.

### "Upload says 'exports to folder instead of uploading'"

The Photos OAuth credentials are not configured (`PHOTOS_OAUTH_CLIENT_ID/SECRET` in `.env`).
To set them up:
1. Go to https://console.cloud.google.com
2. Enable the **"Photos Library API"**.
3. Create an **OAuth 2.0 Client ID** of type "Desktop app".
4. Download the JSON, copy `client_id` and `client_secret` into `.env`.
5. Restart the backend.
6. The first time you apply an upload action, a browser window will open asking you to log in
   and grant permission (one-time, then the token is cached).

### Checking all errors from the terminal

The uvicorn terminal (backend window) shows all errors with full stack traces. If anything in the
UI behaves unexpectedly, check that window first.

---

## Quick reference — all CLI commands

If you prefer running pipelines from the terminal without the UI:

```powershell
# Always activate the venv and combine cd + command on one line
$be = "C:\photo-curator\backend"

# Check environment
cd $be ; .\.venv\Scripts\python -m app.cli env

# Ingest (--takeout overrides .env TAKEOUT_DIR)
cd $be ; .\.venv\Scripts\python -m app.cli ingest --takeout "D:\Takeout"

# Analyze — all stages, 100 at a time
cd $be ; .\.venv\Scripts\python -m app.cli analyze --limit 100

# Analyze — single stage only
cd $be ; .\.venv\Scripts\python -m app.cli analyze --only captions --limit 50
cd $be ; .\.venv\Scripts\python -m app.cli analyze --only faces

# Dedup — with CLIP (slower but catches burst shots)
cd $be ; .\.venv\Scripts\python -m app.cli dedup

# Dedup — fast (perceptual hash only)
cd $be ; .\.venv\Scripts\python -m app.cli dedup --no-clip

# Enhance — all blurry photos
cd $be ; .\.venv\Scripts\python -m app.cli enhance --blurry

# Enhance — specific catalog IDs
cd $be ; .\.venv\Scripts\python -m app.cli enhance --ids 42,107,388

# Maps clustering + reverse-geocoding
cd $be ; .\.venv\Scripts\python -m app.cli maps-cluster

# Video — compress all
cd $be ; .\.venv\Scripts\python -m app.cli video --compress

# Video — build highlights
cd $be ; .\.venv\Scripts\python -m app.cli video --highlights

# Both
cd $be ; .\.venv\Scripts\python -m app.cli video --compress --highlights
```

All commands print a JSON summary on completion.

---

## Typical first-use session (end to end)

```
Day 1:
  1. Export from takeout.google.com — wait for email (a few hours to 2 days)
  2. Download + extract all ZIPs to D:\Takeout

Day 2 (or whenever the export is ready):
  1. Run setup-models.ps1 (20 min, one time only)
  2. Start backend + frontend (two terminals)
  3. Open http://localhost:5177
  4. Catalog → "Ingest Takeout" (20–60 min for large libraries)
  5. Catalog → "Analyze (local AI)" limit 200 (30–60 min per batch)
     Repeat analyze until all photos are captioned.

Day 3 (curation):
  6. Duplicates → "Detect" → review groups → approve → check Review Queue
  7. Enhance → "Enhance all blurry" → review before/after → approve good ones
  8. Maps Studio → "Cluster places" → pick a place → draft → stage
  9. Videos → "Compress all" → review size savings → approve
 10. Videos → "Build highlights" → preview reels → approve
 11. Review Queue → "Apply all approved"
     - Delete checklist saved to exports/ — open Google Photos and delete
     - Uploads go to "Photo Curator (curated)" album in Google Photos
     - Review packages: click "Open in Maps" → post manually
```

---

## 15. Google Cloud Console setup — Places API (New)

This is needed for **Maps Studio** (reverse-geocoding GPS coordinates to place names, and searching
for places by name). Without it, Maps Studio still works but shows raw coordinates instead of place
names. The free tier (10,000 requests/month) is more than enough for personal use.

### Step 1 — Create a project (skip if you already have one)

1. Go to **https://console.cloud.google.com**
2. Click the project dropdown at the top → **"New Project"**
3. Name it (e.g. `photo-curator`) → **"Create"**
4. Wait a few seconds, then select the new project from the dropdown

### Step 2 — Enable Places API (New)

> **Important:** Enable "Places API (New)" — NOT the old "Places API". They are separate APIs with
> different endpoints and pricing. This app uses the new one exclusively.

1. In the left menu go to **"APIs & Services" → "Library"**
2. Search for **`Places API (New)`**
3. Click the result (it shows "Places API (New)" with Google Maps Platform branding)
4. Click **"Enable"**

### Step 3 — Create an API key

1. Go to **"APIs & Services" → "Credentials"**
2. Click **"+ Create Credentials" → "API key"**
3. The key is created — copy it (it looks like `AIzaSy...`)
4. Click **"Edit API key"** (pencil icon) to restrict it:

**Restrict the key (recommended):**
- Under **"API restrictions"** → select **"Restrict key"**
- Choose **"Places API (New)"** from the dropdown
- Click **"Save"**

This prevents the key from being used for anything else if it ever leaks.

### Step 4 — Add the key to your .env

Open `photo-curator/backend/.env` and set:

```
GOOGLE_PLACES_API_KEY=AIzaSy...your key here...
```

Restart the backend (`.\start.ps1`) — Maps Studio will now show place names.

### Step 5 — Verify it works

```powershell
cd "C:\photo-curator\backend"
.\.venv\Scripts\python -c "
from app.services.places import search_text
results = search_text('Eiffel Tower')
print(results[0] if results else 'No results — check your key')
"
```

You should see a dict with `place_id`, `name`, `address`, `rating`.

---

### What the app uses the key for

| Feature | API call | SKU | Cost |
|---------|----------|-----|------|
| Reverse-geocode a photo's GPS to a place name | `searchNearby` | Essentials | $0.017/req |
| Search for a place by name in Maps Studio | `searchText` | Essentials | $0.017/req |
| Fetch full place details for the review URL | `places/{id}` GET | Essentials | $0.017/req |

**All calls use field-masking** (`X-Goog-FieldMask`) to request only the cheapest fields (id,
displayName, formattedAddress, googleMapsUri, rating). This keeps every call in the Essentials SKU.
Opening-hours and editorial summaries are intentionally NOT requested (they trigger the Pro SKU at
$0.068/req).

**Estimated monthly cost for a personal library:**
- ~190 album clusters → ~190 reverse-geocode calls = $3.23
- Well within the $200/month free credit Google gives new accounts.
- After the free credit is used up: still within the 10,000 free requests/month tier.

### Billing safeguard

To prevent unexpected charges, set a budget alert:
1. In Google Cloud Console → **"Billing" → "Budgets & alerts"**
2. Create a budget of **$5/month** with an alert at 90%
3. You'll get an email if usage unexpectedly spikes
