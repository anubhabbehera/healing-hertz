"""Active WAN health probe.

Measures TCP-connect latency, jitter, and connection loss from the machine
running this app to well-known anycast anchors. No credentials required; a
rough but honest proxy for WAN latency/packet loss on the local path.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from statistics import mean, pstdev

TARGETS = [("1.1.1.1", 443), ("8.8.8.8", 443), ("9.9.9.9", 443)]
ATTEMPTS_PER_TARGET = 5
CONNECT_TIMEOUT = 2.0


@dataclass
class WanProbeResult:
    latency_ms: float
    jitter_ms: float
    loss_pct: float
    samples: int
    per_target: dict[str, dict] = field(default_factory=dict)


async def _connect_ms(host: str, port: int) -> float | None:
    start = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=CONNECT_TIMEOUT
        )
    except (TimeoutError, OSError):
        return None
    elapsed = (time.perf_counter() - start) * 1000
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return elapsed


def aggregate(samples: dict[str, list[float | None]]) -> WanProbeResult | None:
    all_ok: list[float] = []
    total = 0
    failures = 0
    per_target: dict[str, dict] = {}
    for host, values in samples.items():
        ok = [v for v in values if v is not None]
        total += len(values)
        failures += len(values) - len(ok)
        all_ok.extend(ok)
        per_target[host] = {
            "latency_ms": round(mean(ok), 1) if ok else None,
            "failed": len(values) - len(ok),
        }
    if total == 0:
        return None
    if not all_ok:
        return WanProbeResult(latency_ms=0.0, jitter_ms=0.0, loss_pct=100.0,
                              samples=total, per_target=per_target)
    return WanProbeResult(
        latency_ms=round(mean(all_ok), 1),
        jitter_ms=round(pstdev(all_ok), 1) if len(all_ok) > 1 else 0.0,
        loss_pct=round(failures / total * 100, 1),
        samples=total,
        per_target=per_target,
    )


async def probe() -> WanProbeResult | None:
    samples: dict[str, list[float | None]] = {}
    for host, port in TARGETS:
        results: list[float | None] = []
        for _ in range(ATTEMPTS_PER_TARGET):
            results.append(await _connect_ms(host, port))
        samples[host] = results
    return aggregate(samples)
