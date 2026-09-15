# Validation — 2026-09-14

Tested locally on macOS ARM64 with Python 3.12 and Node 26.3.0.

| Check | Result |
|---|---|
| Backend regression suite | 42 passed |
| Extension RPC/manifest regression suite | 15 passed |
| TypeScript and Vite production build | Passed |
| Installed Python dependency compatibility (`pip check`) | Passed |
| Updated frontend dependency audit | 0 reported vulnerabilities |
| Isolated Chromium: extension pairing and account fixture | Passed |
| Synthetic library scan → persisted catalog → generated thumbnail analysis | Passed |
| Candidate review images and dry-run completion | Passed; 0 Google mutation requests |
| React pairing cookie and authenticated thumbnail display | Passed |
| Desktop/mobile catalog layout | Checked; mobile sidebar height fixed; no horizontal overflow |
| Browser JavaScript page errors in successful fixture run | 0 |

The browser exercised a separate Chromium profile and a separate synthetic SQLite catalog. All
Google Photos requests were fulfilled by fixtures; preview downloads were replaced with generated
images in the test process. No actual Google account, photo, or live mutation was used.

The first browser assertions relied on a transient toast, a differently worded heading, and clean
cookies across reruns. Those test assumptions were corrected to use persisted operation status,
the actual heading, and cleared test cookies. The completed flow passed.

Backend output includes upstream deprecation warnings (datetime.utcnow and testing-library aliases).
No tests failed. Optional CLIP inference with real model weights, current signed-in Google API
behavior, Google trash/restore outcomes, Windows/Linux runtime behavior, and large-library performance
have not been validated. The optional CLIP similarity/time-window logic is covered with synthetic
embeddings; that does not validate model quality.

## Scan HTTP 405 fix — extension 3.0.1

A real scan reported HTTP 405 before any page reached `/api/gp/sync`. The adapter had
incorrectly derived the RPC root from the visible account route (`/u/N/`). It now uses
Google's `WIZ_global_data.eptZe`, as the vendored Toolkit does, restricted to PhotosUi
service roots on the fixed Google Photos host. The original synthetic fixture shared
the wrong URL assumption and therefore did not catch this defect.

Regression tests now cover the supplied default and account-specific service roots,
rejection of missing/unsafe roots before credentials are sent, and useful HTTP errors
without credentials. All 10 extension tests and 30 backend tests passed after this fix.
Scan errors and retry progress now appear beside the Scan button, separately from
thumbnail analysis status. A successful scan against a signed-in account still needs
to be verified; these regression tests use synthetic responses.

To update an existing installation, run `git pull --ff-only` in its `photo-curator`
folder, reload Photo Curator at `chrome://extensions` (version 3.0.1), then reload both
its app tab and the Google Photos tab. Use **Scan / resume library**. This patch does
not require a backend restart or catalog reset.

## Thumbnail downloads — extension 3.0.2

Thumbnail download follow-up (3.0.2): a real catalog contained 18,679 photos and 706 videos.
Every photo used the newer `photos.fife.usercontent.google.com` host. The old allowlist rejected
it; three bounded, unauthenticated download probes then returned HTTP 403. The extension now
fetches previews through the signed-in Photos tab and sends only bounded image bytes to the
authenticated local cache endpoint. Tests cover host restrictions, browser credentials, no
redirects, account changes, oversized responses, authenticated uploads, resumable cache reuse,
and retaining previous groups when every download fails. Animated previews are cataloged for manual
review and excluded from matching without aborting other downloads. Download messages process ten
items at a time, with durable cache reuse and an outer timeout; the UI reconnects its progress port.
All 38 backend and 14 extension tests pass.

The installed extension and backend were then exercised on the user's existing local catalog.
Authenticated downloads, local image decoding, and pHash calculation succeeded for 36 real previews
in the bounded verification run. Stop retained the cache. The full scan of 19,385 items was also
confirmed by the user. Full-library duplicate results and Google mutations have not been verified.
No photo was trashed or restored during debugging.

## Multi-picture JPEG classification — extension 3.0.3

The follow-up run exposed a classification bug: counting embedded images as animation also
excluded still MPO/JPEG previews. Pillow reports multiple frames for this container. The pipeline
now decodes the primary MPO image and continues excluding actual animated previews from matching.
Preview format and skip reason are stored separately from the original media type. Startup
requeues the old `animation` classifications for direct-catalog items without erasing cached images
or review decisions. The catalog was backed up locally before applying this migration.

Live verification recovered 875 previously skipped items; all decoded as MPO, with no new skipped
previews in that check. The full resumed download is still in progress. Regression tests cover a
two-image MPO (matching only the primary image), true GIF animation exclusion, and idempotent
requeue with existing cached previews retained. All 40 backend and 14 extension tests pass.

## Ownership filtering — extension 3.0.4

Analysis and the browser download queue default to `owned_only=true`, excluding both false and
unknown ownership before fetching, embedding, and matching. Cached non-owned previews are also
excluded. The Library Sync checkbox can opt into wider analysis, while trash approval still requires
owned targets. A pending review can be narrowed using `keep-unowned`, preserving excluded content
and original keepers as protected items. This does not approve the review or create an operation.

All 42 backend and 15 extension tests pass, including default filtering, opt-out propagation,
cached-preview filtering, and pending-review edits with protected exclusions. The interface detector
reported no findings. Native browser reload/visual verification was interrupted by concurrent user
activity; the extension needs reloading to display the new checkbox and review-edit button.

On the existing catalog, review #1 remained pending with 28 eligible targets and 83 protected keys
(the original 50 keepers plus 33 excluded targets). No operation was created. A cached-only analysis
excluded 7,825 catalog items and produced 558 unreviewed candidate groups from 11,075 cached owned
previews; two owned previews were unavailable. Existing reviewed groups were retained.

## GitHub CI configuration

`.ci/cleanup.yml` is a ready-to-use GitHub Actions template for the backend tests, extension tests,
frontend build and npm audit. It is not active. Creation at `.github/workflows/cleanup.yml` was rejected
by the connected GitHub integration (403, resource not accessible by integration). The available CLI
credential also did not advertise workflow-write scope. A credential with workflow permissions can
copy this template to `.github/workflows/cleanup.yml` and push it to activate CI.

## 3.1.0 — likely accidental bursts

- Backend: 47 tests passed; extension: 15 tests passed. Frontend production build passed.
- Added checks for signal detection, burst caps, ownership changes before review, explicit pending review with no operation, dry-run default, protected survivors, persistent ignore, cancellation, stale group IDs, and preventing selection of existing review keepers.
- Installed backend processed 10,423 owned photos from the existing catalog in approximately 14 seconds: one pending suggestion, two unavailable previews and two missing dates. This verifies execution, not classification accuracy. No selection, approval or live trash was performed by this validation.
- Uses cached thumbnails only; no new model downloads or external image uploads. Thresholds are conservative heuristics and have not been calibrated against a labeled personal-photo dataset.
- Native Chrome visual inspection/reload was interrupted by concurrent user activity. Visual layout and manual browser interaction for the new tab remain unverified. The mechanical UI detector reported only the existing progress-bar width transition, outside the new surface.

## 3.1.2 — direct trash from accidental bursts

49 backend tests and 19 extension tests pass. New coverage checks dry-run selection validation without consuming groups or creating reviews, cancellation before any write, unavailable Google Photos before any write, and the confirmed review/approve/live-apply sequence. JavaScript syntax checks and UI detector pass. No actual photo trash or restore was executed as a test. Native UI visual verification remains outstanding.

## 3.2.0 — temporary photos and repeated attempts

- 57 backend tests and 19 extension tests pass. Covers category isolation, label-cache reuse, document labeling priority, time/account/content matching constraints, partial selection protection, and catalog-backed keeperless temporary reviews through live-enabled batch issuance and Undo. Tests simulate backend outcomes and never call Google mutations.
- Apple's local Vision helper compiled successfully and recognized a generated delivery-update image. The helper requires macOS recognition services (sandbox-only execution could not access them). The installed backend ran it successfully outside the development sandbox.
- Live cached-library analysis: 9,624 eligible owned photos; repeated attempts yielded 1,746 groups (two unavailable previews, one missing date). Temporary analysis labeled 69 candidates in 44 category/month groups (two unavailable previews). Classification accuracy has not been validated against human labels.
- Synthetic fixture inspected in the in-app browser: temporary category filters, preview tiles, selection and dry-run toggle changing the button to Trash, and repeated-attempt keeper labeling. This checks rendering and selection UI, not an actual Chrome extension trash action. Code review's execution-guard finding was corrected and regression-tested.
- New collections use no CLIP download. Temporary OCR retains only classification labels and cache signatures, not recognized text. Original-quality comparison, expiration, expressions and personal significance remain user judgments.
