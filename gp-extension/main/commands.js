/**
 * Narrow Google Photos adapter, derived from GPTK v3.2.0 (MIT; vendor/gptk/LICENSE).
 * Invoked with chrome.scripting.executeScript in MAIN world, never via window messages.
 * This function must be self-contained because Chrome serializes it.
 * Only library read, metadata read, trash and restore RPC payloads exist here.
 */
export async function pageCommand(command, args = {}) {
  try {
    const wiz = window.WIZ_global_data;
    const account = typeof wiz?.oPEP7c === "string" ? wiz.oPEP7c : null;
    const pathAccount = location.pathname.match(/^\/u\/(\d+)\//)?.[1] || "0";
    const health = {gptk: true, authed: !!wiz?.SNlM0e && !!account,
                    account, url: location.href};
    if (command === "healthCheck") return {ok: true, result: health};
    if (!health.authed) throw new Error("Stable Google account identity unavailable; sign in and reload Photos");
    if (!args.expectedAccount || args.expectedAccount !== account)
      throw new Error("Google account changed; stop and rescan the correct account");

    async function rpc(id, payload) {
      const params = new URLSearchParams({rpcids: id, "source-path": location.pathname,
        "f.sid": wiz.FdrFJe, bl: wiz.cfb2h, pageId: "none", rt: "c"});
      const path = `/u/${pathAccount}/`;
      const response = await fetch(`https://photos.google.com${path}data/batchexecute?${params}`, {
        method: "POST", credentials: "include", signal: AbortSignal.timeout(60000),
        headers: {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        body: new URLSearchParams({"f.req": JSON.stringify([[[id, JSON.stringify(payload), null, "generic"]]]),
                                  at: wiz.SNlM0e}),
      });
      if (!response.ok) throw new Error(`Google Photos HTTP ${response.status}; stop and retry later`);
      const lines = (await response.text()).split("\n");
      for (const line of lines) {
        if (!line.includes('"wrb.fr"')) continue;
        let envelopes;
        try { envelopes = JSON.parse(line); } catch { continue; }
        const envelope = envelopes.find(e => e?.[0] === "wrb.fr" && e?.[1] === id);
        if (typeof envelope?.[2] === "string") {
          const result = JSON.parse(envelope[2]);
          if (!Array.isArray(result)) throw new Error("Google response schema changed; no result accepted");
          return result;
        }
      }
      throw new Error("Google response schema changed or request failed; no result accepted");
    }
    const iso = value => {
      const n = Number(value);
      return Number.isFinite(n) && n > 0 ? new Date(n).toISOString() : null;
    };
    if (command === "scanPage") {
      const page = await rpc("lcxiM", [args.pageId || null, null, 500, null, 1, 3]);
      if (!Array.isArray(page[0]) && page[0] != null) throw new Error("Library schema changed; scan stopped");
      const raw = page[0] || [];
      if (raw.length > 500 || raw.some(i => typeof i?.[0] !== "string" || !Array.isArray(i?.[1])))
        throw new Error("Library items have an unexpected shape; scan stopped");
      let metadata = [];
      if (raw.length) {
        const fields = Array(37).fill(null); fields[25] = []; fields[36] = [];
        const info = await rpc("EWgK9e", [[[raw.map(i => [i[0]])], [fields]]]);
        metadata = info?.[0]?.[1];
        if (!Array.isArray(metadata)) throw new Error("Metadata schema changed; scan stopped");
      }
      const byKey = new Map(metadata.map(i => [i?.[0]?.[0], i?.[0]]));
      const items = raw.map(i => {
        const m = byKey.get(i[0]);
        const duration = i.at(-1)?.[76647426]?.[0];
        return {media_key: i[0], dedup_key: i[3] || m?.[11] || null,
          file_name: m?.[2] || null, taken_at: iso(i[2]), uploaded_at: iso(i[5]),
          width: i[1][1] ?? null, height: i[1][2] ?? null, bytes: m?.[5] != null ? Number(m[5]) : null,
          duration: duration != null ? Number(duration) / 1000 : null,
          is_owned: Array.isArray(i[7]) ? !i[7].some(a => Array.isArray(a) && a.includes(27)) : null,
          is_original_quality: m?.[30]?.[2] == null ? null : m[30][2] === 2,
          trashed: false, product_url: `https://photos.google.com/u/${pathAccount}/photo/${encodeURIComponent(i[0])}`,
          thumb_url: i[1][0] || null};
      });
      if (page[1] != null && typeof page[1] !== "string") throw new Error("Unexpected scan cursor");
      return {ok: true, result: {items, nextPageId: page[1] || null}};
    }
    if (command === "trash" || command === "restore") {
      const keys = args.keys;
      if (!Array.isArray(keys) || !keys.length || keys.length > 25 || keys.some(k => typeof k !== "string" || !k))
        throw new Error("Invalid mutation batch (maximum 25 content keys)");
      if (args.dryRun !== false) return {ok: true, result: {succeeded: keys, failed: [], dryRun: true}};
      // There is no permanent deletion payload. These are the pinned GPTK trash/restore payloads.
      await rpc("XwAOJf", command === "trash" ? [null, 1, keys, 3] : [null, 3, keys, 2]);
      return {ok: true, result: {succeeded: keys, failed: []}};
    }
    throw new Error("Unsupported Google Photos command");
  } catch (error) {
    return {ok: false, error: String(error?.message || error)};
  }
}
