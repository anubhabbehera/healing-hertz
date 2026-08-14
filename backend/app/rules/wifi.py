from __future__ import annotations

from statistics import median

from app.collectors.snapshot import Snapshot

from .base import Binding, RunHistory
from .sources import band_label


def _online_ap_radios(snapshot: Snapshot):
    """Yield (device_detail, radio) for broadcasting radios of online APs.

    The same join the `online_ap_radios` source performs, kept here for the
    rules that need the radio objects rather than a flat row.
    """
    for dev_id, detail in snapshot.device_details.items():
        overview = next((d for d in snapshot.devices if d.id == dev_id), None)
        if overview is None or overview.state != "ONLINE":
            continue
        if not detail.is_access_point:
            continue
        for radio in detail.interfaces.radios:
            # A disabled radio is reported with channel 0 (or no channel) by the
            # Integration API — it can't cause RF problems.
            if not radio.channel:
                continue
            yield detail, radio


class ChannelOverlap:
    """APs sharing a channel.

    Not declarative: it groups radios by channel and reports per group, and the
    two bands are grouped differently -- 2.4 GHz cares about any two APs
    sharing, 5 GHz only about wide channels overlapping.
    """

    id = "wifi.channel_overlap"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        bindings = []
        by_channel_24: dict[int, list[str]] = {}
        by_channel_5: dict[int, list[tuple[str, int | None]]] = {}
        for dev, radio in _online_ap_radios(snapshot):
            if radio.channel is None:
                continue
            if radio.frequency_ghz == 2.4:
                by_channel_24.setdefault(radio.channel, []).append(dev.name)
            elif radio.frequency_ghz == 5:
                by_channel_5.setdefault(radio.channel, []).append((dev.name, radio.channel_width_mhz))

        for channel, names in by_channel_24.items():
            if len(names) >= 2:
                bindings.append(Binding(key="band_24", vars={
                    "channel": channel,
                    "ap_count": len(names),
                    "ap_names": names,
                    "ap_list": ", ".join(names),
                }))
        for channel, entries in by_channel_5.items():
            wide = [(n, w) for n, w in entries if (w or 0) >= 80]
            if len(wide) >= 2:
                names = [n for n, _ in wide]
                bindings.append(Binding(key="band_5_wide", vars={
                    "channel": channel,
                    "ap_count": len(names),
                    "ap_list": ", ".join(names),
                    # key "ap", not "name" — the advisor payload sanitizer
                    # pseudonymizes "name"/"hostname" as client identity
                    "aps": [{"ap": n, "widthMHz": w} for n, w in wide],
                }))
        return bindings


class Narrow5Width:
    """80 MHz is the sweet spot on 5 GHz — but only when channels are scarce.

    Not declarative: whether a narrow channel is a mistake depends on how many
    radios share the band, so the predicate and the summary both read a
    property of the whole collection rather than of one radio.
    """

    id = "wifi.narrow_5_width"
    # With more radios than this, narrow channels are the correct trade and
    # widening them would create the co-channel interference we flag elsewhere.
    _dense_radio_count = 3

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        radios_5 = [
            (dev, radio) for dev, radio in _online_ap_radios(snapshot)
            if radio.frequency_ghz == 5
        ]
        if len(radios_5) > self._dense_radio_count:
            return []
        return [
            Binding(
                vars={
                    "device_name": dev.name,
                    "width_mhz": radio.channel_width_mhz,
                    "channel": radio.channel,
                    "radios_on_5ghz": len(radios_5),
                },
                subject_type="device",
                subject_id=dev.id,
                subject_name=dev.name,
            )
            for dev, radio in radios_5
            if radio.channel_width_mhz is not None and radio.channel_width_mhz <= 40
        ]


class MeshUplink:
    """An AP that reaches the network through another AP relays every frame.

    Each wireless hop roughly halves throughput and adds latency, so a wired
    uplink is the single biggest win available to a mesh-linked AP.

    Not declarative: counting hops means walking the uplink chain, with a guard
    against a controller reporting a cycle.
    """

    id = "wifi.mesh_uplink"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        ap_ids = {
            dev_id for dev_id, detail in snapshot.device_details.items()
            if detail.is_access_point
        }
        bindings = []
        for dev_id, detail in snapshot.device_details.items():
            if dev_id not in ap_ids or detail.state != "ONLINE":
                continue
            uplink_id = detail.uplink.device_id if detail.uplink else None
            if uplink_id is None or uplink_id not in ap_ids:
                continue
            # Walk up the mesh to see how many wireless hops this AP is from a
            # wired device; guard against a cycle in the reported topology.
            hops, seen, cursor = 1, {dev_id}, uplink_id
            while cursor in ap_ids and cursor not in seen:
                seen.add(cursor)
                parent = snapshot.device_details.get(cursor)
                parent_uplink = parent.uplink.device_id if parent and parent.uplink else None
                if parent_uplink is None or parent_uplink not in ap_ids:
                    break
                hops += 1
                cursor = parent_uplink
            parent_name = (
                snapshot.device_details[uplink_id].name
                if uplink_id in snapshot.device_details else uplink_id
            )
            bindings.append(Binding(
                vars={
                    "device_name": detail.name,
                    "uplink_device_name": parent_name,
                    "hops": hops,
                    # Carries its own leading space: the clause only appears
                    # past one hop, and the sentence has to read either way.
                    "hop_phrase": (
                        f" and sits {hops} wireless hops from a wired device"
                        if hops >= 2 else ""
                    ),
                },
                subject_type="device",
                subject_id=dev_id,
                subject_name=detail.name,
            ))
        return bindings


class RetriesWorsening:
    """TX retries against a median of the last three runs.

    Not declarative: the baseline is keyed by device and frequency together, so
    the lookup key has to be synthesised before history can be consulted.
    """

    id = "wifi.retries_worsening"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        if not history.runs:
            return []
        bindings = []
        for dev_id, stats in snapshot.device_stats.items():
            dev = snapshot.device_details.get(dev_id)
            if dev is None or not dev.is_access_point:
                continue
            for radio in stats.interfaces.radios:
                pct = radio.tx_retries_pct
                if pct is None:
                    continue
                key = f"{dev_id}:{radio.frequency_ghz}"
                prior = [r.radio_retries[key] for r in history.runs[:3] if key in r.radio_retries]
                if not prior:
                    continue
                baseline = median(prior)
                if pct - baseline <= 10:
                    continue
                bindings.append(Binding(
                    vars={
                        "device_name": dev.name,
                        "radio_frequency_ghz": radio.frequency_ghz,
                        "radio_band": band_label(radio.frequency_ghz),
                        "tx_retries_pct": pct,
                        "baseline_pct": baseline,
                        "baseline_pct_rounded": round(baseline, 1),
                    },
                    subject_type="device",
                    subject_id=dev_id,
                    subject_name=dev.name,
                ))
        return bindings
