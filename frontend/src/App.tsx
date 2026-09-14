import {useEffect, useState, FormEvent} from "react";
import {NavLink, Navigate, Route, Routes} from "react-router-dom";
import {get, post} from "./lib/api";
import Catalog from "./pages/Catalog";
import Duplicates from "./pages/Duplicates";
import ReviewQueue from "./pages/ReviewQueue";

export default function App() {
  const [connected, setConnected] = useState(false);
  const [checking, setChecking] = useState(true);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {get("/health").then(() => setConnected(true)).catch(() => {})
    .finally(() => setChecking(false));}, []);
  async function pair(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {await post("/auth/login", {token: token.trim()}); setToken(""); setConnected(true);}
    catch (e) {setError(String(e));}
    finally {setBusy(false);}
  }
  if (checking) return <main className="main"><p role="status">Connecting to your local catalog…</p></main>;
  if (!connected) return <main className="main"><h1>Photo Curator</h1>
    <h2>Connect to your local catalog</h2>
    <p>Paste the local pairing token created during setup. It stays on this computer.</p>
    <form onSubmit={pair} className="pair-form">
      <label htmlFor="token">Local pairing token</label>
      <input id="token" type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} required />
      <button className="btn" disabled={busy}>{busy ? "Connecting…" : "Connect"}</button>
      {error && <p role="alert">{error}. Check that the backend is running and the token is correct, then retry.</p>}
    </form></main>;
  return <div className="app"><aside className="sidebar">
    <h1>Photo Curator<small>local previews · review before trash</small></h1>
    <nav className="nav">{[["catalog", "Catalog"], ["duplicates", "Duplicates"], ["review", "Review queue"]].map(([path, label]) =>
      <NavLink key={path} to={`/${path}`}>{label}</NavLink>)}</nav>
    <p className="muted">Use the Chrome extension to scan Google Photos and execute reviewed operations.</p>
    <button className="btn secondary" onClick={async () => {await post("/auth/logout"); setConnected(false);}}>Disconnect</button>
  </aside><main className="main"><Routes>
    <Route path="/" element={<Navigate to="/catalog" replace />} />
    <Route path="/catalog" element={<Catalog />} />
    <Route path="/duplicates" element={<Duplicates />} />
    <Route path="/review" element={<ReviewQueue />} />
    <Route path="*" element={<Navigate to="/catalog" replace />} />
  </Routes></main></div>;
}
