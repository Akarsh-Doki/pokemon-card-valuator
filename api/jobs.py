from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class JobState:
    job_id: str
    queue: asyncio.Queue
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


_JOBS: Dict[str, JobState] = {}


def create_job() -> JobState:
    job_id = str(uuid.uuid4())
    st = JobState(job_id=job_id, queue=asyncio.Queue())
    _JOBS[job_id] = st
    return st


def get_job(job_id: str) -> Optional[JobState]:
    return _JOBS.get(job_id)


async def push(job_id: str, stage: str, detail: str = "") -> None:
    st = _JOBS.get(job_id)
    if not st:
        return
    await st.queue.put({"type": "progress", "stage": stage, "detail": detail})


async def push_error(job_id: str, msg: str) -> None:
    st = _JOBS.get(job_id)
    if not st:
        return
    st.error = msg
    await st.queue.put({"type": "error", "message": msg})


async def push_result(job_id: str, payload: Dict[str, Any]) -> None:
    st = _JOBS.get(job_id)
    if not st:
        return
    st.result = payload
    await st.queue.put({"type": "result", "payload": payload})
