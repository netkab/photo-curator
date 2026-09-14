import {h, render, fmtInt} from "../../lib/dom.js";
export async function syncTab(root, ctx) {
  const {api, send, toast, onWorkerEvent} = ctx;
  const status = h("p", {role: "status", "aria-live": "polite"});
  const summary = h("p");
  let analysisId = null;
  let disposed = false;
  const scan = h("button.primary", {onclick: () => start(false)}, "Scan / resume library");
  const restart = h("button", {onclick: () => start(true)}, "Rescan from newest");
  const stop = h("button", {onclick: async () => {
    await send({type: "PC_SCAN_CANCEL"});
    if (analysisId) await api.post(`/api/jobs/${analysisId}/cancel`);
    status.textContent = "Stopping after the current page or thumbnail. Saved progress is retained.";
  }}, "Stop");
  const clip = h("input", {type: "checkbox"});
  const analyze = h("button.primary", {onclick: runAnalysis}, "Fetch thumbnails and find duplicates");
  function busy(on) {scan.disabled = restart.disabled = analyze.disabled = on;}
  async function refresh() {
    try {
      const s = await api.gpStatus();
      summary.textContent = `${fmtInt(s.gp_total)} live items scanned · ${fmtInt(s.linked)} catalog entries. ` +
        (s.last_sync ? `Last page saved: ${new Date(s.last_sync).toLocaleString()}.` : "Start with a library scan.");
    } catch (e) {summary.textContent = e.message;}
  }
  async function start(fromNewest) {
    busy(true); status.textContent = "Reading Google Photos metadata…";
    try {
      const result = await send({type: "PC_SCAN", args: {restart: fromNewest}});
      if (!result?.ok) throw new Error(result?.error || "Scan failed");
      status.textContent = `${result.cancelled ? "Stopped" : "Scan complete"}: ${fmtInt(result.total)} items saved. Next, fetch thumbnails.`;
    } catch (e) {status.textContent = e.message + " Saved pages remain; resume or rescan to retry.";}
    finally {busy(false); await refresh();}
  }
  async function runAnalysis() {
    busy(true);
    try {
      const j = await api.post("/api/direct/analyze", {use_clip: clip.checked});
      analysisId = j.id;
      for (;;) {
        const current = await api.job(j.id);
        if (disposed) return;
        status.textContent = current.message || "Analyzing thumbnails…";
        if (current.status === "error") throw new Error(current.error);
        if (current.status !== "running") {
          status.textContent = `${current.status}: ${fmtInt(current.result?.groups)} candidate groups; ` +
            `${fmtInt(current.result?.failed)} unavailable thumbnails. Open Duplicates to review. ` +
            "Rescan and run analysis again to retry unavailable thumbnails.";
          break;
        }
        await new Promise(r => setTimeout(r, 1000));
      }
    } catch (e) {status.textContent = e.message; toast(e.message, "err");}
    finally {analysisId = null; busy(false); await refresh();}
  }
  const off = onWorkerEvent(msg => {
    if (msg.type === "scan:page") status.textContent = `Saved page ${msg.pages}: ${fmtInt(msg.total)} items.`;
    if (msg.type === "scan:error") status.textContent = msg.error;
  });
  render(root, h("div.card", h("h2", "Scan your Google Photos library"),
    h("p.sub", "Keep one signed-in Google Photos tab open. Metadata is saved page by page; you can stop and resume. No Takeout export is needed."),
    h("div.row", scan, restart, stop), summary),
    h("div.card", h("h2", "Find similar photos locally"),
    h("p.sub", "Downloads 512-pixel previews, then compares them on this computer. Videos are cataloged but excluded. Thumbnail matches are suggestions: check originals before trashing."),
    h("label.check", clip, "Also use local CLIP embeddings (requires the optional model setup)"),
    h("div.row", analyze), status));
  await refresh();
  // Recover monitoring when revisiting this tab during an analysis.
  const jobs = await api.get("/api/jobs");
  const running = jobs.find(j => j.name === "direct-analysis" && j.status === "running");
  if (running) {analysisId = running.id; status.textContent = "Analysis is running. Use Stop to cancel, or refresh Duplicates when it finishes.";}
  return () => {disposed = true; off();};
}
