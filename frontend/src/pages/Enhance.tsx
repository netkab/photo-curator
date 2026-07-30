import { useEffect, useState } from "react";
import { get, post } from "../lib/api";
import JobButton from "../components/JobButton";

interface Result { derived_id: number; before: string | null; after: string; source_name: string | null; meta: any; }

export default function Enhance() {
  const [results, setResults] = useState<Result[]>([]);
  const load = () => get<Result[]>("/enhance/results").then(setResults).catch(() => {});
  useEffect(() => { load(); }, []);

  async function approve(id: number) { await post(`/enhance/${id}/approve`); load(); }

  return (
    <div>
      <div className="row spread">
        <h2>Enhance blurry photos</h2>
        <JobButton label="Enhance all blurry" startPath="/enhance/run" body={{ blurry: true }} onDone={load} />
      </div>
      <div className="banner">Enhanced copies are written as <b>new</b> files — originals are never modified. Approve to queue an upload (to a dedicated album) for final review.</div>

      {results.map((r) => (
        <div className="card" key={r.derived_id}>
          <div className="row spread">
            <span>{r.source_name}</span>
            <button className="btn good" onClick={() => approve(r.derived_id)}>Approve enhanced version</button>
          </div>
          <div className="before-after" style={{ marginTop: 10 }}>
            <div>{r.before && <img src={r.before} alt="before" />}<div className="muted">before</div></div>
            <div><img src={r.after} alt="after" /><div className="muted">after (upscaled)</div></div>
          </div>
        </div>
      ))}
      {results.length === 0 && <p className="muted">No enhancement results yet. Run “Enhance all blurry”.</p>}
    </div>
  );
}
