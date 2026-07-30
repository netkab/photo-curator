/**
 * In-grid overlay for photos.google.com.
 *
 * Outlines, in the live grid, the photos Photo Curator has flagged as duplicates, so you can see
 * them in context while browsing. Read-only — all trashing happens from the app page, which has the
 * confirmation, dry run, progress and undo around it.
 *
 * Identification is by **mediaKey**, read from each tile's href (`/photo/<mediaKey>`).
 * The previous version parsed the tile's aria-label ("Photo - Portrait - Jun 3, 2026, 8:14:07 AM")
 * and matched it against a timestamp regex-extracted from the filename. That broke on any non-English
 * UI, on any timezone difference between the filename and Google's displayed local time, and silently
 * skipped every file whose name lacked seconds. mediaKey is exact and locale-independent.
 */
(() => {
  const API = "http://localhost:8077";
  const HIDDEN_KEY = "pc-panel-hidden";

  if (window.__pcPanelReady) return;
  window.__pcPanelReady = true;

  let deleteKeys = new Map();   // mediaKey -> item
  let keeperKeys = new Set();   // never outline a keeper
  let panelOpen = false;
  let observer = null;

  // ── mediaKey extraction ───────────────────────────────────────────────────
  const KEY_RE = /\/photo\/([A-Za-z0-9_-]{10,})/;

  function tileKey(anchor) {
    const href = anchor.getAttribute("href") || "";
    return href.match(KEY_RE)?.[1] || null;
  }

  const tiles = () => document.querySelectorAll('a[href*="/photo/"]');

  // ── highlighting ──────────────────────────────────────────────────────────
  function clearHits() {
    for (const el of document.querySelectorAll(".pc-gp-hit")) {
      el.classList.remove("pc-gp-hit");
      el.querySelector(":scope > .pc-gp-badge")?.remove();
    }
  }

  function highlight() {
    // Our own badge insertions mutate the DOM the observer watches. Without this the callback
    // re-arms itself forever on a page as mutation-heavy as Google Photos.
    observer?.disconnect();
    try {
      clearHits();
      let count = 0;
      for (const tile of tiles()) {
        const key = tileKey(tile);
        if (!key || keeperKeys.has(key) || !deleteKeys.has(key)) continue;

        tile.classList.add("pc-gp-hit");
        if (getComputedStyle(tile).position === "static") tile.style.position = "relative";
        if (!tile.querySelector(":scope > .pc-gp-badge")) {
          const badge = document.createElement("div");
          badge.className = "pc-gp-badge";
          badge.textContent = "DUPLICATE";
          tile.appendChild(badge);
        }
        count++;
      }
      updateCount(count);
    } finally {
      observe();
    }
  }

  let highlightTimer = null;
  const scheduleHighlight = () => {
    clearTimeout(highlightTimer);
    highlightTimer = setTimeout(highlight, 200);
  };

  function observe() {
    observer?.disconnect();
    observer = new MutationObserver(scheduleHighlight);
    observer.observe(document.body, { childList: true, subtree: true });
  }

  // ── data ──────────────────────────────────────────────────────────────────
  async function load() {
    setStatus("Loading…");
    try {
      const res = await fetch(`${API}/api/dedup/extension-queue?limit=100`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      deleteKeys = new Map();
      keeperKeys = new Set();
      let linked = 0;

      for (const group of data.items || []) {
        const keeperKey = group.keeper?.gp?.media_key;
        if (keeperKey) keeperKeys.add(keeperKey);
        for (const del of group.deletes || []) {
          const key = del.gp?.media_key;
          if (!key) continue;
          deleteKeys.set(key, { ...del, group_id: group.group_id, date_key: group.date_key });
          linked++;
        }
      }

      setStatus(linked
        ? `${linked} duplicate${linked === 1 ? "" : "s"} to look for (${data.total_remaining} groups pending)`
        : "No linked duplicates — run a library sync in the app first.");
      highlight();
    } catch (err) {
      setStatus(`Can't reach Photo Curator — is the backend running? (${err.message})`, true);
    }
  }

  // ── UI ────────────────────────────────────────────────────────────────────
  let statusEl, countEl;

  function setStatus(text, isError = false) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.style.color = isError ? "#e0604f" : "#9aa3b2";
  }

  function updateCount(n) {
    if (!countEl) return;
    countEl.textContent = n
      ? `${n} duplicate${n === 1 ? "" : "s"} outlined on screen`
      : "None visible — keep scrolling";
    countEl.style.color = n ? "#e0604f" : "#9aa3b2";
    toggle.textContent = `Photo Curator${n ? ` · ${n}` : ""}`;
  }

  const toggle = el("button", "pc-toggle", "Photo Curator");
  const panel = el("div", "pc-panel");

  function el(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function buildUI() {
    const header = el("div", "pc-header");
    header.append(el("strong", "pc-title", "Duplicates"));

    const btnOpen = el("button", "pc-btn pc-primary", "Open app");
    // A real listener, not an inline onclick attribute: those evaluate in the page's main world
    // (where these functions don't exist) and Google Photos' CSP blocks them outright. Four buttons
    // in the previous version were dead for exactly this reason.
    btnOpen.addEventListener("click", () => {
      chrome.runtime.sendMessage({ type: "PC_OPEN_APP" }).catch(() => {});
    });

    const btnReload = el("button", "pc-btn", "↻");
    btnReload.title = "Reload the duplicate list";
    btnReload.addEventListener("click", load);

    const btnHide = el("button", "pc-btn", "✕");
    btnHide.title = "Hide";
    btnHide.addEventListener("click", () => setOpen(false));

    header.append(btnOpen, btnReload, btnHide);

    statusEl = el("div", "pc-status", "Loading…");
    countEl = el("div", "pc-count", "");

    const note = el("div", "pc-note",
      "Outlined photos are duplicates of one Photo Curator is keeping. Trash them from the app — " +
      "it confirms, tracks progress and can undo.");

    panel.append(header, statusEl, countEl, note);
    toggle.addEventListener("click", () => setOpen(!panelOpen));

    document.body.append(toggle, panel);
  }

  function setOpen(open) {
    panelOpen = open;
    panel.classList.toggle("open", open);
    try { chrome.storage.local.set({ [HIDDEN_KEY]: !open }); } catch { /* storage unavailable */ }
  }

  // ── boot ──────────────────────────────────────────────────────────────────
  buildUI();
  try {
    chrome.storage.local.get(HIDDEN_KEY, (data) => setOpen(!data?.[HIDDEN_KEY]));
  } catch {
    setOpen(true);
  }

  observe();
  addEventListener("scroll", scheduleHighlight, { capture: true, passive: true });
  load();
})();
