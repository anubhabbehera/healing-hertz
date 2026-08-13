"""Checks that need an optional integration before they can run.

Each check names the snapshot enrichment that unlocks it; when that data is
present in a scan the check disappears from the "not checkable" list because
its real rule is running instead.
"""

from app.collectors.snapshot import Snapshot

from .base import UnsupportedCheck

# (check, name of the Snapshot attribute that must be non-None to cover it)
_CHECKS: list[tuple[UnsupportedCheck, str]] = [
    (
        UnsupportedCheck(
            rule_id="wifi.weak_rssi_clients",
            title="Clients with weak WiFi signal",
            reason=(
                "The Integration API does not expose per-client RSSI. Enable by setting "
                "UNIFI_USERNAME / UNIFI_PASSWORD (a read-only local admin) to use the "
                "legacy controller API."
            ),
        ),
        "rf",
    ),
    (
        UnsupportedCheck(
            rule_id="clients.excessive_roaming",
            title="Clients roaming excessively between APs",
            reason=(
                "Roaming events are not available via the Integration API. Enable by "
                "setting UNIFI_USERNAME / UNIFI_PASSWORD (read-only local admin)."
            ),
        ),
        "rf",
    ),
    (
        UnsupportedCheck(
            rule_id="wan.latency_loss",
            title="WAN latency and packet loss",
            reason=(
                "Measured by an active probe from this machine; it is currently disabled "
                "(WAN_PROBE=false or demo mode) or the probe failed."
            ),
        ),
        "wan",
    ),
    (
        UnsupportedCheck(
            rule_id="dns.anomalies",
            title="DNS failures and anomalies",
            reason=(
                "DNS data is not exposed by the Integration API. Enable by setting "
                "NEXTDNS_API_KEY and NEXTDNS_PROFILE_ID to analyze your NextDNS profile."
            ),
        ),
        "dns",
    ),
]


def unsupported_checks(snapshot: Snapshot | None = None) -> list[UnsupportedCheck]:
    """Checks still uncovered given this snapshot's enrichments (all, if None)."""
    if snapshot is None:
        return [check for check, _ in _CHECKS]
    return [check for check, attr in _CHECKS if getattr(snapshot, attr) is None]
