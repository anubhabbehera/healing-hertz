from __future__ import annotations

from statistics import median

from app.collectors.snapshot import Snapshot

from .base import Category, Finding, RunHistory, Severity

GOOD_24_CHANNELS = {1, 6, 11}


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


class Bad24Channel:
    id = "wifi.bad_24_channel"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = []
        for dev, radio in _online_ap_radios(snapshot):
            if radio.frequency_ghz != 2.4 or radio.channel is None:
                continue
            if radio.channel in GOOD_24_CHANNELS:
                continue
            findings.append(
                Finding(
                    rule_id=self.id,
                    severity=Severity.MEDIUM,
                    category=Category.WIFI,
                    title=f"{dev.name} 2.4 GHz on channel {radio.channel}",
                    summary=(
                        f"{dev.name} uses 2.4 GHz channel {radio.channel}; only channels 1, 6 and 11 "
                        "are non-overlapping, so this channel interferes with two channel groups at once."
                    ),
                    evidence={"device": dev.name, "channel": radio.channel,
                              "widthMHz": radio.channel_width_mhz},
                    recommendation="Set the 2.4 GHz radio to channel 1, 6 or 11 (pick the least used by neighbors).",
                    subject_type="device",
                    subject_id=dev.id,
                    subject_name=dev.name,
                )
            )
        return findings


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


class Wide24Width:
    id = "wifi.wide_24_width"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = []
        for dev, radio in _online_ap_radios(snapshot):
            if radio.frequency_ghz != 2.4:
                continue
            if radio.channel_width_mhz is None or radio.channel_width_mhz <= 20:
                continue
            findings.append(
                Finding(
                    rule_id=self.id,
                    severity=Severity.LOW,
                    category=Category.WIFI,
                    title=f"{dev.name} 2.4 GHz width is {radio.channel_width_mhz} MHz",
                    summary=(
                        "40 MHz on 2.4 GHz consumes most of the band and almost always increases "
                        "interference more than it adds throughput."
                    ),
                    evidence={"device": dev.name, "widthMHz": radio.channel_width_mhz,
                              "channel": radio.channel},
                    recommendation="Set 2.4 GHz channel width to 20 MHz.",
                    subject_type="device",
                    subject_id=dev.id,
                    subject_name=dev.name,
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


RULES: list = [Bad24Channel(), ChannelOverlap(), Wide24Width(), HighRetries(), RetriesWorsening()]


class WeakRssiClients:
    id = "wifi.weak_rssi_clients"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        if snapshot.rf is None:
            return []
        weak = [c for c in snapshot.rf.clients
                if c.signal_dbm is not None and c.signal_dbm <= -75]
        if not weak:
            return []
        worst = min(c.signal_dbm for c in weak)
        return [Finding(
            rule_id=self.id,
            severity=Severity.HIGH if worst <= -85 else Severity.MEDIUM,
            category=Category.WIFI,
            title=f"{len(weak)} client(s) with weak WiFi signal",
            summary=(
                "Clients connected below -75 dBm get slow, retry-heavy links and drag "
                "down airtime for everyone on the same AP."
            ),
            evidence={"clients": [
                {"name": c.name, "signalDbm": c.signal_dbm, "ssid": c.essid}
                for c in sorted(weak, key=lambda c: c.signal_dbm or 0)[:10]
            ]},
            recommendation=(
                "Relocate the AP or the device, add an AP to cover the dead zone, or "
                "raise minimum RSSI on the AP so distant clients roam instead of clinging."
            ),
        )]


RULES.append(WeakRssiClients())
