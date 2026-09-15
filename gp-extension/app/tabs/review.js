import {h, render, send} from "../../lib/dom.js";
export async function reviewTab(root, ctx) {
  const {api, toast} = ctx;
  let busy = false;
  async function act(action, command, live = false) {
    if (busy) return;
    if (live) {
      if (!(await api.health()).live_trash_enabled) {toast("Live trash is disabled; see README setup.", "err"); return;}
      if (!confirm(`Move the approved photos in review ${action.id} to Google Photos trash? Check the listed keepers first. Undo works only while Google retains the photos.`)) return;
    }
    busy = true;
    try {
      if (command === "apply") {
        const r = await api.applyAction(action.id, !live);
        toast(`${live ? "Reviewed trash" : "Dry run"} queued as operation ${r.result.operation_id}. Start it in Duplicates.`, "ok");
      } else await api.post(`/api/review/${action.id}/${command}`);
      await load();
    } catch (e) {toast(e.message, "err");}
    finally {busy = false;}
  }
  async function tile(id, label) {
    const m = await api.get(`/api/media/${id}`);
    const img = h("img", {alt: m.name, width: 160, height: 120, style: {objectFit: "contain"}});
    if (m.thumb) send({type: "GET_IMAGE", url: `${api.API}${m.thumb}`}).then(r => {if (r.ok) img.src=r.dataUrl;});
    return h("div", h("strong", label), img, h("p", m.name));
  }
  async function load() {
    const actions = (await api.reviewActions()).filter(a => ["pending", "approved"].includes(a.status));
    const cards = [];
    for (const action of actions.slice(0, 50)) {
      const p = action.payload;
      const keepers = [...new Set([p.keeper_media_id, ...(p.kept_media_ids || []), ...(p.items || []).map(i => i.keeper_media_id)].filter(Boolean))];
      const tiles = await Promise.all([...keepers.map(id => tile(id, "KEEP")),
        ...(p.items || []).map(i => tile(i.media_id, "PROPOSED TRASH"))]);
      cards.push(h("section.card", h("h2", `Review ${action.id} · ${action.status}`),
        h("p.sub", p.reason || "Duplicate candidates"), h("div.row", tiles),
        p.ownership_excluded_media_ids?.length ? h("p.sub", `${p.ownership_excluded_media_ids.length} photos kept because ownership is shared or unknown. ${p.items.length} photos remain selected for trash.`) : null,
        h("div.row",
          action.status === "pending" && h("button", {onclick: () => act(action, "keep-unowned")}, "Keep photos I don’t own"),
          action.status === "pending" && h("button.primary", {onclick: () => act(action, "approve")}, "Approve this selection"),
          action.status === "approved" && h("button.primary", {onclick: () => act(action, "apply")}, "Queue dry run"),
          action.status === "approved" && h("button.danger", {onclick: () => act(action, "apply", true)}, "Queue reviewed trash…"),
          h("button", {onclick: () => act(action, "dismiss")}, "Dismiss"))));
    }
    render(root, h("h2", "Review queue"),
      h("p.sub", "Approval records exactly which photos may be trashed. Queue a dry run first. Start and undo operations in the Duplicates tab."),
      cards.length ? cards : h("p", "No pending reviews. Choose photos in Duplicates or Likely accidents."));
  }
  await load();
}
