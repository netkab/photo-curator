import {h, render, fmtInt} from "../../lib/dom.js";
export async function syncTab(root, ctx) {
  const {api, send, toast, onWorkerEvent} = ctx;
  const scanStatus = h("p", {role: "status", "aria-live": "polite"});
  const status = h("p", {role: "status", "aria-live": "polite"});
  const summary = h("p");
  let analysisId = null;
  let fetchingThumbnails = false;
  let stopRequested = false;
  let previewTotals = {cached: 0, failed: 0, skipped: 0};
  let disposed = false;
  const scan = h("button.primary", {onclick: () => start(false)}, "Scan / resume library");
  const restart = h("button", {onclick: () => start(true)}, "Rescan from newest");
  const stop = h("button", {onclick: async () => {
    stopRequested = true;
    await send({type: "PC_SCAN_CANCEL"});
    if (analysisId) await api.post(`/api/jobs/${analysisId}/cancel`);
    const target = analysisId || fetchingThumbnails ? status : scanStatus;
    target.textContent = "Stopping after the current page or thumbnail. Saved progress is retained.";
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
    busy(true); scanStatus.textContent = "Reading Google Photos metadata…";
    try {
      const result = await send({type: "PC_SCAN", args: {restart: fromNewest}});
      if (!result?.ok) throw new Error(result?.error || "Scan failed");
      scanStatus.textContent = `${result.cancelled ? "Stopped" : "Scan complete"}: ${fmtInt(result.total)} items saved. Next, fetch thumbnails.`;
    } catch (e) {
      scanStatus.textContent = e.message + " Saved pages remain; resume or rescan to retry.";
      toast(e.message, "err");
    }
    finally {busy(false); await refresh();}
  }
  async function runAnalysis() {
    busy(true);
    try {
      fetchingThumbnails = true;
      stopRequested = false;
      previewTotals = {cached: 0, failed: 0, skipped: 0};
      status.textContent = "Fetching previews through your signed-in Google Photos tab…";
      let after = 0;
      for (;;) {
        const fetched = await send({type: "PC_FETCH_THUMBNAILS", args: {after}});
        if (!fetched?.ok) throw new Error(fetched?.error || "Thumbnail downloads failed");
        if (disposed) return;
        if (stopRequested || fetched.cancelled) {status.textContent = "Stopped. Downloaded previews are saved; click Fetch thumbnails to resume."; return;}
        for (const key of ["cached", "failed", "skipped"]) previewTotals[key] += fetched[key] || 0;
        status.textContent = `Downloaded ${fmtInt(previewTotals.cached)} previews; ${fmtInt(previewTotals.skipped)} animated previews skipped; ${fmtInt(previewTotals.failed)} unavailable.`;
        if (!fetched.more) break;
        if (fetched.after <= after) throw new Error("Thumbnail cursor did not advance; stopped safely");
        after = fetched.after;
      }
      fetchingThumbnails = false;
      const j = await api.post("/api/direct/analyze", {use_clip: clip.checked, cached_only: true});
      analysisId = j.id;
      for (;;) {
        const current = await api.job(j.id);
        if (disposed) return;
        status.textContent = current.message || "Analyzing thumbnails…";
        if (current.status === "error") throw new Error(current.error?.split("\n")[0] || "Thumbnail analysis failed");
        if (current.status !== "running") {
          const result = current.result || {};
          status.textContent = `${current.status}: ${fmtInt(result.groups)} candidate groups; ` +
            `${fmtInt((result.cached || 0) + (result.already_cached || 0))} previews available locally; ` +
            `${fmtInt(result.failed)} unavailable thumbnails. Open Duplicates to review.`;
          if (result.failed) status.textContent += ` First download error: ${result.errors?.[0]?.error || "Unknown"}. Retry analysis after resolving this error.`;
          break;
        }
        await new Promise(r => setTimeout(r, 1000));
      }
    } catch (e) {status.textContent = e.message; toast(e.message, "err");}
    finally {analysisId = null; fetchingThumbnails = false; busy(false); await refresh();}
  }
  const off = onWorkerEvent(msg => {
    if (msg.type === "scan:page") scanStatus.textContent = `Saved page ${msg.pages}: ${fmtInt(msg.total)} items.`;
    if (msg.type === "scan:retry") scanStatus.textContent = `Read failed; retrying (${msg.attempt}/3). ${msg.error}`;
    if (msg.type === "scan:error") scanStatus.textContent = msg.error;
    if (msg.type === "thumbnail:progress") status.textContent = `Downloaded ${fmtInt(previewTotals.cached + (msg.cached || 0))} previews; ${fmtInt(previewTotals.skipped + (msg.skipped || 0))} animated previews skipped; ${fmtInt(previewTotals.failed + (msg.failed || 0))} unavailable.` + (msg.error ? ` ${msg.error}` : "");
    if (msg.type === "thumbnail:error") status.textContent = msg.error;
  });
  render(root, h("div.card", h("h2", "Scan your Google Photos library"),
    h("p.sub", "Keep one signed-in Google Photos tab open. Metadata is saved page by page; you can stop and resume. No Takeout export is needed."),
    h("div.row", scan, restart, stop), scanStatus, summary),
    h("div.card", h("h2", "Find similar photos locally"),
    h("p.sub", "Keep Google Photos open while 512-pixel previews download through your browser. Saved previews are reused when you resume. Videos are cataloged but excluded. Check originals before trashing any suggested matches."),
    h("label.check", clip, "Also use local CLIP embeddings (requires the optional model setup)"),
    h("div.row", analyze), status));
  await refresh();
  // Recover monitoring when revisiting this tab during an analysis.
  const jobs = await api.get("/api/jobs");
  const running = jobs.find(j => j.name === "direct-analysis" && j.status === "running");
  if (running) {analysisId = running.id; status.textContent = "Analysis is running. Use Stop to cancel, or refresh Duplicates when it finishes.";}
  return () => {disposed = true; off();};
}
