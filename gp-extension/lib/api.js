/**
 * Photo Curator backend client.
 *
 * Imported by the service worker and the app page (both extension contexts, so no mixed-content or
 * Private Network Access restrictions apply — the backend sends
 * `Access-Control-Allow-Private-Network: true` for the page contexts that do).
 */

export const API = "http://localhost:8077";

class ApiError extends Error {
  constructor(status, body, url) {
    super(`HTTP ${status} — ${url}${body ? `: ${String(body).slice(0, 200)}` : ""}`);
    this.status = status;
    this.body = body;
  }
}

async function request(path, options = {}, timeoutMs = 30000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${API}${path}`, { ...options, signal: ctrl.signal });
    const text = await res.text();
    const body = text ? JSON.parse(text) : null;
    if (!res.ok) throw new ApiError(res.status, body?.detail ?? text, path);
    return body;
  } catch (err) {
    if (err.name === "AbortError") throw new Error(`Backend timed out after ${timeoutMs}ms (${path})`);
    if (err instanceof ApiError) throw err;
    // Almost always "backend isn't running" — say so rather than surfacing a bare TypeError.
    throw new Error(`Cannot reach Photo Curator at ${API} — is the backend running? (${err.message})`);
  } finally {
    clearTimeout(timer);
  }
}

export const get = (path, timeoutMs) => request(path, {}, timeoutMs);

export const post = (path, body, timeoutMs) => request(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body ?? {}),
}, timeoutMs);

// ── typed endpoints ─────────────────────────────────────────────────────────
export const health = () => get("/api/health", 5000);
export const gpStatus = () => get("/api/gp/status");
// Sync pages carry up to 500 items, which is a big POST for a local server doing upserts.
export const gpSync = (account, items) => post("/api/gp/sync", { account, items }, 120000);
export const gpLink = (relink = false) => post("/api/gp/link", { relink });
export const gpUnmatched = (side, limit = 50) => get(`/api/gp/unmatched?side=${side}&limit=${limit}`);
export const job = (id) => get(`/api/jobs/${id}`);

export const listOperations = (status) =>
  get(`/api/gp/operations${status ? `?status=${status}` : ""}`);
export const getOperation = (id) => get(`/api/gp/operations/${id}`);
export const createOperation = (body) => post("/api/gp/operations", body);
export const nextBatch = (id, size, account) =>
  get(`/api/gp/operations/${id}/next?size=${size}&account=${encodeURIComponent(account)}`);
export const reportResult = (id, body) => post(`/api/gp/operations/${id}/result`, body);
export const cancelOperation = (id) => post(`/api/gp/operations/${id}/cancel`);
export const resumeOperation = (id) => post(`/api/gp/operations/${id}/resume`);
export const undoOperation = (id) => post(`/api/gp/operations/${id}/undo`);

export const dedupQueue = (limit = 20) => get(`/api/dedup/extension-queue?limit=${limit}`);
export const approveGroup = (groupId, excludeIds) =>
  post(`/api/dedup/groups/${groupId}/approve`, { exclude_ids: excludeIds ?? null });
export const ignoreGroup = (groupId) => post(`/api/dedup/groups/${groupId}/ignore`);
// Trashes the keeper too — one group at a time, by design. There is no bulk equivalent.
export const trashAllInGroup = (groupId, excludeMediaIds) =>
  post(`/api/dedup/groups/${groupId}/trash-all`, { exclude_media_ids: excludeMediaIds ?? [] });
export const setKeeper = (groupId, mediaId) =>
  post(`/api/dedup/groups/${groupId}/keeper`, { media_id: mediaId });

// ── storage reclaim ─────────────────────────────────────────────────────────
export const reclaimSummary = () => get("/api/reclaim/summary");
// Walks every duplicate group and stats files on disk — slow on a large library, so allow longer.
export const reclaimPreview = (body) => post("/api/reclaim/preview", body, 180000);
export const reclaimRun = (body) => post("/api/reclaim/run", body);
export const reclaimRuns = () => get("/api/reclaim/runs");
export const reclaimUndo = (runId) => post(`/api/reclaim/runs/${runId}/undo`);

// ── enhance ─────────────────────────────────────────────────────────────────
export const enhanceCandidates = (limit = 40, offset = 0) =>
  get(`/api/enhance/candidates?limit=${limit}&offset=${offset}`);
export const enhanceRun = (body) => post("/api/enhance/run", body);
export const enhanceResults = () => get("/api/enhance/results");
export const enhanceApprove = (derivedId) => post(`/api/enhance/${derivedId}/approve`);
export const retireOriginals = (mediaIds, dryRun = false) =>
  post("/api/enhance/retire-originals", { media_ids: mediaIds, dry_run: dryRun });

// ── video ───────────────────────────────────────────────────────────────────
export const videoResults = (status) =>
  get(`/api/videos/results${status ? `?status=${status}` : ""}`);
export const videoApprove = (derivedId) => post(`/api/videos/${derivedId}/approve`);
// Uploading a reel pushes the whole file to Google — allow well over the default timeout.
export const videoRetireSources = (derivedId, mediaIds, dryRun = false) =>
  post(`/api/videos/${derivedId}/retire-sources`, { media_ids: mediaIds, dry_run: dryRun });
export const videoHighlights = () => post("/api/videos/highlights");
export const videoCompress = () => post("/api/videos/compress");

// ── maps ────────────────────────────────────────────────────────────────────
export const mapsClusters = () => get("/api/maps/clusters");
export const mapsAlbumPackage = (clusterId) => get(`/api/maps/clusters/${clusterId}/album-package`);
export const mapsDraft = (clusterId, body) => post(`/api/maps/clusters/${clusterId}/draft`, body, 120000);
export const mapsReviewed = (clusterId, reviewed = true) =>
  post(`/api/maps/clusters/${clusterId}/reviewed`, { reviewed });
export const mapsOpenSelected = (clusterId, mediaIds) =>
  post(`/api/maps/clusters/${clusterId}/open-selected`, { media_ids: mediaIds });

export const reviewActions = (status) => get(`/api/review${status ? `?status=${status}` : ""}`);
export const approveAction = (id) => post(`/api/review/${id}/approve`);
// Applying an `upload` action pushes a whole video to Google — minutes, not seconds.
export const applyAction = (id, timeoutMs = 900000) => post(`/api/review/${id}/apply`, {}, timeoutMs);
