from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.db import repo
from app.scan.orchestrator import run_scan, scan_lock
from app.scan.progress import create_progress, get_progress

from .deps import get_session, make_unifi_client

router = APIRouter(prefix="/api/scans", tags=["scans"])

# Keep strong references so background scan tasks aren't garbage-collected.
_tasks: set[asyncio.Task] = set()


@router.post("", status_code=202)
async def start_scan(session: AsyncSession = Depends(get_session)) -> dict:
    if scan_lock.locked():
        raise HTTPException(status_code=409, detail="A scan is already running")

    settings = get_settings()
    if not settings.demo_mode and not (settings.unifi_host and settings.unifi_api_key):
        raise HTTPException(
            status_code=400,
            detail="UNIFI_HOST and UNIFI_API_KEY are not configured (or set DEMO_MODE=true)",
        )

    run_id = uuid.uuid4().hex[:12]
    await repo.create_run(session, run_id)
    progress = create_progress(run_id)
    client = make_unifi_client(settings)

    async def _run() -> None:
        async with scan_lock:
            await run_scan(run_id, client, settings, progress)

    task = asyncio.create_task(_run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"run_id": run_id}


@router.get("/{run_id}")
async def scan_status(run_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    run = await repo.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    progress = get_progress(run_id)
    latest = progress.events[-1].as_dict() if progress and progress.events else None
    return {"run_id": run_id, "status": run.status, "progress": latest, "error": run.error}


@router.get("/{run_id}/events")
async def scan_events(run_id: str, session: AsyncSession = Depends(get_session)):
    progress = get_progress(run_id)
    if progress is None:
        run = await repo.get_run(session, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        async def replay():
            phase = "done" if run.status == "completed" else "error"
            yield {"event": "progress",
                   "data": json.dumps({"phase": phase, "detail": run.error or "", "pct": 100})}

        return EventSourceResponse(replay())

    async def stream():
        async for event in progress.stream():
            yield {"event": "progress", "data": json.dumps(event.as_dict())}

    return EventSourceResponse(stream())
