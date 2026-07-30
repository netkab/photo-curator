/**
 * Library Sync tab — build and inspect the bridge between the local catalog and Google Photos.
 *
 * This is the foundation every other tab stands on. Until an item has been seen here and matched
 * to a catalog row, the extension has no `dedupKey` for it and therefore cannot act on it at all.
 */
import { h, render, fmtInt, fmtPct, fmtDate, fmtBytes } from "../../lib/dom.js";

export async function syncTab(root, ctx) {
  const { api, send, toast, onWorkerEvent } = ctx;

  const logEl = h("div.log");
  const statsEl = h("div.stats");
  const methodsEl = h("div");
  const unmatchedEl = h("div");

  let scanning = false;

  const log = (msg, kind = "") => {
    logEl.prepend(h(`div.${kind}`, `${new Date().toLocaleTimeString()}  ${msg}`));
    while (logEl.childElementCount > 300) logEl.lastElementChild.remove();
  };

  // ── actions ───────────────────────────────────────────────────────────────
  const btnScan = h("button.primary", { onclick: startScan }, "Scan library");
  const btnCancel = h("button", { onclick: cancelScan, disabled: true }, "Stop");
  const btnLink = h("button", { onclick: () => runLink(false) }, "Re-run matching");
  const btnRelink = h("button.ghost", { onclick: () => runLink(true) }, "Retry unmatched");

  function setScanning(on) {
    scanning = on;
    btnScan.disabled = on;
    btnCancel.disabled = !on;
    btnLink.disabled = on;
    btnRelink.disabled = on;
    btnScan.textContent = on ? "Scanning…" : "Scan library";
  }

  async function startScan() {
    setScanning(true);
    log("Starting library scan…");
    const res = await send({ type: "PC_SCAN", args: {} });
    setScanning(false);
    if (!res?.ok) {
      log(res?.error || "Scan failed", "err");
      toast(res?.error || "Scan failed", "err");
      return;
    }
    if (res.cancelled) {
      log(`Stopped after ${fmtInt(res.total)} items — everything fetched so far is saved.`, "ok");
    } else {
      log(`Scanned ${fmtInt(res.total)} items across ${res.pages} pages.`, "ok");
      await runLink(false);
    }
    await refresh();
  }

  async function cancelScan() {
    await send({ type: "PC_SCAN_CANCEL" });
    log("Stopping after the current page…");
  }

  async function runLink(relink) {
    btnLink.disabled = btnRelink.disabled = true;
    log(relink ? "Retrying previously unmatched items…" : "Matching against the catalog…");
    try {
      const job = await api.gpLink(relink);
      const done = await pollJob(job.id, (j) => log(j.message || "working…"));
      const r = done.result || {};
      log(`Matched ${fmtInt(r.linked)} of ${fmtInt(r.considered)} · ` +
          `${fmtInt(r.ambiguous)} ambiguous · ${fmtInt(r.unmatched)} unmatched`, "ok");
    } catch (err) {
      log(String(err.message || err), "err");
      toast(String(err.message || err), "err");
    } finally {
      btnLink.disabled = btnRelink.disabled = false;
      await refresh();
    }
  }

  async function pollJob(id, onTick) {
    for (;;) {
      const j = await api.job(id);
      // Job statuses are running | done | error | cancelled — anything but "running" is terminal,
      // so treat it that way rather than listing the endings and looping forever on a new one.
      if (j.status !== "running") {
        if (j.status === "error") throw new Error(j.error || "job failed");
        return j;
      }
      onTick?.(j);
      await new Promise((r) => setTimeout(r, 800));
    }
  }

  // ── rendering ─────────────────────────────────────────────────────────────
  function stat(label, value, note, kind = "") {
    return h(`div.stat.${kind}`, h("div.k", label), h("div.v", value), note && h("div.n", note));
  }

  async function refresh() {
    let st;
    try {
      st = await api.gpStatus();
    } catch (err) {
      render(statsEl, h("div.hint.danger", String(err.message || err)));
      return;
    }

    const coverageKind = st.gp_total === 0 ? "" : st.coverage >= 0.95 ? "good"
      : st.coverage >= 0.8 ? "warn" : "bad";

    render(statsEl,
      stat("In Google Photos", fmtInt(st.gp_total), st.last_sync ? `synced ${fmtDate(st.last_sync)}` : "never synced"),
      stat("Linked to catalog", fmtInt(st.linked), fmtPct(st.coverage) + " coverage", coverageKind),
      stat("Google-only", fmtInt(st.gp_only), "uploaded after your Takeout export"),
      stat("Catalog-only", fmtInt(st.catalog_only), `of ${fmtInt(st.catalog_total)} local rows`),
      stat("Ambiguous", fmtInt(st.ambiguous), "needs a manual decision", st.ambiguous ? "warn" : ""),
    );

    const methods = Object.entries(st.by_method || {}).sort((a, b) => b[1] - a[1]);
    render(methodsEl, methods.length
      ? h("table",
          h("thead", h("tr", h("th", "Match pass"), h("th.num", "Items"))),
          h("tbody", methods.map(([m, n]) => h("tr", h("td", describeMethod(m)), h("td.num", fmtInt(n))))))
      : h("p.sub", "Nothing matched yet."));

    if (st.gp_total && st.coverage < 1) await refreshUnmatched();
    else render(unmatchedEl);
  }

  async function refreshUnmatched() {
    try {
      const [gp, cat] = await Promise.all([api.gpUnmatched("gp", 12), api.gpUnmatched("catalog", 12)]);
      render(unmatchedEl,
        h("div.card",
          h("h2", "Unmatched"),
          h("p.sub",
            "Check these before trusting a large trash run. Items only in Google Photos were most " +
            "likely uploaded after your Takeout export; items only in the catalog were most likely " +
            "already deleted."),
          h("div.row", { style: { alignItems: "flex-start", gap: "24px" } },
            unmatchedList(`In Google Photos only (${fmtInt(gp.total)})`, gp.items.map((i) => ({
              name: i.file_name || i.media_key, meta: `${fmtDate(i.taken_at)} · ${i.width}×${i.height}`,
              href: i.product_url,
            }))),
            unmatchedList(`In the catalog only (${fmtInt(cat.total)})`, cat.items.map((i) => ({
              name: i.name, meta: `${fmtDate(i.taken_at)} · ${i.width}×${i.height} · ${fmtBytes(i.bytes)}`,
            }))),
          )));
    } catch (err) {
      render(unmatchedEl, h("div.hint.warn", String(err.message || err)));
    }
  }

  function unmatchedList(title, rows) {
    return h("div", { style: { flex: "1", minWidth: "300px" } },
      h("p.sub", title),
      rows.length
        ? h("table", h("tbody", rows.map((r) => h("tr",
            h("td", r.href ? h("a", { href: r.href, target: "_blank", rel: "noreferrer" }, r.name) : r.name),
            h("td.num", { style: { color: "var(--muted)", fontSize: "12px" } }, r.meta)))))
        : h("p.sub", "None."));
  }

  function describeMethod(m) {
    return {
      "name+ts": "Filename + capture time (strongest)",
      "name+dims": "Filename + dimensions",
      "name-unique": "Filename, unique on both sides",
      "ts+dims": "Capture time + dimensions",
      manual: "Linked by hand",
      ambiguous: "Ambiguous — more than one candidate",
      none: "No candidate found",
      pending: "Not matched yet",
    }[m] || m;
  }

  // ── worker events ─────────────────────────────────────────────────────────
  const off = onWorkerEvent((msg) => {
    if (msg.type === "scan:start") log(`Signed in as ${msg.account}. Fetching pages of 500…`);
    else if (msg.type === "scan:page") log(`Page ${msg.pages} · ${fmtInt(msg.total)} items (${fmtInt(msg.inserted)} new)`);
    else if (msg.type === "scan:done") log(`Scan complete — ${fmtInt(msg.total)} items.`, "ok");
    else if (msg.type === "scan:error") log(msg.error, "err");
    else if (msg.type === "page:progress" && msg.message) log(msg.message);
  });

  // ── paint ─────────────────────────────────────────────────────────────────
  render(root,
    h("div.card",
      h("h2", "Library sync"),
      h("p.sub",
        "Reads your Google Photos library through the open Photos tab and links each item to the " +
        "local Takeout catalog. Read-only — nothing is changed here."),
      h("div.row", btnScan, btnCancel, h("span.spacer"), btnLink, btnRelink),
      logEl,
    ),
    h("div.card", h("h2", "Coverage"), h("p.sub", "How much of your library the extension can act on."), statsEl,
      h("div", { style: { marginTop: "16px" } }, methodsEl)),
    unmatchedEl,
  );

  await refresh();

  return () => { off(); };
}
