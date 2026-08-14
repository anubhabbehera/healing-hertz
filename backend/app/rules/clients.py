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
