> Historical upstream guide. For this cleanup fork, follow [the repository README](../README.md). Takeout and legacy model setup are not required.

# Photo Curator — Google Photos Bridge

A Chrome extension that lets Photo Curator's local analysis **act on** your Google Photos library:
sync the live library into the catalog, then trash duplicates in bulk with progress, resume and undo.

> **Learning the tool?** → **[GUIDE.md](GUIDE.md)** — what it does, why, and a safe first session.
> This file is install + troubleshooting only.

## Install (one time)

1. Open `chrome://extensions`
2. Turn on **Developer mode** (top-right)
3. **Load unpacked** → select this folder
   (`C:\photo-curator\gp-extension`)
4. Pin the extension so its toolbar icon is visible

## Run

```powershell
cd "C:\photo-curator" ; .\start.ps1
```

Then open **https://photos.google.com**, sign in, and leave the tab open — every Google call is made
through it using your own session. Click the toolbar icon to open the app page; the status dot should
read **Connected · /u/0**.

First thing to do is **Library Sync → Scan library**. Nothing else works until that has run; see
[GUIDE.md §1](GUIDE.md) for why.

## After changing code here

Hit the **↻ reload** icon on `chrome://extensions`, **and reload any open photos.google.com tab**.
An already-open tab keeps running the old content script — this is the single most common cause of
"it stopped working".

## Layout

| Path | Role |
|---|---|
| `app/` | The tab page — Sync and Duplicates tabs |
| `background/worker.js` | Executor: batching, pacing, retries, backoff |
| `content/` | `bridge.js` (isolated world relay) + `inject.js` (MAIN-world injector) |
| `main/` | Runs in the page's own JS context; the only code that calls Google |
| `panel/` | The read-only in-grid overlay |
| `lib/` | Backend client + DOM helpers |
| `vendor/gptk/` | Pinned third-party API layer (MIT) — see its `VERSION.md` |

No build step. Plain ES modules, loaded unpacked as-is.

## Troubleshooting

| Symptom | Cause |
|---|---|
| "Backend offline" | `.\start.ps1` isn't running, or isn't on port 8077 |
| "No Google Photos tab" | Open photos.google.com and leave it open |
| "Toolkit not loaded" | Tab was open before the extension loaded — **reload the tab** |
| "Photos tab not signed in" | No `WIZ_global_data.SNlM0e` on that tab; sign in again |
| "Could not reach the Google Photos tab" | Tab predates the last extension reload; reload it |
| Group shows "already done" / "keeper gone" | Normal — those copies were deleted from Google Photos previously. Use **Clean up whole backlog** ([GUIDE §4](GUIDE.md)) |
| A tile says "not in Google Photos" | The photo isn't there any more. Nothing to link; not fixable by re-matching |
| Backend changes seem to do nothing | A stale `uvicorn --reload` child can survive its parent and keep serving old code. Check `netstat -ano \| findstr :8077`, kill the PID, restart |
| Operation paused | Expected on failure — read the error, fix it, hit **Resume**. It restarts at the same slice; nothing is skipped |
| Nothing is outlined in the grid | The overlay only marks *linked* duplicates. Check coverage in Library Sync |
| Buttons in the overlay do nothing | Reload the tab. If it persists, check the page console — GP's CSP blocks inline handlers, so all listeners must be attached in JS |

Backend-side checks, without the browser:

```powershell
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli gp-status
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli gp-link --relink
```
