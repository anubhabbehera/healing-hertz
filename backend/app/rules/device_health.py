from __future__ import annotations

from app.collectors.snapshot import Snapshot

from .base import Binding, RunHistory

DAY_SEC = 86400


class RebootLoop:
    """Repeated short uptimes across runs spanning more than a day.

    Not declarative: it filters run history by a per-device projection, then
    measures the elapsed time between the first and last surviving run. One
    low uptime is a reboot; the pattern over time is the finding.
    """

    id = "device.reboot_loop"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        if len(history.runs) < 2:
            return []
        bindings = []
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
            bindings.append(Binding(
                vars={
                    "device_name": name,
                    "uptime_sec": up,
                    # This scan plus the prior ones that also saw low uptime.
                    "low_uptime_runs": len(prior) + 1,
                },
                subject_type="device",
                subject_id=dev_id,
                subject_name=name,
            ))
        return bindings


class MixedApFirmware:
    """APs on different firmware negotiate roaming inconsistently.

    Not declarative: it groups online APs by version and then reports across
    those groups, and the recommendation names the newest version found.
    """

    id = "firmware.version_drift"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
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
        return [Binding(vars={
            "version_count": len(versions),
            "by_version": by_version,
            "newest": max(versions),
        })]
