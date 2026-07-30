/**
 * Isolated-world relay: chrome.runtime  <->  window.postMessage.
 *
 * The service worker cannot postMessage into a page, and the page cannot use chrome.runtime.
 * This content script is the only thing that can see both, so every command travels:
 *
 *   worker.js --chrome.tabs.sendMessage--> bridge.js --postMessage--> commands.js (MAIN world)
 *                                        <--postMessage--          <-- result
 *
 * Correlation is by requestId. Both directions are gated on APP_ID and `ev.source === window` so a
 * hostile or merely chatty page cannot inject fake results — the page can see these messages, so
 * treat anything arriving here as untrusted input and forward nothing else.
 */
(() => {
  const APP_ID = "photo-curator-gp";
  if (window.__pcBridgeReady) return;
  window.__pcBridgeReady = true;

  const pending = new Map();   // requestId -> {resolve, reject, timer}
  let lastHealth = null;

  window.addEventListener("message", (ev) => {
    const msg = ev.data;
    if (ev.source !== window) return;
    if (!msg || msg.app !== APP_ID || msg.dir !== "from-page") return;

    if (msg.type === "ready") {
      lastHealth = msg.health;
      return;
    }

    if (msg.type === "progress") {
      // Fire-and-forget: the app page may not be open, and a dropped progress tick is harmless.
      chrome.runtime.sendMessage({
        type: "PC_PROGRESS", requestId: msg.requestId,
        message: msg.message, detail: msg.detail,
      }).catch(() => {});
      return;
    }

    if (msg.type === "result") {
      const entry = pending.get(msg.requestId);
      if (!entry) return;
      clearTimeout(entry.timer);
      pending.delete(msg.requestId);
      entry.resolve({ ok: msg.ok, result: msg.result, error: msg.error });
    }
  });

  /** Send one command into the MAIN world and await its reply. */
  function callPage(command, args, timeoutMs) {
    return new Promise((resolve) => {
      const requestId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const timer = setTimeout(() => {
        pending.delete(requestId);
        // A timeout is a real outcome, not an exception: the worker pauses the operation and the
        // backend cursor stays put, so the same slice is retried rather than skipped.
        resolve({ ok: false, error: `timed out after ${timeoutMs}ms` });
      }, timeoutMs);

      pending.set(requestId, { resolve, timer });
      window.postMessage({ app: APP_ID, dir: "to-page", requestId, command, args }, "*");
    });
  }

  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (msg?.type !== "PC_GP_COMMAND") return false;
    callPage(msg.command, msg.args || {}, msg.timeoutMs || 60000).then(respond);
    return true;   // async response
  });

  // Let the worker discover an already-loaded tab without waiting for the next "ready".
  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (msg?.type !== "PC_GP_PING") return false;
    respond({ ok: true, health: lastHealth });
    return true;
  });
})();
