import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { get, Job, post, runJob } from "../lib/api";
import JobButton from "../components/JobButton";

interface Img {
  id: number; media_type: string; thumb: string | null; file: string | null; caption?: string | null;
  is_portrait: boolean; person_in_focus: boolean; faces: number; is_document: boolean; recommended: boolean;
}
interface ClusterItem {
  id: number; place_id: string | null; name: string | null; address: string | null;
  lat: number; lng: number; count: number; matched: boolean; manual: boolean; reviewed: boolean;
  thumbs: { id: number; thumb: string | null }[];
}
interface ClusterDetail extends Omit<ClusterItem, "thumbs"> {
  needs_analysis: boolean; shown: number; images: Img[];
}
interface Suggestion {
  place_id: string; name: string | null; address: string | null;
  rating?: number | null; rating_count?: number | null; primary_type?: string | null;
}
interface DraftResp { rating: number; text: string; captions_used: number; grounded_by: string; no_captions: boolean; }
interface Ignored { id: number; label: string; place_id: string | null; lat: number; lng: number; }
interface ReviewOptions {
  types: { value: string; label: string }[];
  aspects_by_type: Record<string, { key: string; label: string }[]>;
  uploads_enabled: boolean;
}
interface VideoUploadState { status: "uploading" | "done" | "error"; url?: string; error?: string; }

export default function MapsStudio() {
  const [clusters, setClusters] = useState<ClusterItem[]>([]);
  const [detail, setDetail] = useState<ClusterDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const [name, setName] = useState("");
  const [rating, setRating] = useState(5);
  const [placeType, setPlaceType] = useState("place");
  const [aspects, setAspects] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [reviewOpts, setReviewOpts] = useState<ReviewOptions | null>(null);
  const [text, setText] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [draftMeta, setDraftMeta] = useState<DraftResp | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [staged, setStaged] = useState<{ export_dir: string; exported_images: number; review_url?: string } | null>(null);
  const [rotations, setRotations] = useState<Record<number, number>>({});
  const [lightbox, setLightbox] = useState<number | null>(null); // index into detail.images
  const [videoUploads, setVideoUploads] = useState<Record<number, VideoUploadState>>({});
  const [loadingMore, setLoadingMore] = useState(false);

  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [loadingSugg, setLoadingSugg] = useState(false);
  const [query, setQuery] = useState("");

  const [ignored, setIgnored] = useState<Ignored[]>([]);
  const [showIgnored, setShowIgnored] = useState(false);
  const [showReviewed, setShowReviewed] = useState(false);

  const [mjob, setMjob] = useState<Job | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const jobBusy = useRef(false);

  const load = () => get<ClusterItem[]>("/maps/clusters").then(setClusters).catch(() => {});
  const loadIgnored = () => get<Ignored[]>("/maps/ignored").then(setIgnored).catch(() => {});
  useEffect(() => {
    load(); loadIgnored();
    get<ReviewOptions>("/maps/review-options").then(setReviewOpts).catch(() => {});
  }, []);

  function changeType(t: string) { setPlaceType(t); setAspects([]); }   // aspects are type-specific
  function toggleAspect(k: string) {
    setAspects((a) => (a.includes(k) ? a.filter((x) => x !== k) : [...a, k]));
  }

  // Poll the job runner so the live status banner + Stop button reflect the maps clustering job.
  useEffect(() => {
    const tick = () => {
      if (jobBusy.current) return;
      jobBusy.current = true;
      get<Job[]>("/jobs")
        .then((j) => setMjob(j.find((x) => x.name === "maps") || null))
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
      const r = await post<{ cancelled: number }>("/maps/cancel");
      setNotice(r.cancelled ? "Stopping — the current lookup finishes first." : "No clustering job is running.");
    } catch (e: any) { setNotice(`Stop failed: ${e.message || e}`); }
  }

  function onClusterDone(j: Job) {
    load();
    const r = j.result || {};
    if (r.cancelled) {
      setNotice("Clustering stopped.");
    } else {
      const total = r.place_clusters ?? "?";
      const made = r.new_clusters ?? 0;
      setNotice(`✓ Clustering done — ${total} place cluster(s) total, ${made} new this run.`);
    }
  }

  function onReprocessDone(j: Job) {
    load();
    const b = j.result?.backfill || {};
    const p = j.result?.prune || {};
    setNotice(`✓ Reprocessed — recomputed face metrics for ${b.updated ?? 0} photo(s); removed `
      + `${p.photos_removed ?? 0} person/group photo(s) from ${p.clusters_changed ?? 0} cluster(s). `
      + `Reviewed places and ignored places were left untouched.`);
  }

  async function rebuild() {
    if (!confirm("Rebuild from scratch? Deletes ALL place clusters (including manual matches/splits) so the next run regroups everything. Your ignore-list is kept.")) return;
    setNotice(null);
    try { const r = await post<{ deleted: number }>("/maps/reset"); setNotice(`Cleared ${r.deleted} cluster(s). Run “Cluster places” to rebuild.`); load(); }
    catch (e: any) { setNotice(`Rebuild failed: ${e.message || e}`); }
  }

  async function openCluster(id: number) {
    setLoadingDetail(true);
    setSuggestions(null); setQuery(""); setDraftMeta(null); setText(""); setStaged(null);
    setRotations({}); setLightbox(null); setPlaceType("place"); setAspects([]); setNote("");
    setVideoUploads({});
    try {
      const d = await get<ClusterDetail>(`/maps/clusters/${id}`);
      setDetail(d);
      setName(d.name || "");
      setSelected(d.images.filter((i) => i.recommended).map((i) => i.id));
    } catch (e: any) { setNotice(`Couldn't open cluster: ${e.message || e}`); }
    finally { setLoadingDetail(false); }
  }

  function toggle(id: number) {
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  }

  function rotate(id: number, dir: 1 | -1) {
    setRotations((r) => ({ ...r, [id]: (((r[id] || 0) + dir * 90) % 360 + 360) % 360 }));
  }

  // Lightbox keyboard nav: Esc to close, ←/→ to move between photos.
  useEffect(() => {
    if (lightbox === null || !detail) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightbox(null);
      else if (e.key === "ArrowRight") setLightbox((i) => (i === null ? i : Math.min(detail.images.length - 1, i + 1)));
      else if (e.key === "ArrowLeft") setLightbox((i) => (i === null ? i : Math.max(0, i - 1)));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightbox, detail]);

  async function makeDraft() {
    if (!detail) return;
    setDrafting(true);
    try {
      // Drafting may run several on-demand model calls (fresh image descriptions) — allow up to 2 min.
      const d = await post<DraftResp>(`/maps/clusters/${detail.id}/draft`,
        { rating, image_ids: selected, place_type: placeType, aspects, note }, 120000);
      setDraftMeta(d); setText(d.text);
    } catch (e: any) { setNotice(`Draft failed: ${e.message || e}`); }
    finally { setDrafting(false); }
  }

  async function findNearby() {
    if (!detail) return;
    setLoadingSugg(true);
    try { setSuggestions(await get<Suggestion[]>(`/maps/clusters/${detail.id}/place-suggestions`)); }
    catch (e: any) { setNotice(`Nearby lookup failed: ${e.message || e}`); }
    finally { setLoadingSugg(false); }
  }

  async function runSearch() {
    if (!detail || !query.trim()) return;
    setLoadingSugg(true);
    try { setSuggestions(await get<Suggestion[]>(`/maps/search?q=${encodeURIComponent(query)}&lat=${detail.lat}&lng=${detail.lng}`)); }
    catch (e: any) { setNotice(`Search failed: ${e.message || e}`); }
    finally { setLoadingSugg(false); }
  }

  async function matchPlace(place_id: string) {
    if (!detail) return;
    try {
      const r = await post<{ name: string; address: string; place_id: string; place_type?: string }>(`/maps/clusters/${detail.id}/match`, { place_id });
      setDetail({ ...detail, name: r.name, address: r.address, place_id: r.place_id, matched: true, manual: true });
      setName(r.name || ""); setSuggestions(null);
      if (r.place_type) changeType(r.place_type);   // default the guided-form type to the matched place
      load();
    } catch (e: any) { setNotice(`Match failed: ${e.message || e}`); }
  }

  async function ignoreCluster(id: number, suggestedLabel?: string) {
    const label = prompt("Label this personal place (it'll be skipped from now on):", suggestedLabel || "Home");
    if (!label) return;
    try {
      await post(`/maps/clusters/${id}/ignore`, { label });
      if (detail?.id === id) setDetail(null);
      setNotice(`Ignored as “${label}”. It won't cluster again.`);
      load(); loadIgnored();
    } catch (e: any) { setNotice(`Ignore failed: ${e.message || e}`); }
  }

  async function unignore(id: number) {
    try { await post(`/maps/ignored/${id}/unignore`); loadIgnored(); }
    catch (e: any) { setNotice(`Un-ignore failed: ${e.message || e}`); }
  }

  async function markReviewed(id: number, value: boolean) {
    try {
      await post(`/maps/clusters/${id}/reviewed`, { reviewed: value });
      if (detail?.id === id) setDetail({ ...detail, reviewed: value });
      load();
    } catch (e: any) { setNotice(`Couldn't update: ${e.message || e}`); }
  }

  async function sendToPhotos(mediaId: number) {
    if (!detail) return;
    setVideoUploads((v) => ({ ...v, [mediaId]: { status: "uploading" } }));
    try {
      const j = await runJob(`/maps/clusters/${detail.id}/upload-video`, { media_id: mediaId });
      setVideoUploads((v) => ({ ...v, [mediaId]: { status: "done", url: j.result?.product_url } }));
    } catch (e: any) {
      setVideoUploads((v) => ({ ...v, [mediaId]: { status: "error", error: String(e.message || e) } }));
    }
  }

  async function loadMoreImages() {
    if (!detail || loadingMore) return;
    setLoadingMore(true);
    try {
      const d = await get<ClusterDetail>(`/maps/clusters/${detail.id}?offset=${detail.images.length}&limit=60`);
      setDetail({ ...detail, shown: d.shown, images: [...detail.images, ...d.images] });
    } catch (e: any) { setNotice(`Couldn't load more: ${e.message || e}`); }
    finally { setLoadingMore(false); }
  }

  async function openSelectedInExplorer() {
    if (!detail || selected.length === 0) return;
    try {
      const r = await post<{ count: number; highlighted?: boolean }>(
        `/maps/clusters/${detail.id}/open-selected`, { media_ids: selected });
      setNotice(r.highlighted === false
        ? `Opened the folder for ${r.count} file(s) (couldn't highlight individually).`
        : `Opened Explorer with ${r.count} file(s) highlighted.`);
    } catch (e: any) { setNotice(`Couldn't open Explorer: ${e.message || e}`); }
  }

  async function splitOut() {
    if (!detail || selected.length === 0) return;
    if (selected.length >= detail.count) { setNotice("Can't move the whole cluster — leave at least one photo."); return; }
    if (!confirm(`Move ${selected.length} photo(s) into a new, separate cluster?`)) return;
    try {
      const r = await post<{ moved: number }>(`/maps/clusters/${detail.id}/split`, { image_ids: selected });
      setNotice(`Moved ${r.moved} photo(s) to a new cluster.`);
      await openCluster(detail.id); load();
    } catch (e: any) { setNotice(`Split failed: ${e.message || e}`); }
  }

  async function stage() {
    if (!detail) return;
    const rot: Record<number, number> = {};
    selected.forEach((id) => { if (rotations[id]) rot[id] = rotations[id]; });
    try {
      const r = await post<{ export_dir: string; exported_images: number; review_url?: string }>(
        `/maps/clusters/${detail.id}/review`,
        { place_id: detail.place_id, name, rating, text, image_ids: selected, rotations: rot });
      setStaged(r);
      setNotice(`✓ Review package staged for “${name || detail.name || "this place"}” — ${r.exported_images} image(s) exported.`);
      navigator.clipboard?.writeText(text).catch(() => {});   // best-effort: text ready to paste
    } catch (e: any) { setNotice(`Stage failed: ${e.message || e}`); }
  }

  async function copyText() {
    try { await navigator.clipboard.writeText(text); setNotice("📋 Review text copied to clipboard."); }
    catch { setNotice("Couldn't copy automatically — select the text and copy it manually."); }
  }

  async function openFolder() {
    if (!staged) return;
    try { await post("/maps/open-folder", { path: staged.export_dir }); }
    catch (e: any) { setNotice(`Couldn't open folder: ${e.message || e}`); }
  }

  // One click (a real user gesture, so the popup/clipboard aren't blocked): copy the text, open the
  // place on Google Maps, and open the export folder ready to drag the images in.
  function postOnMaps() {
    navigator.clipboard?.writeText(text).catch(() => {});
    if (staged?.review_url) window.open(staged.review_url, "_blank", "noopener");
    openFolder();
  }

  return (
    <div>
      <div className="row spread">
        <h2>Maps Review Studio</h2>
        <div className="row">
          <JobButton label="Test: 10 samples" startPath="/maps/cluster" jobName="maps" body={{ limit: 10 }} onDone={onClusterDone} className="secondary" />
          <JobButton label="Cluster places" startPath="/maps/cluster" jobName="maps" onDone={onClusterDone} />
          <JobButton label="Recheck people/groups" startPath="/maps/reprocess-person-focus" jobName="maps" onDone={onReprocessDone} className="secondary" />
          <button className="btn" onClick={stop} disabled={mjob?.status !== "running"}>Stop</button>
          <button className="btn danger" onClick={rebuild}>Rebuild</button>
        </div>
      </div>
      <div className="banner">Drafts a star rating + review text from your geo-tagged, non-portrait photos. <b>You post it to Google Maps manually</b> — no API can submit a review. Approve to stage a package in the Review Queue.<br />
        <span className="muted" style={{ fontSize: 12 }}>Clusters persist — re-running only folds in new photos (within {""}~75&nbsp;m). Match a place on demand, ignore personal spots, or split a mis-grouped cluster.
        {" "}“Recheck people/groups” re-scans existing clusters for photos where a person or group is the subject and removes them — it never deletes a cluster and skips anything you've already reviewed.</span>
      </div>
      {notice && <div className="banner" style={{ borderColor: "var(--accent)" }}>{notice}</div>}

      {mjob?.status === "running" && (
        <div className="banner" style={{ borderColor: "var(--accent)" }}>
          ⏳ <b>{Math.round((mjob.progress || 0) * 100)}%</b> — {mjob.message || "working…"}
          <div className="progress" style={{ marginTop: 6 }}><div style={{ width: `${(mjob.progress || 0) * 100}%` }} /></div>
        </div>
      )}
      {mjob?.status === "error" && (
        <div className="banner" style={{ borderColor: "var(--danger)", color: "var(--danger)" }}>
          ✖ Last clustering job failed: {(mjob.error || "").split("\n")[0]}
        </div>
      )}

      {/* ---------------- List view ---------------- */}
      {!detail && (
        <>
          {(() => { const reviewedCount = clusters.filter((c) => c.reviewed).length; return (
          <>
          {reviewedCount > 0 && (
            <label className="muted" style={{ fontSize: 12, display: "block", marginBottom: 10 }}>
              <input type="checkbox" checked={showReviewed} onChange={(e) => setShowReviewed(e.target.checked)} />
              {" "}Show already-reviewed places ({reviewedCount})
            </label>
          )}
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(240px,1fr))" }}>
            {clusters.filter((c) => showReviewed || !c.reviewed).map((c) => (
              <div className="card" key={c.id} style={{ marginBottom: 0, opacity: c.reviewed ? 0.6 : 1 }}>
                <div className="row spread" style={{ alignItems: "start" }}>
                  <b style={{ cursor: "pointer" }} onClick={() => openCluster(c.id)}>{c.name || "Unmatched place"}</b>
                  <span className={`pill ${c.reviewed ? "done" : c.matched ? "approved" : "pending"}`}>
                    {c.reviewed ? "✓ Reviewed" : c.matched ? "Matched" : "Unmatched"}
                  </span>
                </div>
                <div className="muted" style={{ fontSize: 12, cursor: "pointer" }} onClick={() => openCluster(c.id)}>
                  {c.address || `${c.lat?.toFixed(4)}, ${c.lng?.toFixed(4)}`} · {c.count} items
                </div>
                <div className="row" style={{ marginTop: 8, cursor: "pointer" }} onClick={() => openCluster(c.id)}>
                  {c.thumbs.slice(0, 4).map((t) => t.thumb && <img key={t.id} src={t.thumb} width={48} height={48} style={{ borderRadius: 6, objectFit: "cover" }} />)}
                </div>
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn secondary" style={{ fontSize: 12, padding: "4px 10px" }} onClick={() => openCluster(c.id)}>Open</button>
                  <button className="btn secondary" style={{ fontSize: 12, padding: "4px 10px" }} onClick={() => markReviewed(c.id, !c.reviewed)}>
                    {c.reviewed ? "Unmark" : "Mark reviewed"}
                  </button>
                  <button className="btn secondary" style={{ fontSize: 12, padding: "4px 10px" }} onClick={() => ignoreCluster(c.id, c.name || undefined)}>Ignore</button>
                </div>
              </div>
            ))}
            {clusters.filter((c) => showReviewed || !c.reviewed).length === 0 && (
              <p className="muted">{clusters.length === 0 ? "No place clusters yet. Run “Cluster places”." : "All places reviewed 🎉 — tick the box above to see them."}</p>
            )}
          </div>
          </>); })()}

          <div style={{ marginTop: 18 }}>
            <button className="btn secondary" onClick={() => setShowIgnored((v) => !v)}>
              {showIgnored ? "Hide" : "Show"} ignored places ({ignored.length})
            </button>
            {showIgnored && (
              <div className="card" style={{ marginTop: 10 }}>
                {ignored.length === 0 && <span className="muted">No ignored places yet.</span>}
                {ignored.map((ip) => (
                  <div className="row spread" key={ip.id} style={{ padding: "4px 0", borderBottom: "1px solid var(--line)" }}>
                    <span><b>{ip.label}</b> <span className="muted" style={{ fontSize: 12 }}>{ip.lat?.toFixed(4)}, {ip.lng?.toFixed(4)}</span></span>
                    <button className="btn secondary" style={{ fontSize: 12, padding: "4px 10px" }} onClick={() => unignore(ip.id)}>Un-ignore</button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {/* ---------------- Detail view ---------------- */}
      {detail && (
        <>
          <div className="row spread" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>
              {detail.name || "Unmatched place"} <span className="muted" style={{ fontSize: 13 }}>· {detail.count} items</span>
              {detail.reviewed && <span className="pill done" style={{ marginLeft: 8 }}>✓ Reviewed</span>}
            </h3>
            <div className="row">
              <button className={`btn ${detail.reviewed ? "secondary" : "good"}`} onClick={() => markReviewed(detail.id, !detail.reviewed)}>
                {detail.reviewed ? "Unmark reviewed" : "✓ Mark reviewed"}
              </button>
              <button className="btn secondary" onClick={() => ignoreCluster(detail.id, detail.name || undefined)}>Ignore place</button>
              <button className="btn secondary" onClick={() => setDetail(null)}>← back</button>
            </div>
          </div>

          <div className="maps-detail">
            {/* Left: review builder */}
            <div>
              <div className="card">
                <b>Match place</b>
                {detail.matched && (
                  <div className="muted" style={{ fontSize: 12, margin: "6px 0" }}>✓ {detail.name} — {detail.address}</div>
                )}
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn secondary" onClick={findNearby} disabled={loadingSugg}>Find nearby places</button>
                  {loadingSugg && <span className="spinner" />}
                </div>
                <div className="row" style={{ marginTop: 8 }}>
                  <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="…or search by name"
                         style={{ flex: 1 }} onKeyDown={(e) => e.key === "Enter" && runSearch()} />
                  <button className="btn secondary" onClick={runSearch}>Search</button>
                </div>
                {suggestions && (
                  <div style={{ marginTop: 8 }}>
                    {suggestions.length === 0 && <span className="muted" style={{ fontSize: 12 }}>No matches (or no Places API key).</span>}
                    {suggestions.map((s) => (
                      <div className="suggest-item" key={s.place_id} onClick={() => matchPlace(s.place_id)}>
                        <b>{s.name}</b>{s.rating ? <span className="muted"> · ★{s.rating} ({s.rating_count})</span> : null}
                        <div className="muted" style={{ fontSize: 11 }}>{s.address}</div>
                      </div>
                    ))}
                  </div>
                )}
                <div style={{ marginTop: 10 }}>
                  <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>Or type a name manually:</div>
                  <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Place name" style={{ width: "100%" }} />
                </div>
              </div>

              <div className="card">
                <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
                  <label>Type:&nbsp;
                    <select value={placeType} onChange={(e) => changeType(e.target.value)}>
                      {reviewOpts?.types.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                  </label>
                  <label>Rating:&nbsp;
                    <select value={rating} onChange={(e) => setRating(Number(e.target.value))}>
                      {[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{"★".repeat(n)}</option>)}
                    </select>
                  </label>
                </div>
                <div style={{ marginTop: 10 }}>
                  <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>What stood out? (tap any that apply)</div>
                  <div className="row" style={{ gap: 6 }}>
                    {(reviewOpts?.aspects_by_type[placeType] || []).map((a) => (
                      <button key={a.key} type="button" className={`chip ${aspects.includes(a.key) ? "on" : ""}`}
                              onClick={() => toggleAspect(a.key)}>{a.label}</button>
                    ))}
                  </div>
                </div>
                <input value={note} onChange={(e) => setNote(e.target.value)}
                       placeholder="Anything specific to mention? (optional)" style={{ width: "100%", marginTop: 10 }} />
                <div className="row" style={{ marginTop: 10 }}>
                  <button className="btn" onClick={makeDraft} disabled={drafting}>
                    {drafting ? "Drafting…" : text ? "Regenerate" : "Draft review with AI"}
                  </button>
                  {drafting && <span className="spinner" />}
                </div>
                <textarea style={{ marginTop: 12, minHeight: 180 }} value={text}
                          onChange={(e) => setText(e.target.value)}
                          placeholder="Your review will appear here — draft it with AI or write your own." />
                {draftMeta?.no_captions && (
                  <div className="muted" style={{ fontSize: 12, color: "var(--warn)" }}>
                    Couldn’t auto-draft (model unavailable) — write your own, or run the analyze pass for captions.
                  </div>
                )}
                {draftMeta && !draftMeta.no_captions && (
                  <div className="muted" style={{ fontSize: 11 }}>
                    Drafted from {draftMeta.captions_used} {draftMeta.grounded_by === "images" ? "photo(s)" : "caption(s)"}.
                  </div>
                )}
                <div className="row" style={{ marginTop: 12 }}>
                  <button className="btn good" onClick={stage} disabled={!text.trim() || selected.length === 0}>
                    Stage review package ({selected.length} items)
                  </button>
                </div>
                {(!text.trim() || selected.length === 0) && (
                  <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                    {selected.length === 0 ? "Select at least one photo or video" : "Write or draft a review"} to stage the package.
                  </div>
                )}
                {staged && (
                  <div className="banner" style={{ marginTop: 10, borderColor: "var(--good)" }}>
                    ✓ <b>Staged.</b> {staged.exported_images} image(s) + <code>review.txt</code> exported. Review text copied.
                    <div className="muted" style={{ fontSize: 11, margin: "4px 0" }}>{staged.export_dir}</div>
                    <div className="row" style={{ gap: 8, marginBottom: 6 }}>
                      <button className="btn good" onClick={postOnMaps}>📍 Post on Maps</button>
                      <span className="muted" style={{ fontSize: 11 }}>opens Maps + the photo folder, and copies your text</span>
                    </div>
                    <div className="row" style={{ gap: 8 }}>
                      <button className="btn secondary" onClick={copyText}>📋 Copy text</button>
                      {staged.review_url && <a className="btn secondary" href={staged.review_url} target="_blank" rel="noreferrer">📍 Open Maps ↗</a>}
                      <button className="btn secondary" onClick={openFolder}>📂 Open folder</button>
                      <Link className="btn secondary" to="/review">Review Queue</Link>
                    </div>
                    <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
                      Nothing is posted automatically — on the Maps page, paste the text and drag in the exported photos.
                      {detail.images.some((i) => i.media_type === "video" && selected.includes(i.id)) && (
                        <> Videos can't be dragged in — open each one (🔍/▶ in the gallery) and use "Send to Google Photos"
                        first, then pick it from there in Maps' photo picker.</>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Right: photo gallery */}
            <div>
              <div className="gallery-head">
                <div><b>Photos &amp; videos</b> <span className="muted" style={{ fontSize: 12 }}>· tap to select · 🔍/▶ to enlarge</span></div>
                <div className="row" style={{ gap: 8 }}>
                  <span className="muted" style={{ fontSize: 12 }}>{selected.length} selected</span>
                  {selected.length > 0 && (
                    <button className="btn secondary" style={{ fontSize: 12, padding: "4px 10px" }} onClick={openSelectedInExplorer}>
                      📂 Open in Explorer
                    </button>
                  )}
                  {selected.length > 0 && (
                    <button className="btn secondary" style={{ fontSize: 12, padding: "4px 10px" }} onClick={splitOut}>
                      ⤴ Move to new cluster
                    </button>
                  )}
                </div>
              </div>
              <div className="muted" style={{ fontSize: 12, margin: "2px 0 8px" }}>
                Recommended (scenic, non-portrait) are pre-selected.
              </div>
              {detail.needs_analysis && (
                <div className="banner" style={{ marginBottom: 8 }}>
                  These photos aren’t analyzed yet — run the analyze pass (faces/blur) for smarter, portrait-free picks.
                </div>
              )}
              {loadingDetail ? <p className="muted">Loading photos… <span className="spinner" /></p> : (
                <div className="grid">
                  {detail.images.map((im, idx) => {
                    const sel = selected.includes(im.id);
                    const deg = rotations[im.id] || 0;
                    const isVideo = im.media_type === "video";
                    return (
                      <div className={`tile ${sel ? "keeper" : ""}`} key={im.id} onClick={() => toggle(im.id)} style={{ cursor: "pointer" }}>
                        {im.thumb && <img src={im.thumb} loading="lazy" />}
                        {isVideo && <span className="play">▶</span>}
                        <div className="badges">
                          {isVideo && <span className="badge">🎬 Video</span>}
                          {im.person_in_focus && <span className="badge warn">People</span>}
                          {im.is_document && <span className="badge warn">Doc</span>}
                          {im.faces > 0 && <span className="badge">👥{im.faces}</span>}
                          {deg > 0 && <span className="badge">⟳{deg}°</span>}
                        </div>
                        {sel && <span className="check">✓</span>}
                        <button className="enlarge" title={isVideo ? "Play" : "Enlarge & rotate"}
                                onClick={(e) => { e.stopPropagation(); setLightbox(idx); }}>{isVideo ? "▶" : "🔍"}</button>
                      </div>
                    );
                  })}
                </div>
              )}
              {detail.images.length < detail.count && (
                <div className="row" style={{ marginTop: 8, alignItems: "center", gap: 8 }}>
                  <button className="btn secondary" onClick={loadMoreImages} disabled={loadingMore}>
                    {loadingMore ? <>Loading… <span className="spinner" /></> : `Load more (${detail.count - detail.images.length} remaining)`}
                  </button>
                  <span className="muted" style={{ fontSize: 12 }}>Showing {detail.images.length} of {detail.count} items.</span>
                </div>
              )}
            </div>
          </div>

          {/* Lightbox: enlarge + rotate photos / play videos (rotation is baked into the exported copy) */}
          {lightbox !== null && detail.images[lightbox] && (() => {
            const im = detail.images[lightbox];
            const deg = rotations[im.id] || 0;
            const sel = selected.includes(im.id);
            const isVideo = im.media_type === "video";
            const upload = videoUploads[im.id];
            return (
              <div className="lightbox" onClick={() => setLightbox(null)}>
                <div className="lightbox-body" onClick={(e) => e.stopPropagation()}>
                  <div className="row spread" style={{ marginBottom: 8 }}>
                    <span className="muted" style={{ fontSize: 12 }}>
                      {lightbox + 1} / {detail.images.length}{im.caption ? ` · ${im.caption}` : ""}
                    </span>
                    <div className="row" style={{ gap: 8 }}>
                      {!isVideo && <button className="btn secondary" onClick={() => rotate(im.id, -1)}>⟲ Rotate left</button>}
                      {!isVideo && <button className="btn secondary" onClick={() => rotate(im.id, 1)}>⟳ Rotate right</button>}
                      {isVideo && reviewOpts && (
                        <button className="btn secondary" disabled={!reviewOpts.uploads_enabled || upload?.status === "uploading"}
                                title={reviewOpts.uploads_enabled ? "Upload to the curated Google Photos album so it's pickable in Maps" : "Configure PHOTOS_OAUTH_CLIENT_ID/SECRET in backend/.env to enable"}
                                onClick={() => sendToPhotos(im.id)}>
                          {upload?.status === "uploading" ? <>Sending… <span className="spinner" /></> : "📤 Send to Google Photos"}
                        </button>
                      )}
                      <button className={`btn ${sel ? "good" : "secondary"}`} onClick={() => toggle(im.id)}>{sel ? "✓ Selected" : "Select"}</button>
                      <button className="btn secondary" onClick={() => setLightbox(null)}>Close ✕</button>
                    </div>
                  </div>
                  {isVideo && upload?.status === "done" && (
                    <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                      ✓ Sent to "{detail.name || "your"}" album — <a href={upload.url} target="_blank" rel="noreferrer">open in Google Photos ↗</a>, then pick it from there in Maps.
                    </div>
                  )}
                  {isVideo && upload?.status === "error" && (
                    <div className="muted" style={{ fontSize: 12, color: "var(--danger)", marginBottom: 6 }}>Upload failed: {upload.error}</div>
                  )}
                  <div className="lightbox-img">
                    {isVideo
                      ? <video src={`/api/media/${im.id}/file`} controls autoPlay style={{ maxWidth: "86vw", maxHeight: "76vh" }} />
                      : <img src={`/api/media/${im.id}/file`} style={{ transform: `rotate(${deg}deg)` }} />}
                  </div>
                  <div className="row spread" style={{ marginTop: 8 }}>
                    <button className="btn secondary" disabled={lightbox === 0} onClick={() => setLightbox(lightbox - 1)}>‹ Prev</button>
                    <span className="muted" style={{ fontSize: 12 }}>{isVideo ? "🎬 Video" : deg ? `Rotated ${deg}° — applied to the posted copy` : "Not rotated"}</span>
                    <button className="btn secondary" disabled={lightbox === detail.images.length - 1} onClick={() => setLightbox(lightbox + 1)}>Next ›</button>
                  </div>
                </div>
              </div>
            );
          })()}
        </>
      )}
    </div>
  );
}
