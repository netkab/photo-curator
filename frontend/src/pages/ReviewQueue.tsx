import { useEffect, useState } from "react";
import { get, post } from "../lib/api";
import { fmtDate } from "./Duplicates";

interface Action {
  id: number; kind: string; status: string; payload: any; note: string | null;
  created_at: string; applied_at: string | null;
}

const STATUSES = ["pending", "approved", "done", "dismissed", ""];

function summarize(a: Action): string {
  const p = a.payload || {};
  switch (a.kind) {
    case "delete": return `${(p.items || []).length} item(s) — ${p.reason || "manual deletion"}`;
    case "upload": return `${p.kind} → ${p.path}${p.taken_at ? ` · keeps original date ${fmtDate(p.taken_at)}` : ""}`;
    case "review": return `${p.name || "place"} · ${p.rating}★ · ${(p.images || []).length} images`;
    case "caption": return `caption #${p.caption_id}`;
    default: return JSON.stringify(p).slice(0, 80);
  }
}

export default function ReviewQueue() {
  const [filter, setFilter] = useState("pending");
  const [actions, setActions] = useState<Action[]>([]);
  const [last, setLast] = useState<string | null>(null);

  const load = () => {
    const qs = filter ? `?status=${filter}` : "";
    get<Action[]>(`/review${qs}`).then(setActions).catch(() => {});
  };
  useEffect(() => { load(); }, [filter]);

  async function act(id: number, verb: string) {
    const r = await post<any>(`/review/${id}/${verb}`);
    if (verb === "apply" && r.result) setLast(JSON.stringify(r.result));
    load();
  }
  async function applyApproved() {
    const r = await post<any>("/review/apply-approved");
    setLast(`Applied ${r.applied} approved action(s).`);
    load();
  }

  return (
    <div>
      <div className="row spread">
        <h2>Review Queue</h2>
        <button className="btn good" onClick={applyApproved}>Apply all approved</button>
      </div>
      <div className="banner">The single gate for every change. Deletes become a manual checklist (the API can’t delete your photos); uploads go to a dedicated album (or an export folder); reviews open the Maps URL for manual posting.</div>

      <div className="row" style={{ margin: "10px 0" }}>
        {STATUSES.map((s) => (
          <button key={s || "all"} className={`btn ${filter === s ? "" : "secondary"}`} onClick={() => setFilter(s)}>
            {s || "all"}
          </button>
        ))}
      </div>

      {last && <div className="card"><code style={{ fontSize: 12 }}>{last}</code></div>}

      {actions.map((a) => (
        <div className="card" key={a.id}>
          <div className="row spread">
            <div>
              <span className={`pill ${a.status}`}>{a.status}</span>{" "}
              <b style={{ textTransform: "capitalize" }}>{a.kind}</b> — <span className="muted">{summarize(a)}</span>
            </div>
            <div className="row">
              {a.status === "pending" && <button className="btn" onClick={() => act(a.id, "approve")}>Approve</button>}
              {(a.status === "pending" || a.status === "approved") && <button className="btn good" onClick={() => act(a.id, "apply")}>Apply</button>}
              {a.status !== "done" && a.status !== "dismissed" && <button className="btn danger" onClick={() => act(a.id, "dismiss")}>Dismiss</button>}
              {a.kind === "review" && a.payload?.review_url && <a className="btn secondary" href={a.payload.review_url} target="_blank">Open in Maps</a>}
            </div>
          </div>
          {a.kind === "delete" && (a.payload?.items || []).length > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary className="muted" style={{ cursor: "pointer", fontSize: 12 }}>
                Show {a.payload.items.length} photo(s) to delete — find these dates in Google Photos
              </summary>
              <ul style={{ fontSize: 12, lineHeight: 1.7, marginTop: 6 }}>
                {[...a.payload.items]
                  .sort((x: any, y: any) => (x.taken_at || "").localeCompare(y.taken_at || ""))
                  .map((it: any) => (
                    <li key={it.media_id}>
                      <span style={{ color: "var(--accent)" }}>📅 {fmtDate(it.taken_at)}</span>
                      {it.place_name ? ` · ${it.place_name}` : ""} — {it.name}
                    </li>
                  ))}
              </ul>
            </details>
          )}
        </div>
      ))}
      {actions.length === 0 && <p className="muted">No {filter || ""} actions.</p>}
    </div>
  );
}
