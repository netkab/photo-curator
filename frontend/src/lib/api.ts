// Thin REST client + job-polling helper for the FastAPI backend.

export async function api<T = any>(path: string, opts: RequestInit = {}, timeoutMs = 20000): Promise<T> {
  const controller = new AbortController();
  // 20s default: while a video/analyze job runs, the server is briefly GIL-starved and reads can stall
  // a few seconds. A short timeout made the UI show "offline"/"no results" mid-job even though data
  // exists. Pass a larger timeout for endpoints that run the local model on demand (e.g. AI drafting).
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      ...opts,
    });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return res.json();
  } finally {
    clearTimeout(timer);
  }
}

export const get = <T = any>(path: string, timeoutMs?: number) => api<T>(path, {}, timeoutMs);
export const post = <T = any>(path: string, body?: unknown, timeoutMs?: number) =>
  api<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }, timeoutMs);

export interface Job {
  id: string;
  name: string;
  status: "running" | "done" | "error";
  progress: number;
  message: string;
  result: any;
  error: string | null;
}

// Start a job (endpoint returns a Job), then poll until done/error.
export async function runJob(
  startPath: string,
  body: unknown,
  onProgress?: (j: Job) => void
): Promise<Job> {
  const started = await post<Job>(startPath, body);
  return new Promise((resolve, reject) => {
    const poll = setInterval(async () => {
      try {
        const j = await get<Job>(`/jobs/${started.id}`);
        onProgress?.(j);
        if (j.status === "done") { clearInterval(poll); resolve(j); }
        else if (j.status === "error") { clearInterval(poll); reject(new Error(j.error || "job failed")); }
      } catch (e) { clearInterval(poll); reject(e); }
    }, 1000);
  });
}
