import { useEffect, useState } from "react";
import { get, post } from "../lib/api";

interface Item { id: number; name: string; thumb: string | null; file: string; duration: number | null; bytes: number | null; }
interface Page { total: number; items: Item[]; }
interface InferPreview { undated: number; matched: number; samples: { id: number; name: string; date: string }[]; }

const PAGE = 60;
function mb(n?: number | null) { return n ? `${(n / 1e6).toFixed(0)} MB` : ""; }
function dur(s?: number | null) {
  if (!s) return "";
  const m = Math.floor(s / 60), ss = Math.round(s % 60);
  return `${m}:${ss.toString().padStart(2, "0")}`;
}

export default function VideoMetadata() {
  const [total, setTotal] = useState(0);
  const [items, setItems] = useState<Item[]>([]);
  const [offset, setOffset] = useState(0);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [play, setPlay] = useState<number | null>(null);
  const [date, setDate] = useState("");
  const [place, setPlace] = useState("");
  const [gps, setGps] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [infer, setInfer] = useState<InferPreview | null>(null);

  function load(at = offset) {
    get<Page>(`/videos/needs-metadata?limit=${PAGE}&offset=${at}`)
      .then((p) => { setTotal(p.total); setItems(p.items); setSel(new Set()); setPlay(null); })
      .catch((e) => setMsg(`Load failed: ${e.message || e}`));
  }
  useEffect(() => { load(0); }, []);

  function toggle(id: number) {
    setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }
  const allOnPage = items.length > 0 && items.every((i) => sel.has(i.id));
  function toggleAll() {
    setSel((s) => {
      if (allOnPage) { const n = new Set(s); items.forEach((i) => n.delete(i.id)); return n; }
      const n = new Set(s); items.forEach((i) => n.add(i.id)); return n;
    });
  }

  async function previewInfer() {
    setBusy(true); setMsg(null);
    try { setInfer(await get<InferPreview>("/videos/infer-dates")); }
    catch (e: any) { setMsg(`Scan failed: ${e.message || e}`); }
    finally { setBusy(false); }
  }
  async function applyInfer() {
    setBusy(true); setMsg(null);
    try {
      const r = await post<{ updated: number }>("/videos/infer-dates");
      setInfer(null);
      setMsg(`Filled ${r.updated} date(s) from filenames. Those clips now cluster by day on the next "Build highlights".`);
      setOffset(0); load(0); // dated clips drop off this list
    } catch (e: any) { setMsg(`Apply failed: ${e.message || e}`); }
    finally { setBusy(false); }
  }

  async function apply() {
    if (sel.size === 0) { setMsg("Select at least one video first."); return; }
    if (!date && !place.trim() && !gps.trim()) { setMsg("Enter a date and/or a place to assign."); return; }
    let gps_lat: number | undefined, gps_lng: number | undefined;
    if (gps.trim()) {
      const [a, b] = gps.split(",").map((x) => parseFloat(x.trim()));
      if (Number.isFinite(a) && Number.isFinite(b)) { gps_lat = a; gps_lng = b; }
      else { setMsg('GPS must be "lat, lng" (e.g. 12.97, 77.59).'); return; }
    }
    setBusy(true); setMsg(null);
    try {
      const r = await post<{ updated: number }>("/videos/assign-metadata", {
        ids: [...sel], date: date || undefined, place_name: place.trim() || undefined, gps_lat, gps_lng,
      });
      setMsg(`Updated ${r.updated} video(s). They'll be grouped on the next "Build highlights".`);
      load(offset); // assigned videos drop off the list
    } catch (e: any) {
      setMsg(`Update failed: ${e.message || e}`);
    } finally { setBusy(false); }
  }

  const page = Math.floor(offset / PAGE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <div>
      <h2>Fix video dates &amp; places</h2>
      <div className="banner">
        These <b>{total}</b> videos have no capture date and no location, so <b>Build highlights</b> skips them.
        Select some, assign a shared <b>date</b> and/or <b>place</b>, and they'll be grouped into an event on the
        next run. Originals on disk are never modified — only the catalog.
      </div>

      <div className="card">
        <div className="row spread">
          <b style={{ fontSize: 13 }}>Auto · infer dates from filenames</b>
          <div className="row">
            <button className="btn secondary" onClick={previewInfer} disabled={busy}>Scan filenames</button>
            {infer && infer.matched > 0 && (
              <button className="btn good" onClick={applyInfer} disabled={busy}>Apply {infer.matched} inferred date(s)</button>
            )}
          </div>
        </div>
        {infer && (
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            Found a date in <b>{infer.matched}</b> of {infer.undated} undated videos
            {infer.matched === 0 ? " — nothing to apply." : "."}
            {infer.samples.length > 0 && (
              <div style={{ marginTop: 4 }}>
                {infer.samples.slice(0, 5).map((s) => `${s.name} → ${s.date.slice(0, 10)}`).join("  ·  ")}
              </div>
            )}
          </div>
        )}
        <hr style={{ border: 0, borderTop: "1px solid var(--line)", margin: "12px 0" }} />
        <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>Or assign a date/place manually to the selected videos below:</div>
        <div className="row">
          <label>Date <input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
          <label>Place <input type="text" placeholder="e.g. Goa Trip" value={place} onChange={(e) => setPlace(e.target.value)} style={{ width: 180 }} /></label>
          <label>GPS <input type="text" placeholder="lat, lng (optional)" value={gps} onChange={(e) => setGps(e.target.value)} style={{ width: 160 }} /></label>
          <button className="btn good" onClick={apply} disabled={busy || sel.size === 0}>
            {busy ? "Applying…" : `Apply to ${sel.size} selected`}
          </button>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn secondary" onClick={toggleAll} disabled={items.length === 0}>
            {allOnPage ? "Clear page" : "Select all on page"}
          </button>
          <span className="muted" style={{ fontSize: 12 }}>{sel.size} selected · page {page}/{pages} · {total} remaining</span>
        </div>
        {msg && <div className="muted" style={{ fontSize: 12, marginTop: 8, color: "var(--accent)" }}>{msg}</div>}
      </div>

      <div className="grid">
        {items.map((it) => (
          <div key={it.id} className="card" style={{ padding: 8, margin: 0, outline: sel.has(it.id) ? "2px solid var(--accent)" : "none" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
              <input type="checkbox" checked={sel.has(it.id)} onChange={() => toggle(it.id)} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{it.name}</span>
            </label>
            {play === it.id ? (
              <video src={it.file} controls autoPlay style={{ width: "100%", height: 120, marginTop: 6, borderRadius: 6, background: "#000" }} />
            ) : (
              <div onClick={() => setPlay(it.id)} style={{ position: "relative", marginTop: 6, cursor: "pointer" }}>
                {it.thumb
                  ? <img src={it.thumb} style={{ width: "100%", height: 120, objectFit: "cover", borderRadius: 6, background: "#000" }} />
                  : <div style={{ width: "100%", height: 120, borderRadius: 6, background: "#000" }} />}
                <span style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", fontSize: 26, color: "#fff", textShadow: "0 0 6px #000" }}>▶</span>
              </div>
            )}
            <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>{dur(it.duration)} · {mb(it.bytes)}</div>
          </div>
        ))}
      </div>
      {items.length === 0 && <p className="muted">🎉 No videos are missing date &amp; location.</p>}

      {total > PAGE && (
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn secondary" disabled={offset === 0} onClick={() => { const o = Math.max(0, offset - PAGE); setOffset(o); load(o); }}>← Prev</button>
          <button className="btn secondary" disabled={offset + PAGE >= total} onClick={() => { const o = offset + PAGE; setOffset(o); load(o); }}>Next →</button>
        </div>
      )}
    </div>
  );
}
