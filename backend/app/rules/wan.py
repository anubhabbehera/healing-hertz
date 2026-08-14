from __future__ import annotations

from statistics import median

from app.collectors.snapshot import Snapshot

from .base import Binding, RunHistory


class WanLatencyLoss:
    """Probe loss and probe latency, reported independently under one id.

    Not declarative: the two conditions are unrelated, and the latency arm is
    suppressed when every probe failed, since latency over zero successful
    connections is not a measurement.
    """

    id = "wan.latency_loss"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        wan = snapshot.wan
        if wan is None:
            return []
        bindings = []
        if wan.loss_pct >= 2:
            bindings.append(Binding(key="loss", vars={
                "loss_pct": wan.loss_pct,
                "samples": wan.samples,
                "per_target": wan.per_target,
            }))
        if wan.latency_ms >= 80 and wan.loss_pct < 100:
            bindings.append(Binding(key="latency", vars={
                "latency_ms": wan.latency_ms,
                "jitter_ms": wan.jitter_ms,
                "per_target": wan.per_target,
            }))
        return bindings


class WanLatencyWorsening:
    """Latency against a median of the last three runs."""

    id = "wan.latency_worsening"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        wan = snapshot.wan
        if wan is None or not history.runs:
            return []
        prior = [r.site_metrics["wan.latency_ms"] for r in history.runs[:3]
                 if "wan.latency_ms" in r.site_metrics]
        if not prior:
            return []
        baseline = median(prior)
        if baseline <= 0 or wan.latency_ms < max(baseline * 1.5, baseline + 20):
            return []
        return [Binding(vars={
            "latency_ms": wan.latency_ms,
            "baseline_ms": baseline,
            "baseline_ms_rounded": round(baseline, 1),
        })]
