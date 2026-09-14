import { useEffect, useState } from "react";
import { get } from "../lib/api";
import JobButton from "../components/JobButton";

interface Stats {
  photos: number; videos: number; with_gps: number;
  captioned: number; with_text: number; faces: number; people: number;
  analyzed: number;
}
interface Item {
  id: number; type: string; name: string;
  thumb: string | null; place_name: string | null; caption: string | null;
}

function pct(done: number, total: number): string {
  if (!total) return "";
  return ` (${Math.round(done * 100 / total)}%)`;
}

export default function Catalog() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [q, setQ] = useState("");
  const [type, setType] = useState("");

  function load() {
    get<Stats>("/stats").then(setStats).catch(() => {});
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (type) params.set("type", type);
    get<{ items: Item[] }>(`/media?${params}`).then((r) => setItems(r.items)).catch(() => {});
  }
  useEffect(() => { load(); }, []);

  const remaining = stats ? stats.photos - stats.analyzed : 0;

  return (
    <div>
      <div className="row spread">
        <h2>Catalog</h2>
        <div className="row">
          <JobButton label="Fetch thumbnails and find duplicates" startPath="/direct/analyze"
            jobName="direct-analysis" body={{use_clip: false}} onDone={load} />
        </div>
      </div>

      {stats && (
        <div className="card row" style={{ justifyContent: "space-around", flexWrap: "wrap" }}>
          <div className="stat"><b>{stats.photos.toLocaleString()}</b><span className="muted">photos</span></div>
          <div className="stat"><b>{stats.videos.toLocaleString()}</b><span className="muted">videos</span></div>
          <div className="stat">
            <b style={{ color: remaining > 0 ? "var(--warn)" : "var(--good)" }}>
              {stats.analyzed.toLocaleString()}/{stats.photos.toLocaleString()}
            </b>
            <span className="muted">analyzed{pct(stats.analyzed, stats.photos)}</span>
          </div>
        </div>
      )}

      <p className="muted">Scan your library in the Chrome extension first. No Takeout export is needed.
        Photos are analyzed from small local previews; videos are listed for reference.</p>

      <div className="card">
        <div className="row">
          <input aria-label="Search catalog filenames" placeholder="Search filenames…" value={q}
                 onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} style={{ flex: 1 }} />
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">all</option>
            <option value="photo">photos</option>
            <option value="video">videos</option>
          </select>
          <button className="btn" onClick={load}>Search</button>
        </div>
      </div>

      <div className="grid">
        {items.map((m) => (
          <div className="tile" key={m.id}>
            {m.thumb ? <img src={m.thumb} alt={m.name} /> : <div style={{ height: 130 }} />}
            <div className="cap">{m.caption || m.name}{m.place_name ? ` . ${m.place_name}` : ""}</div>
          </div>
        ))}
        {items.length === 0 && <p className="muted">No catalog items yet. Open Library Sync in the Chrome extension to scan Google Photos.</p>}
      </div>
    </div>
  );
}
