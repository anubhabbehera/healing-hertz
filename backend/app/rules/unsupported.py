"""Checks that need an optional integration before they can run.

Each check names the snapshot enrichment that unlocks it; when that data is
present in a scan the check disappears from the "not checkable" list because
its real rule is running instead. A check with no enrichment name (None) can
never be covered — it needs configuration state the read-only Integration API
does not expose at all, and saying so is more useful than guessing.
"""

from app.collectors.snapshot import Snapshot

from .base import UnsupportedCheck

# (check, name of the Snapshot attribute that must be non-None to cover it)
_CHECKS: list[tuple[UnsupportedCheck, str | None]] = [
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
    (
        UnsupportedCheck(
            rule_id="clients.slow_phy_rate",
            title="Clients stuck on legacy data rates",
            reason=(
                "Per-client PHY rates are not exposed by the Integration API. Enable by "
                "setting UNIFI_USERNAME / UNIFI_PASSWORD (read-only local admin)."
            ),
        ),
        "rf",
    ),
    (
        UnsupportedCheck(
            rule_id="wifi.band_steering_ineffective",
            title="Strong-signal clients stuck on 2.4 GHz",
            reason=(
                "Which band a client is associated on is not exposed by the Integration "
                "API. Enable by setting UNIFI_USERNAME / UNIFI_PASSWORD (read-only local "
                "admin)."
            ),
        ),
        "rf",
    ),
    (
        UnsupportedCheck(
            rule_id="capacity.ap_client_load",
            title="Client load per access point",
            reason=(
                "The Integration API reports clients per site, not per AP. Enable by "
                "setting UNIFI_USERNAME / UNIFI_PASSWORD (read-only local admin)."
            ),
        ),
        "rf",
    ),
    (
        UnsupportedCheck(
            rule_id="wifi.radio_config_audit",
            title="Radio settings: transmit power, band steering, DTIM, minimum RSSI",
            reason=(
                "The Integration API reports radio state but no wireless configuration, so "
                "these cannot be audited from here. Community consensus: 2.4 GHz transmit "
                "power medium and 5 GHz low-to-medium in multi-AP homes (high only for a "
                "single AP), band steering set to prefer 5 GHz, airtime fairness on, DTIM "
                "period 3 on both bands, and minimum RSSI around -75 dBm so distant clients "
                "roam instead of clinging."
            ),
        ),
        None,
    ),
    (
        UnsupportedCheck(
            rule_id="wifi.auto_optimize",
            title="Automatic channel management (WiFi AI / auto-optimize)",
            reason=(
                "Whether auto-optimization is enabled is not exposed by the Integration "
                "API. It commonly selects DFS channels, or 2.4 GHz channels other than 1, "
                "6 and 11; pinning channels manually is the usual fix if scans keep "
                "reporting new channel findings after each optimization run."
            ),
        ),
        None,
    ),
    (
        UnsupportedCheck(
            rule_id="security.policy_audit",
            title="Firewall, UPnP, VLAN and port isolation policy",
            reason=(
                "Firewall rules, UPnP state, port forwards, VLAN assignments and guest "
                "isolation are not readable through the Integration API. Audit them in the "
                "console: IoT and camera VLANs isolated from the trusted network, UPnP off "
                "unless a specific application needs it, and no forgotten static port "
                "forwards."
            ),
        ),
        None,
    ),
]


def unsupported_checks(snapshot: Snapshot | None = None) -> list[UnsupportedCheck]:
    """Checks still uncovered given this snapshot's enrichments (all, if None)."""
    if snapshot is None:
        return [check for check, _ in _CHECKS]
    return [
        check for check, attr in _CHECKS
        if attr is None or getattr(snapshot, attr) is None
    ]
