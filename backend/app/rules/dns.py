from __future__ import annotations

from statistics import median

from app.collectors.snapshot import Snapshot

from .base import Category, Finding, RunHistory, Severity


class DnsSecurityBlocks:
    id = "dns.security_blocks"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        dns = snapshot.dns
        if dns is None or dns.security_block_count == 0:
            return []
        return [Finding(
            rule_id=self.id,
            severity=Severity.HIGH,
            category=Category.CLIENTS,
            title=f"{dns.security_block_count} security-category DNS blocks in 24h",
            summary=(
                "NextDNS blocked queries in security categories (threat feeds, phishing, "
                "malware, etc.) — a device on the network tried to reach flagged domains."
            ),
            evidence={"reasons": dns.security_blocks,
                      "topBlockedDomains": dns.top_blocked[:5]},
            recommendation=(
                "Open the NextDNS logs, filter by the security reason, and identify which "
                "device issued these queries; scan it for malware or misbehaving apps."
            ),
        )]


class DnsBlockedSpike:
    id = "dns.anomalies"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        dns = snapshot.dns
        if dns is None or dns.queries == 0:
            return []
        findings = []
        prior = [r.site_metrics["dns.blocked_pct"] for r in history.runs[:3]
                 if "dns.blocked_pct" in r.site_metrics]
        if prior:
            baseline = median(prior)
            if dns.blocked_pct - baseline > 15:
                findings.append(Finding(
                    rule_id=self.id,
                    severity=Severity.MEDIUM,
                    category=Category.CLIENTS,
                    title=(
                        f"Blocked-DNS ratio spiked ({baseline:.0f}% → {dns.blocked_pct:.0f}%)"
                    ),
                    summary=(
                        "The share of blocked DNS queries jumped versus recent scans — often "
                        "a new device, app, or infection generating unwanted queries."
                    ),
                    evidence={"blockedPct": round(dns.blocked_pct, 1),
                              "baselinePct": round(baseline, 1),
                              "queries24h": dns.queries,
                              "topBlockedDomains": dns.top_blocked[:5]},
                    recommendation=(
                        "Check the NextDNS top-blocked domains and per-device logs for what "
                        "changed since the last scan."
                    ),
                ))
        if dns.blocked_pct >= 30:
            findings.append(Finding(
                rule_id=self.id,
                severity=Severity.LOW,
                category=Category.CLIENTS,
                title=f"High blocked-DNS ratio ({dns.blocked_pct:.0f}%)",
                summary=(
                    f"{dns.blocked_pct:.0f}% of the last 24h of DNS queries were blocked — "
                    "usually a chatty IoT/ad-heavy device, occasionally something worse."
                ),
                evidence={"blockedPct": round(dns.blocked_pct, 1),
                          "queries24h": dns.queries,
                          "topBlockedDomains": dns.top_blocked[:5]},
                recommendation=(
                    "Review the NextDNS top-blocked list; if one device dominates, decide "
                    "whether to isolate it or allowlist legitimate domains."
                ),
            ))
        return findings
