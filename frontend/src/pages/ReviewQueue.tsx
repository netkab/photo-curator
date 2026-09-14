import {useEffect, useState} from "react";
import {get, post} from "../lib/api";
export default function ReviewQueue() {
  const [actions, setActions] = useState<any[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => get<any[]>("/review").then(setActions).catch(e => setMessage(String(e)));
  useEffect(() => {load();}, []);
  async function act(id: number, command: string) {
    setBusy(true);
    try {await post(`/review/${id}/${command}`); await load();}
    catch (e) {setMessage(String(e));} finally {setBusy(false);}
  }
  return <div><h2>Review queue</h2>
    <p>Check each proposed keeper and its duplicates. Approval records your selection; it does not trash photos.
       Use the extension’s Duplicates tab to start queued dry runs and review operation history.</p>
    <p role="status">{message}</p>
    {!actions.length && <p>No reviews yet. Choose a group in Duplicates.</p>}
    {actions.map(a => <section className="card" key={a.id}>
      <h3>Review {a.id} · {a.status}</h3><p>{a.payload?.reason}</p>
      <ul>{a.payload?.items?.map((i: any) => <li key={i.media_id}>{i.name}</li>)}</ul>
      {a.status === "pending" && <button className="btn" disabled={busy} onClick={() => act(a.id, "approve")}>Approve selection</button>}
      {a.status === "approved" && <button className="btn" disabled={busy} onClick={async () => {
        setBusy(true); try {const r = await post(`/review/${a.id}/apply`, {dry_run: true});
          setMessage(`Dry run ${r.result.operation_id} is ready. Open the extension to start it.`);
        } catch(e) {setMessage(String(e));} finally {setBusy(false);}
      }}>Queue dry run</button>}
      {["pending", "approved"].includes(a.status) && <button className="btn secondary" disabled={busy}
        onClick={() => act(a.id, "dismiss")}>Dismiss</button>}
    </section>)}
  </div>;
}
