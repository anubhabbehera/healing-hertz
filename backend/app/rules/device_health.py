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


def _version_key(version: str) -> tuple[int, ...]:
    """Sortable form of a dotted version: 6.6.55 outranks 6.6.9."""
    parts = []
    for chunk in version.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


class MixedApFirmware:
    """APs on different firmware negotiate roaming inconsistently.

    Only dedicated APs on a shared firmware train are comparable. An
    all-in-one console (Express 7, a Dream Machine) is an AP as well as a
    gateway or switch, but it runs its own firmware line, and a model frozen
    on an older major version is a different line again. Neither is drift the
    operator can resolve, so both are excluded before versions are compared.

    Not declarative: it groups online APs by version and then reports across
    those groups, and the recommendation names the newest version found.
    """

    id = "firmware.version_drift"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        aps = [
            d for d in snapshot.devices
            if d.state == "ONLINE" and set(d.features) == {"accessPoint"} and d.firmware_version
        ]
        # Group by major version: that is the firmware train, and only APs on
        # the same train can be brought to a common version.
        trains: dict[int, list] = {}
        for d in aps:
            trains.setdefault(_version_key(d.firmware_version)[0], []).append(d)

        drifting = [
            devs for devs in trains.values()
            if len(devs) >= 2 and len({d.firmware_version for d in devs}) >= 2
        ]
        if not drifting:
            return []
        # More than one train drifting at once is possible but vanishingly
        # rare; report the largest, since "update everything to {newest}" is
        # only sound advice within a single train.
        fleet = max(drifting, key=len)

        by_version: dict[str, list[str]] = {}
        for d in fleet:
            by_version.setdefault(d.firmware_version, []).append(d.name or d.model)
        return [Binding(vars={
            "version_count": len(by_version),
            "by_version": by_version,
            "newest": max(by_version, key=_version_key),
        })]
