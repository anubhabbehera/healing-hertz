from __future__ import annotations

from typing import ClassVar

from app.collectors.snapshot import Snapshot

from .base import Category, Finding, RunHistory, Severity

DAY_SEC = 86400


class OfflineDevice:
    id = "device.offline"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        return [
            Finding(
                rule_id=self.id,
                severity=Severity.CRITICAL,
                category=Category.DEVICE_HEALTH,
                title=f"{d.name or d.model} is offline",
                summary=f"Device {d.name or d.mac_address} ({d.model}) is not reachable by the controller.",
                evidence={"device": d.name, "model": d.model, "mac": d.mac_address, "state": d.state},
                recommendation=(
                    "Check power and PoE on the device's switch port, verify the uplink cable, "
                    "and power-cycle the device. If it stays offline, factory-reset and re-adopt."
                ),
                subject_type="device",
                subject_id=d.id,
                subject_name=d.name,
            )
            for d in snapshot.devices
            if d.state == "OFFLINE"
        ]


class DegradedState:
    id = "device.degraded_state"
    _states: ClassVar[set[str]] = {
        "CONNECTION_INTERRUPTED", "ISOLATED", "U5G_INCORRECT_TOPOLOGY"
    }

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        return [
            Finding(
                rule_id=self.id,
                severity=Severity.HIGH,
                category=Category.DEVICE_HEALTH,
                title=f"{d.name or d.model} is in state {d.state}",
                summary=f"Device {d.name or d.mac_address} reports a degraded connection state ({d.state}).",
                evidence={"device": d.name, "model": d.model, "state": d.state},
                recommendation=(
                    "Inspect the device's uplink path (cable, switch port, mesh signal). ISOLATED APs "
                    "have lost their wired uplink; CONNECTION_INTERRUPTED often indicates flapping links."
                ),
                subject_type="device",
                subject_id=d.id,
                subject_name=d.name,
            )
            for d in snapshot.devices
            if d.state in self._states
        ]


class PendingAdoption:
    id = "device.pending_adoption"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = [
            Finding(
                rule_id=self.id,
                severity=Severity.MEDIUM,
                category=Category.DEVICE_HEALTH,
                title=f"{p.name or p.model or p.mac_address} awaiting adoption",
                summary="A UniFi device is visible on the network but not adopted into this site.",
                evidence={"device": p.name, "model": p.model, "mac": p.mac_address},
                recommendation="Adopt the device in the UniFi console, or remove it from the network if unexpected.",
                subject_type="device",
                subject_id=p.id,
                subject_name=p.name,
            )
            for p in snapshot.pending_devices
        ]
        findings += [
            Finding(
                rule_id=self.id,
                severity=Severity.MEDIUM,
                category=Category.DEVICE_HEALTH,
                title=f"{d.name or d.model} stuck in PENDING_ADOPTION",
                summary="An adopted device has fallen back to pending-adoption state.",
                evidence={"device": d.name, "model": d.model, "state": d.state},
                recommendation="Re-adopt the device; if it loops, verify the inform URL and controller reachability.",
                subject_type="device",
                subject_id=d.id,
                subject_name=d.name,
            )
            for d in snapshot.devices
            if d.state == "PENDING_ADOPTION"
        ]
        return findings


class UnsupportedDevice:
    id = "device.unsupported"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        return [
            Finding(
                rule_id=self.id,
                severity=Severity.LOW,
                category=Category.DEVICE_HEALTH,
                title=f"{d.name or d.model} is no longer supported",
                summary="This device model is not fully supported by the current Network application.",
                evidence={"device": d.name, "model": d.model, "firmware": d.firmware_version},
                recommendation="Plan a hardware refresh; unsupported devices stop receiving security fixes.",
                subject_type="device",
                subject_id=d.id,
                subject_name=d.name,
            )
            for d in snapshot.devices
            if not d.supported
        ]


class HighCpu:
    id = "device.high_cpu"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = []
        for dev_id, stats in snapshot.device_stats.items():
            cpu = stats.cpu_utilization_pct
            if cpu is None or cpu < 75:
                continue
            dev = snapshot.device_details.get(dev_id)
            name = dev.name if dev else dev_id
            findings.append(
                Finding(
                    rule_id=self.id,
                    severity=Severity.HIGH if cpu >= 90 else Severity.MEDIUM,
                    category=Category.DEVICE_HEALTH,
                    title=f"High CPU on {name} ({cpu:.0f}%)",
                    summary=f"{name} is running at {cpu:.1f}% CPU, which can cause latency and dropped features.",
                    evidence={"device": name, "cpuUtilizationPct": cpu,
                              "loadAverage5Min": stats.load_average_5_min},
                    recommendation=(
                        "Identify heavy features (IDS/IPS, DPI, smart queues) and disable or right-size them; "
                        "check for runaway processes after a firmware update, or upgrade the gateway if "
                        "sustained at high WAN throughput."
                    ),
                    subject_type="device",
                    subject_id=dev_id,
                    subject_name=name,
                )
            )
        return findings


class HighMemory:
    id = "device.high_memory"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = []
        for dev_id, stats in snapshot.device_stats.items():
            mem = stats.memory_utilization_pct
            if mem is None or mem < 80:
                continue
            dev = snapshot.device_details.get(dev_id)
            name = dev.name if dev else dev_id
            findings.append(
                Finding(
                    rule_id=self.id,
                    severity=Severity.HIGH if mem >= 90 else Severity.MEDIUM,
                    category=Category.DEVICE_HEALTH,
                    title=f"High memory on {name} ({mem:.0f}%)",
                    summary=f"{name} is using {mem:.1f}% of memory; exhaustion leads to reboots.",
                    evidence={"device": name, "memoryUtilizationPct": mem},
                    recommendation=(
                        "Reduce enabled services on the device, check for memory-leak advisories on its "
                        "firmware version, and schedule a reboot as a stopgap."
                    ),
                    subject_type="device",
                    subject_id=dev_id,
                    subject_name=name,
                )
            )
        return findings


class HighLoad:
    id = "device.high_load"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = []
        for dev_id, stats in snapshot.device_stats.items():
            l5, l15 = stats.load_average_5_min, stats.load_average_15_min
            if l5 is None or l15 is None or l15 <= 0:
                continue
            if l5 >= 2.0 and l5 > 1.5 * l15:
                dev = snapshot.device_details.get(dev_id)
                name = dev.name if dev else dev_id
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=Severity.MEDIUM,
                        category=Category.DEVICE_HEALTH,
                        title=f"Load spike on {name}",
                        summary=f"5-minute load ({l5:.1f}) is well above the 15-minute baseline ({l15:.1f}).",
                        evidence={"device": name, "loadAverage5Min": l5, "loadAverage15Min": l15},
                        recommendation="Re-scan shortly; if the spike persists, treat as sustained high CPU.",
                        subject_type="device",
                        subject_id=dev_id,
                        subject_name=name,
                    )
                )
        return findings


class RecentReboot:
    id = "device.recent_reboot"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = []
        for dev_id, stats in snapshot.device_stats.items():
            up = stats.uptime_sec
            if up is None or up >= 3600:
                continue
            dev = snapshot.device_details.get(dev_id)
            name = dev.name if dev else dev_id
            findings.append(
                Finding(
                    rule_id=self.id,
                    severity=Severity.INFO,
                    category=Category.DEVICE_HEALTH,
                    title=f"{name} rebooted recently",
                    summary=f"{name} has been up for only {up // 60} minutes.",
                    evidence={"device": name, "uptimeSec": up},
                    recommendation="No action needed if this was a planned restart; watch for repeats.",
                    subject_type="device",
                    subject_id=dev_id,
                    subject_name=name,
                )
            )
        return findings


class RebootLoop:
    id = "device.reboot_loop"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        if len(history.runs) < 2:
            return []
        findings = []
        for dev_id, stats in snapshot.device_stats.items():
            up = stats.uptime_sec
            if up is None or up >= DAY_SEC:
                continue
            prior = [r for r in history.runs if r.device_uptimes.get(dev_id, DAY_SEC) < DAY_SEC]
            if len(prior) < 2:
                continue
            span = (prior[0].started_at - prior[-1].started_at).total_seconds()
            if span < DAY_SEC:
                continue
            dev = snapshot.device_details.get(dev_id)
            name = dev.name if dev else dev_id
            findings.append(
                Finding(
                    rule_id=self.id,
                    severity=Severity.HIGH,
                    category=Category.DEVICE_HEALTH,
                    title=f"{name} appears to be reboot-looping",
                    summary=(
                        f"{name} has shown under 24h uptime across {len(prior) + 1} scans spanning "
                        "more than a day — it is likely restarting repeatedly."
                    ),
                    evidence={"device": name, "uptimeSec": up,
                              "lowUptimeRuns": len(prior) + 1},
                    recommendation=(
                        "Check PoE budget on its switch port, inspect for overheating, and review "
                        "firmware release notes; downgrade if a recent update introduced instability."
                    ),
                    subject_type="device",
                    subject_id=dev_id,
                    subject_name=name,
                )
            )
        return findings


class FirmwareUpdateAvailable:
    id = "firmware.update_available"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        return [
            Finding(
                rule_id=self.id,
                severity=Severity.MEDIUM,
                category=Category.FIRMWARE,
                title=f"Firmware update available for {d.name or d.model}",
                summary=f"{d.name or d.mac_address} is on {d.firmware_version} with an update available.",
                evidence={"device": d.name, "model": d.model, "currentFirmware": d.firmware_version},
                recommendation=(
                    "Review the release notes and update during a maintenance window. Keep all APs on "
                    "the same firmware to avoid roaming quirks."
                ),
                subject_type="device",
                subject_id=d.id,
                subject_name=d.name,
            )
            for d in snapshot.devices
            if d.firmware_updatable
        ]


RULES = [
    OfflineDevice(),
    DegradedState(),
    PendingAdoption(),
    UnsupportedDevice(),
    HighCpu(),
    HighMemory(),
    HighLoad(),
    RecentReboot(),
    RebootLoop(),
    FirmwareUpdateAvailable(),
]
