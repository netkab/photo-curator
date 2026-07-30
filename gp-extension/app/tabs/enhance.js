/**
 * Enhance tab — improve blurry photos locally, then retire the originals in Google Photos.
 *
 * Enhancement itself cannot move into the extension: Real-ESRGAN needs the original pixels and
 * Google only ever serves a downscaled thumbnail. So the loop is
 *
 *     enhance locally (GPU)  ->  upload the improved copy  ->  trash the blurry original
 *
 * and this tab owns the first and last steps. The middle one needs the appendonly OAuth upload; if
 * that isn't configured the backend exports files for manual upload instead, and retiring stays
 * blocked until the replacement is confirmed uploaded — trashing an original before its replacement
 * is safely in Google would leave you with neither.
 */
import { h, render, fmtInt, fmtDate, send } from "../../lib/dom.js";

export async function enhanceTab(root, ctx) {
  const { api, toast, onWorkerEvent } = ctx;

  const headEl = h("div");
  const listEl = h("div.groups");
  const resultsEl = h("div");

  let data = null;
  let results = [];
  let busy = false;
  const selected = new Set();

  function thumb(item) {
    const img = h("img", { alt: item.name || "", loading: "lazy" });
    if (item.gp?.thumb_url) img.src = `${item.gp.thumb_url}=w256-h256-k-no`;
    else if (item.thumb) {
      send({ type: "GET_IMAGE", url: `${api.API}${item.thumb}` })
        .then((r) => { if (r?.ok) img.src = r.dataUrl; });
    }
    return img;
  }

  async function load() {
    try {
      [data, results] = await Promise.all([api.enhanceCandidates(40), api.enhanceResults()]);
    } catch (err) {
      render(listEl, h("div.hint.danger", String(err.message || err)));
      return;
    }
    paint();
  }

  function paint() {
    const items = data?.items || [];
    const uploadsOff = data && !data.uploads_enabled;

    render(headEl,
      h("div.row",
        h("span", `${fmtInt(data?.total || 0)} blurry photos still in Google Photos ` +
                  `(sharpness below ${data?.threshold ?? "—"})`),
        h("span.spacer"),
        h("button.small", {
          disabled: busy || !selected.size,
          onclick: () => enhanceSelected(),
        }, `Enhance ${selected.size || ""} selected`),
        h("button.small.ghost", { disabled: busy, onclick: load }, "Refresh"),
      ),
      uploadsOff && h("div.hint.warn", { style: { marginTop: "10px" } },
        h("strong", "Uploads aren't configured. "),
        "Enhanced photos will be exported to a folder for you to upload by hand, and originals " +
        "can't be retired until their replacement is confirmed in Google Photos. Set ",
        h("code", "PHOTOS_OAUTH_CLIENT_ID"), " and ", h("code", "PHOTOS_OAUTH_CLIENT_SECRET"),
        " in backend/.env to automate it — see the guide."),
    );

    render(listEl, items.length
      ? items.map(card)
      : h("div.empty",
          data ? "No blurry photos found that are still in Google Photos." : "Loading…"));

    paintResults();
  }

  function card(item) {
    const isSel = selected.has(item.media_id);
    const state = item.derived?.status;

    return h("div.group",
      h("div.group-head",
        h("span.date", fmtDate(item.taken_at)),
        h("span.pill", `sharpness ${item.blur_score}`),
        state && h(`span.pill.${state === "uploaded" ? "ok" : ""}`, state),
        h("span.spacer"),
        h("label.check",
          h("input", {
            type: "checkbox", checked: isSel, disabled: busy || !!state,
            onchange: (e) => {
              if (e.target.checked) selected.add(item.media_id);
              else selected.delete(item.media_id);
              paint();
            },
          }),
          state ? "already enhanced" : "select"),
        state === "uploaded" && h("button.danger.small", {
          disabled: busy, onclick: () => retire([item.media_id]),
        }, "Retire original"),
      ),
      h("div.tiles",
        h("div.tile.del", thumb(item), h("div.tag", "BLURRY"), h("div.cap", item.name),
          h("div.cap", `${item.width || "?"}×${item.height || "?"}`),
          item.gp?.product_url && h("a", {
            href: item.gp.product_url, target: "_blank", rel: "noreferrer",
          }, "open in Google Photos")),
        item.derived && h("div.tile.keep",
          h("img", { src: `${api.API}${item.derived.after}`, alt: "enhanced", loading: "lazy" }),
          h("div.tag", "ENHANCED"), h("div.cap", "local result")),
      ),
    );
  }

  function paintResults() {
    const pending = results.filter((r) => r.derived_id);
    render(resultsEl, pending.length
      ? h("div.card",
          h("h2", "Awaiting approval"),
          h("p.sub",
            "Approve to queue the upload. The original stays untouched until its replacement is " +
            "confirmed in Google Photos."),
          h("div.tiles", pending.map((r) => h("div", { style: { position: "relative" } },
            h("div.tile.keep",
              h("img", { src: `${api.API}${r.after}`, alt: r.source_name, loading: "lazy" }),
              h("div.tag", "ENHANCED"),
              h("div.cap", r.source_name || "")),
            h("button.small.primary", {
              style: { marginTop: "6px", width: "168px" }, disabled: busy,
              onclick: () => approve(r.derived_id),
            }, "Approve upload"),
          ))))
      : null);
  }

  async function enhanceSelected() {
    if (!selected.size || busy) return;
    busy = true;
    paint();
    try {
      const job = await api.enhanceRun({ ids: [...selected], blurry: false });
      toast(`Enhancing ${selected.size} photo(s) on the GPU…`);
      await pollJob(job.id);
      selected.clear();
      toast("Enhancement finished — review the results below.", "ok");
      await load();
    } catch (err) {
      toast(String(err.message || err), "err");
    } finally {
      busy = false;
      paint();
    }
  }

  async function approve(derivedId) {
    try {
      await api.enhanceApprove(derivedId);
      toast("Queued for upload — apply it from the Review Queue in the web UI.", "ok");
      await load();
    } catch (err) {
      toast(String(err.message || err), "err");
    }
  }

  async function retire(mediaIds) {
    const ok = confirm(
      `Move ${mediaIds.length} original(s) to the Google Photos bin?\n\n` +
      "Only originals whose enhanced replacement is already uploaded can be retired.\n" +
      "Recoverable for 60 days, and undoable from the Duplicates tab's Operations panel.",
    );
    if (!ok) return;

    busy = true;
    try {
      const r = await api.retireOriginals(mediaIds);
      if (r.blocked?.length) {
        toast(`${r.blocked.length} blocked: ${r.blocked[0].reason}`, "err");
      }
      if (r.operation_id) {
        toast(`Retiring ${fmtInt(r.scheduled)} original(s)…`);
        send({ type: "PC_RUN_OP", opId: r.operation_id });
      }
      await load();
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
      await new Promise((r) => setTimeout(r, 1200));
    }
  }

  const off = onWorkerEvent(async (msg) => {
    if (msg.type === "op:done") { toast("Originals retired.", "ok"); await load(); }
    else if (msg.type === "op:error" || msg.type === "op:paused") toast(msg.error, "err");
  });

  render(root,
    h("div.card",
      h("h2", "Enhance blurry photos"),
      h("p.sub",
        "Restoration runs locally on the GPU (Real-ESRGAN + GFPGAN) because it needs the original " +
        "pixels — Google only serves downscaled thumbnails. The extension handles the last step: " +
        "retiring the blurry original once its replacement is safely in Google Photos."),
      headEl,
    ),
    resultsEl,
    listEl,
  );

  await load();
  return () => { off(); };
}
