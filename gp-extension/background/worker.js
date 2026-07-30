/**
 * Service worker — command routing and the batch executor.
 *
 * The executor drains a backend GpOperation: ask for the next slice, run it in the Google Photos
 * tab, report what happened, pace, repeat.
 *
 * Durability deliberately does NOT depend on this worker staying alive. MV3 kills idle workers, and
 * the user will close the browser mid-run. The cursor lives in SQLite on the backend, so the worst
 * case is that one in-flight batch is retried — every operation here is idempotent (trashing an
 * already-trashed key is a no-op), which is what makes "retry the slice" safe.
 *
 * Pacing is the other half. The reference implementation sends 250 keys per request with no delay
 * and no rate-limit handling, and its users report trash runs stalling at exactly 2,500 items
 * (10 x 250) and 504s on large batches. We send 100 with a 5 s gap and back off rather than
 * hammering, and we pause loudly instead of silently skipping a failed chunk.
 */
import * as api from "../lib/api.js";

const DEFAULTS = {
  batchSize: 100,
  delayMs: 5000,       // between batches
  timeoutMs: 60000,    // per batch, in the page
  maxAttempts: 3,
  backoffMs: 2000,     // doubles per attempt
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── settings ────────────────────────────────────────────────────────────────
async function settings() {
  const stored = await chrome.storage.local.get("pc-settings");
  return { ...DEFAULTS, ...(stored["pc-settings"] || {}) };
}

// ── talking to the Google Photos tab ────────────────────────────────────────
async function findPhotosTab() {
  const tabs = await chrome.tabs.query({ url: "https://photos.google.com/*" });
  return tabs.find((t) => !t.discarded) || tabs[0] || null;
}

/**
 * Run one command in the Google Photos page.
 * Resolves to {ok, result} | {ok:false, error} — it never throws, so every caller handles failure
 * the same way rather than some paths crashing the loop.
 */
async function callPage(command, args = {}, timeoutMs = DEFAULTS.timeoutMs) {
  const tab = await findPhotosTab();
  if (!tab) {
    return { ok: false, error: "No Google Photos tab is open. Open photos.google.com and retry." };
  }
  try {
    const res = await chrome.tabs.sendMessage(tab.id, {
      type: "PC_GP_COMMAND", command, args, timeoutMs,
    });
    return res || { ok: false, error: "No response from the Google Photos tab." };
  } catch (err) {
    // Almost always "the content script isn't loaded in that tab yet" — after installing or
    // reloading the extension, existing tabs keep running the old (or no) content script.
    return {
      ok: false,
      error: `Could not reach the Google Photos tab (${err.message}). Reload photos.google.com and retry.`,
    };
  }
}

async function health() {
  const res = await callPage("healthCheck", {}, 10000);
  if (!res.ok) return { ok: false, error: res.error };
  return { ok: true, ...res.result };
}

// ── progress fan-out to the app page ────────────────────────────────────────
const ports = new Set();

function broadcast(msg) {
  for (const port of ports) {
    try { port.postMessage(msg); } catch { ports.delete(port); }
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "pc-app") return;
  ports.add(port);
  // A connected port also keeps this worker alive while the app page is open, so a long drain
  // isn't interrupted by the idle timeout. Durability doesn't rely on it, but it avoids churn.
  port.onDisconnect.addListener(() => ports.delete(port));
});

// ── library scan ────────────────────────────────────────────────────────────
const scanState = { running: false, cancel: false };

/**
 * Walk the whole library newest-first, POSTing each page to the backend as it arrives.
 *
 * Pages are pushed immediately rather than accumulated: a 30k-item library would otherwise sit
 * entirely in the worker's memory, and any failure would throw away the whole scan. Streaming means
 * an interrupted scan still leaves everything fetched so far in the catalog.
 */
async function runScan({ source = null } = {}) {
  if (scanState.running) return { ok: false, error: "A scan is already running." };
  scanState.running = true;
  scanState.cancel = false;

  let pageId = null;
  let pages = 0;
  let total = 0;

  try {
    const h = await health();
    if (!h.ok) return { ok: false, error: h.error };
    if (!h.gptk) return { ok: false, error: "Google Photos Toolkit did not load. Reload photos.google.com." };
    if (!h.authed) return { ok: false, error: "That Google Photos tab is not signed in." };

    const account = h.account;
    broadcast({ type: "scan:start", account });

    do {
      if (scanState.cancel) {
        broadcast({ type: "scan:cancelled", pages, total });
        return { ok: true, cancelled: true, pages, total, account };
      }

      const res = await callPage("scanPage", { pageId, source }, 120000);
      if (!res.ok) throw new Error(res.error);

      const { items, nextPageId } = res.result;
      if (items.length) {
        const synced = await api.gpSync(account, items);
        total += items.length;
        pages += 1;
        broadcast({ type: "scan:page", pages, total, inserted: synced.inserted, account });
      }
      pageId = nextPageId;
      // Pace the scan too — it is read-only, but 60+ back-to-back pages of 500 is still a burst.
      if (pageId) await sleep(1000);
    } while (pageId);

    broadcast({ type: "scan:done", pages, total, account });
    return { ok: true, pages, total, account };
  } catch (err) {
    broadcast({ type: "scan:error", error: String(err.message || err) });
    return { ok: false, error: String(err.message || err) };
  } finally {
    scanState.running = false;
  }
}

// ── operation executor ──────────────────────────────────────────────────────
const running = new Set();   // operation ids currently being drained

async function runOperation(opId) {
  if (running.has(opId)) return { ok: false, error: `Operation ${opId} is already running.` };
  running.add(opId);

  const cfg = await settings();
  try {
    const h = await health();
    if (!h.ok) return { ok: false, error: h.error };
    if (!h.gptk) return { ok: false, error: "Google Photos Toolkit did not load. Reload photos.google.com." };
    if (!h.authed) return { ok: false, error: "That Google Photos tab is not signed in." };

    let op = await api.getOperation(opId);
    // The account guard is enforced by the backend too; checking here gives a clearer message
    // before any work starts rather than a 409 mid-drain.
    if (op.account !== h.account) {
      const msg = `Operation ${opId} targets account ${op.account}, but this tab is signed in as ${h.account}.`;
      broadcast({ type: "op:error", opId, error: msg });
      return { ok: false, error: msg };
    }

    broadcast({ type: "op:start", opId, op });

    for (;;) {
      const slice = await api.nextBatch(opId, cfg.batchSize, h.account);
      if (slice.done || !slice.batch?.length) break;

      const outcome = await runBatchWithRetry(slice, cfg, opId);

      if (outcome.error) {
        // Report the batch-level failure so the backend pauses at this cursor. The same slice is
        // re-issued on resume; nothing is skipped and nothing is double-counted.
        op = await api.reportResult(opId, { cursor: slice.cursor, error: outcome.error });
        broadcast({ type: "op:paused", opId, op, error: outcome.error });
        return { ok: false, error: outcome.error, op };
      }

      op = await api.reportResult(opId, {
        cursor: slice.cursor,
        succeeded: outcome.succeeded,
        failed: outcome.failed,
      });
      broadcast({ type: "op:progress", opId, op });

      // This loop can run for thousands of iterations; never let it spin on an unmoving cursor.
      if (op.cursor <= slice.cursor && op.status !== "done") {
        const msg = `Operation ${opId} did not advance past item ${slice.cursor}.`;
        broadcast({ type: "op:error", opId, error: msg });
        return { ok: false, error: msg, op };
      }

      if (op.status === "cancelled") {
        broadcast({ type: "op:cancelled", opId, op });
        return { ok: true, cancelled: true, op };
      }
      if (op.status === "done") break;

      await sleep(cfg.delayMs);
    }

    const final = await api.getOperation(opId);
    broadcast({ type: "op:done", opId, op: final });
    return { ok: true, op: final };
  } catch (err) {
    const error = String(err.message || err);
    broadcast({ type: "op:error", opId, error });
    return { ok: false, error };
  } finally {
    running.delete(opId);
  }
}

/** One slice, with bounded retries. Returns {succeeded, failed} or {error} to pause the operation. */
async function runBatchWithRetry(slice, cfg, opId) {
  // trash/restore carry bare keys; parameterised ops (descriptions, timestamps, albums) carry
  // objects. Send both shapes plus the operation-level args, and let the handler take what it needs.
  const keys = slice.batch.map((entry) => (typeof entry === "string" ? entry : entry.key));
  const entries = slice.batch.map((entry) => (typeof entry === "string" ? { key: entry } : entry));
  const args = slice.args || {};

  for (let attempt = 1; attempt <= cfg.maxAttempts; attempt++) {
    const res = await callPage(
      slice.op,
      { ...args, keys, entries, mediaKeys: keys, dryRun: slice.dry_run },
      cfg.timeoutMs,
    );

    if (res.ok) {
      const r = res.result || {};
      const succeeded = r.succeeded || [];
      const failed = r.failed || [];
      // The cursor advances by succeeded + failed. If a handler ever reported neither for a
      // non-empty slice we would re-request the same slice forever, so treat it as a fault.
      if (!succeeded.length && !failed.length) {
        return { error: `The page reported no outcome for ${keys.length} item(s).` };
      }
      return { succeeded, failed };
    }

    if (attempt < cfg.maxAttempts) {
      const wait = cfg.backoffMs * 2 ** (attempt - 1);
      broadcast({
        type: "op:retry", opId, attempt, of: cfg.maxAttempts, waitMs: wait, error: res.error,
      });
      await sleep(wait);
    } else {
      return { error: `${res.error} (after ${cfg.maxAttempts} attempts)` };
    }
  }
  return { error: "exhausted retries" };
}

/** Pick up anything left mid-flight by a previous session. */
async function resumeInterrupted() {
  try {
    const { items } = await api.listOperations("running");
    for (const op of items) {
      if (!running.has(op.id)) runOperation(op.id);
    }
  } catch {
    // Backend down — the app page surfaces that; nothing useful to do from here.
  }
}

// ── message routing ─────────────────────────────────────────────────────────
const ROUTES = {
  PC_HEALTH: () => health(),
  PC_SCAN: (msg) => runScan(msg.args || {}),
  PC_SCAN_CANCEL: () => { scanState.cancel = true; return { ok: true }; },
  PC_RUN_OP: (msg) => runOperation(msg.opId),
  PC_GP_CALL: (msg) => callPage(msg.command, msg.args, msg.timeoutMs),
  PC_OPEN_APP: () => openApp(),
};

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  // Progress relayed up from the bridge; forward to the app page and stop. Spread first — the
  // incoming message carries its own `type` and would otherwise overwrite the one we're setting.
  if (msg?.type === "PC_PROGRESS") {
    broadcast({ ...msg, type: "page:progress" });
    return false;
  }

  const route = ROUTES[msg?.type];
  if (!route) return false;
  Promise.resolve(route(msg))
    .then(respond)
    .catch((err) => respond({ ok: false, error: String(err.message || err) }));
  return true;   // async response
});

// ── thumbnails ──────────────────────────────────────────────────────────────
/**
 * Proxy localhost thumbnails for the in-grid panel.
 *
 * photos.google.com is HTTPS, so an <img src="http://localhost/..."> is blocked as mixed content.
 * The worker has no such restriction, so it fetches and hands back a data URL.
 */
const thumbCache = new Map();
const THUMB_CACHE_MAX = 300;

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg?.type !== "GET_IMAGE") return false;

  const cached = thumbCache.get(msg.url);
  if (cached) { respond({ ok: true, dataUrl: cached }); return false; }

  fetch(msg.url)
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.blob(); })
    .then((blob) => new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result);
      reader.readAsDataURL(blob);
    }))
    .then((dataUrl) => {
      if (thumbCache.size >= THUMB_CACHE_MAX) {
        thumbCache.delete(thumbCache.keys().next().value);   // crude FIFO; these are small
      }
      thumbCache.set(msg.url, dataUrl);
      respond({ ok: true, dataUrl });
    })
    .catch((err) => respond({ ok: false, err: String(err) }));

  return true;
});

// ── entry points ────────────────────────────────────────────────────────────
const APP_URL = chrome.runtime.getURL("app/app.html");

/** Focus the app tab if it is already open rather than piling up duplicates of it. */
async function openApp() {
  const [existing] = await chrome.tabs.query({ url: APP_URL });
  if (existing) {
    await chrome.tabs.update(existing.id, { active: true });
    await chrome.windows.update(existing.windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url: APP_URL });
  }
  return { ok: true };
}

chrome.action.onClicked.addListener(openApp);

chrome.runtime.onStartup.addListener(resumeInterrupted);
chrome.runtime.onInstalled.addListener(resumeInterrupted);
