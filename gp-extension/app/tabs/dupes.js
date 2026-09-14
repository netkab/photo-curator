/** Review thumbnail-based candidate groups, choose keepers, rehearse, then explicitly trash. */
import { h, render, fmtInt, fmtDate, send } from "../../lib/dom.js";



export async function dupesTab(root, ctx) {
  const { api, toast, onWorkerEvent } = ctx;

  const opsEl = h("div");
  const groupsEl = h("div.groups");
  const summaryEl = h("div");

  // 50 measured at ~140 ms / 66 KB for ~113 tiles, and every tile resolves to a Google thumbnail
  // URL — so they load as plain lazy <img> with no worker round-trip. 100 is also fine if this ever
  // needs raising; the cost is linear and small.
  const PAGE = 50;
  let limit = PAGE;
  let queue = null;
  let busy = false;
  // Set while an operation is draining. Every trash control is disabled against it, so a second
  // click can't queue a rival operation (and silently consume another batch of groups).
  let runningOpId = null;
  // Completed runs stay collapsed behind a summary. Clearing 3,000+ groups means well over a
  // hundred of them; listing every one permanently would push the actual queue off the screen.
  let opsExpanded = false;
  // media_ids the user marked "keep this too". CLIP groups *near*-duplicates, so a group is often
  // several genuinely different shots seconds apart rather than copies — keeping more than one is
  // the normal case, not an edge case.
  const keepAlso = new Set();

  const dryRun = h("input", { type: "checkbox", checked: true, onchange: () => paint() });
  // ── thumbnails ────────────────────────────────────────────────────────────
  /**
   * Prefer Google's own HTTPS thumbnail. The local one lives on http://localhost, which this page
   * can't load directly, so it goes through the worker (which has no such restriction).
   */
  function thumb(item, cls, keptToo = false) {
    const img = h("img", { alt: item.name || "", loading: "lazy", decoding: "async" });
    loadLocal(img, item);
    return h(`div.tile.${keptToo ? "keep" : cls}${item.live === false ? ".dead" : ""}`,
      img,
      h("div.tag", cls === "keep" ? "KEEP" : keptToo ? "KEEP TOO" : "TRASH"),
      h("div.cap", item.name || "—"),
      h("div.cap", `${item.width || "?"}×${item.height || "?"} · ${fmtDate(item.taken_at)}`),
      item.gp?.product_url
        ? h("a", { href: item.gp.product_url, target: "_blank", rel: "noreferrer" },
            "open in Google Photos")
        // Not a matching failure to fix — the photo is no longer in Google Photos, so there is
        // nothing to link it to. Say that instead of leaving a blank space.
        : h("div.cap.gone", { title: "No longer in Google Photos — most likely already deleted." },
            "not in Google Photos"),
    );
  }

  // Data URLs already fetched this session. paint() rebuilds every tile from scratch, so without
  // this a single repaint fires one worker message per visible thumbnail — ~50 of them — and a
  // repaint happens on every keep toggle and every progress tick.
  const thumbCache = new Map();

  function loadLocal(img, item) {
    if (!item.thumb) return;
    const url = `${api.API}${item.thumb}`;
    const hit = thumbCache.get(url);
    if (hit) { img.src = hit; return; }
    send({ type: "GET_IMAGE", url }).then((res) => {
      if (res?.ok && res.dataUrl) {
        thumbCache.set(url, res.dataUrl);
        img.src = res.dataUrl;
      }
    });
  }

  // ── data ──────────────────────────────────────────────────────────────────
  async function load() {
    try {
      queue = await api.dedupQueue(limit);
    } catch (err) {
      render(groupsEl, h("div.hint.danger", String(err.message || err)));
      return;
    }
    paint();
  }

  function paint() {
    const items = queue?.items || [];
    // A group only counts as actionable if something in it is actually still selected for trashing.
    const actionable = items.filter((g) => g.actionable &&
      (g.deletes || []).some((d) => d.live && !keepAlso.has(d.media_id)));
    const resolved = items.filter((g) => g.blocked_reason === "already-deduplicated");
    const keeperGone = items.filter((g) => g.blocked_reason === "keeper-missing");
    const keptCount = items.reduce((n, g) =>
      n + (g.deletes || []).filter((d) => keepAlso.has(d.media_id)).length, 0);

    render(summaryEl,
      h("div.row",
        h("span", `Showing ${fmtInt(items.length)} of ${fmtInt(queue?.total_remaining || 0)} unreviewed groups`),
        resolved.length > 0 && h("span.pill", `${fmtInt(resolved.length)} already done`),
        keeperGone.length > 0 && h("span.pill.bad", `${fmtInt(keeperGone.length)} keeper gone`),
        keptCount > 0 && h("span.pill.ok", { title: "Marked “keep this too” — never trashed." },
          `${fmtInt(keptCount)} kept`),
        h("span.spacer"),
        h("label.check", dryRun, "Dry run (no changes)"),
        h("button.small", { onclick: () => { limit += PAGE; load(); } }, `Load ${PAGE} more`),
        h("button.small.ghost", { onclick: load }, "Refresh"),
        h("button.danger", {
          // runningOpId blocks a second click from queuing a rival operation while one is draining.
          disabled: !actionable.length || busy || !!runningOpId,
          onclick: () => trashAll(actionable),
        }, runningOpId
          ? "Running…"
          : `${dryRun.checked ? "Preview" : "Trash"} duplicates in ${fmtInt(actionable.length)} group${actionable.length === 1 ? "" : "s"}`),
      ),
      (resolved.length || keeperGone.length) > 0 && h("div.hint.warn", { style: { marginTop: "10px" } },
        h("strong", "Some groups were already resolved outside this tool. "),
        "The catalog is a snapshot, so anything removed outside this tool may still " +
        "has a catalog row. Tidying these up is safe — it only marks groups reviewed and promotes a " +
        "surviving copy to keeper; nothing is deleted.",
        h("div.row", { style: { marginTop: "10px" } },
          h("button.small", { disabled: busy, onclick: () => reconcile(false) }, "Preview clean-up"),
          h("button.small.primary", { disabled: busy, onclick: () => reconcile(true) },
            "Clean up whole backlog"),
        ),
      ),
    );

    render(groupsEl, items.length
      ? items.map(groupCard)
      : h("div.empty", "No unreviewed duplicate groups. Run the dedup pipeline in Photo Curator."));
  }

  const STATUS = {
    "already-deduplicated": {
      cls: "pill", label: "already done",
      title: "0 or 1 copies remain in Google Photos — the duplicates were removed previously.",
    },
    "keeper-missing": {
      cls: "pill.bad", label: "keeper gone",
      title: "Copies survive, but the one marked KEEP is no longer in Google Photos. " +
             "Clean up the backlog, or pick a surviving copy as the keeper.",
    },
    oversized: {
      cls: "pill.bad", label: "too large — check by hand",
      title: "Far too many members to be one duplicate set. This is a clustering artifact from " +
             "the old detection, so it is excluded from bulk trashing. Re-run the dedup pipeline " +
             "to rebuild it properly.",
    },
  };

  function groupCard(g) {
    const dupes = g.deletes || [];
    const status = STATUS[g.blocked_reason];
    const toTrash = dupes.filter((d) => d.live && !keepAlso.has(d.media_id)).length;
    const kept = dupes.filter((d) => keepAlso.has(d.media_id)).length;
    const canTrash = g.actionable && toTrash > 0;

    return h(`div.group${canTrash ? "" : ".blocked"}`,
      h("div.group-head",
        h("span.date", g.date_key),
        h("span.pill", g.method),
        h("span.pill", `${dupes.length} duplicate${dupes.length === 1 ? "" : "s"}`),
        kept > 0 && h("span.pill.ok", `${fmtInt(kept)} kept`),
        g.actionable
          ? h(`span.pill${toTrash ? ".ok" : ""}`,
              { title: "Keeper survives; the live duplicates can be trashed." },
              toTrash ? `${fmtInt(toTrash)} to trash` : "nothing selected")
          : h(`span.${status?.cls || "pill.bad"}`, { title: status?.title || "" },
              status?.label || "blocked"),
        h("span.spacer"),
        h("button.small", { disabled: busy, onclick: () => ignore(g) },
          g.blocked_reason === "already-deduplicated" ? "Mark done" : "Not a duplicate"),
        h("button.danger.small", {
          disabled: !canTrash || busy,
          // Name the targets. "Trash 1" alone doesn't say *which* one, and in a two-photo group
          // that is the entire question the user is asking.
          title: canTrash
            ? `Keeps ${g.keeper?.name || "the keeper"}. Trashes: ` +
              dupes.filter((d) => d.live && !keepAlso.has(d.media_id)).map((d) => d.name).join(", ")
            : "",
          onclick: () => trashAll([g]),
        }, toTrash ? `${dryRun.checked ? "Preview" : "Trash"} ${fmtInt(toTrash)}` : "No duplicates selected"),
      ),

      // Spell out the outcome in the card itself, not just on hover — and say *why* this one won,
      // so the automatic choice can be accepted at a glance instead of eyeballed every time.
      canTrash && h("p.sub", { style: { margin: "0 0 10px" } },
        "Keeping ", h("strong", g.keeper?.name || "—"),
        g.keeper_reason && h("span.pill.ok", { style: { marginLeft: "6px" } }, g.keeper_reason),
        " · trashing ",
        h("strong", dupes.filter((d) => d.live && !keepAlso.has(d.media_id))
          .map((d) => d.name).join(", ")),
      ),
      h("div.tiles",
        g.keeper && thumb(g.keeper, "keep"),
        ...dupes.map((d) => {
          const keptToo = keepAlso.has(d.media_id);
          return h("div", { style: { position: "relative", width: "168px" } },
            thumb(d, "del", keptToo),
            // The common case in a CLIP group: several good shots, keep more than one.
            h("button.small", {
              class: keptToo ? "primary" : "",
              style: { marginTop: "6px", width: "168px" },
              disabled: busy || d.live === false,
              title: d.live === false
                ? "Not in Google Photos, so there is nothing to trash."
                : keptToo ? "Currently kept — click to trash it instead."
                : "Keep this one as well as the keeper.",
              onclick: () => {
                if (keptToo) keepAlso.delete(d.media_id);
                else keepAlso.add(d.media_id);
                recomputeActionable(g);
                paint();
              },
            }, keptToo ? "✓ Keeping" : "Keep this too"),
            // Always offered. Hiding this for single-duplicate groups was a mistake: a 2-photo
            // group is the commonest kind, and "keep the other one instead" is exactly the choice
            // you want to make there.
            h("button.small.ghost", {
              style: { marginTop: "4px", width: "168px" },
              disabled: busy || d.live === false,
              title: d.live === false
                ? "Not in Google Photos, so keeping it wouldn't help."
                : `Keep ${d.name} instead, and trash ${g.keeper?.name || "the current keeper"}.`,
              onclick: () => makeKeeper(g, d),
            }, "Make keeper"),
          );
        }),
      ),
    );
  }

  // ── actions ───────────────────────────────────────────────────────────────
  /** Live members of a group that aren't marked "keep this too". */
  function liveTotal(g) {
    const members = [g.keeper, ...(g.deletes || [])].filter(Boolean);
    return members.filter((m) => m.live && !keepAlso.has(m.media_id)).length;
  }

  /**
   * Trash every member of a group, keeper included — for a group that is simply junk.
   *
   * Kept apart from the duplicate flow because that flow guarantees the keeper survives, and that
   * guarantee shouldn't be reachable by a flag. One group at a time, always confirmed, and the
   * confirmation says plainly that nothing will remain.
   */
  async function ignore(g) {
    try {
      await api.ignoreGroup(g.group_id);
      toast("Marked as not a duplicate.", "ok");
      await load();
    } catch (err) {
      toast(String(err.message || err), "err");
    }
  }

  /**
   * Reconcile the backlog against what still exists in Google Photos.
   *
   * Previews by default. Nothing here deletes anything — it marks already-resolved groups reviewed
   * and promotes a surviving copy to keeper where the chosen one is gone.
   */
  async function reconcile(apply) {
    if (busy) return;
    busy = true;
    paint();
    try {
      const r = await api.post("/api/dedup/reconcile", { apply });
      const summary =
        `${fmtInt(r.closed_already_deduplicated)} already done · ` +
        `${fmtInt(r.keepers_repicked)} keeper re-picked · ` +
        `${fmtInt(r.actionable_after)} left to trash`;

      if (!apply) {
        const ok = confirm(
          `Clean-up preview (nothing changed yet)\n\n` +
          `Groups examined:        ${fmtInt(r.groups_examined)}\n` +
          `Already deduplicated:   ${fmtInt(r.closed_already_deduplicated)}  → mark reviewed\n` +
          `Keeper no longer in GP: ${fmtInt(r.keepers_repicked)}  → promote a surviving copy\n` +
          `Actionable afterwards:  ${fmtInt(r.actionable_after)}\n\n` +
          "Apply this now?",
        );
        if (ok) { busy = false; return reconcile(true); }
        toast(summary, "ok");
        return;
      }

      toast(`Clean-up applied — ${summary}`, "ok");
      await load();
    } catch (err) {
      toast(String(err.message || err), "err");
    } finally {
      busy = false;
      paint();
    }
  }

  /**
   * Promote a duplicate to keeper, swapping it with the current one **in place**.
   *
   * Deliberately does NOT reload the queue. Groups are ordered by the keeper's capture date, so
   * changing the keeper moves the group in the sort order — reloading made it vanish from the page
   * entirely and look like it had been consumed.
   */
  async function makeKeeper(g, member) {
    try {
      await api.setKeeper(g.group_id, member.media_id);

      const oldKeeper = g.keeper;
      g.deletes = g.deletes.map((d) => (d.media_id === member.media_id ? oldKeeper : d))
        .filter(Boolean);
      g.keeper = member;

      // The header date follows the keeper, and a formerly-kept item is no longer marked keep-too.
      g.date_key = (member.taken_at || "").slice(0, 10) || g.date_key;
      keepAlso.delete(member.media_id);
      recomputeActionable(g);

      toast(`Keeping ${member.name}. Its position in the list follows the new date.`, "ok");
      paint();
    } catch (err) {
      toast(String(err.message || err), "err");
    }
  }

  /** Mirror the backend's rule locally so the badge and buttons stay honest after an edit. */
  function recomputeActionable(g) {
    const keeperLive = !!g.keeper?.live;
    const liveDupes = (g.deletes || []).filter((d) => d.live && !keepAlso.has(d.media_id));
    g.actionable = keeperLive && liveDupes.length > 0;
    g.live_count = (g.deletes || []).filter((d) => d.live).length + (keeperLive ? 1 : 0);
    g.blocked_reason = g.live_count <= 1 ? "already-deduplicated"
      : !keeperLive ? "keeper-missing"
      : null;
  }

  /**
   * Confirm the executor can actually reach Google Photos.
   *
   * Called BEFORE anything is approved. Approving marks groups reviewed — so if the tab is missing
   * we must fail here, with the queue untouched, rather than consume groups for an operation that
   * can never run.
   */
  async function canExecute() {
    const h = await send({ type: "PC_HEALTH" });
    if (!h?.ok) return h?.error || "Can't reach Google Photos.";
    if (!h.gptk) return "Google Photos Toolkit didn't load — reload the photos.google.com tab.";
    if (!h.authed) return "That Google Photos tab isn't signed in.";
    return null;
  }

  async function trashAll(groups) {
    if (busy || runningOpId) return;
    busy = true;
    paint();
    try {
      const problem = await canExecute();
      if (problem) {
        toast(`${problem} Nothing was queued — open photos.google.com and try again.`, "err");
        return;
      }
      if (dryRun.checked) await dryRunTrash(groups);
      else await realTrash(groups);
    } catch (err) {
      toast(String(err.message || err), "err");
    } finally {
      busy = false;
      paint();
    }
  }

  /**
   * Run an operation and surface the outcome.
   *
   * The result used to be discarded, so an executor that couldn't reach the page failed in total
   * silence — which is how ten clicks produced ten stuck operations and no error message.
   */
  /**
   * Kick off an operation. Returns the promise, but **trash paths must not await it**.
   *
   * The worker drains the whole operation — batching, 5 s pacing, 60 s per-batch timeouts, retries —
   * which runs for minutes. Awaiting that in the UI leaves the page sitting on "Running…" looking
   * frozen. Progress arrives over the worker port instead. Only the dry run awaits, because there
   * the completion message is the entire point and it is short.
   */
  function startOperation(opId) {
    if (runningOpId) {
      return Promise.resolve({ ok: false, error: "Another operation is already running." });
    }
    runningOpId = opId;
    paint();
    return send({ type: "PC_RUN_OP", opId })
      .then((res) => {
        if (!res?.ok && !res?.cancelled) toast(res?.error || "The operation did not run.", "err");
        return res;
      })
      .catch((err) => {
        const msg = String(err?.message || err);
        toast(msg, "err");
        return { ok: false, error: msg };
      })
      .finally(() => {
        runningOpId = null;
        refreshOps().then(paint, paint);
      });
  }

  /**
   * Exercise the entire chain — tab connection, toolkit, account guard, batching, cursor advance —
   * without sending a single mutation. `main/commands.js` returns before the RPC, so this cannot
   * change the library no matter what else is wrong.
   *
   * It deliberately bypasses the review queue: nothing is approved, no group is marked reviewed, so
   * the same groups are still waiting for you afterwards.
   */
  async function dryRunTrash(groups) {
    const keys = groups.flatMap((g) => (g.deletes || [])
      .filter((d) => !keepAlso.has(d.media_id))
      .map((d) => d.gp?.dedup_key)
      .filter(Boolean));
    if (!keys.length) { toast("Nothing selected to trash in those groups.", "err"); return; }

    const account = groups.find((g) => g.keeper?.gp?.account)?.keeper.gp.account || "/u/0";
    const op = await api.createOperation({
      op: "trash", keys, account, dry_run: true,
      note: `Dry run over ${groups.length} duplicate group(s)`,
    });

    toast(`Dry run: walking ${fmtInt(op.total)} item(s), nothing will be changed…`);
    await refreshOps();
    const res = await startOperation(op.id);
    if (!res?.ok) { toast(res?.error || "Dry run failed", "err"); return; }

    dryRun.checked = true;
    toast(`Dry run complete — ${fmtInt(op.total)} item(s) would be trashed. Review the previews before explicitly enabling a real run.`, "ok");
    await refreshOps();
  }

  /**
   * Approve the groups into ONE review action, apply it to get ONE operation, then drain it.
   *
   * One action for the whole selection rather than one per group: at this library's scale that is
   * the difference between a single resumable operation and several thousand, and it stays a single
   * thing to undo.
   */
  async function realTrash(groups) {
    if (!(await api.health()).live_trash_enabled) throw new Error("Live trash is disabled. Follow README setup to enable it after reviewing dry runs.");
    const count = groups.reduce((n, g) =>
      n + (g.deletes || []).filter((d) => d.live && !keepAlso.has(d.media_id)).length, 0);
    if (!count) { toast("Nothing selected to trash.", "err"); return; }

    const keptHere = groups.reduce((n, g) =>
      n + (g.deletes || []).filter((d) => keepAlso.has(d.media_id)).length, 0);

    // For a small selection, list the actual filenames. A bare count gives you no way to check the
    // keeper/duplicate assignment is the one you meant before it happens.
    const names = groups.flatMap((g) => (g.deletes || [])
      .filter((d) => d.live && !keepAlso.has(d.media_id)).map((d) => d.name));
    const keepers = [...new Set(groups.map((g) => g.keeper?.name).filter(Boolean))];
    const detail = names.length <= 8
      ? `\nTrashing:\n  ${names.join("\n  ")}\n\nKeeping:\n  ${keepers.join("\n  ")}\n`
      : "";

    const ok = confirm(
      `Move ${count} item(s) to the Google Photos bin?\n` + detail +
      (keptHere ? `\n${keptHere} marked "keep this too" will be left alone.\n` : "") +
      "\nThey stay recoverable only while Google retains them in its bin. This run can be undone here.\n" +
      "Your local originals are not touched.",
    );
    if (!ok) return;

    const approved = await api.post("/api/dedup/approve-bulk", {
      group_ids: groups.map((g) => g.group_id),
      exclude_media_ids: [...keepAlso],
    });
    if (!approved.action_id) { toast("Nothing to trash in those groups.", "err"); return; }
    if (approved.skipped_unlinked) {
      toast(`Skipped ${approved.skipped_unlinked} group(s) that aren't fully linked.`, "err");
    }
    if (approved.skipped_all_kept) {
      // Left unreviewed on purpose — keeping everything is a decision about *this* run, not a
      // verdict that the group is resolved.
      toast(`${approved.skipped_all_kept} group(s) had everything kept; left for later.`, "ok");
    }

    await api.approveAction(approved.action_id);
    const applied = await api.applyAction(approved.action_id, false);
    dryRun.checked = true;
    const opId = applied?.result?.operation_id;
    if (!opId) {
      toast("Nothing was linked to Google Photos — see the exported checklist.", "err");
      await refreshOps();
      return;
    }

    toast(`Trashing ${fmtInt(approved.queued_for_deletion)} item(s)…`);
    await refreshOps();
    startOperation(opId);   // not awaited — errors surface via toast + the worker port
  }

  // ── operations panel ──────────────────────────────────────────────────────
  async function refreshOps() {
    let ops;
    try {
      ({ items: ops } = await api.listOperations());
    } catch {
      render(opsEl);
      return;
    }
    const mine = ops.filter((o) => o.op === "trash" || o.op === "restore");

    // Three buckets, because they deserve very different amounts of screen:
    //   active   — needs your attention or is moving. Always expanded.
    //   finished — history. One summary line; the list only on request.
    //   dead     — cancelled having done nothing. Wreckage, not history. Counted, never listed.
    const active = mine.filter((o) => ["running", "pending", "paused"].includes(o.status));
    const finished = mine.filter((o) => o.done_count > 0);
    const dead = mine.filter((o) => o.status === "cancelled" && o.done_count === 0).length;

    if (!active.length && !finished.length && !dead) { render(opsEl); return; }

    const trashed = finished
      .filter((o) => o.op === "trash" && !o.dry_run)
      .reduce((n, o) => n + o.done_count, 0);
    const restored = finished
      .filter((o) => o.op === "restore" && !o.dry_run)
      .reduce((n, o) => n + o.done_count, 0);
    // The undo people actually reach for is "that thing I just did".
    const undoable = finished.find((o) => o.op === "trash" && o.done_count > 0 && !o.dry_run);

    render(opsEl, h("div.card",
      h("div.row",
        h("h2", { style: { margin: 0 } }, "Operations"),
        active.length > 0 && h("span.pill.ok", `${active.length} active`),
        h("span.spacer"),
        finished.length > 0 && h("span.sub",
          `${fmtInt(trashed)} trashed` + (restored ? ` · ${fmtInt(restored)} restored` : "") +
          ` · ${fmtInt(finished.length)} run${finished.length === 1 ? "" : "s"}`),
        undoable && h("button.small", {
          disabled: !!runningOpId,
          title: `Undo the most recent run (${ago(undoable.created_at)}, ` +
                 `${fmtInt(undoable.done_count)} items).`,
          onclick: () => undo(undoable),
        }, `Undo last (${fmtInt(undoable.done_count)})`),
        finished.length > 0 && h("button.small.ghost", {
          onclick: () => { opsExpanded = !opsExpanded; refreshOps(); },
        }, opsExpanded ? "Hide history" : "History"),
      ),

      active.length > 0
        ? h("div", { style: { marginTop: "12px" } }, active.map(opRow))
        : h("p.sub", { style: { margin: "6px 0 0" } },
            "Nothing running. Progress is stored in Photo Curator, so a run interrupted by closing " +
            "this page resumes rather than restarting."),

      opsExpanded && h("div", { style: { marginTop: "12px" } },
        finished.slice(0, 20).map(historyRow),
        finished.length > 20 && h("p.sub", `Showing the last 20 of ${fmtInt(finished.length)}.`),
        dead > 0 && h("p.sub", { style: { opacity: ".7" } },
          `${fmtInt(dead)} attempt${dead === 1 ? "" : "s"} never started (no Google Photos tab was ` +
          "reachable) and changed nothing."),
      ),
    ));
  }

  /**
   * A finished run: one line, no progress bar.
   *
   * A full-width 100% bar per completed run conveys nothing and is what made this panel grow without
   * bound — at ~20 groups a run, clearing the backlog is well over a hundred of them.
   */
  function historyRow(o) {
    const what = (o.note || "").split("—")[0].trim();
    return h("div.row", {
      style: { padding: "6px 0", borderTop: "1px solid var(--line)", fontSize: "13px" },
    },
      h("span", { style: { color: "var(--muted)", minWidth: "62px" } },
        o.op === "trash" ? "Trash" : "Restore"),
      h("span", { style: { flex: "1", minWidth: "0" } }, what || "—"),
      o.dry_run && h("span.pill", "dry run"),
      o.failed_count > 0 && h("span.pill.bad", `${fmtInt(o.failed_count)} failed`),
      h("span.sub", { title: o.created_at || "" }, ago(o.created_at)),
      h("span.sub", { style: { minWidth: "54px", textAlign: "right" } },
        `${fmtInt(o.done_count)}`),
      o.op === "trash" && !o.dry_run && h("button.small.ghost", {
        disabled: !!runningOpId,
        onclick: () => undo(o),
      }, "Undo"),
    );
  }

  /** "just now" / "12 min ago" / "yesterday 15:24" — enough to tell two identical runs apart. */
  function ago(iso) {
    if (!iso) return "";
    const then = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
    if (Number.isNaN(then.getTime())) return "";
    const mins = Math.round((Date.now() - then.getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    if (mins < 24 * 60) return `${Math.round(mins / 60)} h ago`;
    return then.toLocaleString(undefined,
      { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function opRow(o) {
    const pct = o.total ? Math.round((o.cursor / o.total) * 100) : 0;
    const barKind = { done: "done", paused: "paused", failed: "err", cancelled: "err" }[o.status] || "";
    // The note says what the run was ("duplicates (20 groups) — review action 56"); the part after
    // the dash is bookkeeping, so show only the human half.
    const what = (o.note || "").split("—")[0].trim();

    return h("div", {
      style: {
        marginBottom: "14px", paddingBottom: "12px",
        borderBottom: "1px solid var(--line)",
      },
    },
      h("div.row",
        h("strong", o.op === "trash" ? "Trash" : "Restore"),
        what && h("span.sub", what),
        h("span.pill", o.status),
        o.dry_run && h("span.pill", "dry run"),
        h("span.spacer"),
        // Two runs of the same size are indistinguishable without this — which is exactly the
        // situation where an Undo button next to each is alarming rather than reassuring.
        h("span.sub", { title: o.created_at || "" }, ago(o.created_at)),
        h("span.sub", `${fmtInt(o.done_count)} / ${fmtInt(o.total)}`),
        o.failed_count > 0 && h("span.pill.bad", `${fmtInt(o.failed_count)} failed`),
      ),
      h(`div.bar.${barKind}`, h("i", { style: { width: `${pct}%` } })),
      // Only surface the error where it's actionable — a paused run needs fixing and resuming.
      // Rendering a full-width red box per finished operation is what buried the queue.
      o.error && (o.status === "paused" || o.status === "failed")
        && h("div.hint.danger", { style: { marginTop: "6px" } }, o.error),
      h("div.row",
        // A queued operation that never started had no control at all — it just sat at 0/N with no
        // way to run it once the Google Photos tab was available again.
        o.status === "pending" && h("button.small.primary", {
          disabled: !!runningOpId,
          onclick: async () => {
            const problem = await canExecute();
            if (problem) { toast(problem, "err"); return; }
            startOperation(o.id);
          },
        }, `Start (${fmtInt(o.total)})`),
        ["paused", "running"].includes(o.status) && h("button.small", {
          disabled: !!runningOpId,
          onclick: async () => {
            await api.resumeOperation(o.id);
            startOperation(o.id);
            toast("Resuming…");
          },
        }, "Resume"),
        o.status === "running" && h("button.small", {
          onclick: async () => { await api.cancelOperation(o.id); await refreshOps(); },
        }, "Stop"),
        // No Undo here on purpose: opRow only renders *active* operations, and offering to reverse
        // a run that is still writing invites a half-undone state. Undo lives in the history list.
      ),
    );
  }

  async function undo(o) {
    // Name the specific run. With several same-sized rows on screen, "Restore 24 items?" gives no
    // way to tell whether you're about to undo today's run or last week's.
    const what = (o.note || "").split("—")[0].trim();
    const ok = confirm(
      `Restore ${o.done_count} item(s) from the Google Photos bin?\n\n` +
      `Run: ${what || "trash"}\nWhen: ${ago(o.created_at)}\n\n` +
      "They go back to where they were in your library.",
    );
    if (!ok) return;
    try {
      const inverse = await api.undoOperation(o.id);
      toast(`Restoring ${fmtInt(inverse.total)} item(s)…`);
      await refreshOps();
      startOperation(inverse.id);
    } catch (err) {
      toast(String(err.message || err), "err");
    }
  }

  // ── worker events ─────────────────────────────────────────────────────────
  const off = onWorkerEvent(async (msg) => {
    if (!String(msg.type || "").startsWith("op:")) return;
    await refreshOps();
    if (msg.type === "op:done") {
      toast(`Finished: ${fmtInt(msg.op?.done_count)} item(s).`, "ok");
      await load();
    } else if (msg.type === "op:paused") {
      toast(`Paused: ${msg.error}`, "err");
    } else if (msg.type === "op:error") {
      toast(msg.error, "err");
    } else if (msg.type === "op:retry") {
      toast(`Retry ${msg.attempt}/${msg.of} in ${Math.round(msg.waitMs / 1000)}s — ${msg.error}`);
    }
  });

  // ── paint ─────────────────────────────────────────────────────────────────
  render(root,
    h("div.card",
      h("h2", "Duplicates"),
      h("p.sub",
        "Candidates found from local thumbnails (perceptual hash and optional CLIP). Matching previews do not prove identical originals. Trash can be undone only while Google retains the items in its bin."),
      summaryEl,
    ),
    opsEl,
    groupsEl,
  );

  await Promise.all([load(), refreshOps()]);

  return () => { off(); };
}
