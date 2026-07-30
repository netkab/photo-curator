import { useEffect, useMemo, useRef, useState } from "react";
import { get } from "../lib/api";

interface KeeperInfo {
  name: string; thumb: string | null; taken_at: string | null;
  width: number; height: number; path: string;
}
interface Item {
  media_id: number; action_id: number; name: string;
  thumb: string | null; taken_at: string | null; date_key: string;
  place_name: string | null; width: number; height: number; bytes: number;
  path: string; reason: string | null; keeper: KeeperInfo | null;
}
interface Data { total: number; estimated_mb: number; items: Item[]; }

const STORAGE_KEY = "delete-helper-done";

function loadDone(): Set<number> {
  try { return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]")); }
  catch { return new Set(); }
}
function saveDone(s: Set<number>) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(s)));
}

function prettyDate(date_key: string): string {
  if (date_key === "no-date") return "Undated";
  return new Date(date_key).toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
}

function shortDate(date_key: string): string {
  if (date_key === "no-date") return "Undated";
  return new Date(date_key).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall through */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;left:-9999px;top:0";
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}

// Fullscreen side-by-side comparison modal
function Preview({ dup, keeper, onClose }: { dup: Item; keeper: KeeperInfo | null; onClose: () => void }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.92)", zIndex: 200,
      display: "flex", flexDirection: "column", padding: 16,
    }}>
      <div className="row spread" style={{ marginBottom: 10, color: "#fff" }}>
        <b>Comparison — find the DELETE photo visually in Google Photos</b>
        <button className="btn secondary" onClick={onClose}>Close (Esc)</button>
      </div>

      <div onClick={e => e.stopPropagation()} style={{
        display: "grid", gridTemplateColumns: keeper ? "1fr 1fr" : "1fr",
        gap: 16, flex: 1, minHeight: 0,
      }}>
        {keeper && (
          <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div style={{ color: "var(--good)", fontWeight: 700, marginBottom: 6, fontSize: 13 }}>
              ✓ KEEP — {keeper.width}×{keeper.height}
            </div>
            <img src={keeper.thumb || ""} alt="keeper"
                 style={{ flex: 1, minHeight: 0, objectFit: "contain", background: "#111", borderRadius: 8,
                           border: "2px solid var(--good)" }} />
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ color: "var(--danger)", fontWeight: 700, marginBottom: 6, fontSize: 13 }}>
            ✗ DELETE — {dup.width}×{dup.height}
            {dup.taken_at && <span style={{ color: "#aaa", fontWeight: 400, marginLeft: 8 }}>taken {fmtTime(dup.taken_at)}</span>}
          </div>
          <img src={`/api/media/${dup.media_id}/file`} alt="delete"
               style={{ flex: 1, minHeight: 0, objectFit: "contain", background: "#111", borderRadius: 8,
                         border: "2px solid var(--danger)" }} />
        </div>
      </div>

      <div style={{ marginTop: 10, textAlign: "center", color: "#aaa", fontSize: 12 }}>
        💡 Keep this window open as a reference. In Google Photos, scroll to <b style={{ color: "#fff" }}>{prettyDate(dup.date_key)}</b>,
        find the visually matching DELETE photo, hover it → checkbox → trash.
      </div>
    </div>
  );
}

// Date group: shows a compact visual grid of all DELETE thumbnails for one date
function DateGroup({ dateKey, items, done, onToggle, onMarkAll, onPreview }: {
  dateKey: string;
  items: Item[];
  done: Set<number>;
  onToggle: (id: number) => void;
  onMarkAll: (items: Item[]) => void;
  onPreview: (item: Item) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const doneCount = items.filter(i => done.has(i.media_id)).length;
  const allDone = doneCount === items.length;

  return (
    <div className="card" style={{ padding: 12 }}>
      {/* Date header */}
      <div className="row spread" style={{ marginBottom: collapsed ? 0 : 12 }}>
        <div onClick={() => setCollapsed(c => !c)}
             style={{ cursor: "pointer", userSelect: "none", display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 13 }}>{collapsed ? "▸" : "▾"}</span>
          <b style={{ fontSize: 14 }}>{prettyDate(dateKey)}</b>
          <span className="muted" style={{ fontSize: 12 }}>
            {doneCount}/{items.length} done
            {" · "}{((items.reduce((s, i) => s + (i.bytes || 0), 0)) / 1_000_000).toFixed(0)} MB
          </span>
          {allDone && <span style={{ color: "var(--good)", fontSize: 12, fontWeight: 600 }}>✓ Complete</span>}
        </div>
        <div className="row" style={{ gap: 6 }}>
          <a className="btn secondary" href="https://photos.google.com/" target="gphotos" rel="noopener"
             style={{ fontSize: 12 }}>
            Open GP
          </a>
          <button className="btn good" style={{ fontSize: 12 }} onClick={() => onMarkAll(items)}>
            Mark all done ({items.length})
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          {/* GP workflow tip for this date */}
          <div style={{
            background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 8,
            padding: "8px 12px", marginBottom: 12, fontSize: 12, color: "var(--muted)",
          }}>
            <b style={{ color: "var(--text)" }}>In Google Photos:</b>
            {" "}Use the <b style={{ color: "var(--accent)" }}>year scrubber</b> on the right edge to jump to{" "}
            <b style={{ color: "var(--text)" }}>{shortDate(dateKey)}</b>.
            Hover any photo below for a checkbox, select the matching ones, then press trash.
            {" "}The <b style={{ color: "var(--danger)" }}>red border</b> = DELETE, <b style={{ color: "var(--good)" }}>green</b> = keep.
          </div>

          {/* Visual grid: KEEP + DELETE pairs */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            {items.map(it => {
              const isDone = done.has(it.media_id);
              return (
                <div key={it.media_id} style={{
                  background: "var(--panel2)", border: isDone ? "2px solid var(--good)" : "1px solid var(--line)",
                  borderRadius: 8, overflow: "hidden", opacity: isDone ? 0.45 : 1,
                  width: 200, flexShrink: 0, position: "relative",
                }}>
                  {/* KEEP + DELETE side by side, small */}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
                    {it.keeper?.thumb
                      ? <img src={it.keeper.thumb} alt="keep" loading="lazy"
                             style={{ width: "100%", height: 80, objectFit: "cover", display: "block",
                                       borderRight: "1px solid var(--line)" }} />
                      : <div style={{ height: 80, background: "#000" }} />
                    }
                    <div style={{ position: "relative" }}>
                      {it.thumb
                        ? <img src={it.thumb} alt="del" loading="lazy"
                               style={{ width: "100%", height: 80, objectFit: "cover", display: "block" }} />
                        : <div style={{ height: 80, background: "#000" }} />
                      }
                      <div style={{
                        position: "absolute", top: 3, right: 3,
                        background: "var(--danger)", color: "#fff",
                        padding: "1px 4px", borderRadius: 3, fontSize: 8, fontWeight: 700,
                      }}>DEL</div>
                    </div>
                  </div>

                  {/* Info */}
                  <div style={{ padding: "6px 8px", fontSize: 10 }}>
                    <div style={{ color: "var(--danger)", fontWeight: 600, marginBottom: 2 }}>
                      Delete: {it.width}×{it.height}
                      {it.taken_at ? ` · ${fmtTime(it.taken_at)}` : ""}
                    </div>
                    {it.keeper && (
                      <div style={{ color: "var(--good)" }}>
                        Keep: {it.keeper.width}×{it.keeper.height}
                      </div>
                    )}
                  </div>

                  {/* Done overlay */}
                  {isDone && (
                    <div style={{
                      position: "absolute", inset: 0, display: "flex",
                      alignItems: "center", justifyContent: "center",
                      background: "rgba(70,192,138,0.3)", fontSize: 28, color: "#fff",
                    }}>✓</div>
                  )}

                  {/* Actions */}
                  <div className="row" style={{ gap: 4, padding: "4px 6px" }}>
                    <button className="btn secondary" onClick={() => onPreview(it)}
                            style={{ flex: 1, fontSize: 10, padding: "4px 6px" }}>
                      🔍 Full size
                    </button>
                    <label style={{
                      cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
                      fontSize: 11, padding: "4px 6px", borderRadius: 6,
                      background: isDone ? "var(--good)" : "var(--panel2)",
                      color: isDone ? "#03241a" : "var(--text)",
                      border: "1px solid var(--line)", fontWeight: isDone ? 700 : 400,
                    }}>
                      <input type="checkbox" checked={isDone}
                             onChange={() => onToggle(it.media_id)}
                             style={{ margin: 0, accentColor: "var(--good)" }} />
                      {isDone ? "Done ✓" : "Done"}
                    </label>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

export default function DeleteHelper() {
  const [data, setData] = useState<Data | null>(null);
  const [done, setDone] = useState<Set<number>>(loadDone());
  const [hideD, setHideD] = useState(true);
  const [preview, setPreview] = useState<Item | null>(null);
  const [toast, setToast] = useState<{ ok: boolean; msg: string; key: number } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    get<Data>("/review/delete-items").then(setData).catch(() => {});
  }, []);

  function showToast(ok: boolean, msg: string) {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ ok, msg, key: Date.now() });
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }

  function toggleDone(id: number) {
    setDone(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      saveDone(next);
      return next;
    });
  }

  function markAllDone(items: Item[]) {
    setDone(prev => {
      const next = new Set(prev);
      items.forEach(i => next.add(i.media_id));
      saveDone(next);
      return next;
    });
    showToast(true, `Marked ${items.length} photos as done`);
  }

  function clearProgress() {
    if (!confirm("Clear all progress and start over?")) return;
    setDone(new Set());
    saveDone(new Set());
  }

  const allItems = data?.items ?? [];
  const visibleItems = hideD ? allItems.filter(i => !done.has(i.media_id)) : allItems;

  const grouped = useMemo(() => {
    const m = new Map<string, Item[]>();
    for (const it of visibleItems) {
      if (!m.has(it.date_key)) m.set(it.date_key, []);
      m.get(it.date_key)!.push(it);
    }
    // Sort by most items first (tackle biggest batches first)
    return Array.from(m.entries()).sort((a, b) => b[1].length - a[1].length);
  }, [visibleItems]);

  if (!data) return <p className="muted">Loading…</p>;

  const total = data.total;
  const doneCount = allItems.filter(i => done.has(i.media_id)).length;
  const remaining = total - doneCount;
  const pct = total ? Math.round(doneCount * 100 / total) : 0;
  const mbLeft = allItems.filter(i => !done.has(i.media_id))
                         .reduce((s, i) => s + (i.bytes || 0), 0) / 1_000_000;

  return (
    <div>
      <div className="row spread">
        <h2>Delete Helper</h2>
        <div className="row">
          <a className="btn secondary" href="https://photos.google.com/" target="gphotos" rel="noopener">
            🗂️ Open Google Photos
          </a>
          <button className="btn secondary" onClick={clearProgress}>Reset progress</button>
        </div>
      </div>

      {/* How it works */}
      <div className="banner">
        <b>How this works:</b> Keep this page open on one side, Google Photos on the other.
        Each card shows a date with <span style={{ color: "var(--good)" }}>KEEP</span> (left) and{" "}
        <span style={{ color: "var(--danger)" }}>DELETE</span> (right) thumbnails side-by-side.
        In Google Photos, use the <b>right-edge year scrubber</b> to jump to the date, visually
        spot the DELETE photo, hover it → checkbox → trash.
        Dates are sorted by <b>most duplicates first</b> for efficiency.
      </div>

      {/* Progress */}
      <div className="card" style={{ padding: "12px 16px" }}>
        <div className="row spread" style={{ marginBottom: 6 }}>
          <div>
            <b style={{ fontSize: 18 }}>{doneCount.toLocaleString()}</b>/{total.toLocaleString()} done
            {" · "}<b style={{ color: "var(--warn)" }}>{remaining.toLocaleString()} left</b>
            {" · "}~<b>{mbLeft.toFixed(0)} MB</b> to free
            {" · "}<b>{grouped.length}</b> date{grouped.length !== 1 ? "s" : ""}
          </div>
          <label className="row" style={{ gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={hideD} onChange={e => setHideD(e.target.checked)} />
            Hide done
          </label>
        </div>
        <div className="progress" style={{ height: 8 }}>
          <div style={{ width: `${pct}%`, background: pct === 100 ? "var(--good)" : "var(--accent)" }} />
        </div>
      </div>

      {grouped.length === 0 && (
        <p className="muted">
          {total === 0 ? "No photos queued. Approve duplicate stacks first." : "All done! ✓"}
        </p>
      )}

      {grouped.map(([dk, items]) => (
        <DateGroup key={dk} dateKey={dk} items={items} done={done}
                   onToggle={toggleDone} onMarkAll={markAllDone} onPreview={setPreview} />
      ))}

      {preview && <Preview dup={preview} keeper={preview.keeper} onClose={() => setPreview(null)} />}

      {toast && (
        <div key={toast.key} onClick={() => setToast(null)} style={{
          position: "fixed", bottom: 32, left: "50%", transform: "translateX(-50%)",
          background: toast.ok ? "var(--good)" : "var(--danger)",
          color: toast.ok ? "#03241a" : "#fff",
          padding: "12px 22px", borderRadius: 999, fontWeight: 600, fontSize: 14,
          boxShadow: "0 6px 24px rgba(0,0,0,0.5)", zIndex: 300, cursor: "pointer",
        }}>
          {toast.ok ? "✓ " : "⚠ "}{toast.msg}
        </div>
      )}
    </div>
  );
}
