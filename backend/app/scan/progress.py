from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class ProgressEvent:
    phase: str  # collect | analyze | advise | persist | done | error
    detail: str = ""
    pct: int | None = None

    def as_dict(self) -> dict:
        return {"phase": self.phase, "detail": self.detail, "pct": self.pct}


@dataclass
class ScanProgress:
    run_id: str
    events: list[ProgressEvent] = field(default_factory=list)
    _cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    finished: bool = False

    async def emit(self, phase: str, detail: str = "", pct: int | None = None) -> None:
        async with self._cond:
            self.events.append(ProgressEvent(phase, detail, pct))
            if phase in ("done", "error"):
                self.finished = True
            self._cond.notify_all()

    async def stream(self) -> AsyncIterator[ProgressEvent]:
        """Yield all events from the start, then follow live until finished."""
        idx = 0
        while True:
            async with self._cond:
                while idx >= len(self.events) and not self.finished:
                    await self._cond.wait()
                batch = self.events[idx:]
                idx = len(self.events)
                done = self.finished and idx >= len(self.events)
            for event in batch:
                yield event
            if done:
                return


_registry: dict[str, ScanProgress] = {}


def create_progress(run_id: str) -> ScanProgress:
    progress = ScanProgress(run_id=run_id)
    _registry[run_id] = progress
    # Keep only the most recent handful; completed runs live in the DB.
    if len(_registry) > 10:
        for key in list(_registry)[:-10]:
            _registry.pop(key, None)
    return progress


def get_progress(run_id: str) -> ScanProgress | None:
    return _registry.get(run_id)
