import { useEffect, useRef, useState } from "react";
import { get, Job, post } from "../lib/api";
import JobButton from "../components/JobButton";

interface Source { id: number; name: string; thumb: string | null; file: string; taken_at: string | null; duration: number | null; bytes: number | null; }
interface Result { derived_id: number; kind: "compressed" | "highlight" | string; url: string; sources: Source[]; meta: any; }

function mb(n?: number | null) { return n ? `${(n / 1e6).toFixed(1)} MB` : "—"; }
function dur(s?: number | null) {
  if (!s) return "";
  const m = Math.floor(s / 60), ss = Math.round(s % 60);
  return `${m}:${ss.toString().padStart(2, "0")}`;
}
function fmtDate(iso?: string | null) {
  if (!iso) return "no date";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
function savedPct(meta: any): number | null {
  if (!meta?.bytes_before || !meta?.bytes_after) return null;
  return Math.round((1 - meta.bytes_after / meta.bytes_before) * 100);
}

// ---------- Compact tile ----------
function Tile({ r, onClick }: { r: Result; onClick: () => void }) {
  const poster = r.sources[0]?.thumb || null;
  const pct = savedPct(r.meta);
  const badge = r.kind === "highlight" ? `${r.meta?.clips_used ?? r.meta?.source_count ?? r.sources.length} clips` : (pct != null ? `−${pct}%` : "compressed");
  const date = r.kind === "highlight" ? r.meta?.taken_at : r.sources[0]?.taken_at;
  return (
    <div onClick={onClick} style={{ cursor: "pointer", borderRadius: 10, overflow: "hidden", border: "1px solid var(--line)", background: "var(--panel2)" }}
      onMouseEnter={(e) => (e.currentTarget.style.transform = "scale(1.03)")}
      onMouseLeave={(e) => (e.currentTarget.style.transform = "scale(1)")}>
      <div style={{ position: "relative", aspectRatio: "16 / 10", background: "#000" }}>
        {poster
          ? <img src={poster} loading="lazy" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
          : <div style={{ width: "100%", height: "100%", background: "#000" }} />}
        <span style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", fontSize: 30, color: "#fff", textShadow: "0 0 8px #000" }}>▶</span>
        <span className="pill" style={{ position: "absolute", top: 6, left: 6, background: "rgba(0,0,0,0.7)" }}>{r.kind}</span>
        <span style={{ position: "absolute", bottom: 6, right: 6, background: r.kind === "highlight" ? "var(--accent)" : "var(--good)", color: "#06122b", padding: "2px 8px", borderRadius: 999, fontSize: 11, fontWeight: 700 }}>{badge}</span>
      </div>
      <div className="muted" style={{ fontSize: 11, padding: "6px 8px" }}>{fmtDate(date)}</div>
    </div>
  );
}

// ---------- Detail + comparison modal ----------
function DetailModal({ r, onClose, onApproved }: { r: Result; onClose: () => void; onApproved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<Source | null>(r.kind === "highlight" ? r.sources[0] ?? null : null);
  const pct = savedPct(r.meta);

  async function approve() {
    setBusy(true);
    try { await post(`/videos/${r.derived_id}/approve`); onApproved(); onClose(); }
    finally { setBusy(false); }
  }

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.85)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: "var(--panel)", borderRadius: 14, padding: 20, maxWidth: "min(1500px, 95vw)", width: "100%", maxHeight: "94vh", overflowY: "auto", border: "1px solid var(--line)" }}>
        <div className="row spread" style={{ marginBottom: 6 }}>
          <h3 style={{ margin: 0 }}><span className="pill">{r.kind}</span> &middot; {fmtDate(r.kind === "highlight" ? r.meta?.taken_at : r.sources[0]?.taken_at)}</h3>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn secondary" onClick={onClose}>Close</button>
            <button className="btn good" disabled={busy} onClick={approve}>{busy ? "Queuing…" : "Approve → queue upload"}</button>
          </div>
        </div>
        <div className="muted" style={{ fontSize: 12, marginBottom: 14 }}>
          <b>Approve</b> queues this new file for upload to your dedicated Google Photos album (via Review Queue). Nothing uploads until you apply it there, and your original videos are never touched.
        </div>

        {r.kind === "compressed" ? (
          <div className="before-after">
            <div>
              <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>Original &middot; {mb(r.sources[0]?.bytes)}</div>
              {r.sources[0]
                ? <video src={r.sources[0].file} controls preload="metadata" style={{ width: "100%", maxHeight: 420, borderRadius: 8, background: "#000" }} />
                : <div className="muted">original not found</div>}
            </div>
            <div>
              <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                Compressed &middot; {mb(r.meta?.bytes_after)}{pct != null && <b style={{ color: "var(--good)" }}> (−{pct}%)</b>}
              </div>
              <video src={r.url} controls preload="metadata" style={{ width: "100%", maxHeight: 420, borderRadius: 8, background: "#000" }} />
            </div>
          </div>
        ) : (
          <>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 420px", minWidth: 280 }}>
                <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                  Highlight reel &middot; {r.meta?.scenes} scenes
                  {r.meta?.clips_used != null ? ` · uses ${r.meta.clips_used} of ${r.meta.source_count} clips` : ` from ${r.meta?.source_count} clips`}
                </div>
                <video src={r.url} controls autoPlay preload="metadata" style={{ width: "100%", maxHeight: 420, borderRadius: 8, background: "#000" }} />
              </div>
              <div style={{ flex: "1 1 420px", minWidth: 280 }}>
                <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>{sel ? `Source clip · ${sel.name}` : "Pick a source clip below to compare"}</div>
                {sel
                  ? <video key={sel.id} src={sel.file} controls preload="metadata" style={{ width: "100%", maxHeight: 420, borderRadius: 8, background: "#000" }} />
                  : <div style={{ width: "100%", height: 240, borderRadius: 8, background: "#000", display: "grid", placeItems: "center" }} className="muted">no clip selected</div>}
              </div>
            </div>
            <div className="muted" style={{ fontSize: 12, margin: "14px 0 6px" }}>
              Candidate clips from this event (<b>{r.sources.length}</b>)
              {r.meta?.clips_used != null && <> &middot; <b>{r.meta.clips_used}</b> used in the reel</>}
              {r.sources.length === 0 && " — older reel, source list not recorded (rebuild to enable comparison)"}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: 8 }}>
              {r.sources.map((s) => (
                <div key={s.id} onClick={() => setSel(s)} title={s.name} style={{ cursor: "pointer", borderRadius: 6, overflow: "hidden", border: `2px solid ${sel?.id === s.id ? "var(--accent)" : "var(--line)"}`, background: "var(--panel2)" }}>
                  {s.thumb ? <img src={s.thumb} loading="lazy" style={{ width: "100%", height: 80, objectFit: "cover" }} /> : <div style={{ height: 80, background: "#000" }} />}
                  <div className="muted" style={{ fontSize: 10, padding: "2px 4px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{dur(s.duration)} · {s.name}</div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function Videos() {
  const [results, setResults] = useState<Result[]>([]);
  const [open, setOpen] = useState<Result | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loadErr, setLoadErr] = useState(false);
  const [vjob, setVjob] = useState<Job | null>(null);
  // In-flight guards: never stack a second poll while one is pending. Stacked polls (during a busy
  // backend) once filled the server's connection queue and hung it — this prevents that recurring.
  const loadBusy = useRef(false);
  const jobBusy = useRef(false);
  const load = () => {
    if (loadBusy.current) return Promise.resolve();
    loadBusy.current = true;
    return get<Result[]>("/videos/results")
      .then((r) => { setResults(r); setLoadErr(false); })
      .catch(() => setLoadErr(true))
      .finally(() => { loadBusy.current = false; });
  };
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);
  // Poll the job runner so the live status banner shows the current step / progress / any error.
  useEffect(() => {
    const tick = () => {
      if (jobBusy.current) return;
      jobBusy.current = true;
      get<Job[]>("/jobs")
        .then((j) => setVjob(j.find((x) => x.name === "video") || null))
        .catch(() => {})
        .finally(() => { jobBusy.current = false; });
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => clearInterval(t);
  }, []);

  async function stop() {
    setNotice(null);
    try {
      const r = await post<{ cancelled: number }>("/videos/cancel");
      setNotice(r.cancelled ? "Stopping — the current clip finishes first." : "No video job is running.");
    } catch (e: any) { setNotice(`Stop failed: ${e.message || e}`); }
  }

  async function resetAll() {
    if (!confirm("Start from scratch? Deletes all not-yet-uploaded compressed clips and highlight reels. Originals are never touched.")) return;
    setNotice(null);
    try {
      setBusy("Stopping any running job…");
      await post("/videos/cancel");
      for (let i = 0; i < 20; i++) {
        const jobs = await get<Job[]>("/jobs");
        if (!jobs.some((j) => j.name === "video" && j.status === "running")) break;
        await new Promise((r) => setTimeout(r, 1500));
      }
      setBusy("Clearing results…");
      const r = await post<{ removed: number; kept_uploaded: number }>("/videos/reset");
      setNotice(`Cleared ${r.removed} result(s)${r.kept_uploaded ? `, kept ${r.kept_uploaded} already-uploaded` : ""}.`);
      load();
    } catch (e: any) { setNotice(`Reset failed: ${e.message || e}`); }
    finally { setBusy(null); }
  }

  const highlights = results.filter((r) => r.kind === "highlight");
  const compressed = results.filter((r) => r.kind === "compressed");

  return (
    <div>
      <div className="row spread">
        <h2>Videos — compress &amp; highlights</h2>
        <div className="row">
          <JobButton label="Compress all" startPath="/videos/compress" jobName="video" onDone={load} className="secondary" />
          <JobButton label="Test: 5 reels" startPath="/videos/highlights" jobName="video" body={{ limit: 5 }} onDone={load} className="secondary" />
          <JobButton label="Build highlights" startPath="/videos/highlights" jobName="video" onDone={load} />
          <button className="btn" onClick={stop} disabled={!!busy}>Stop</button>
          <button className="btn danger" onClick={resetAll} disabled={!!busy}>{busy || "Reset (start over)"}</button>
        </div>
      </div>
      <div className="banner">
        New files (≥ HD, good bitrate); originals are never replaced. Click a tile to preview and <b>compare with the original(s)</b>.
        <b> Approve</b> queues a file for upload to your Google Photos album (via Review Queue) — nothing uploads automatically.
      </div>
      {notice && <div className="banner" style={{ borderColor: "var(--accent)" }}>{notice}</div>}

      {vjob?.status === "running" && (
        <div className="banner" style={{ borderColor: "var(--accent)" }}>
          ⏳ <b>{Math.round((vjob.progress || 0) * 100)}%</b> — {vjob.message || "working…"}
          <div className="progress" style={{ marginTop: 6 }}><div style={{ width: `${(vjob.progress || 0) * 100}%` }} /></div>
        </div>
      )}
      {vjob?.status === "error" && (
        <div className="banner" style={{ borderColor: "var(--danger)", color: "var(--danger)" }}>
          ✖ Last video job failed: {(vjob.error || "").split("\n")[0]}
        </div>
      )}
      {vjob?.status === "done" && vjob.result && (
        <div className="banner" style={{ borderColor: "var(--good)" }}>
          ✓ Last job done: {typeof vjob.result === "object" ? JSON.stringify(vjob.result) : String(vjob.result)}
        </div>
      )}

      {[["Highlight reels", highlights], ["Compressed clips", compressed]].map(([title, list]: any) => (
        list.length > 0 && (
          <div key={title} style={{ marginTop: 18 }}>
            <h3 style={{ fontSize: 15, margin: "0 0 10px" }}>{title} <span className="muted" style={{ fontSize: 12 }}>({list.length})</span></h3>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 14 }}>
              {list.map((r: Result) => <Tile key={r.derived_id} r={r} onClick={() => setOpen(r)} />)}
            </div>
          </div>
        )
      ))}

      {results.length === 0 && (loadErr
        ? <p className="muted">Couldn't load results — the backend is likely busy running a job (it briefly stops answering during heavy scene detection). This refreshes automatically every few seconds.</p>
        : <p className="muted">No video results yet. Run compress or build highlights.</p>)}

      {open && <DetailModal r={open} onClose={() => setOpen(null)} onApproved={load} />}
    </div>
  );
}
