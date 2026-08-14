from __future__ import annotations

from app.collectors.snapshot import Snapshot

from .base import Binding, RunHistory


class NoWirelessClients:
    """Online APs but no wireless clients anywhere.

    Not declarative: it asserts the *absence* of matches in one collection
    conditioned on the presence of matches in another, which a per-row
    predicate cannot express. The Integration API doesn't expose client→AP
    association, so the check is site-wide.
    """

    id = "clients.none_on_ap"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        online_aps = [
            d for d in snapshot.devices
            if d.state == "ONLINE" and "accessPoint" in d.features
        ]
        wireless = [c for c in snapshot.clients if c.type.upper() == "WIRELESS"]
        if not online_aps or wireless:
            return []
        return [Binding(vars={
            "ap_count": len(online_aps),
            "ap_names": [d.name for d in online_aps],
            # Zero by construction -- the rule only fires when it is -- but the
            # evidence states it so the operator sees both sides of the claim.
            "wireless_count": 0,
        })]


class ExcessiveRoaming:
    """Clients bouncing between APs.

    Not declarative: the evidence is a name→count mapping rather than a list of
    projected rows, and roam counts arrive already aggregated per client.
    """

    id = "clients.excessive_roaming"
    _threshold = 10  # roam events per client per 24h

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        if snapshot.rf is None or not snapshot.rf.roam_data_available:
            return []
        flappers = {who: n for who, n in snapshot.rf.roam_counts.items()
                    if n >= self._threshold}
        if not flappers:
            return []
        return [Binding(vars={
            "client_count": len(flappers),
            "roams": dict(sorted(flappers.items(), key=lambda kv: kv[1], reverse=True)[:10]),
        })]


class ApClientLoad:
    """Associated clients per AP.

    Not declarative: it groups clients by AP MAC and then joins that back to the
    device list through a case-normalised MAC map to get a display name.
    """

    id = "capacity.ap_client_load"
    _threshold = 35  # associated wireless clients on a single AP

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
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
        bindings = []
        for ap_mac, count in per_ap.items():
            if count < self._threshold:
                continue
            bindings.append(Binding(vars={
                "ap_name": names.get(ap_mac.lower(), ap_mac),
                "client_count": count,
                "threshold": self._threshold,
            }))
        return bindings
