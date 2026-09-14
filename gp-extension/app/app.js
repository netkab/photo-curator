/**
 * App shell — tab routing, connection status, and the shared event bus.
 *
 * Holds one long-lived port to the service worker. That is how progress from a running scan or
 * operation reaches the UI, and it also keeps the worker from being torn down mid-drain while this
 * page is open. Nothing depends on it for correctness: the backend owns the cursor, so a worker
 * that dies is picked up again on the next run.
 */
import { h, render, send } from "../lib/dom.js";
import * as api from "../lib/api.js";
import { reviewTab } from "./tabs/review.js";
import { setupTab } from "./tabs/setup.js";
import { syncTab } from "./tabs/sync.js";
import { dupesTab } from "./tabs/dupes.js";

const TABS = [
  { id: "setup", label: "Setup", render: setupTab },
  { id: "sync", label: "Library Sync", render: syncTab },
  { id: "dupes", label: "Duplicates", render: dupesTab },
  { id: "review", label: "Review queue", render: reviewTab },
];

const view = document.getElementById("view");
const tabsEl = document.getElementById("tabs");
const statusText = document.getElementById("status-text");
const dotBackend = document.getElementById("dot-backend");
const toastEl = document.getElementById("toast");

let active = location.hash.slice(1) || ((await chrome.storage.local.get("pc-token"))["pc-token"] ? "sync" : "setup");

// ── event bus ───────────────────────────────────────────────────────────────
const listeners = new Set();

/** Subscribe to worker events. Returns an unsubscribe function tabs call on teardown. */
export function onWorkerEvent(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function connectProgress() {
  try {
    const port = chrome.runtime.connect({ name: "pc-app" });
    port.onMessage.addListener((msg) => {
      for (const fn of listeners) {
        try { fn(msg); } catch (err) { console.error("[photo-curator] listener failed", err); }
      }
    });
    port.onDisconnect.addListener(() => setTimeout(connectProgress, 1000));
  } catch { /* Extension was reloaded: reopen this page. */ }
}
connectProgress();

// ── toast ───────────────────────────────────────────────────────────────────
let toastTimer = null;

export function toast(message, kind = "") {
  clearTimeout(toastTimer);
  toastEl.className = `toast ${kind}`;
  toastEl.textContent = message;
  toastEl.hidden = false;
  toastTimer = setTimeout(() => { toastEl.hidden = true; }, kind === "err" ? 9000 : 4500);
}

// ── shared context handed to every tab ──────────────────────────────────────
export const ctx = { api, send, toast, onWorkerEvent, refreshStatus };

// ── connection status ───────────────────────────────────────────────────────
let teardown = null;

async function refreshStatus() {
  try {
    await api.health();
    dotBackend.className = "dot ok";
  } catch (err) {
    dotBackend.className = "dot bad";
    statusText.textContent = "Check backend / pairing";
    statusText.title = err.message;
    return;
  }

  const gp = await send({ type: "PC_HEALTH" });
  if (!gp?.ok) {
    dotBackend.className = "dot warn";
    statusText.textContent = "No Google Photos tab";
    statusText.title = gp?.error || "";
  } else if (!gp.gptk || !gp.authed) {
    dotBackend.className = "dot warn";
    statusText.textContent = !gp.authed ? "Photos tab not signed in" : "Toolkit not loaded";
    statusText.title = JSON.stringify(gp);
  } else {
    dotBackend.className = "dot ok";
    statusText.textContent = `Connected · ${gp.account}`;
    statusText.title = gp.url || "";
  }
}

// ── routing ─────────────────────────────────────────────────────────────────
function paintTabs() {
  render(tabsEl, TABS.map((t) => h(
    `button.tab${t.id === active ? ".active" : ""}`,
    { onclick: () => go(t.id) },
    t.label,
  )));
}

async function go(id) {
  active = id;
  location.hash = id;
  paintTabs();

  // Tabs poll and subscribe; letting a stale one keep running would double-fetch and fight over
  // the DOM it no longer owns.
  if (teardown) { try { teardown(); } catch { /* tab already gone */ } teardown = null; }

  render(view, h("div.empty", "Loading…"));
  const tab = TABS.find((t) => t.id === active) || TABS[0];
  try {
    teardown = await tab.render(view, ctx);
  } catch (err) {
    console.error(err);
    render(view, h("div.card",
      h("h2", "This tab failed to load"),
      h("p.sub", String(err.message || err)),
      h("button.primary", { onclick: () => go(active) }, "Retry"),
    ));
  }
}

window.addEventListener("hashchange", () => {
  const id = location.hash.slice(1);
  if (id && id !== active) go(id);
});

paintTabs();
go(active);
refreshStatus();
setInterval(refreshStatus, 15000);
