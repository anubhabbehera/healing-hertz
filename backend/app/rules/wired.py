from __future__ import annotations

from app.collectors.snapshot import Snapshot

from .base import Category, Finding, RunHistory, Severity


class GatewaySaturation:
    id = "site.gateway_saturation"
    _threshold = 0.85

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = []
        for dev_id, detail in snapshot.device_details.items():
            if not detail.is_gateway:
                continue
            stats = snapshot.device_stats.get(dev_id)
            if stats is None or stats.uplink is None:
                continue
            tx, rx = stats.uplink.tx_rate_bps or 0, stats.uplink.rx_rate_bps or 0
            up_ports = [p.speed_mbps for p in detail.interfaces.ports
                        if p.state == "UP" and p.speed_mbps]
            if not up_ports:
                continue
            capacity_bps = max(up_ports) * 1_000_000
            utilization = (tx + rx) / capacity_bps if capacity_bps else 0
            if utilization < self._threshold:
                continue
            findings.append(
                Finding(
                    rule_id=self.id,
                    severity=Severity.MEDIUM,
                    category=Category.CAPACITY,
                    title=f"Gateway uplink near saturation ({utilization:.0%})",
                    summary=(
                        f"{detail.name} is pushing {(tx + rx) / 1e6:.0f} Mbps against roughly "
                        f"{max(up_ports)} Mbps of port capacity."
                    ),
                    evidence={"device": detail.name, "txRateBps": tx, "rxRateBps": rx,
                              "portCapacityMbps": max(up_ports)},
                    recommendation=(
                        "Enable smart queues / QoS to protect latency, identify top talkers, and "
                        "consider a faster uplink if this is sustained."
                    ),
                    subject_type="device",
                    subject_id=dev_id,
                    subject_name=detail.name,
                )
            )
        return findings
