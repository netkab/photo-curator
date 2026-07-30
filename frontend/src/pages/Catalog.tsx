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
          <JobButton label="Ingest Takeout" startPath="/ingest" jobName="ingest" onDone={load} className="secondary" />
          <JobButton
            label={remaining > 0 ? `Analyze next 200 (${remaining.toLocaleString()} left)` : "Analyze (local AI)"}
            startPath="/analyze" jobName="analyze" body={{ limit: 200 }} onDone={load}
          />
        </div>
      </div>

      {stats && (
        <div className="card row" style={{ justifyContent: "space-around", flexWrap: "wrap" }}>
          <div className="stat"><b>{stats.photos.toLocaleString()}</b><span className="muted">photos</span></div>
          <div className="stat"><b>{stats.videos.toLocaleString()}</b><span className="muted">videos</span></div>
          <div className="stat"><b>{stats.with_gps.toLocaleString()}</b><span className="muted">geo-tagged</span></div>
          <div className="stat">
            <b>{stats.captioned.toLocaleString()}</b>
            <span className="muted">captioned{pct(stats.captioned, stats.photos)}</span>
          </div>
          <div className="stat"><b>{stats.with_text.toLocaleString()}</b><span className="muted">with text</span></div>
          <div className="stat"><b>{stats.people}</b><span className="muted">people</span></div>
          <div className="stat">
            <b style={{ color: remaining > 0 ? "var(--warn)" : "var(--good)" }}>
              {stats.analyzed.toLocaleString()}/{stats.photos.toLocaleString()}
            </b>
            <span className="muted">analyzed{pct(stats.analyzed, stats.photos)}</span>
          </div>
        </div>
      )}

      {stats && remaining > 0 && (
        <div className="banner" style={{ marginBottom: 16 }}>
          <b>{remaining.toLocaleString()} photos</b> still need analysis.
          Each click processes 200. For bulk processing use the CLI:<br/>
          <code style={{ fontSize: 11 }}>
            cd backend ; .\.venv\Scripts\python -m app.cli analyze --batch --limit 200
          </code>
          <span className="muted"> (runs until done, Ctrl+C safe, 30s cooldown between batches)</span>
        </div>
      )}

      <div className="card">
        <div className="row">
          <input placeholder="Search captions, OCR, names..." value={q}
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
        {items.length === 0 && <p className="muted">No media yet. Run "Ingest Takeout", then "Analyze".</p>}
      </div>
    </div>
  );
}
