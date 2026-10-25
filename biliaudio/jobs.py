"""后台任务注册表：进度、日志、取消。"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections import OrderedDict

MAX_LOG_LINES = 300


class Job:
    def __init__(self, kind: str, title: str) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.title = title
        self.state = "running"          # running | done | error | cancelled
        self.progress = 0.0
        self.message = ""
        self.result: dict | None = None
        self.error = ""
        self.logs: list[dict] = []
        self.cancel_event = threading.Event()
        self.started = time.time()
        self.finished = 0.0

    def log(self, text: str, level: str = "info") -> None:
        self.logs.append({"t": round(time.time() - self.started, 2), "text": text, "level": level})
        if len(self.logs) > MAX_LOG_LINES:
            del self.logs[: len(self.logs) - MAX_LOG_LINES]

    def check_cancel(self) -> bool:
        return self.cancel_event.is_set()

    def to_dict(self, with_logs: bool = True) -> dict:
        data = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "progress": round(self.progress, 4),
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "started": self.started,
            "finished": self.finished,
        }
        if with_logs:
            data["logs"] = self.logs[-60:]
        return data


_JOBS: "OrderedDict[str, Job]" = OrderedDict()
_LOCK = threading.Lock()


def start(kind: str, title: str, target, *args) -> Job:
    """在后台线程里运行 target(job, *args)。"""
    job = Job(kind, title)
    with _LOCK:
        _JOBS[job.id] = job
        while len(_JOBS) > 40:
            _JOBS.popitem(last=False)

    def runner() -> None:
        try:
            job.result = target(job, *args) or {}
            if job.check_cancel():
                job.state = "cancelled"
                job.message = "已取消"
            else:
                job.state = "done"
                job.progress = 1.0
        except Exception as exc:  # noqa: BLE001
            if job.check_cancel() or type(exc).__name__ == "CancelledError":
                job.state = "cancelled"
                job.message = "已取消"
            else:
                job.state = "error"
                job.error = str(exc) or type(exc).__name__
                job.log(job.error, "error")
                job.log(traceback.format_exc(limit=3), "debug")
        finally:
            job.finished = time.time()

    threading.Thread(target=runner, name=f"job-{kind}-{job.id}", daemon=True).start()
    return job


def get(job_id: str) -> Job | None:
    with _LOCK:
        return _JOBS.get(job_id)


def list_jobs() -> list[Job]:
    with _LOCK:
        return list(_JOBS.values())


def active(kind_prefix: str = "") -> Job | None:
    for job in list_jobs():
        if job.state == "running" and job.kind.startswith(kind_prefix):
            return job
    return None


def cancel(job_id: str) -> bool:
    job = get(job_id)
    if not job or job.state != "running":
        return False
    job.cancel_event.set()
    job.message = "正在取消…"
    return True
