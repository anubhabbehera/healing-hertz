from __future__ import annotations

from statistics import median

from app.collectors.snapshot import Snapshot

from .base import Binding, RunHistory


class DnsSecurityBlocks:
    id = "dns.security_blocks"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        dns = snapshot.dns
        if dns is None or dns.security_block_count == 0:
            return []
        return [Binding(vars={
            "block_count": dns.security_block_count,
            "reasons": dns.security_blocks,
            "top_blocked": dns.top_blocked[:5],
        })]


class DnsBlockedSpike:
    """Blocked-query ratio, against its own recent baseline and in absolute terms.

    Not declarative: the baseline is a median over the last three runs, and the
    two conditions are independent, so a scan can report either, both or
    neither under the same id.
    """

    id = "dns.anomalies"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        dns = snapshot.dns
        if dns is None or dns.queries == 0:
            return []
        bindings = []
        prior = [r.site_metrics["dns.blocked_pct"] for r in history.runs[:3]
                 if "dns.blocked_pct" in r.site_metrics]
        if prior:
            baseline = median(prior)
            if dns.blocked_pct - baseline > 15:
                bindings.append(Binding(key="spike", vars={
                    "blocked_pct": dns.blocked_pct,
                    "baseline_pct": baseline,
                    "blocked_pct_rounded": round(dns.blocked_pct, 1),
                    "baseline_pct_rounded": round(baseline, 1),
                    "queries": dns.queries,
                    "top_blocked": dns.top_blocked[:5],
                }))
        if dns.blocked_pct >= 30:
            bindings.append(Binding(key="high_ratio", vars={
                "blocked_pct": dns.blocked_pct,
                "blocked_pct_rounded": round(dns.blocked_pct, 1),
                "queries": dns.queries,
                "top_blocked": dns.top_blocked[:5],
            }))
        return bindings
