---
name: photo-dev
description: Start Photo Curator locally for development — runs the FastAPI backend (uvicorn, port 8077) and the React/Vite frontend (port 5177) together, and covers loading/reloading the Chrome extension. Use to develop or test the web review interface or the extension.
---

Start the Photo Curator backend and frontend for local development. They run as two processes.

## Steps

1. Start the backend (FastAPI + SQLite) with autoreload:

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8077
   ```

2. In a second terminal, start the frontend (Vite dev server, proxies `/api` → backend):

   ```powershell
   cd "C:\photo-curator\frontend" ; npm run dev
   ```

3. Open the UI at **http://localhost:5177**. API docs (Swagger) are at
   **http://localhost:8077/docs**.

## Chrome extension

The extension (`gp-extension/`) is plain ES modules with **no build step** — load it unpacked.

1. `chrome://extensions` → Developer mode → **Load unpacked** → `gp-extension/`
2. Open **photos.google.com** and sign in; leave the tab open (every Google call rides its session)
3. Click the toolbar icon for the app page

**After changing extension code**: hit ↻ on `chrome://extensions` **and reload any open
photos.google.com tab**. An already-open tab keeps running the old content script — this is the most
common "my change did nothing".

## Notes

- Run `photo-setup` first if `backend/.venv` or `frontend/node_modules` is missing.
- The backend serves thumbnails and derived media from `backend/data/`. If the catalog is empty,
  run `photo-ingest` then `photo-analyze` first.
- Long-running pipelines started from the UI run in the backend's in-process job runner; watch the
  uvicorn console or the Review Queue page for progress.
- **`uvicorn --reload` can leave an orphan.** If the parent dies, a `multiprocessing.spawn` child can
  keep holding port 8077 and serving *old* code, making backend edits look like no-ops. Diagnose with
  `netstat -ano | findstr :8077`, then `Stop-Process -Id <pid> -Force`.
- Debugging the extension: service worker logs via `chrome://extensions` → *service worker*; app page
  and content-script logs in the page's own DevTools console.

## Rules

- Do not run pipelines that mutate Google Photos from this skill — `photo-dev` only starts processes.
