/**
 * Video tab — review reels and compressed clips, upload them, then retire the sources they replace.
 *
 * The asymmetry between the two kinds is the whole design:
 *
 *   compressed  — a 1:1 replacement of one clip. Retiring its source is the obvious next step.
 *   highlight   — a montage cut from many clips. A 30-second reel is NOT a substitute for the
 *                 forty videos it samples, so nothing is pre-selected and there is no "retire all"
 *                 shortcut. You pick, clip by clip, what you're willing to lose.
 *
 * Both share the guard that matters: a source can only be retired once its replacement has actually
 * reached Google Photos. Anything else risks ending up with neither.
 */
import { h, render, fmtInt, fmtBytes, fmtDate, send } from "../../lib/dom.js";
import { API } from "../../lib/api.js";

export async function videoTab(root, ctx) {
  const { api, toast, onWorkerEvent } = ctx;

  const headEl = h("div");
  const listEl = h("div.groups");

  let rows = [];
  let busy = false;
  // derived_id -> Set(media_id) the user has marked for retirement. Opt-in, never pre-filled.
  const retire = new Map();

  const pick = (id) => retire.get(id) || retire.set(id, new Set()).get(id);

  async function load() {
    try {
      const [pending, approved, uploaded] = await Promise.all([
        api.videoResults("pending"), api.videoResults("approved"), api.videoResults("uploaded"),
      ]);
      rows = [...pending, ...approved, ...uploaded];
    } catch (err) {
      render(listEl, h("div.hint.danger", String(err.message || err)));
      return;
    }
    paint();
  }

  function paint() {
    const uploadsOff = rows.length && !rows[0].uploads_enabled;
    const reels = rows.filter((r) => r.kind === "highlight");
    const comp = rows.filter((r) => r.kind === "compressed");

    render(headEl,
      h("div.row",
        h("span", `${fmtInt(reels.length)} highlight reel${reels.length === 1 ? "" : "s"} · ` +
                  `${fmtInt(comp.length)} compressed`),
        h("span.spacer"),
        h("button.small", { disabled: busy, onclick: () => runJob("highlights") }, "Build more reels"),
        h("button.small", { disabled: busy, onclick: () => runJob("compress") }, "Compress videos"),
        h("button.small.ghost", { disabled: busy, onclick: load }, "Refresh"),
      ),
      uploadsOff && h("div.hint.warn", { style: { marginTop: "10px" } },
        h("strong", "Uploads aren't configured, so nothing can be uploaded or retired yet. "),
        "Approved files are exported to a folder for you to upload by hand instead. Set ",
        h("code", "PHOTOS_OAUTH_CLIENT_ID"), " and ", h("code", "PHOTOS_OAUTH_CLIENT_SECRET"),
        " in ", h("code", "backend/.env"), " — the walkthrough is in that file, about five minutes ",
        "in the Google Cloud console, and the scope requested (", h("code", "appendonly"),
        ") cannot read, edit or delete anything already in your library."),
    );

    render(listEl, rows.length
      ? rows.map(card)
      : h("div.empty", "No reels or compressed clips yet. Build some above."));
  }

  function card(r) {
    const sel = pick(r.derived_id);
    const isReel = r.kind === "highlight";
    const uploaded = r.status === "uploaded";
    const liveSel = r.sources.filter((s) => sel.has(s.id) && s.live).length;
    const freed = r.sources.filter((s) => sel.has(s.id)).reduce((n, s) => n + (s.bytes || 0), 0);

    return h("div.group",
      h("div.group-head",
        h("span.date", isReel ? (r.meta?.cluster || "reel") : (r.sources[0]?.name || "clip")),
        h("span.pill", r.kind),
        h(`span.pill${uploaded ? ".ok" : ""}`, r.status),
        h("span.pill", `${fmtInt(r.sources.length)} source${r.sources.length === 1 ? "" : "s"}`),
        r.live_sources < r.sources.length &&
          h("span.pill.bad", `${fmtInt(r.sources.length - r.live_sources)} not in Google`),
        h("span.spacer"),
        r.status === "pending" && h("button.small.primary", {
          disabled: busy, onclick: () => approveAndUpload(r),
        }, r.uploads_enabled ? "Approve & upload" : "Approve (export)"),
        r.status === "approved" && h("button.small.primary", {
          disabled: busy, onclick: () => applyUpload(r),
        }, "Upload now"),
        uploaded && h("button.danger.small", {
          disabled: busy || !liveSel,
          title: liveSel ? "" : "Select the source clips you want to retire first.",
          onclick: () => retireSelected(r),
        }, liveSel ? `Retire ${fmtInt(liveSel)} (${fmtBytes(freed)})` : "Retire sources"),
      ),

      h("video", {
        src: `${API}${r.url}`, controls: true, preload: "metadata",
        style: { width: "320px", borderRadius: "8px", background: "#000", display: "block" },
      }),

      isReel && h("div.hint.warn", { style: { marginTop: "10px" } },
        h("strong", "This reel is a montage, not a replacement. "),
        `It samples ${fmtInt(r.sources.length)} clips down to one cut — retiring a source means ` +
        "losing that footage. Nothing is selected for you; tick only what you're happy to lose."),

      h("div", { style: { marginTop: "10px" } },
        h("div.row",
          h("span.sub", "Source clips"),
          h("span.spacer"),
          uploaded && h("button.small.ghost", {
            disabled: busy,
            onclick: () => {
              const live = r.sources.filter((s) => s.live).map((s) => s.id);
              if (live.every((id) => sel.has(id))) live.forEach((id) => sel.delete(id));
              else live.forEach((id) => sel.add(id));
              paint();
            },
          }, "Toggle all"),
        ),
        h("div.tiles", r.sources.map((s) => sourceTile(r, s, sel, uploaded))),
      ),
    );
  }

  function sourceTile(r, s, sel, uploaded) {
    const marked = sel.has(s.id);
    const img = h("img", { alt: s.name || "", loading: "lazy" });
    if (s.thumb) {
      send({ type: "GET_IMAGE", url: `${API}${s.thumb}` })
        .then((res) => { if (res?.ok) img.src = res.dataUrl; });
    }

    return h("div", { style: { width: "168px" } },
      h(`div.tile.${marked ? "del" : "keep"}${s.live ? "" : ".dead"}`,
        img,
        h("div.tag", marked ? "RETIRE" : "KEEP"),
        h("div.cap", s.name || "—"),
        h("div.cap", `${fmtBytes(s.bytes)} · ${fmtDate(s.taken_at)}`),
        s.gp?.product_url
          ? h("a", { href: s.gp.product_url, target: "_blank", rel: "noreferrer" }, "open in Google Photos")
          : h("div.cap.gone", "not in Google Photos"),
      ),
      h("button.small", {
        class: marked ? "wipe" : "",
        style: { marginTop: "6px", width: "168px" },
        // Only meaningful once the replacement is safely uploaded, and only for clips Google still has.
        disabled: busy || !uploaded || !s.live,
        title: !uploaded ? "Upload the replacement first."
          : !s.live ? "Not in Google Photos — nothing to retire."
          : marked ? "Currently marked for retirement — click to keep." : "Mark this clip to retire.",
        onclick: () => { marked ? sel.delete(s.id) : sel.add(s.id); paint(); },
      }, marked ? "✓ Retiring" : "Retire this"),
    );
  }

  // ── actions ───────────────────────────────────────────────────────────────
  async function runJob(kind) {
    busy = true; paint();
    try {
      const job = kind === "highlights" ? await api.videoHighlights() : await api.videoCompress();
      toast(`Started — this is a GPU job and runs for a while.`);
      await pollJob(job.id, (j) => { if (j.message) toast(j.message); });
      toast("Finished.", "ok");
      await load();
    } catch (err) {
      toast(String(err.message || err), "err");
    } finally { busy = false; paint(); }
  }

  async function approveAndUpload(r) {
    busy = true; paint();
    try {
      await api.videoApprove(r.derived_id);
      if (!r.uploads_enabled) {
        toast("Approved. Uploads aren't configured, so it'll be exported to a folder on apply.", "ok");
        await load();
        return;
      }
      await load();
      const fresh = rows.find((x) => x.derived_id === r.derived_id);
      if (fresh) await applyUpload(fresh, true);
    } catch (err) {
      toast(String(err.message || err), "err");
    } finally { busy = false; paint(); }
  }

  /** Find and apply the queued upload action for this derived file. */
  async function applyUpload(r, quiet = false) {
    if (!quiet) { busy = true; paint(); }
    try {
      const actions = await api.reviewActions("approved");
      const mine = actions.find((a) => a.kind === "upload" && a.payload?.derived_id === r.derived_id);
      if (!mine) { toast("No pending upload action for this file.", "err"); return; }

      toast("Uploading to Google Photos — this can take a few minutes…");
      const res = await api.applyAction(mine.id);
      const uploaded = res?.result?.uploaded?.[0];
      if (uploaded?.error) toast(`Upload failed: ${uploaded.error}`, "err");
      else if (res?.result?.mode === "manual") {
        toast(`Exported to ${res.result.exported_to} — upload it by hand.`, "ok");
      } else {
        toast("Uploaded. You can now retire the sources it replaces.", "ok");
      }
      await load();
    } catch (err) {
      toast(String(err.message || err), "err");
    } finally { if (!quiet) { busy = false; paint(); } }
  }

  async function retireSelected(r) {
    const sel = pick(r.derived_id);
    const ids = r.sources.filter((s) => sel.has(s.id) && s.live).map((s) => s.id);
    if (!ids.length) return;

    const freed = r.sources.filter((s) => ids.includes(s.id)).reduce((n, s) => n + (s.bytes || 0), 0);
    const ok = confirm(
      `Move ${ids.length} source clip(s) to the Google Photos bin?\n\n` +
      (r.kind === "highlight"
        ? "These are SOURCE footage for a montage — the reel does not contain all of it.\n"
        : "The compressed replacement is already uploaded.\n") +
      `About ${fmtBytes(freed)} in Google Photos.\n\n` +
      "Recoverable from the bin for 60 days and undoable from the Duplicates tab. " +
      "Your local originals are not touched.",
    );
    if (!ok) return;

    busy = true; paint();
    try {
      const res = await api.videoRetireSources(r.derived_id, ids);
      if (res.blocked?.length) toast(`${res.blocked.length} blocked: ${res.blocked[0].reason}`, "err");
      if (res.operation_id) {
        toast(`Retiring ${fmtInt(res.scheduled)} clip(s)…`);
        sel.clear();
        send({ type: "PC_RUN_OP", opId: res.operation_id });
      }
      await load();
    } catch (err) {
      toast(String(err.message || err), "err");
    } finally { busy = false; paint(); }
  }

  async function pollJob(id, onTick) {
    for (;;) {
      const j = await api.job(id);
      if (j.status !== "running") {
        if (j.status === "error") throw new Error(j.error || "job failed");
        return j;
      }
      onTick?.(j);
      await new Promise((res) => setTimeout(res, 3000));
    }
  }

  const off = onWorkerEvent(async (msg) => {
    if (msg.type === "op:done") { toast("Sources retired.", "ok"); await load(); }
    else if (msg.type === "op:error" || msg.type === "op:paused") toast(msg.error, "err");
  });

  render(root,
    h("div.card",
      h("h2", "Video"),
      h("p.sub",
        "Compression and highlight reels run on the GPU against the original files — Google only " +
        "serves downscaled thumbnails, so none of it can be done from the live library. The " +
        "extension handles the rest: upload the result, then retire what it replaces."),
      headEl,
    ),
    listEl,
  );

  await load();
  return () => { off(); };
}
