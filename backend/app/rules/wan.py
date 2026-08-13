from __future__ import annotations

from statistics import median

from app.collectors.snapshot import Snapshot

from .base import Category, Finding, RunHistory, Severity


class WanLatencyLoss:
    id = "wan.latency_loss"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        wan = snapshot.wan
        if wan is None:
            return []
        findings = []
        if wan.loss_pct >= 2:
            findings.append(Finding(
                rule_id=self.id,
                severity=Severity.HIGH if wan.loss_pct >= 10 else Severity.MEDIUM,
                category=Category.CAPACITY,
                title=f"WAN probe failures: {wan.loss_pct:.0f}% of connections",
                summary=(
                    f"{wan.loss_pct:.1f}% of {wan.samples} TCP probes to public anchors "
                    "failed — indicates packet loss or an unstable WAN path."
                ),
                evidence={"lossPct": wan.loss_pct, "samples": wan.samples,
                          "perTarget": wan.per_target},
                recommendation=(
                    "Check the WAN link (modem/ONT stats, cabling) and re-run at a quiet "
                    "hour; sustained loss is worth an ISP ticket with these numbers."
                ),
            ))
        if wan.latency_ms >= 80 and wan.loss_pct < 100:
            findings.append(Finding(
                rule_id=self.id,
                severity=Severity.HIGH if wan.latency_ms >= 150 else Severity.MEDIUM,
                category=Category.CAPACITY,
                title=f"High WAN latency ({wan.latency_ms:.0f} ms)",
                summary=(
                    f"Average TCP-connect latency to public anchors is {wan.latency_ms:.0f} ms "
                    f"(jitter {wan.jitter_ms:.0f} ms) — typical wired links measure 5–30 ms."
                ),
                evidence={"latencyMs": wan.latency_ms, "jitterMs": wan.jitter_ms,
                          "perTarget": wan.per_target},
                recommendation=(
                    "If sustained, check for bufferbloat (enable Smart Queues sized to your "
                    "plan), a saturated uplink, or ISP path issues."
                ),
            ))
        return findings


class WanLatencyWorsening:
    id = "wan.latency_worsening"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
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
        return [Finding(
            rule_id=self.id,
            severity=Severity.MEDIUM,
            category=Category.CAPACITY,
            title=f"WAN latency worsening ({baseline:.0f} → {wan.latency_ms:.0f} ms)",
            summary=(
                f"Probe latency rose to {wan.latency_ms:.0f} ms from a recent baseline of "
                f"{baseline:.0f} ms across the last scans."
            ),
            evidence={"latencyMs": wan.latency_ms, "baselineMs": round(baseline, 1)},
            recommendation=(
                "Something changed on the WAN path — check for new heavy uploads/downloads, "
                "QoS misconfiguration, or ISP degradation."
            ),
        )]


RULES = [WanLatencyLoss(), WanLatencyWorsening()]
