/**
 * Maps tab — turn a place cluster into a Google Photos album and a ready-to-paste review.
 *
 * What is automated: building the album. Album membership is your own data, keyed on `mediaKey`
 * (albums are the one Google Photos operation where dedupKey is the wrong identifier).
 *
 * What is NOT automated, deliberately: posting the review, and contributing photos to the public
 * place listing. That is public content about someone else's business, and automated posting is
 * exactly what Google's anti-abuse systems are built to catch — it would put the account at risk in
 * a way that managing your own library does not. So this tab gets everything ready and hands you
 * the last click: text on the clipboard, the write-review page open, and the shortlisted files in a
 * folder ready to drag in.
 */
import { h, render, fmtInt, fmtDate, send } from "../../lib/dom.js";

export async function mapsTab(root, ctx) {
  const { api, toast, onWorkerEvent } = ctx;

  const listEl = h("div.groups");
  const headEl = h("div");
  const detailEl = h("div");

  let clusters = [];
  let open = null;      // { pkg, draft }
  let busy = false;
  let showReviewed = false;

  async function load() {
    try {
      clusters = await api.mapsClusters();
    } catch (err) {
      render(listEl, h("div.hint.danger", String(err.message || err)));
      return;
    }
    paint();
  }

  function paint() {
    const visible = clusters.filter((c) => showReviewed || !c.reviewed);
    render(headEl,
      h("div.row",
        h("span", `${fmtInt(visible.length)} place${visible.length === 1 ? "" : "s"}`),
        h("span.spacer"),
        h("label.check",
          h("input", {
            type: "checkbox", checked: showReviewed,
            onchange: (e) => { showReviewed = e.target.checked; paint(); },
          }), "Show already reviewed"),
        h("button.small.ghost", { disabled: busy, onclick: load }, "Refresh"),
      ),
    );

    render(listEl, visible.length
      ? visible.map(clusterCard)
      : h("div.empty",
          "No place clusters. Run Maps clustering in the Photo Curator web UI first."));
  }

  function clusterCard(c) {
    return h("div.group",
      h("div.group-head",
        h("span.date", c.name || `Place ${c.id}`),
        h("span.pill", `${fmtInt(c.count)} photos`),
        c.matched ? h("span.pill.ok", "matched") : h("span.pill.bad", "no place match"),
        c.reviewed && h("span.pill", "reviewed"),
        h("span.spacer"),
        h("button.small", { disabled: busy, onclick: () => openCluster(c) }, "Prepare"),
      ),
      c.address && h("p.sub", { style: { margin: "0 0 10px" } }, c.address),
      h("div.tiles", (c.thumbs || []).map((t) => {
        const img = h("img", { alt: "", loading: "lazy" });
        if (t.thumb) {
          send({ type: "GET_IMAGE", url: `${api.API}${t.thumb}` })
            .then((r) => { if (r?.ok) img.src = r.dataUrl; });
        }
        return h("div.tile", img);
      })),
      open?.pkg?.cluster_id === c.id && detailEl,
    );
  }

  async function openCluster(c) {
    if (busy) return;
    busy = true;
    render(detailEl, h("p.sub", "Loading package…"));
    paint();
    try {
      const pkg = await api.mapsAlbumPackage(c.id);
      open = { pkg, draft: null };
      paintDetail();
    } catch (err) {
      toast(String(err.message || err), "err");
      open = null;
    } finally {
      busy = false;
      paint();
    }
  }

  function paintDetail() {
    if (!open?.pkg) return;
    const p = open.pkg;
    const notLive = p.total - p.live_count;

    render(detailEl,
      h("div", { style: { marginTop: "14px", borderTop: "1px solid var(--line)", paddingTop: "14px" } },
        h("div.row",
          h("span.pill.ok", `${fmtInt(p.photos)} photos`),
          h("span.pill.ok", `${fmtInt(p.videos)} videos`),
          notLive > 0 && h("span.pill.bad", `${fmtInt(notLive)} not in Google Photos`),
        ),

        h("h2", { style: { marginTop: "14px", fontSize: "14px" } }, "1 · Album in Google Photos"),
        h("p.sub",
          `Creates "${p.album_title}" and adds the ${fmtInt(p.live_count)} live items. ` +
          "Non-destructive — album membership doesn't move or copy anything."),
        h("div.row",
          h("button.primary", {
            disabled: busy || !p.live_count, onclick: () => buildAlbum(p),
          }, "Create album & add items"),
          h("button.small", {
            disabled: busy || !p.live_count, onclick: () => buildAlbum(p, true),
          }, "Dry run"),
        ),

        h("h2", { style: { marginTop: "18px", fontSize: "14px" } }, "2 · Review text"),
        h("p.sub", "Drafted from the photos' own captions and OCR, on your GPU."),
        h("div.row",
          h("button", { disabled: busy, onclick: () => draft(p) }, "Draft review"),
          open.draft && h("button", {
            onclick: () => copy(open.draft.text),
          }, "Copy text"),
          open.draft && h("span.pill", `${open.draft.rating}★`),
        ),
        open.draft && h("div.log", { style: { maxHeight: "150px", whiteSpace: "pre-wrap" } },
          open.draft.text || "(no text drafted — the photos may not be analyzed yet)"),

        h("h2", { style: { marginTop: "18px", fontSize: "14px" } }, "3 · Post it yourself"),
        h("div.hint.warn",
          h("strong", "This last step stays manual on purpose. "),
          "A Maps review is public content about someone else's business, and automating review " +
          "posting is what Google's anti-abuse systems target. Everything is staged for you; you " +
          "click Post."),
        h("div.row", { style: { marginTop: "10px" } },
          p.review_url && h("button", {
            onclick: () => chrome.tabs.create({ url: p.review_url }),
          }, "Open write-review page"),
          h("button.small", {
            disabled: busy,
            onclick: () => openFolder(p),
          }, "Show photos in Explorer"),
          h("span.spacer"),
          h("button.small.ghost", {
            disabled: busy, onclick: () => markReviewed(p),
          }, "Mark place reviewed"),
        ),
      ),
    );
  }

  async function buildAlbum(p, dryRun = false) {
    const keys = p.items.filter((i) => i.live && i.media_key).map((i) => i.media_key);
    if (!keys.length) { toast("Nothing in this place is live in Google Photos.", "err"); return; }

    busy = true;
    paintDetail();
    try {
      const op = await api.createOperation({
        op: "add_to_album",
        keys,
        args: { title: p.album_title },
        account: p.accounts?.[0] || "/u/0",
        dry_run: dryRun,
        note: `Maps place album: ${p.title}`,
      });
      toast(dryRun
        ? `Dry run: would add ${fmtInt(op.total)} items to "${p.album_title}".`
        : `Adding ${fmtInt(op.total)} items to "${p.album_title}"…`);
      send({ type: "PC_RUN_OP", opId: op.id });
    } catch (err) {
      toast(String(err.message || err), "err");
    } finally {
      busy = false;
      paintDetail();
    }
  }

  async function draft(p) {
    busy = true;
    paintDetail();
    try {
      open.draft = await api.mapsDraft(p.cluster_id, { rating: 5 });
      if (open.draft.no_captions) {
        toast("These photos aren't analyzed yet — run the analyze pipeline for a better draft.", "err");
      }
    } catch (err) {
      toast(String(err.message || err), "err");
    } finally {
      busy = false;
      paintDetail();
    }
  }

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text || "");
      toast("Review text copied. Paste it on the write-review page.", "ok");
    } catch {
      toast("Couldn't reach the clipboard — select the text and copy manually.", "err");
    }
  }

  async function openFolder(p) {
    const ids = p.items.filter((i) => i.live).map((i) => i.media_id);
    try {
      await api.mapsOpenSelected(p.cluster_id, ids);
      toast("Opened the folder with those files selected — drag them into the review.", "ok");
    } catch (err) {
      toast(String(err.message || err), "err");
    }
  }

  async function markReviewed(p) {
    try {
      await api.mapsReviewed(p.cluster_id, true);
      toast("Marked reviewed.", "ok");
      open = null;
      await load();
    } catch (err) {
      toast(String(err.message || err), "err");
    }
  }

  const off = onWorkerEvent((msg) => {
    if (msg.type === "op:done") toast(`Album updated — ${fmtInt(msg.op?.done_count)} items added.`, "ok");
    else if (msg.type === "op:error" || msg.type === "op:paused") toast(msg.error, "err");
  });

  render(root,
    h("div.card",
      h("h2", "Maps Review Studio"),
      h("p.sub",
        "Places built from your photos' GPS. The extension can gather each place's photos and " +
        "videos into a Google Photos album and stage the review — you post it."),
      headEl,
    ),
    listEl,
  );

  await load();
  return () => { off(); };
}
