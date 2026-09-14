# Validation — 2026-09-14

Tested locally on macOS ARM64 with Python 3.12 and Node 26.3.0.

| Check | Result |
|---|---|
| Backend regression suite | 30 passed |
| Extension RPC/manifest regression suite | 7 passed |
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

## GitHub CI

`.ci/cleanup.yml` is a ready-to-use GitHub Actions template for the backend tests, extension tests,
frontend build and npm audit. It is not active. Creation at `.github/workflows/cleanup.yml` was rejected
by the connected GitHub integration (403, resource not accessible by integration). The available CLI
credential also did not advertise workflow-write scope. A credential with workflow permissions can
copy this template to `.github/workflows/cleanup.yml` and push it to activate CI.
