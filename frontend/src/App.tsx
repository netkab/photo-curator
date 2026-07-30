import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { get } from "./lib/api";
import Catalog from "./pages/Catalog";
import Duplicates from "./pages/Duplicates";
import Enhance from "./pages/Enhance";
import MapsStudio from "./pages/MapsStudio";
import Videos from "./pages/Videos";
import VideoMetadata from "./pages/VideoMetadata";
import ReviewQueue from "./pages/ReviewQueue";
import DeleteHelper from "./pages/DeleteHelper";

interface Env {
  gpu: string | null;
  ollama: boolean;
  exiftool: boolean;
  ffmpeg: boolean;
  places_enabled: boolean;
  uploads_enabled: boolean;
}

const links = [
  { to: "/catalog", label: "Catalog" },
  { to: "/duplicates", label: "Duplicates" },
  { to: "/enhance", label: "Enhance" },
  { to: "/maps", label: "Maps Studio" },
  { to: "/videos", label: "Videos" },
  { to: "/video-metadata", label: "Fix Video Dates" },
  { to: "/review", label: "Review Queue" },
  { to: "/delete-helper", label: "Delete Helper" },
];

const ok  = (v: boolean) => <span style={{ color: v ? "var(--good)" : "var(--danger)" }}>{v ? "✓" : "✗"}</span>;
const opt = (v: boolean) => <span style={{ color: v ? "var(--good)" : "var(--muted)" }}>{v ? "✓" : "—"}</span>;

export default function App() {
  const [env, setEnv]         = useState<Env | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    get<Env>("/env")
      .then(e => { setEnv(e); setOffline(false); })
      .catch(() => setOffline(true));

    // Re-poll every 15s so status refreshes after server restart
    const t = setInterval(() =>
      get<Env>("/env")
        .then(e => { setEnv(e); setOffline(false); })
        .catch(() => setOffline(true)),
      15000
    );
    return () => clearInterval(t);
  }, []);

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>Photo Curator<small>local-first · review before apply</small></h1>
        <nav className="nav">
          {links.map((l) => (
            <NavLink key={l.to} to={l.to} className={({ isActive }) => (isActive ? "active" : "")}>
              {l.label}
            </NavLink>
          ))}
        </nav>

        <div style={{ marginTop: 24, fontSize: 11 }}>
          {offline ? (
            <div style={{ color: "var(--danger)" }}>⚠ Backend offline<br/>
              <span className="muted">Start: .\start.ps1</span>
            </div>
          ) : env ? (
            <div className="muted">
              <div>GPU: {env.gpu
                ? <span style={{ color: "var(--good)" }}>{env.gpu}</span>
                : <span style={{ color: "var(--warn)" }}>CPU only</span>}
              </div>
              <div>Ollama: {ok(env.ollama)} · ffmpeg: {ok(env.ffmpeg)}</div>
              <div>exiftool: {ok(env.exiftool)}</div>
              <div>Places: {opt(env.places_enabled)} · Upload: {opt(env.uploads_enabled)}</div>
            </div>
          ) : (
            <div className="muted" style={{ fontStyle: "italic" }}>checking…</div>
          )}
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/catalog" replace />} />
          <Route path="/catalog" element={<Catalog />} />
          <Route path="/duplicates" element={<Duplicates />} />
          <Route path="/enhance" element={<Enhance />} />
          <Route path="/maps" element={<MapsStudio />} />
          <Route path="/videos" element={<Videos />} />
          <Route path="/video-metadata" element={<VideoMetadata />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/delete-helper" element={<DeleteHelper />} />
        </Routes>
      </main>
    </div>
  );
}
