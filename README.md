# Photo Curator — find and delete duplicate photos in Google Photos

**A free, private, open-source tool that finds duplicate photos in your Google Photos library and
actually deletes them — in bulk, with one-click undo. Runs entirely on your own computer.**

Google Photos has no "find duplicates" button, and its API cannot delete your photos. Photo Curator
solves both: it compares your **original files** (not compressed thumbnails) to find real duplicates,
then moves them to the Google Photos bin through a Chrome extension. Nothing is uploaded to a server.
No subscription. No account to create.

> **Deleted 43,000 photos by accident?** You can't. Everything goes to the Google Photos **bin**,
> recoverable for 60 days, and every run has an **Undo** button.

---

## What it does

| | |
|---|---|
| 🔍 **Finds duplicates properly** | Three passes: identical files (SHA-256), resized/re-compressed copies (perceptual hash), and near-identical burst shots (CLIP AI). Works on your *original* files, so it catches duplicates that thumbnail-based tools miss. |
| 🧠 **Picks the best copy for you** | Scores each photo on sharpness, face clarity, whether the subject is centred, resolution and compression — and tells you *why* it chose one ("sharpest · best centred"). |
| 🗑️ **Deletes in bulk, safely** | Sends duplicates to the Google Photos bin. Resumable, undoable, and it refuses to delete the last remaining copy of anything. |
| 💾 **Frees disk space** | Moves redundant local copies to a quarantine folder — never deletes, always reversible. |
| ✨ **Fixes blurry photos** | Upscales and restores them locally (Real-ESRGAN + GFPGAN), then can retire the blurry original. |
| 📍 **Drafts Google Maps reviews** | Groups your geo-tagged photos by place and writes a review from what's in them. You post it. |
| 🎬 **Shrinks and highlights videos** | GPU compression to 1080p, plus automatic highlight reels from your clips. |

**Everything runs on your machine.** Your photos, faces and locations never leave it.

---

## Is this for you?

✅ You have thousands of photos in Google Photos and lots are duplicates
✅ You want the duplicates **gone**, not just listed
✅ You'd rather not upload your family photos to a stranger's server
✅ You have a Windows PC (an NVIDIA GPU helps but isn't required for de-duplication)

❌ You want a phone app — this is a desktop tool
❌ You want something that works without any setup — budget about 30 minutes

---

## Setup for non-technical users

You'll copy and paste a few commands. You don't need to understand them. **About 30 minutes**, most
of it waiting for downloads.

### Step 1 — Install two free programs

1. **Python** — <https://www.python.org/downloads/>
   Click the big yellow download button. **When installing, tick "Add Python to PATH"** on the first
   screen. This matters; if you miss it, nothing else will work.
2. **Node.js** — <https://nodejs.org/> → click the **LTS** button, accept all defaults.

### Step 2 — Download Photo Curator

On this page click the green **Code** button → **Download ZIP**. Unzip it somewhere simple like
`C:\photo-curator`.

*(If you know what git is: `git clone https://github.com/YOUR-USERNAME/photo-curator.git`)*

### Step 3 — Get your photos out of Google

Google won't let any app read your whole library any more, so you export it once:

1. Go to <https://takeout.google.com>
2. Click **Deselect all**, then scroll down and tick **Google Photos** only
3. Choose **.zip** and the largest size option, then **Create export**
4. Google emails you when it's ready — this can take hours or a day for a big library
5. Download the files and unzip them all into one folder, e.g. `D:\Takeout`

### Step 4 — Run the setup script

Open the `photo-curator` folder, right-click in some empty space, and choose
**"Open in Terminal"** (or "Open PowerShell window here"). Paste this and press Enter:

```powershell
.\scripts\setup-models.ps1
```

This creates a private Python environment and downloads the AI models. It takes a while and prints a
lot — that's normal. If it reports a missing tool, install it and run the script again.

### Step 5 — Point it at your photos

In the `backend` folder, copy `.env.example` to `.env`, open the copy in Notepad, and set the folder
you unzipped Takeout into:

```
TAKEOUT_DIR=D:\Takeout
```

Save and close. Everything else in that file is optional.

### Step 6 — Start it

```powershell
.\start.ps1
```

Two windows open. Then visit **<http://localhost:5177>** in your browser.

### Step 7 — Load the Chrome extension

This is the part that can actually delete photos from Google Photos.

1. In Chrome, go to `chrome://extensions`
2. Turn on **Developer mode** (top-right)
3. Click **Load unpacked** and select the `gp-extension` folder
4. Open <https://photos.google.com>, sign in, and **leave that tab open**
5. Click the Photo Curator icon in your Chrome toolbar

### Step 8 — Use it

In this order:

1. **Library Sync → Scan library** — connects your Google Photos to the catalogue. *Nothing works
   until this has run.*
2. Build the duplicate list (paste into the terminal):
   ```powershell
   cd backend ; .\.venv\Scripts\python -m app.cli ingest ; .\.venv\Scripts\python -m app.cli dedup
   ```
3. **Duplicates tab** → tick **Dry run** and press **Trash duplicates** once. Nothing is deleted; it
   just proves everything is connected.
4. Untick Dry run and do it for real. Start with one group.

📖 **[Full walkthrough with screenshots →](gp-extension/GUIDE.md)**

---

## Is it safe?

This is your photo library, so the rules are strict and enforced in code:

- **Nothing is ever permanently deleted.** Only "move to bin", which Google keeps for 60 days. The
  code has an allow-list that makes permanent deletion impossible.
- **Every run is undoable** with one click, restoring exactly what succeeded.
- **Your local original files are never touched** — they're only ever read.
- **It won't delete your last copy.** If a group's keeper is already gone from Google Photos, the
  whole group is blocked.
- **Dry run first**, by default, until you've completed one.
- **Nothing is uploaded or deleted without your approval.**

Your photos are never sent anywhere. All AI runs locally.

---

## Frequently asked questions

**Do I need a good graphics card?**
No for finding duplicates. A GPU (NVIDIA, 8 GB) makes the AI passes much faster — a GTX 1070 handles
a 28,000-photo library comfortably.

**Will this delete photos I want to keep?**
It picks a keeper in each group and only bins the rest, showing you which is which before you
confirm. You can change the keeper, keep several, or skip a group. And everything is undoable.

**Why do I need the Google Takeout export?**
Google's API can't read your full library or delete anything (they removed that access in March
2025). Takeout gives the original files, which is also what makes duplicate detection accurate —
Google only serves shrunken thumbnails.

**Is this against Google's terms?**
The extension drives Google Photos' own web interface from your signed-in browser — the same actions
you could do by hand, just faster. It's your account and your photos. It uses an undocumented
internal endpoint, so Google could change it at any time and break the tool.

**Does it work on Mac or Linux?**
The core is Python and runs anywhere, but the helper scripts are Windows PowerShell. Mac/Linux users
will need to run the commands manually.

**How long does it take?**
Setup ~30 min. Scanning a 28,000-photo library: a few minutes for the sync, ~2 hours for full AI
analysis on a GPU. Deleting is about 100 photos a minute.

---

## For developers

<details>
<summary>Architecture, API and pipelines</summary>

**Stack:** Python 3.11 · FastAPI · SQLAlchemy + SQLite · React + Vite + TypeScript · Chrome MV3
extension (no build step) · PyTorch / open-clip / InsightFace / Real-ESRGAN · ffmpeg.

```
backend/     FastAPI app, pipelines, SQLite catalog
frontend/    React review UI (port 5177)
gp-extension/  Chrome MV3 extension — the only thing that can act on Google Photos
docs/        Usage notes
```

**How it deletes**, given the Library API cannot: the extension injects a script into
`photos.google.com` and calls Google's internal `batchexecute` RPC endpoint using the page's own
session tokens. Mutations key on `dedupKey` (content identity), never `mediaKey`.

**Resumability:** batch progress lives in SQLite, not the browser. The extension asks the backend for
the next slice, executes it, and reports back — so closing Chrome mid-run costs at most one batch.

Key docs: [CLAUDE.md](CLAUDE.md) (architecture + safety invariants) ·
[gp-extension/GUIDE.md](gp-extension/GUIDE.md) (usage) ·
[gp-extension/INSTALL.md](gp-extension/INSTALL.md) (install + troubleshooting) ·
[docs/USAGE.md](docs/USAGE.md).

</details>

---

## Credits and licence

MIT — see [LICENSE](LICENSE).

Builds on [Google-Photos-Toolkit](https://github.com/xob0t/Google-Photos-Toolkit) by **xob0t** (MIT),
vendored in `gp-extension/vendor/gptk/`, which encodes the request shapes for Google Photos' internal
API. The approach of driving that API from an extension was pioneered by
[google-photos-deduper](https://github.com/mtalcott/google-photos-deduper) by **mtalcott**.

Also uses: OpenCLIP · InsightFace · Real-ESRGAN · GFPGAN · Florence-2 · Ollama · PySceneDetect ·
ffmpeg.

---

<sub>Keywords: google photos duplicate finder, delete duplicate photos google photos, remove
duplicates from google photos, google photos cleanup tool, find duplicate photos free, google photos
deduplicate, bulk delete google photos, google takeout duplicate remover, offline photo organizer,
local AI photo management, free duplicate photo remover windows.</sub>
