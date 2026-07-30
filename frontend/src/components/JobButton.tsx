import { useCallback, useEffect, useRef, useState } from "react";
import { get, Job, post } from "../lib/api";

interface Props {
  label: string;
  startPath: string;
  /** The job name (e.g. "ingest", "analyze") — used to find already-running jobs after page refresh. */
  jobName?: string;
  body?: unknown;
  className?: string;
  onDone?: (job: Job) => void;
}

export default function JobButton({ label, startPath, jobName, body, className, onDone }: Props) {
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone; // always latest callback without triggering effects

  const running = job?.status === "running";

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback((jobId: string) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const j = await get<Job>(`/jobs/${jobId}`);
        setJob(j);
        if (j.status === "done") {
          stopPolling();
          onDoneRef.current?.(j);
        } else if (j.status === "error") {
          stopPolling();
          setErr(j.error || "job failed");
        }
      } catch {
        stopPolling();
      }
    }, 1500);
  }, [stopPolling]);

  // On mount, check if a job with this name is already running (e.g. after page refresh)
  useEffect(() => {
    if (!jobName) return;
    let cancelled = false;

    get<Job[]>("/jobs").then(jobs => {
      if (cancelled) return;
      const active = jobs.find(j => j.name === jobName && j.status === "running");
      if (active) {
        setJob(active);
        startPolling(active.id);
      }
    }).catch(() => {});

    return () => { cancelled = true; stopPolling(); };
  }, [jobName, startPolling, stopPolling]);

  // Cleanup on unmount
  useEffect(() => stopPolling, [stopPolling]);

  async function start() {
    setErr(null);
    try {
      const started = await post<Job>(startPath, body ?? {});
      setJob(started);
      startPolling(started.id);
    } catch (e: any) {
      // 409 = already running — pick up the existing job
      if (e.message?.includes("409") && jobName) {
        try {
          const jobs = await get<Job[]>("/jobs");
          const active = jobs.find(j => j.name === jobName && j.status === "running");
          if (active) {
            setJob(active);
            startPolling(active.id);
            return;
          }
        } catch { /* fall through */ }
      }
      setErr(String(e.message || e));
      setJob(null);
    }
  }

  return (
    <div>
      <button className={`btn ${className || ""}`} onClick={start} disabled={running}>
        {running ? `${label}... ${Math.round((job?.progress || 0) * 100)}%` : label}
      </button>
      {running && (
        <div className="progress" style={{ width: 220 }}>
          <div style={{ width: `${(job?.progress || 0) * 100}%` }} />
        </div>
      )}
      {running && job?.message && <div className="muted" style={{ fontSize: 11 }}>{job.message}</div>}
      {err && <div style={{ color: "var(--danger)", fontSize: 12 }}>{err}</div>}
    </div>
  );
}
