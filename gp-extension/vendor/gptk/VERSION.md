# Vendored: Google Photos Toolkit (GPTK)

| | |
|---|---|
| Upstream | https://github.com/xob0t/Google-Photos-Toolkit |
| Version | **v3.2.0** |
| Asset | `google_photos_toolkit.user.js` from the v3.2.0 GitHub release |
| Retrieved | 2026-07-27 |
| License | MIT — see `LICENSE` (Copyright (c) 2024 xob0t) |

## Why this is vendored rather than reimplemented

Google Photos has no public API that can delete, trash, or modify a user's existing media — the
Library API was cut back to app-created data only on 2025-03-31 and has never supported deletion.
The only mechanism that works is Google Photos' own **undocumented internal `batchexecute` RPC
endpoint**, driven from the signed-in page. GPTK encodes the request payload shape for each `rpcid`,
which is the fiddly, entirely-undocumented part. Reimplementing it would mean owning every breakage
when Google reshuffles an array index.

We pin an exact release rather than tracking `main` so an upstream change can never silently alter
what our trash operation sends.

## How it is loaded

`content/inject.js` injects, into the page's **MAIN world**, in this order:

1. `main/gptk-shim.js` — userscript-manager stubs (`unsafeWindow`, `GM_registerMenuCommand`)
2. `vendor/gptk/google-photos-toolkit.user.js` — exposes `window.gptkApi` / `gptkCore` / `gptkApiUtils`
3. `main/commands.js` — our command handler, the only thing that calls into GPTK

GPTK also injects its own UI (`#gptk`, `#gptk-button`). We drive it programmatically, so both are
hidden by `panel/panel.css` to avoid two competing toolbars on the page.

## What we actually call

| Our command | GPTK call | rpcid |
|---|---|---|
| `scanPage` | `gptkApi.getItemsByTakenDate(timestamp, source, pageId, 500)` | `lcxiM` |
| `mediaInfo` | `gptkApi.getBatchMediaInfo(mediaKeys)` | `EWgK9e` |
| `trash` | `gptkApi.moveItemsToTrash(dedupKeys)` | `XwAOJf` |
| `restore` | `gptkApi.restoreFromTrash(dedupKeys)` | `XwAOJf` |

`getItemsByTakenDate` does **not** return `fileName` or `size` — only `getBatchMediaInfo` does, keyed
by `mediaKey`. Our scan is therefore two-stage, and the second stage is mandatory: without
`fileName` the catalog matcher loses its three strongest passes.

Mutations take **`dedupKey`** (content identity), never `mediaKey` (per-item id). The same bytes
surfaced through a shared album carry different `mediaKey`s but one `dedupKey`.

## Upgrading

1. Download the new release asset over `google-photos-toolkit.user.js`.
2. Re-check that `unsafeWindow.gptkApi` is still the exported global and that the four calls above
   keep their signatures (`main/commands.js` is the only caller).
3. Update the version/date in this file.
4. Re-run a **dry run** trash before trusting a real one.
