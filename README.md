# Photo Curator — local Google Photos cleanup

A cleanup-focused fork of [shivarya/photo-curator](https://github.com/shivarya/photo-curator).
Scan Google Photos directly, cache small previews, find duplicate/near-duplicate candidates locally,
review the keeper, rehearse with a dry run, and optionally move approved photos to Trash with Undo.
**No Google Takeout export, Google OAuth app, GPU, paid extension, or cloud AI service is required.**

This is an experimental personal tool using **undocumented Google Photos web APIs**. The adapter is
derived from the repository's pinned Google Photos Toolkit v3.2.0 integration. Google can change or
block these APIs without notice. Automated tests use synthetic data; this fork has not been validated
against a real signed-in library. Start with a small scan and visually inspect the results.

## Setup (macOS, Linux, Windows)

Use **Python 3.12**, current Chrome/Chromium, and optionally **Node 22.12+** for the React UI.
The extension has no build step and includes the entire scan/review/undo workflow; the React UI is optional.

1. Clone your fork:

   ```sh
   git clone https://github.com/netkab/photo-curator.git
   cd photo-curator/backend
   python3.12 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements-cleanup.txt
   ```

   On Windows use `py -3.12 -m venv .venv`, then `.\.venv\Scripts\Activate.ps1` instead.
   The cleanup requirements pin the tested macOS dependencies, including transitive packages. Other platforms may resolve additional platform-specific packages. Artifact hashes are not yet included.
   Do not run the legacy GPU/model setup script for this workflow.

2. Open `chrome://extensions`, turn on Developer mode, choose **Load unpacked**, and select
   the repository's **gp-extension** directory. Copy its extension ID.

3. From the activated backend environment, allow that specific extension and display the local token:

   ```sh
   python -m app.setup --extension-id YOUR_32_CHARACTER_EXTENSION_ID
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8077
   ```

   Run **one backend process / one uvicorn worker**. Keep it bound to loopback; never expose it on
   a LAN or through a tunnel. Setup stores the allowed ID in `backend/.env`. The token is generated
   in `backend/data/.local-token` (owner read/write on POSIX) and is ignored by Git. Protect the data
   directory with your OS account permissions on Windows. `python -m app.setup` displays it again.

4. Click the Photo Curator extension icon. In **Setup**, paste the token and click **Pair and test
   connection**. Open exactly one signed-in `photos.google.com` tab. The extension's token stays
   in trusted extension storage in this Chrome profile; it is not exposed to the Google page.

If Chrome requests permission to access the local network, allow it only for this extension.
No wildcard CORS or Private Network Access response headers are used. A new unpacked extension ID
requires updating the allowed ID and restarting the backend.

### Optional React UI

In a separate terminal:

```sh
cd photo-curator/frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5177`, paste the same local token, and connect. The UI receives a local
HttpOnly, SameSite=Strict session cookie, valid for 12 hours or until backend restart. API and cached
thumbnails are authenticated. Use the extension to scan and execute queued operations.
`start.ps1` starts both local servers on Windows after dependencies are installed; it refuses occupied
ports and never kills existing Python/Node processes.

## Use the cleanup workflow

1. **Library Sync → Scan / resume library.** The extension reads library + archived-item metadata
   in pages of up to 500 and saves a durable cursor with each page. Stop at any time. Resume continues
   from the last committed page. **Rescan from newest** restarts enumeration without erasing the catalog.
2. **Fetch thumbnails and find duplicates.** Keep the signed-in Photos tab open: the extension
   downloads up to 512-pixel previews through that tab and passes image bytes to the local backend,
   never original photos or videos. Google cookies stay in Chrome. Cached items are skipped on later
   runs. Keep Photo Curator open too: it requests small batches to avoid Chrome's worker time limits.
   Five consecutive download failures stop the attempt with the actual error; partial progress
   is saved. Animated previews are excluded for manual review. Multi-picture JPEG (MPO) previews
   use their primary still image; embedded auxiliary images are not treated as animation. Stop is cooperative after the current
   request. Rescan only to refresh expired URLs.
   **Only photos I own** is checked by default: shared and unknown ownership are excluded before
   downloads, embeddings and matching, including already-cached previews. Unchecking it includes
   those photos in analysis but does not bypass the ownership requirement for trash approval.
3. **Duplicates.** Compare each proposed group and keeper, open originals in Google Photos when useful,
   pick another keeper, or mark additional photos **Keep too**. Dismiss unrelated groups. Each new
   group has a suggested keeper based on reported original dimensions, then thumbnail sharpness.
   If an older pending review contains non-owned photos, **Review queue → Keep photos I don’t own**
   removes them from the trash selection and protects their content keys. The review remains pending
   until you explicitly approve its revised selection; no operation is created by this edit.
4. **Dry run is always the initial choice.** It exercises account checks, batching and durable result
   accounting without issuing a mutation. A successful dry run does not disable dry-run mode and does
   not consume the group's review state.
5. **Live trash is disabled by the backend by default.** Once you have inspected the results, set
   `PC_ENABLE_LIVE_TRASH=1` in `backend/.env` and restart. Then explicitly uncheck Dry run and confirm
   the selected photos in the extension. Approval snapshots the account, selected content keys, and
   protected keeper keys. An operation cannot reuse that approval for different photos.
6. **Review queue** preserves proposed/approved selections if a step fails or you close a tab. You can
   inspect, approve, dismiss, or queue a dry run there. **Duplicates → operation history** shows pending,
   running, paused and finished operations. Start/Resume requires an explicit click; browser startup
   never resumes trash automatically. Each mutation batch has at most 25 keys with a 5-second pause.
7. **Undo** creates a restore operation from the recorded successful keys. Stop an active operation
   first. Undo remains blocked while a batch has an unknown/in-flight outcome. A tab closure or lost
   response may require waiting 150 seconds for the lease, then explicitly resuming that idempotent
   batch so its result can be recorded. Stop prevents future batches; an already-issued batch may finish.

**There is no permanent-delete or empty-trash operation.** Undo only works while Google still retains
those items in Trash; Google may expire them, and manually emptying Trash makes restoration impossible.
The API's batch response does not prove individual per-item outcomes, so inspect Google Photos after
real trash/restore runs. This is not a backup service.

## What analysis means

- Default: 64-bit perceptual hashes on locally decoded thumbnails, Hamming distance <= 4, compatible
  aspect ratios, and conservative seed-based groups of at most 25. Five-part hash indexing avoids
  a full pairwise library comparison. Each candidate is similar to the keeper; similarities do not
  chain unrelated photos into a giant group.
- Matching or even byte-identical thumbnails **do not prove that original files are identical**.
  Preview compression can conceal detail, edits, face expressions and resolution differences.
  Unknown ownership/shared items are blocked from live trash, and equal Google content keys are
  not treated as separate removable copies. Reported dimensions and file sizes can be missing.
- The catalog stores a namespaced remote-identity digest in the legacy `sha256` column for direct
  items (`source=google-photos-thumbnail`); it is not an original-file hash. `thumbnail_sha256`
  is a separate digest of the normalized cached preview. Identical previews keep separate catalog rows.
- Videos are cataloged but excluded from duplicate detection. Animated/unreadable/low-information
  previews are skipped. No face recognition, originals-based quality scoring, GPS enrichment or
  storage-savings guarantee is provided by this workflow.
- One Google account per data directory. The stable `WIZ_global_data.oPEP7c` identity is checked on
  every scan and mutation. The mutable `/u/0` slot is not an account identity. If that field becomes
  unavailable, the extension stops. Use a fresh `DATA_DIR` for another account or a pre-fork catalog
  containing legacy `/u/N` records; legacy operations must not be trusted or silently migrated.
- A scan is not an atomic snapshot. Missing items are never automatically considered deleted, and
  the catalog can be stale after changes made outside this tool. Recheck keepers in Google Photos
  immediately before a real run. Google changes or expiring cursors may require a fresh scan.
- Cached previews, filenames, account identity and optional embeddings stay on disk unencrypted.
  Preview URLs can grant access to private images: do not share the catalog, logs or data directory.
  Downloads go only to allowlisted `lh<number>.googleusercontent.com` and
  `photos.fife.usercontent.google.com` hosts over HTTPS, without redirects. The extension downloads
  in the Google Photos page using its existing browser session; Google cookies are never copied to
  the backend, and the local pairing token never enters the Google page. The backend's optional
  direct downloader uses no cookies or environment proxies and cannot fetch session-protected images.
  URL expiry or Google API/CORS changes can prevent downloads; they remain cataloged for retry.

### Optional local CLIP embeddings

The default pHash workflow is sufficient to start. For additional nearby-burst candidates, install
`requirements-clip.txt` in the backend environment and supply a trusted **local OpenCLIP ViT-B-32**
checkpoint using `PC_CLIP_CHECKPOINT` in `.env`. Enable the checkbox in Library Sync. The application
never downloads weights automatically. The first optional run can be slow on CPU; embeddings are
cached in SQLite. Restart with a fresh embedding cache if you change model weights.

CLIP candidates require cosine similarity >= 0.96, compatible aspect ratio, and capture times within
120 seconds. Semantic similarity is weak evidence: every group still requires visual review.
The optional model package is pinned, but its platform-specific Torch dependencies are not fully
locked. Actual model inference is not part of the automated smoke tests; it needs separate validation
on your hardware. No GPU or CLIP installation is needed for pHash matching.

## Tests and build

```sh
cd backend
python -m pip install -r requirements-test.txt
python -m pytest -q
cd ..
node --test gp-extension/tests/*.test.mjs
cd frontend
npm ci
npm run build
npm audit
```

Backend tests cover authentication/CORS/CSRF, persistent scans, account isolation, approval binding,
keeper aliases, unknown ownership, dry-run defaults, batch leases, result validation, cancellation,
Undo, thumbnail limits and resumable analysis. JavaScript tests check the pinned RPC shapes, metadata
parser fixtures, dry-run short-circuits, account changes and rejected commands. These are synthetic
regression tests, not evidence that today's Google internal API works on your account.
See [validation results](docs/VALIDATION.md), including the inactive CI template and remaining manual checks.

## Scope and upstream code

The existing SQLite catalog, duplicate review UI, keeper selection and durable operation history are
preserved where useful. Maps reviews, enhancement, uploads, video creation/compression, local-file
reclaim, and manual/fuzzy Takeout linking are not mounted in the cleanup API or shown in its navigation.
Their legacy source remains for reference. The old full GPTK userscript and page-message bridge are
not loaded; a small adapter retains just the required request formats and licensing attribution.
See [SECURITY.md](SECURITY.md) for the trust boundary and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
for upstream credits. Existing legacy guides are historical and are not setup instructions for this fork.

### Likely accidents (extension 3.1.0)

After downloading previews in Library Sync, open **Likely accidents** and choose **Find likely accidents**. No CLIP model or Takeout is needed. This local pass considers only live photos marked as owned. It groups shots at most 12 seconds apart, capped at 90 seconds and 25 photos. A suggestion needs at least three dark, low-detail or possibly blurry previews making up 60% of the burst. Up to two neighboring photos on each side within five minutes provide context.

Nothing is preselected. Inspect the original in Google Photos, select unwanted burst photos, then send the selection to the Review queue. Unselected burst photos and nearby context are protected; at least one surviving photo is required. Approval, dry run, live-trash opt-in and Undo use the existing review flow. **Keep this burst** persistently hides that burst, including overlapping suggestions on later runs. No automatic trash occurs.

This is a conservative heuristic on reduced previews, not a judgment of original image quality: intentional night scenes, soft backgrounds, and abstract images can be flagged. Isolated blurry images and short bursts may be missed. Missing previews and timestamps are reported. Analysis can be stopped without replacing existing suggestions; restarting the backend requires starting the analysis again. Suggestions persist in the local catalog. Screenshots, temporary-photo classification, semantic similar scenes, and people detection are not part of this first release. Large-video cleanup remains in Google Photos.
