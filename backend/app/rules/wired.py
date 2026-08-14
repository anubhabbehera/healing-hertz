from __future__ import annotations

from app.collectors.snapshot import Snapshot

from .base import Category, Finding, RunHistory, Severity


class UplinkNegotiation:
    id = "wired.uplink_negotiation"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = []
        for dev_id, detail in snapshot.device_details.items():
            for port in detail.interfaces.ports:
                if port.state != "UP" or port.speed_mbps is None or port.max_speed_mbps is None:
                    continue
                if port.speed_mbps <= 100 < port.max_speed_mbps:
                    findings.append(
                        Finding(
                            rule_id=self.id,
                            severity=Severity.HIGH,
                            category=Category.WIRED,
                            title=(
                                f"{detail.name} port {port.idx} linked at {port.speed_mbps} Mbps "
                                f"(capable of {port.max_speed_mbps})"
                            ),
                            summary=(
                                f"Port {port.idx} on {detail.name} negotiated only {port.speed_mbps} Mbps "
                                "on a faster-capable link — the classic signature of a damaged cable or "
                                "bad termination (a failed wire pair forces 100 Mbps)."
                            ),
                            evidence={"device": detail.name, "port": port.idx,
                                      "speedMbps": port.speed_mbps,
                                      "maxSpeedMbps": port.max_speed_mbps,
                                      "connector": port.connector},
                            recommendation=(
                                "Re-terminate or replace the cable on this port, then confirm it "
                                "renegotiates at full speed. Also check for a forced-speed setting."
                            ),
                            subject_type="device",
                            subject_id=dev_id,
                            subject_name=detail.name,
                        )
                    )
        return findings


class PoeLimited:
    id = "wired.poe_limited"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        # PoE state DOWN on an enabled port is normal (non-PoE client attached);
        # only LIMITED signals a genuine power-delivery problem.
        findings = []
        for dev_id, detail in snapshot.device_details.items():
            for port in detail.interfaces.ports:
                if port.poe is None or port.poe.state != "LIMITED":
                    continue
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=Severity.MEDIUM,
                        category=Category.WIRED,
                        title=f"PoE limited on {detail.name} port {port.idx}",
                        summary=(
                            f"Port {port.idx} on {detail.name} is delivering limited PoE — the attached "
                            "device may be underpowered, causing instability or reduced performance."
                        ),
                        evidence={"device": detail.name, "port": port.idx,
                                  "poeStandard": port.poe.standard, "poeState": port.poe.state},
                        recommendation=(
                            "Check the switch's total PoE budget and the attached device's power class; "
                            "move high-draw devices to a port/switch with headroom or use a PoE+ injector."
                        ),
                        subject_type="device",
                        subject_id=dev_id,
                        subject_name=detail.name,
                    )
                )
        return findings


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
