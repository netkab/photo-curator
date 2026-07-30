import { useEffect, useState } from "react";
import { get, post } from "../lib/api";
import JobButton from "../components/JobButton";

interface Member {
  media_id: number; thumb: string | null; name: string; score: number;
  width: number; height: number; blur_score: number; taken_at: string | null;
  is_keeper: boolean;
}
interface Group { id: number; method: string; keeper_media_id: number; date_key: string; members: Member[]; }
interface PageData { total: number; total_dupes: number; offset: number; items: Group[]; }

export function fmtDate(iso: string | null): string {
  if (!iso) return "no date";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function prettyDateHeader(dk: string): string {
  if (dk === "no-date") return "Undated";
  return new Date(dk).toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
}

// Group an already-date-sorted list of stacks into [date, stacks[]] sections, preserving order.
function groupByDate(items: Group[]): [string, Group[]][] {
  const out: [string, Group[]][] = [];
  for (const g of items) {
    const last = out[out.length - 1];
    if (last && last[0] === g.date_key) last[1].push(g);
    else out.push([g.date_key, [g]]);
  }
  return out;
}

const PAGE_SIZE = 60;  // Show many more per page now that each tile is compact

// ---------- Stacked tile (one per group) ----------
function StackTile({ g, onClick }: { g: Group; onClick: () => void }) {
  const keeper = g.members.find(m => m.media_id === g.keeper_media_id) || g.members[0];
  const dupes  = g.members.length - 1;

  return (
    <div onClick={onClick} style={{
      position: "relative", cursor: "pointer", borderRadius: 8,
      transition: "transform 0.15s", aspectRatio: "1 / 1",
    }}
      onMouseEnter={e => e.currentTarget.style.transform = "scale(1.04)"}
      onMouseLeave={e => e.currentTarget.style.transform = "scale(1)"}
    >
      {/* Stacked card effect — two offset cards behind to suggest depth */}
      <div style={{
        position: "absolute", inset: 0, transform: "translate(4px, 4px) rotate(1.5deg)",
        background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 8,
      }} />
      <div style={{
        position: "absolute", inset: 0, transform: "translate(2px, 2px) rotate(-1deg)",
        background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 8,
      }} />

      {/* Top photo (keeper) */}
      <div style={{
        position: "relative", width: "100%", height: "100%", borderRadius: 8, overflow: "hidden",
        border: "1px solid var(--line)", background: "var(--panel2)",
      }}>
        {keeper?.thumb
          ? <img src={keeper.thumb} alt={keeper.name} loading="lazy"
                 style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
          : <div style={{ width: "100%", height: "100%", background: "#000" }} />
        }
        {/* Count badge — top-right */}
        <div style={{
          position: "absolute", top: 6, right: 6,
          background: "rgba(0,0,0,0.75)", color: "#fff",
          padding: "3px 8px", borderRadius: 999, fontSize: 11, fontWeight: 700,
          display: "flex", alignItems: "center", gap: 4,
        }}>
          <span style={{ fontSize: 12 }}>&#9783;</span>  {/* stack glyph */}
          {g.members.length}
        </div>
        {/* Removable count badge — bottom-right */}
        <div style={{
          position: "absolute", bottom: 6, right: 6,
          background: "var(--danger)", color: "#fff",
          padding: "2px 7px", borderRadius: 999, fontSize: 10, fontWeight: 700,
        }}>
          -{dupes}
        </div>
      </div>
    </div>
  );
}

// ---------- Detail modal (one stack expanded) ----------
function GroupModal({ g, onClose, onChanged }: { g: Group; onClose: () => void; onChanged: () => void }) {
  const [members, setMembers] = useState<Member[]>(g.members);
  const [keeperId, setKeeperId] = useState(g.keeper_media_id);
  const [busy, setBusy] = useState(false);

  // Excluded members will not be queued for deletion (user keeps them too)
  const [excluded, setExcluded] = useState<Set<number>>(new Set());

  async function setAsKeeper(mid: number) {
    setBusy(true);
    try {
      await post(`/dedup/groups/${g.id}/keeper`, { media_id: mid });
      setKeeperId(mid);
    } finally { setBusy(false); }
  }

  function toggleExclude(mid: number) {
    setExcluded(s => {
      const next = new Set(s);
      if (next.has(mid)) next.delete(mid); else next.add(mid);
      return next;
    });
  }

  async function approve() {
    setBusy(true);
    try {
      await post(`/dedup/groups/${g.id}/approve`, { exclude_ids: Array.from(excluded) });
      onChanged();
      onClose();
    } finally { setBusy(false); }
  }

  const keeper = members.find(m => m.media_id === keeperId) || members[0];
  const dupes  = members.filter(m => m.media_id !== keeperId);
  const toDelete = dupes.filter(m => !excluded.has(m.media_id));

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.85)", zIndex: 100,
      display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: "var(--panel)", borderRadius: 14, padding: 20,
        maxWidth: "min(1600px, 95vw)", width: "100%", maxHeight: "94vh", overflowY: "auto",
        border: "1px solid var(--line)",
      }}>
        {/* Header */}
        <div className="row spread" style={{ marginBottom: 14 }}>
          <div>
            <h3 style={{ margin: 0 }}>Duplicate stack &middot; {members.length} photos</h3>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              Method: <span className="pill">{g.method}</span>
              {" "}&middot; queueing <b style={{ color: "var(--danger)" }}>{toDelete.length}</b> for deletion
              {excluded.size > 0 && <span className="muted"> (excluded {excluded.size})</span>}
            </div>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn secondary" onClick={onClose}>Close</button>
            <button className="btn good" disabled={busy || toDelete.length === 0} onClick={approve}>
              Approve &middot; queue {toDelete.length} for deletion
            </button>
          </div>
        </div>

        {/* Big keeper preview */}
        <div style={{ display: "flex", gap: 16, marginBottom: 16 }}>
          <div style={{ flex: "0 0 360px", maxWidth: 360 }}>
            <div style={{ border: "3px solid var(--good)", borderRadius: 12, overflow: "hidden",
                          position: "relative", background: "#000" }}>
              <div style={{
                position: "absolute", top: 10, left: 10, background: "var(--good)", color: "#03241a",
                padding: "3px 12px", borderRadius: 999, fontWeight: 700, fontSize: 12, zIndex: 1,
              }}>KEEP</div>
              {keeper?.thumb
                ? <img src={keeper.thumb} alt={keeper.name}
                       style={{ width: "100%", height: 320, objectFit: "contain", display: "block" }} />
                : <div style={{ height: 320, background: "#000" }} />
              }
            </div>
            <div style={{ marginTop: 8, fontSize: 12 }}>
              <div style={{ fontWeight: 600 }}>{keeper?.name}</div>
              <div className="muted">{keeper?.width}x{keeper?.height} &middot; sharpness {Math.round(keeper?.blur_score || 0)}</div>
              <div style={{ color: "var(--accent)" }}>{fmtDate(keeper?.taken_at)}</div>
            </div>
          </div>

          {/* Duplicates — click to swap, X to exclude from deletion */}
          <div style={{ flex: 1 }}>
            <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
              <b>{dupes.length} duplicates</b> &middot; click to make keeper &middot; tap &times; to keep this one too (don't delete)
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))", gap: 8 }}>
              {dupes.map(m => {
                const isExcluded = excluded.has(m.media_id);
                return (
                  <div key={m.media_id} style={{
                    position: "relative", borderRadius: 8, overflow: "hidden",
                    border: `2px solid ${isExcluded ? "var(--warn)" : "var(--line)"}`,
                    background: "var(--panel2)",
                  }}>
                    {/* X / undo button — top-right */}
                    <button
                      onClick={() => toggleExclude(m.media_id)}
                      title={isExcluded ? "Re-include for deletion" : "Keep this photo too (skip deletion)"}
                      style={{
                        position: "absolute", top: 4, right: 4, zIndex: 2,
                        background: isExcluded ? "var(--warn)" : "rgba(0,0,0,0.75)", color: "#fff",
                        border: "none", borderRadius: "50%", width: 22, height: 22,
                        cursor: "pointer", fontWeight: 700, fontSize: 12, lineHeight: 1,
                      }}
                    >{isExcluded ? "+" : "x"}</button>

                    {/* Delete badge — top-left */}
                    {!isExcluded && (
                      <div style={{
                        position: "absolute", top: 4, left: 4, zIndex: 1,
                        background: "var(--danger)", color: "#fff",
                        padding: "1px 6px", borderRadius: 999, fontSize: 9, fontWeight: 700,
                      }}>DELETE</div>
                    )}

                    {/* Thumb — clicking makes this the keeper */}
                    <div onClick={() => setAsKeeper(m.media_id)} style={{ cursor: "pointer" }}>
                      {m.thumb
                        ? <img src={m.thumb} alt={m.name} loading="lazy"
                               style={{ width: "100%", height: 110, objectFit: "cover", display: "block",
                                        opacity: isExcluded ? 0.5 : 1 }} />
                        : <div style={{ height: 110, background: "#000" }} />
                      }
                      <div style={{ padding: "4px 6px", fontSize: 10 }}>
                        <div className="muted">{m.width}x{m.height}</div>
                        <div style={{ color: "var(--accent)" }}>{fmtDate(m.taken_at)}</div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Duplicates() {
  const [data, setData] = useState<PageData | null>(null);
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<Group | null>(null);

  function load(p?: number) {
    const off = (p ?? page) * PAGE_SIZE;
    get<PageData>(`/dedup/groups?limit=${PAGE_SIZE}&offset=${off}`).then(setData).catch(() => {});
  }
  useEffect(() => { load(); }, [page]);

  function goPage(p: number) { setPage(p); window.scrollTo(0, 0); }

  const total      = data?.total ?? 0;
  const totalDupes = data?.total_dupes ?? 0;
  const groups     = data?.items ?? [];
  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div>
      <div className="row spread">
        <h2>Duplicates &amp; similar</h2>
        <div className="row">
          <JobButton label="Detect (with CLIP)" startPath="/dedup/run" jobName="dedup"
                     body={{ use_clip: true }} onDone={() => { setPage(0); load(0); }} />
          <JobButton label="Detect (fast)" startPath="/dedup/run" jobName="dedup"
                     body={{ use_clip: false }} onDone={() => { setPage(0); load(0); }} className="secondary" />
        </div>
      </div>

      <div className="banner">
        Each tile is a stack of duplicates. The shown photo is the auto-picked <b style={{ color: "var(--good)" }}>keeper</b>;
        the badge top-right shows how many photos in the stack, bottom-right shows how many will be deleted.
        Click any stack to review and approve.
      </div>

      {total > 0 && (
        <div style={{ margin: "12px 0", fontSize: 13 }} className="row spread">
          <span>
            <b>{total.toLocaleString()}</b> stacks &middot; <b>{totalDupes.toLocaleString()}</b> photos involved
            &middot; ~<b>{(totalDupes - total).toLocaleString()}</b> can be removed
          </span>
          {totalPages > 1 && (
            <div className="row" style={{ gap: 4 }}>
              <button className="btn secondary" disabled={page === 0} onClick={() => goPage(page - 1)}>Prev</button>
              <span className="muted" style={{ fontSize: 12 }}>Page {page + 1} of {totalPages}</span>
              <button className="btn secondary" disabled={page >= totalPages - 1} onClick={() => goPage(page + 1)}>Next</button>
            </div>
          )}
        </div>
      )}

      {/* Date-grouped sections, newest first (like Google Photos) */}
      {groupByDate(groups).map(([dk, stacks]) => (
        <div key={dk} style={{ marginBottom: 24 }}>
          <div style={{
            position: "sticky", top: 0, zIndex: 5,
            background: "var(--bg)", padding: "8px 0",
            borderBottom: "1px solid var(--line)", marginBottom: 12,
            display: "flex", alignItems: "baseline", gap: 10,
          }}>
            <h3 style={{ margin: 0, fontSize: 15 }}>{prettyDateHeader(dk)}</h3>
            <span className="muted" style={{ fontSize: 12 }}>
              {stacks.length} stack{stacks.length !== 1 ? "s" : ""} ·{" "}
              {stacks.reduce((s, g) => s + g.members.length - 1, 0)} to remove
            </span>
          </div>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
            gap: 14,
          }}>
            {stacks.map(g => (
              <StackTile key={g.id} g={g} onClick={() => setOpen(g)} />
            ))}
          </div>
        </div>
      ))}

      {total > 0 && totalPages > 1 && (
        <div className="row" style={{ justifyContent: "center", margin: "20px 0", gap: 4 }}>
          <button className="btn secondary" disabled={page === 0} onClick={() => goPage(page - 1)}>Prev</button>
          <span className="muted" style={{ fontSize: 12, padding: "0 8px" }}>
            Page {page + 1} of {totalPages}
          </span>
          <button className="btn secondary" disabled={page >= totalPages - 1} onClick={() => goPage(page + 1)}>Next</button>
        </div>
      )}

      {total === 0 && <p className="muted">No duplicate groups. Run detection above.</p>}

      {open && <GroupModal g={open} onClose={() => setOpen(null)} onChanged={() => load()} />}
    </div>
  );
}
