# Validation — 2026-09-14

Tested locally on macOS ARM64 with Python 3.12 and Node 26.3.0.

| Check | Result |
|---|---|
| Backend regression suite | 37 passed |
| Extension RPC/manifest regression suite | 12 passed |
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

## GitHub CI

Thumbnail download follow-up (3.0.2): a real catalog contained 18,679 photos and 706 videos.
Every photo used the newer `photos.fife.usercontent.google.com` host. The old allowlist rejected
it; three bounded, unauthenticated download probes then returned HTTP 403. The extension now
fetches previews through the signed-in Photos tab and sends only bounded image bytes to the
authenticated local cache endpoint. Tests cover host restrictions, browser credentials, no
redirects, account changes, oversized responses, authenticated uploads, resumable cache reuse,
and retaining previous groups when every download fails. All 37 backend and 12 extension tests pass.

`.ci/cleanup.yml` is a ready-to-use GitHub Actions template for the backend tests, extension tests,
frontend build and npm audit. It is not active. Creation at `.github/workflows/cleanup.yml` was rejected
by the connected GitHub integration (403, resource not accessible by integration). The available CLI
credential also did not advertise workflow-write scope. A credential with workflow permissions can
copy this template to `.github/workflows/cleanup.yml` and push it to activate CI.
