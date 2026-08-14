from __future__ import annotations

from statistics import median

from app.collectors.snapshot import Snapshot

from .base import Category, Finding, RunHistory, Severity


def _online_ap_radios(snapshot: Snapshot):
    """Yield (device_detail, radio) for broadcasting radios of online APs.

    A disabled radio is reported with channel 0 (or no channel) by the
    Integration API — skip those; they can't cause RF problems.
    """
    for dev_id, detail in snapshot.device_details.items():
        overview = next((d for d in snapshot.devices if d.id == dev_id), None)
        if overview is None or overview.state != "ONLINE":
            continue
        if not detail.is_access_point:
            continue
        for radio in detail.interfaces.radios:
            if not radio.channel:
                continue
            yield detail, radio


class ChannelOverlap:
    id = "wifi.channel_overlap"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = []
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
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=Severity.MEDIUM,
                        category=Category.WIFI,
                        title=f"{len(names)} APs share 2.4 GHz channel {channel}",
                        summary=(
                            f"APs {', '.join(names)} all broadcast on 2.4 GHz channel {channel}; "
                            "co-channel contention reduces airtime for every client."
                        ),
                        evidence={"channel": channel, "aps": names},
                        recommendation="Spread these APs across channels 1, 6 and 11.",
                    )
                )
        for channel, entries in by_channel_5.items():
            wide = [(n, w) for n, w in entries if (w or 0) >= 80]
            if len(wide) >= 2:
                names = [n for n, _ in wide]
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=Severity.MEDIUM,
                        category=Category.WIFI,
                        title=f"{len(names)} APs share 5 GHz channel {channel} at wide width",
                        summary=(
                            f"APs {', '.join(names)} share 5 GHz channel {channel} with 80 MHz+ width; "
                            "wide overlapping channels amplify co-channel interference."
                        ),
                        # key "ap", not "name" — the advisor payload sanitizer
                        # pseudonymizes "name"/"hostname" as client identity
                        evidence={"channel": channel,
                                  "aps": [{"ap": n, "widthMHz": w} for n, w in wide]},
                        recommendation=(
                            "Assign distinct 5 GHz channels (e.g. 36/52/100/149) or reduce channel "
                            "width to 40 MHz in dense deployments."
                        ),
                    )
                )
        return findings


class Narrow5Width:
    """80 MHz is the sweet spot on 5 GHz — but only when channels are scarce."""

    id = "wifi.narrow_5_width"
    # With more radios than this, narrow channels are the correct trade and
    # widening them would create the co-channel interference we flag elsewhere.
    _dense_radio_count = 3

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        radios_5 = [
            (dev, radio) for dev, radio in _online_ap_radios(snapshot)
            if radio.frequency_ghz == 5
        ]
        if len(radios_5) > self._dense_radio_count:
            return []
        return [
            Finding(
                rule_id=self.id,
                severity=Severity.LOW,
                category=Category.WIFI,
                title=f"{dev.name} 5 GHz width is {radio.channel_width_mhz} MHz",
                summary=(
                    f"{dev.name} runs a {radio.channel_width_mhz} MHz channel on 5 GHz. With only "
                    f"{len(radios_5)} radio(s) on the band there is room for 80 MHz, which roughly "
                    "doubles throughput for modern clients."
                ),
                evidence={"device": dev.name, "widthMHz": radio.channel_width_mhz,
                          "channel": radio.channel, "radiosOn5GHz": len(radios_5)},
                recommendation=(
                    "Set the 5 GHz channel width to 80 MHz (VHT80/HE80). Keep it at 40 MHz only "
                    "if you later add APs and channels start overlapping."
                ),
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
    """

    id = "wifi.mesh_uplink"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        ap_ids = {
            dev_id for dev_id, detail in snapshot.device_details.items()
            if detail.is_access_point
        }
        findings = []
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
            findings.append(
                Finding(
                    rule_id=self.id,
                    severity=Severity.HIGH if hops >= 2 else Severity.MEDIUM,
                    category=Category.WIFI,
                    title=f"{detail.name} is wirelessly meshed via {parent_name}",
                    summary=(
                        f"{detail.name} uplinks through {parent_name} over the air"
                        + (f" and sits {hops} wireless hops from a wired device" if hops >= 2 else "")
                        + ". A meshed AP splits its radio time between serving clients and "
                        "relaying, roughly halving throughput per hop."
                    ),
                    evidence={"device": detail.name, "uplinkDevice": parent_name,
                              "wirelessHops": hops},
                    recommendation=(
                        "Run Ethernet to this AP if at all possible. If it must stay meshed, keep "
                        "it within one hop of a wired AP and make sure both radios see each other "
                        "well above -65 dBm."
                    ),
                    subject_type="device",
                    subject_id=dev_id,
                    subject_name=detail.name,
                )
            )
        return findings


class HighRetries:
    id = "wifi.high_retries"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = []
        for dev_id, stats in snapshot.device_stats.items():
            dev = snapshot.device_details.get(dev_id)
            if dev is None or not dev.is_access_point:
                continue
            for radio in stats.interfaces.radios:
                pct = radio.tx_retries_pct
                if pct is None or pct < 15:
                    continue
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=Severity.HIGH if pct >= 30 else Severity.MEDIUM,
                        category=Category.WIFI,
                        title=f"High TX retries on {dev.name} {radio.frequency_ghz} GHz ({pct:.0f}%)",
                        summary=(
                            f"{pct:.1f}% of transmissions on {dev.name}'s {radio.frequency_ghz} GHz radio "
                            "are retries — a sign of interference, distant clients, or co-channel contention."
                        ),
                        evidence={"device": dev.name, "frequencyGHz": radio.frequency_ghz,
                                  "txRetriesPct": pct},
                        recommendation=(
                            "Fix the channel plan first (overlap/width findings), then check for non-WiFi "
                            "interferers near this AP and consider raising minimum data rates."
                        ),
                        subject_type="device",
                        subject_id=dev_id,
                        subject_name=dev.name,
                    )
                )
        return findings


class RetriesWorsening:
    id = "wifi.retries_worsening"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        if not history.runs:
            return []
        findings = []
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
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=Severity.MEDIUM,
                        category=Category.WIFI,
                        title=f"Retries worsening on {dev.name} {radio.frequency_ghz} GHz",
                        summary=(
                            f"TX retries rose to {pct:.1f}% from a recent baseline of {baseline:.1f}% — "
                            "something changed in the RF environment."
                        ),
                        evidence={"device": dev.name, "frequencyGHz": radio.frequency_ghz,
                                  "txRetriesPct": pct, "baselinePct": round(baseline, 1)},
                        recommendation=(
                            "Look for new interference sources (neighbor APs, cameras, microwaves) and "
                            "re-run the channel plan."
                        ),
                        subject_type="device",
                        subject_id=dev_id,
                        subject_name=dev.name,
                    )
                )
        return findings
