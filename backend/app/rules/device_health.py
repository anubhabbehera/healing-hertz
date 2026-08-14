from __future__ import annotations

from app.collectors.snapshot import Snapshot

from .base import Category, Finding, RunHistory, Severity

DAY_SEC = 86400


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


class MixedApFirmware:
    """APs on different firmware negotiate roaming inconsistently."""

    id = "firmware.version_drift"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        aps = [
            d for d in snapshot.devices
            if d.state == "ONLINE" and "accessPoint" in d.features and d.firmware_version
        ]
        versions = {d.firmware_version for d in aps}
        if len(aps) < 2 or len(versions) < 2:
            return []
        by_version: dict[str, list[str]] = {}
        for d in aps:
            by_version.setdefault(d.firmware_version, []).append(d.name or d.model)
        newest = max(versions)
        return [
            Finding(
                rule_id=self.id,
                severity=Severity.LOW,
                category=Category.FIRMWARE,
                title=f"Access points run {len(versions)} different firmware versions",
                summary=(
                    "Roaming, band steering and fast-transition behaviour are negotiated between "
                    "APs. When they run different firmware those features can behave "
                    "inconsistently, producing dropouts that look like RF problems."
                ),
                evidence={"versions": by_version, "newest": newest},
                recommendation=(
                    f"Bring every AP to the same version (currently {newest}) in one maintenance "
                    "window rather than updating them piecemeal."
                ),
            )
        ]
