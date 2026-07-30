---
name: photo-setup
description: One-time environment setup for Photo Curator — create the Python venv, install backend + frontend dependencies, pull the Ollama vision model, pre-download model weights (Florence-2, InsightFace, Real-ESRGAN/GFPGAN), and verify the GPU, exiftool, and ffmpeg are available. Run before any other photo-curator skill.
---

Set up everything Photo Curator needs to run locally on the GTX 1070. Run this once (and again after
dependency changes).

## Steps

1. Run the setup script (creates `backend/.venv`, installs `requirements.txt`, runs `npm install` in
   `frontend/`, pulls the Ollama model, and runs tool checks):

   ```powershell
   cd "C:\photo-curator" ; .\scripts\setup-models.ps1
   ```

2. If the script reports a missing CLI tool, install it and re-run:
   - **Ollama** — https://ollama.com (then `ollama pull moondream`)
   - **exiftool** — https://exiftool.org (unzip, rename to `exiftool.exe`, add to PATH)
   - **ffmpeg/ffprobe** — https://ffmpeg.org (add `bin` to PATH)

3. Verify the environment manually if needed:

   ```powershell
   nvidia-smi
   ollama list
   exiftool -ver
   ffmpeg -version
   ```

4. Copy and edit env:

   ```powershell
   cd "C:\photo-curator\backend" ; Copy-Item .env.example .env
   ```

   Set `TAKEOUT_DIR` (where you unzipped Takeout). The app runs fine without any Google keys, but
   each one unlocks something:

   | Key | Unlocks | Without it |
   |---|---|---|
   | `GOOGLE_PLACES_API_KEY` | place matching in Maps Studio | clusters show raw coordinates |
   | `PHOTOS_OAUTH_CLIENT_ID` / `_SECRET` | automatic upload of enhanced/compressed files | files exported to a folder for manual upload, and `photo-enhance` can't retire originals |
   | `QUARANTINE_DIR` | destination for `photo-reclaim` | defaults to a sibling of `TAKEOUT_DIR` |

   OAuth setup is ~5 free minutes in the Google Cloud console — the walkthrough is inline in
   `.env.example`. The scope is `photoslibrary.appendonly` **only**, which by Google's design cannot
   read, edit or delete anything already in the library.

5. Load the Chrome extension (needed for anything that acts on Google Photos):
   `chrome://extensions` → Developer mode → **Load unpacked** → `gp-extension/`.
   No build step — it's plain ES modules. See `gp-extension/GUIDE.md`.

## Notes

- `QUARANTINE_DIR` should be on the **same drive** as `TAKEOUT_DIR` — a same-volume move is instant
  and atomic; crossing drives copies every byte and can half-finish.

- First analyze run downloads Florence-2 / InsightFace / Real-ESRGAN weights on demand into
  `backend/weights/` if not pre-fetched.
- GTX 1070 is Pascal (8 GB): the script installs the CUDA 11.8 PyTorch build. Models run fp16/int4.

## Rules

- Only create the venv and install declared dependencies. Do not upgrade global Python or system CUDA.
- Never write secrets into tracked files — only into `backend/.env` (gitignored).
