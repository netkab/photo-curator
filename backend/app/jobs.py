"""Tiny in-process background job runner.

FastAPI's BackgroundTasks can't report progress, so we keep a thread-backed registry the UI can poll
via ``GET /api/jobs/{id}``. One job at a time per name avoids hammering the GPU concurrently.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

# Keep Windows awake while a job runs. SetThreadExecutionState is per-thread and the OS keeps the
# system awake while *any* thread holds the requirement, so setting it inside each worker thread (and
# clearing on exit) ref-counts itself across concurrent jobs. ES_SYSTEM_REQUIRED only — the display
# may still sleep. No-op on non-Windows.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _keep_awake(on: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        flags = _ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))
    except Exception:
        pass


@dataclass
class Job:
    id: str
    name: str
    status: str = "running"          # running | done | error | cancelled
    progress: float = 0.0            # 0..1
    message: str = ""
    result: Any = None
    error: str | None = None
    cancelled: bool = False          # set by a cancel request; the job target stops cooperatively
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "progress": round(self.progress, 3),
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "cancelled": self.cancelled,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def is_running(self, name: str) -> bool:
        with self._lock:
            return any(j.name == name and j.status == "running" for j in self._jobs.values())

    def submit(self, name: str, target: Callable[["JobHandle"], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], name=name)
        with self._lock:
            self._jobs[job.id] = job
        handle = JobHandle(job)

        def _run() -> None:
            _keep_awake(True)   # hold off system sleep for this job (per-thread; released in finally)
            try:
                job.result = target(handle)
                job.status = "cancelled" if job.cancelled else "done"
                job.progress = 1.0
            except Exception as exc:  # noqa: BLE001 — surface to UI
                job.status = "error"
                job.error = f"{exc}\n{traceback.format_exc()}"
            finally:
                job.finished_at = datetime.utcnow()
                _keep_awake(False)

        threading.Thread(target=_run, name=f"job-{name}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)

    def cancel(self, job_id: str) -> bool:
        """Flag a running job to stop. Cooperative — the target stops at its next checkpoint."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job.status == "running":
                job.cancelled = True
                return True
        return False

    def cancel_name(self, name: str) -> int:
        """Flag every running job with this name to stop. Returns how many were flagged."""
        with self._lock:
            running = [j for j in self._jobs.values() if j.name == name and j.status == "running"]
            for j in running:
                j.cancelled = True
            return len(running)


class JobHandle:
    """Passed to job targets so they can report progress."""

    def __init__(self, job: Job) -> None:
        self._job = job

    @property
    def cancelled(self) -> bool:
        """Long-running targets should poll this between work items and stop when True."""
        return self._job.cancelled

    def update(self, progress: float | None = None, message: str | None = None) -> None:
        if progress is not None:
            self._job.progress = max(0.0, min(1.0, progress))
        if message is not None:
            self._job.message = message


manager = JobManager()
