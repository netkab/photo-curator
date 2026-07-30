/**
 * Storage tab — reclaim disk space from redundant local duplicates.
 *
 * The naive version of this idea is dangerous, so the framing matters: the photos that feel most
 * "already handled" are usually the ones you deleted from Google Photos long ago, which makes your
 * Takeout copy the *only* copy left. Those are shown here as protected and are never candidates.
 *
 * Files are moved, never deleted, and every run writes a manifest that can be undone.
 */
import { h, render, fmtInt, fmtBytes, fmtDate, send } from "../../lib/dom.js";

export async function storageTab(root, ctx) {
  const { api, toast } = ctx;

  const summaryEl = h("div.stats");
  const previewEl = h("div");
  const runsEl = h("div");
  const includeVideos = h("input", { type: "checkbox" });
  let busy = false;
  let plan = null;

  function stat(label, value, note, kind = "") {
    return h(`div.stat.${kind}`, h("div.k", label), h("div.v", value), note && h("div.n", note));
  }

  async function loadSummary() {
    try {
      const s = await api.reclaimSummary();
      render(summaryEl,
        stat("Library on disk", `${s.total.gb} GB`, `${fmtInt(s.total.files)} files`),
        stat("Backed by Google", `${s.backed_by_google.gb} GB`,
             `${fmtInt(s.backed_by_google.files)} files also in Google Photos`, "good"),
        stat("Sole copies", `${s.sole_copies.gb} GB`,
             `${fmtInt(s.sole_copies.files)} files — last copy anywhere`, "bad"),
        stat("Already quarantined", `${s.archived.gb} GB`, `${fmtInt(s.archived.files)} files`),
      );
      return s;
    } catch (err) {
      render(summaryEl, h("div.hint.danger", String(err.message || err)));
      return null;
    }
  }

  async function loadPreview() {
    if (busy) return;
    busy = true;
    render(previewEl, h("p.sub", "Checking every duplicate against Google Photos and the disk…"));
    try {
      plan = await api.reclaimPreview({ include_videos: includeVideos.checked });
      paintPreview();
    } catch (err) {
      render(previewEl, h("div.hint.danger", String(err.message || err)));
    } finally {
      busy = false;
    }
  }

  const SKIP_LABEL = {
    keeper_not_in_google: "keeper isn't in Google Photos — your disk copy would be the only one",
    sha_unique_elsewhere: "no other copy of those exact bytes exists",
    keeper: "the file is itself a keeper of another group",
    file_missing: "file already gone from disk",
    keeper_file_missing: "keeper file no longer on disk",
    video: "video (excluded unless you tick the box)",
    already_archived: "already moved in an earlier run",
  };

  function paintPreview() {
    if (!plan) return render(previewEl);

    const skipped = Object.entries(plan.skipped || {}).filter(([, n]) => n > 0)
      .sort((a, b) => b[1] - a[1]);

    render(previewEl,
      h("div.row", { style: { marginBottom: "12px" } },
        h("div", { style: { flex: "1" } },
          h("div", { style: { fontSize: "22px", fontWeight: "600" } },
            `${plan.gb} GB in ${fmtInt(plan.candidates)} files`),
          h("p.sub", { style: { margin: "2px 0 0" } },
            plan.candidates ? `Would move to ${plan.quarantine_dir}` : "Nothing is safe to move."),
        ),
        h("button.primary", { disabled: !plan.candidates || busy, onclick: apply },
          "Move to quarantine"),
      ),

      skipped.length > 0 && h("div",
        h("p.sub", "Excluded by the safety rule:"),
        h("table", h("tbody", skipped.map(([reason, n]) => h("tr",
          h("td.num", { style: { width: "80px" } }, fmtInt(n)),
          h("td", SKIP_LABEL[reason] || reason))))),
      ),

      plan.sample?.length > 0 && h("div", { style: { marginTop: "16px" } },
        h("p.sub", "Largest candidates:"),
        h("table",
          h("thead", h("tr", h("th", "File"), h("th", "Keeper it duplicates"), h("th.num", "Size"))),
          h("tbody", plan.sample.map((c) => h("tr",
            h("td", c.name),
            h("td", { style: { color: "var(--muted)" } }, c.keeper_name),
            h("td.num", fmtBytes(c.bytes)))))),
      ),
    );
  }

  async function apply() {
    if (!plan?.candidates || busy) return;
    const ok = confirm(
      `Move ${fmtInt(plan.candidates)} files (${plan.gb} GB) to:\n${plan.quarantine_dir}\n\n` +
      "Nothing is deleted. The catalog follows the files, and the whole run can be undone.\n\n" +
      "Every file being moved has a keeper that exists both on disk AND in Google Photos, so two " +
      "independent copies of the content survive.",
    );
    if (!ok) return;

    busy = true;
    paintPreview();
    try {
      const job = await api.reclaimRun({ include_videos: includeVideos.checked });
      toast("Moving files…");
      const done = await pollJob(job.id);
      const r = done.result || {};
      toast(`Moved ${fmtInt(r.moved)} files (${r.gb} GB). Undo from the history below.`, "ok");
      plan = null;
      await Promise.all([loadSummary(), loadRuns()]);
      render(previewEl, h("p.sub", "Run a preview again to see what's left."));
    } catch (err) {
      toast(String(err.message || err), "err");
    } finally {
      busy = false;
    }
  }

  async function pollJob(id) {
    for (;;) {
      const j = await api.job(id);
      if (j.status !== "running") {
        if (j.status === "error") throw new Error(j.error || "job failed");
        return j;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
  }

  async function loadRuns() {
    try {
      const { items } = await api.reclaimRuns();
      render(runsEl, items.length
        ? h("div.card",
            h("h2", "History"),
            h("p.sub", "Each run has a manifest. Undo moves every file back where it came from."),
            h("table",
              h("thead", h("tr", h("th", "Run"), h("th", "When"), h("th.num", "Files"),
                           h("th.num", "Size"), h("th", ""))),
              h("tbody", items.map((r) => h("tr",
                h("td", r.run_id),
                h("td", { style: { color: "var(--muted)" } }, fmtDate(r.created_at)),
                h("td.num", fmtInt(r.moved)),
                h("td.num", `${r.gb} GB`),
                h("td", h("button.small", { disabled: busy, onclick: () => undo(r) }, "Undo")))))))
        : null);
    } catch {
      render(runsEl);
    }
  }

  async function undo(r) {
    if (!confirm(`Restore ${fmtInt(r.moved)} files (${r.gb} GB) back to the Takeout folder?`)) return;
    busy = true;
    try {
      const job = await api.reclaimUndo(r.run_id);
      const done = await pollJob(job.id);
      toast(`Restored ${fmtInt(done.result?.restored)} files.`, "ok");
      await Promise.all([loadSummary(), loadRuns()]);
    } catch (err) {
      toast(String(err.message || err), "err");
    } finally {
      busy = false;
    }
  }

  render(root,
    h("div.card",
      h("h2", "Storage"),
      h("p.sub", "Where your library's bytes are, and which of them are genuinely redundant."),
      summaryEl,
      h("div.hint", { style: { marginTop: "16px" } },
        h("strong", "Sole copies are never touched. "),
        "A file that is no longer in Google Photos is the last copy in existence — those look the " +
        "most \"already dealt with\", which is exactly why deleting by that instinct loses photos."),
    ),

    h("div.card",
      h("h2", "Reclaim duplicates"),
      h("p.sub",
        "Moves a local duplicate only when its keeper exists on disk AND is live in Google Photos, " +
        "so two independent copies of the content always survive. Files are moved, never deleted."),
      h("div.row",
        h("button", { onclick: loadPreview }, "Preview"),
        h("label.check", includeVideos, "Include videos"),
        h("span.spacer"),
      ),
      h("div", { style: { marginTop: "14px" } }, previewEl),
    ),

    runsEl,
  );

  await Promise.all([loadSummary(), loadRuns()]);
  render(previewEl, h("p.sub", "Click Preview to see what can be reclaimed. Nothing moves until you confirm."));

  return () => {};
}
