/** Scanning and reviewed operations; only extension pages may initiate these commands. */
import * as api from "../lib/api.js";
import {pageCommand} from "../main/commands.js";
const sleep = ms => new Promise(r => setTimeout(r, ms));
const appUrl = chrome.runtime.getURL("app/app.html");
const trusted = sender => sender?.id === chrome.runtime.id && sender?.url?.split("#")[0] === appUrl;
const ports = new Set();
chrome.runtime.onConnect.addListener(port => {
  if (port.name !== "pc-app" || !trusted(port.sender)) return;
  ports.add(port);
  port.onDisconnect.addListener(() => ports.delete(port));
});
function broadcast(msg) { for (const p of ports) { try {p.postMessage(msg);} catch {ports.delete(p);} } }
async function photosTab() {
  const tabs = await chrome.tabs.query({url: "https://photos.google.com/*"});
  const live = tabs.filter(t => !t.discarded);
  if (live.length !== 1) throw new Error("Keep exactly one Google Photos tab open while scanning or running an operation");
  return live[0];
}
async function callPage(tabId, command, args = {}) {
  const execution = chrome.scripting.executeScript({target: {tabId}, world: "MAIN", func: pageCommand,
                                                   args: [command, args]});
  let timer;
  let result;
  try {
    result = command === "fetchThumbnail" ? await Promise.race([execution, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("Google preview timed out; keep the Photos tab open and retry")), 20000);
    })]) : await execution;
  } finally {clearTimeout(timer);}
  const res = result?.[0]?.result;
  if (!res?.ok) throw new Error(res?.error || "Google Photos tab closed or navigation interrupted the request");
  return res.result;
}
async function health() {
  const tab = await photosTab();
  return {ok: true, ...await callPage(tab.id, "healthCheck")};
}
let scanning = false, cancelScan = false, runningOp = null;
async function scan({restart = false} = {}) {
  if (scanning || runningOp) throw new Error("Wait for the current scan or operation to finish");
  scanning = true; cancelScan = false;
  try {
    const tab = await photosTab();
    const h = await callPage(tab.id, "healthCheck");
    if (!h.authed) throw new Error("Sign in to Google Photos and reload the tab");
    const state = await api.post("/api/gp/scan-start", {account: h.account, restart});
    let pageId = state.page_id, pages = state.pages, total = state.items;
    const seen = new Set();
    broadcast({type: "scan:start", account: h.account});
    do {
      if (cancelScan) return {ok: true, cancelled: true, total, pages};
      if (seen.has(pageId)) throw new Error("Google repeated a page cursor; scan stopped to avoid looping");
      seen.add(pageId);
      let page;
      // Only read operations are retried automatically. Mutation failures require explicit resume.
      for (let attempt = 0; attempt < 3; attempt++) {
        try { page = await callPage(tab.id, "scanPage", {pageId, expectedAccount: h.account}); break; }
        catch (e) {
          if (attempt === 2) throw e;
          broadcast({type: "scan:retry", attempt: attempt + 2, error: e.message});
          await sleep(2000 * (attempt + 1));
        }
      }
      if (page.nextPageId && page.nextPageId === pageId) throw new Error("Google returned a repeated cursor");
      const saved = await api.post("/api/gp/sync", {account: h.account, items: page.items,
                                   page_id: pageId, next_page_id: page.nextPageId}, 120000);
      total += page.items.length; pages++;
      broadcast({type: "scan:page", total, pages, inserted: saved.inserted});
      pageId = page.nextPageId;
      if (pageId) await sleep(1000);
    } while (pageId);
    broadcast({type: "scan:done", total, pages});
    return {ok: true, total, pages};
  } finally {scanning = false;}
}
async function runOperation(id) {
  if (runningOp || scanning) throw new Error("Only one scan or operation may run at a time");
  runningOp = id;
  try {
    const tab = await photosTab();
    const h = await callPage(tab.id, "healthCheck");
    const op = await api.getOperation(id);
    if (!h.authed || h.account !== op.account) throw new Error("Google account does not match the reviewed operation");
    broadcast({type: "op:start", opId: id, op});
    for (;;) {
      const slice = await api.nextBatch(id, 25, h.account);
      if (slice.done) break;
      let result;
      try {
        result = await callPage(tab.id, slice.op, {keys: slice.batch, dryRun: slice.dry_run,
                                                  expectedAccount: slice.account});
      } catch (error) {
        await api.reportResult(id, {cursor: slice.cursor, error: String(error.message)});
        broadcast({type: "op:paused", opId: id, error: error.message});
        throw error;
      }
      const current = await api.reportResult(id, {cursor: slice.cursor, ...result});
      broadcast({type: "op:progress", opId: id, op: current});
      if (["done", "cancelled", "paused"].includes(current.status)) break;
      if (current.cursor <= slice.cursor) throw new Error("Operation did not advance; stopped");
      await sleep(5000);
    }
    const final = await api.getOperation(id);
    broadcast({type: "op:done", opId: id, op: final});
    return {ok: true, op: final};
  } finally {runningOp = null;}
}
async function fetchThumbnails({after = 0, ownedOnly = true} = {}) {
  if (scanning || runningOp) throw new Error("Wait for the current scan or operation to finish");
  if (!Number.isSafeInteger(after) || after < 0) throw new Error("Invalid thumbnail cursor");
  scanning = true; cancelScan = false;
  let cached = 0, failed = 0, skipped = 0, consecutiveFailures = 0;
  try {
    const tab = await photosTab();
    const h = await callPage(tab.id, "healthCheck");
    if (!h.authed) throw new Error("Sign in to Google Photos and reload the tab");
    // Keep each extension message below Chrome's long-running event limit.
    // The app requests another small batch; each saved image is already durable.
      const {items} = await api.get(`/api/direct/thumbnail-queue?after=${after}&limit=10&owned_only=${ownedOnly !== false}`);
      if (!items.length) return {ok: true, cached, failed, skipped, more: false, after};
      for (const item of items) {
        if (cancelScan) return {ok: true, cancelled: true, cached, failed};
        if (item.account !== h.account) throw new Error("Google account does not match the catalog");
        let preview;
        try {
          preview = await callPage(tab.id, "fetchThumbnail", {url: item.url, expectedAccount: h.account});
        } catch (e) {
          failed++; consecutiveFailures++;
          broadcast({type: "thumbnail:progress", cached, failed, error: e.message});
          if (consecutiveFailures >= 5) throw new Error(`Thumbnail downloads stopped after 5 consecutive failures: ${e.message}`);
          after = item.media_id;
          continue;
        }
        const saved = await api.post(`/api/direct/thumbnails/${item.media_id}`, {account: item.account, data: preview.data});
        if (saved.skipped) skipped++; else cached++;
        consecutiveFailures = 0; after = item.media_id;
        broadcast({type: "thumbnail:progress", cached, failed, skipped});
      }
      return {ok: true, cached, failed, skipped, more: true, after};
  } finally {scanning = false;}
}
async function getImage(url) {
  const blob = await api.localImage(url);
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let text = "";
  for (let i = 0; i < bytes.length; i += 8192) text += String.fromCharCode(...bytes.subarray(i, i+8192));
  return {ok: true, dataUrl: `data:${blob.type};base64,${btoa(text)}`};
}
const routes = {
  PC_HEALTH: () => health(), PC_SCAN: m => scan(m.args), PC_FETCH_THUMBNAILS: m => fetchThumbnails(m.args),
  PC_SCAN_CANCEL: () => {cancelScan = true; return {ok: true};},
  PC_RUN_OP: m => runOperation(m.opId), GET_IMAGE: m => getImage(m.url),
};
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!trusted(sender) || !routes[msg?.type]) return false;
  Promise.resolve().then(() => routes[msg.type](msg)).then(reply).catch(error => {
    broadcast({type: msg.type === "PC_SCAN" ? "scan:error" : msg.type === "PC_FETCH_THUMBNAILS" ? "thumbnail:error" : "op:error", error: error.message});
    reply({ok: false, error: error.message});
  });
  return true;
});
async function openApp() {
  const tabs = await chrome.tabs.query({url: appUrl});
  if (tabs[0]) await chrome.tabs.update(tabs[0].id, {active: true});
  else await chrome.tabs.create({url: appUrl});
}
chrome.action.onClicked.addListener(openApp);
// Interrupted operations remain stopped until the user explicitly resumes them.
chrome.runtime.onInstalled.addListener(() => chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"}));
