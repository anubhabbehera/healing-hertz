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


RULES.append(ExcessiveRoaming())
