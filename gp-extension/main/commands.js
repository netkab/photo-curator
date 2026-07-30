/**
 * MAIN-world command handler — the only code that talks to Google Photos.
 *
 * Runs in the page's own JavaScript context (not the extension's isolated world) because that is
 * the only place `window.gptkApi` and the page's session tokens exist. Communicates with the
 * extension exclusively through `window.postMessage`, gated on APP_ID so we never react to a
 * message the page itself (or another extension) happened to post.
 *
 * Everything here is a thin, auditable wrapper. Batching, pacing, retries and progress accounting
 * all live in the service worker, driven by the backend's cursor — this file just does one thing
 * per message and reports what happened.
 */
(() => {
  const APP_ID = "photo-curator-gp";
  const INFO_CHUNK = 1000;   // getBatchMediaInfo tolerates thousands; stay well under GPTK's 5000
  const PAGE_SIZE = 500;     // getItemsByTakenDate's own page size

  if (window.__pcCommandsReady) return;
  window.__pcCommandsReady = true;

  const api = () => window.gptkApi;

  const post = (payload) => window.postMessage({ app: APP_ID, dir: "from-page", ...payload }, "*");
  const progress = (requestId, message, detail) =>
    post({ type: "progress", requestId, message, detail });

  // ── account ───────────────────────────────────────────────────────────────
  /**
   * Which Google account this tab is signed in as, as the "/u/N" path segment.
   *
   * Every operation is pinned to this. Running a trash list built for one account against another
   * would target whatever happens to share those dedup keys — so a mismatch is a hard stop, not a
   * warning.
   */
  function accountPath() {
    const m = location.pathname.match(/^\/u\/(\d+)\//);
    return m ? `/u/${m[1]}` : "/u/0";
  }

  function healthCheck() {
    const wiz = window.WIZ_global_data || window.globalThis?.WIZ_global_data;
    return {
      gptk: typeof api()?.getItemsByTakenDate === "function",
      wiz: !!wiz,
      account: accountPath(),
      // SNlM0e is the XSRF token batchexecute requires; its absence means we are not really
      // signed in on this tab and every RPC would fail with an opaque error.
      authed: !!(wiz && wiz.SNlM0e),
      url: location.href,
    };
  }

  // ── scanning ──────────────────────────────────────────────────────────────
  /**
   * One page of the library, newest-first, enriched with the fields the backend matcher needs.
   *
   * getItemsByTakenDate returns mediaKey/dedupKey/timestamp/dimensions but NOT fileName or size;
   * those come only from getBatchMediaInfo. The enrichment is mandatory — without fileName the
   * catalog matcher is down to its weakest pass.
   */
  async function scanPage({ requestId, pageId = null, source = null, pageSize = PAGE_SIZE }) {
    const page = await api().getItemsByTakenDate(null, source, pageId, pageSize);
    const items = page?.items || [];
    if (!items.length) return { items: [], nextPageId: null };

    progress(requestId, `Fetched ${items.length} items, getting file names…`);
    const info = await mediaInfo(items.map((i) => i.mediaKey));
    const byKey = new Map(info.map((i) => [i.mediaKey, i]));

    return {
      items: items.map((it) => toItem(it, byKey.get(it.mediaKey))),
      nextPageId: page.nextPageId || null,
    };
  }

  async function mediaInfo(mediaKeys) {
    const out = [];
    for (let i = 0; i < mediaKeys.length; i += INFO_CHUNK) {
      const chunk = mediaKeys.slice(i, i + INFO_CHUNK);
      const res = await api().getBatchMediaInfo(chunk);
      if (Array.isArray(res)) out.push(...res);
    }
    return out;
  }

  /** Google reports epoch milliseconds as strings. Normalise to an ISO string the backend accepts. */
  function isoOf(ms) {
    const n = Number(ms);
    if (!n || Number.isNaN(n)) return null;
    const d = new Date(n);
    return Number.isNaN(d.getTime()) ? null : d.toISOString();
  }

  function toItem(it, info) {
    return {
      media_key: it.mediaKey,
      dedup_key: it.dedupKey || null,
      file_name: info?.fileName || null,
      taken_at: isoOf(it.timestamp),
      uploaded_at: isoOf(it.creationTimestamp),
      width: it.resWidth ?? null,
      height: it.resHeight ?? null,
      bytes: info?.size != null ? Number(info.size) : null,
      duration: it.duration != null ? Number(it.duration) / 1000 : null,
      is_owned: it.isOwned ?? null,
      is_original_quality: info?.isOriginalQuality ?? null,
      trashed: false,
      product_url: `https://photos.google.com${accountPath()}/photo/${it.mediaKey}`,
      thumb_url: it.thumb || null,
    };
  }

  // ── mutations ─────────────────────────────────────────────────────────────
  /**
   * Trash or restore one batch of dedup keys.
   *
   * `dryRun` short-circuits before the RPC so the whole pipeline — batching, cursor advance,
   * progress reporting, the backend's bookkeeping — can be exercised end to end without touching
   * the library. That is the first thing you should run against a new install.
   *
   * Google's response does not itemise per-key outcomes, so a resolved call means the batch was
   * accepted as a whole. Both operations are idempotent, which is what makes the service worker's
   * "retry the same slice" recovery safe.
   *
   * A dry run reports the keys as succeeded rather than failed: it processed them, it just didn't
   * send anything. The operation carries `dry_run`, which is what blocks undo and stops the backend
   * closing out the review action — the counts are for the progress bar, not the audit trail.
   */
  async function mutate({ op, keys, dryRun }) {
    if (!keys?.length) return { succeeded: [], failed: [] };
    if (dryRun) return { succeeded: keys, failed: [], dryRun: true };

    if (op === "trash") await api().moveItemsToTrash(keys);
    else if (op === "restore") await api().restoreFromTrash(keys);
    else throw new Error(`unsupported op '${op}'`);

    return { succeeded: keys, failed: [] };
  }

  // ── albums ────────────────────────────────────────────────────────────────
  /**
   * Album membership is the one Google Photos organising primitive that is both reliable and
   * non-destructive, which makes it the right home for a Maps place package or a set of highlight
   * reels. Album operations take **mediaKey**, unlike every mutation above — this is the one place
   * dedupKey is the wrong identifier.
   */
  async function ensureAlbum({ title, albumMediaKey }) {
    if (albumMediaKey) return { albumMediaKey, created: false };

    // Reuse an existing album of the same name rather than minting "Goa (2)" on every run.
    let pageId = null;
    do {
      const page = await api().getAlbums(pageId);
      const hit = (page?.items || []).find((a) => a.title === title);
      if (hit) return { albumMediaKey: hit.mediaKey, created: false, title };
      pageId = page?.nextPageId || null;
    } while (pageId);

    const created = await api().createAlbum(title);
    const key = typeof created === "string" ? created : created?.mediaKey;
    if (!key) throw new Error(`createAlbum('${title}') returned no album key`);
    return { albumMediaKey: key, created: true, title };
  }

  async function addToAlbum({ title, albumMediaKey, mediaKeys, dryRun }) {
    if (!mediaKeys?.length) return { succeeded: [], failed: [] };
    if (dryRun) return { succeeded: mediaKeys, failed: [], dryRun: true };

    const album = await ensureAlbum({ title, albumMediaKey });
    await api().addItemsToAlbum(mediaKeys, album.albumMediaKey);
    return { succeeded: mediaKeys, failed: [], album };
  }

  // ── metadata ──────────────────────────────────────────────────────────────
  /** Write a description onto each item. Entries: [{key, description}]. */
  async function setDescriptions({ entries, dryRun }) {
    if (!entries?.length) return { succeeded: [], failed: [] };
    if (dryRun) return { succeeded: entries.map((e) => e.key), failed: [], dryRun: true };

    const succeeded = [];
    const failed = [];
    // No bulk RPC for descriptions — one call per item, so this is paced by the worker's batching
    // rather than fired all at once.
    for (const e of entries) {
      try {
        await api().setItemDescription(e.key, e.description ?? "");
        succeeded.push(e.key);
      } catch (err) {
        failed.push({ key: e.key, error: String(err?.message || err) });
      }
    }
    return { succeeded, failed };
  }

  /** Correct capture times. Entries: [{key, timestampSec, timezoneSec}]. */
  async function setTimestamps({ entries, dryRun }) {
    if (!entries?.length) return { succeeded: [], failed: [] };
    if (dryRun) return { succeeded: entries.map((e) => e.key), failed: [], dryRun: true };

    await api().setItemsTimestamp(entries.map((e) => ({
      dedupKey: e.key,
      timestampSec: e.timestampSec,
      timezoneSec: e.timezoneSec ?? 0,
    })));
    return { succeeded: entries.map((e) => e.key), failed: [] };
  }

  // ── dispatch ──────────────────────────────────────────────────────────────
  const HANDLERS = {
    healthCheck: async () => healthCheck(),
    scanPage,
    mediaInfo: async ({ mediaKeys }) => ({ items: await mediaInfo(mediaKeys) }),
    trash: (args) => mutate({ ...args, op: "trash" }),
    restore: (args) => mutate({ ...args, op: "restore" }),
    ensureAlbum,
    add_to_album: addToAlbum,
    set_description: setDescriptions,
    set_timestamp: setTimestamps,
  };

  window.addEventListener("message", async (ev) => {
    const msg = ev.data;
    if (ev.source !== window) return;
    if (!msg || msg.app !== APP_ID || msg.dir !== "to-page") return;

    const { requestId, command, args = {} } = msg;
    const handler = HANDLERS[command];
    if (!handler) {
      post({ type: "result", requestId, ok: false, error: `unknown command '${command}'` });
      return;
    }

    try {
      const result = await handler({ ...args, requestId });
      post({ type: "result", requestId, ok: true, result });
    } catch (err) {
      post({ type: "result", requestId, ok: false, error: String(err?.message || err) });
    }
  });

  post({ type: "ready", health: healthCheck() });
})();
