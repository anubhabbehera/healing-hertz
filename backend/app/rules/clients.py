from __future__ import annotations

from app.collectors.snapshot import Snapshot

from .base import Category, Finding, RunHistory, Severity


class NoWirelessClients:
    # The Integration API doesn't expose client→AP association, so this check
    # is site-wide: online APs but zero wireless clients anywhere.
    id = "clients.none_on_ap"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        online_aps = [
            d for d in snapshot.devices
            if d.state == "ONLINE" and "accessPoint" in d.features
        ]
        wireless = [c for c in snapshot.clients if c.type.upper() == "WIRELESS"]
        if not online_aps or wireless:
            return []
        return [
            Finding(
                rule_id=self.id,
                severity=Severity.INFO,
                category=Category.CLIENTS,
                title="No wireless clients despite online APs",
                summary=(
                    f"{len(online_aps)} access point(s) are online but no wireless clients are "
                    "connected — SSIDs may be disabled or misconfigured."
                ),
                evidence={"onlineAps": [d.name for d in online_aps], "wirelessClients": 0},
                recommendation="Verify SSIDs are enabled and broadcasting on the expected bands.",
            )
        ]


class UnauthorizedGuests:
    id = "clients.unauthorized_guests"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        stuck = [
            c for c in snapshot.clients
            if c.access is not None
            and c.access.type == "GUEST"
            and c.access.authorized is False
        ]
        if not stuck:
            return []
        return [
            Finding(
                rule_id=self.id,
                severity=Severity.LOW,
                category=Category.CLIENTS,
                title=f"{len(stuck)} guest client(s) stuck unauthorized",
                summary=(
                    "Guest clients are connected but not authorized through the portal — they hold "
                    "an association without network access."
                ),
                evidence={"clients": [
                    {"name": c.name, "mac": c.mac_address, "ip": c.ip_address} for c in stuck
                ]},
                recommendation=(
                    "Check the guest portal flow (captive portal reachability, voucher validity); "
                    "authorize or disconnect these clients."
                ),
            )
        ]


RULES = [NoWirelessClients(), UnauthorizedGuests()]


class ExcessiveRoaming:
    id = "clients.excessive_roaming"
    _threshold = 10  # roam events per client per 24h

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        if snapshot.rf is None or not snapshot.rf.roam_data_available:
            return []
        flappers = {who: n for who, n in snapshot.rf.roam_counts.items()
                    if n >= self._threshold}
        if not flappers:
            return []
        return [Finding(
            rule_id=self.id,
            severity=Severity.MEDIUM,
            category=Category.WIFI,
            title=f"{len(flappers)} client(s) roaming excessively",
            summary=(
                "Clients bouncing between APs many times a day usually sit in an overlap "
                "zone with near-equal signal, causing stalls on every hop."
            ),
            evidence={"roamsLast24h": dict(
                sorted(flappers.items(), key=lambda kv: kv[1], reverse=True)[:10]
            )},
            recommendation=(
                "Lower transmit power on the overlapping APs (or reposition them) so each "
                "area has one clearly-best AP; check band-steering settings."
            ),
        )]


class SlowPhyRate:
    """One slow client can hold an entire cell hostage.

    A client negotiating legacy rates occupies the radio far longer per byte
    than a modern one, so without airtime fairness it drags every other client
    on that AP down with it.
    """

    id = "clients.slow_phy_rate"
    _slow_kbps = 54_000  # 54 Mbps — the ceiling of 802.11a/g

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        if snapshot.rf is None:
            return []
        slow = [
            c for c in snapshot.rf.clients
            if c.tx_rate_kbps is not None and 0 < c.tx_rate_kbps <= self._slow_kbps
        ]
        if not slow:
            return []
        return [Finding(
            rule_id=self.id,
            severity=Severity.MEDIUM if len(slow) > 1 else Severity.LOW,
            category=Category.WIFI,
            title=f"{len(slow)} client(s) connected at legacy data rates",
            summary=(
                "These clients negotiated 54 Mbps or less. Every frame they send occupies "
                "the radio for many times longer than a modern client's, so they eat the "
                "airtime of everyone else on the same AP."
            ),
            evidence={"clients": [
                {"name": c.name, "txRateKbps": c.tx_rate_kbps, "signalDbm": c.signal_dbm,
                 "ssid": c.essid}
                for c in sorted(slow, key=lambda c: c.tx_rate_kbps or 0)[:10]
            ]},
            recommendation=(
                "Enable Airtime Fairness on the APs so a slow client can't monopolise the "
                "cell, and raise the minimum data rate to drop legacy 802.11b rates. If a "
                "client is slow because it is distant, fix coverage instead."
            ),
        )]


class ApClientLoad:
    id = "capacity.ap_client_load"
    _threshold = 35  # associated wireless clients on a single AP

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        if snapshot.rf is None:
            return []
        per_ap: dict[str, int] = {}
        for client in snapshot.rf.clients:
            if client.ap_mac:
                per_ap[client.ap_mac] = per_ap.get(client.ap_mac, 0) + 1
        names = {
            d.mac_address.lower(): (d.name or d.model) for d in snapshot.devices
            if d.mac_address
        }
        findings = []
        for ap_mac, count in per_ap.items():
            if count < self._threshold:
                continue
            name = names.get(ap_mac.lower(), ap_mac)
            findings.append(Finding(
                rule_id=self.id,
                severity=Severity.MEDIUM,
                category=Category.CAPACITY,
                title=f"{name} is carrying {count} wireless clients",
                summary=(
                    f"{count} clients share {name}'s radios. Past roughly {self._threshold} "
                    "associations the cell spends more time arbitrating airtime than moving "
                    "data, and latency becomes erratic even at strong signal."
                ),
                evidence={"ap": name, "clients": count},
                recommendation=(
                    "Add an AP to split the coverage area, or move stationary devices "
                    "(TVs, consoles, desktops) to Ethernet to free airtime."
                ),
            ))
        return findings


class BandSteeringIneffective:
    id = "wifi.band_steering_ineffective"
    _strong_dbm = -65  # comfortably within 5 GHz range of its AP
    _threshold = 3

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        if snapshot.rf is None:
            return []
        stuck = [
            c for c in snapshot.rf.clients
            if c.band_ghz == 2.4
            and c.signal_dbm is not None
            and c.signal_dbm >= self._strong_dbm
        ]
        if len(stuck) < self._threshold:
            return []
        return [Finding(
            rule_id=self.id,
            severity=Severity.MEDIUM,
            category=Category.WIFI,
            title=f"{len(stuck)} strong-signal client(s) stuck on 2.4 GHz",
            summary=(
                "These clients sit close enough to their AP for 5 GHz yet remain on the "
                "slower, more congested 2.4 GHz band — the signature of band steering "
                "being off, or of separate 2.4 and 5 GHz SSIDs."
            ),
            evidence={"clients": [
                {"name": c.name, "signalDbm": c.signal_dbm, "channel": c.channel,
                 "ssid": c.essid}
                for c in sorted(stuck, key=lambda c: c.signal_dbm or 0, reverse=True)[:10]
            ]},
            recommendation=(
                "Combine the 2.4 and 5 GHz SSIDs into one name and set Band Steering to "
                "'Prefer 5G'. Clients that genuinely lack 5 GHz radios (many IoT devices) "
                "will stay on 2.4 GHz on their own."
            ),
        )]


RULES += [ExcessiveRoaming(), SlowPhyRate(), ApClientLoad(), BandSteeringIneffective()]
