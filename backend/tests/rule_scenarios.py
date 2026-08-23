"""Snapshot scenarios that between them exercise every registered rule.

Each scenario starts from a fresh demo snapshot, mutates it, and optionally
supplies run history. The same list drives both the generator that writes
``tests/golden/findings.json`` and the test that asserts against it, so any
change to how findings are *produced* can be proved output-identical.

Scenarios deliberately include negative cases (a rule that must stay quiet).
A rule that stops firing is just as much a regression as one that changes its
wording, and only a full-output diff catches both.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.analytics.timeseries import Point, Series
from app.collectors.snapshot import Snapshot, collect_snapshot
from app.integrations.legacy_unifi import ClientRF, RfSnapshot
from app.integrations.nextdns import DnsSnapshot
from app.integrations.wan_probe import WanProbeResult
from app.rules.base import HistoricalRun, RunHistory
from app.unifi.models import PendingDevice, Radio, WifiClientFiltering

# Fixed so the golden file is reproducible. Rules only ever read *differences*
# between run timestamps, never the absolute value, so any epoch works.
BASE_TIME = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
DAY_SEC = 86400


@dataclass
class Scenario:
    name: str
    mutate: Callable[[Snapshot], None] | None = None
    history: RunHistory = field(default_factory=RunHistory)


# --- mutations -------------------------------------------------------------


def _degraded_and_pending(s: Snapshot) -> None:
    by_id = {d.id: d for d in s.devices}
    by_id["sw1"].state = "CONNECTION_INTERRUPTED"
    by_id["ap2"].state = "PENDING_ADOPTION"
    s.pending_devices.append(
        PendingDevice(id="pend1", macAddress="f4:e2:c6:00:11:22", name="", model="U7P")
    )


def _unsupported_device(s: Snapshot) -> None:
    next(d for d in s.devices if d.id == "sw2").supported = False


def _memory_load_reboot(s: Snapshot) -> None:
    stats = s.device_stats["gw1"]
    stats.memory_utilization_pct = 91.5   # >= 80, and >= 90 escalates
    stats.load_average_5_min = 5.0        # >= 2.0 and > 1.5 * l15
    stats.load_average_15_min = 2.0
    s.device_stats["sw1"].uptime_sec = 1500  # < 3600 -> recent reboot


def _stale_uptime(s: Snapshot) -> None:
    s.device_stats["gw1"].uptime_sec = 200 * DAY_SEC


def _reboot_loop(s: Snapshot) -> None:
    s.device_stats["gw1"].uptime_sec = 1200


def _narrow_5_width(s: Snapshot) -> None:
    s.device_details["ap1"].interfaces.radios[1].channel_width_mhz = 40


def _narrow_5_width_dense(s: Snapshot) -> None:
    # A fourth 5 GHz radio makes the band busy enough that narrow channels are
    # a deliberate trade rather than a mistake — the rule must stay quiet.
    _narrow_5_width(s)
    s.device_details["ap2"].interfaces.radios.append(
        Radio(wlanStandard="802.11ax", frequencyGHz=5, channelWidthMHz=80, channel=36)
    )


def _legacy_radio_standard(s: Snapshot) -> None:
    s.device_details["ap1"].interfaces.radios[0].wlan_standard = "802.11g"


def _legacy_radio_standard_5ghz(s: Snapshot) -> None:
    # Guards how a band reads in prose. Radio.frequency_ghz is a float and the
    # API reports 5 GHz as an integer, so the raw value interpolates as
    # "5.0 GHz"; sources.band_label is what makes it read "5 GHz". Only the
    # 5 GHz radio shows the difference, so only this scenario catches a
    # regression in it.
    s.device_details["ap1"].interfaces.radios[1].wlan_standard = "802.11a"


def _mesh_two_hops(s: Snapshot) -> None:
    s.device_details["ap2"].uplink.device_id = "ap1"


def _mesh_cycle(s: Snapshot) -> None:
    # A controller reporting each AP as the other's uplink must not hang the walk.
    s.device_details["ap2"].uplink.device_id = "ap4"


def _firmware_drift_cleared(s: Snapshot) -> None:
    for dev in s.devices:
        if "accessPoint" in dev.features:
            dev.firmware_version = "6.6.55"


def _disabled_radio_channel_zero(s: Snapshot) -> None:
    # A disabled 2.4 GHz radio reports channel 0; it must trigger nothing.
    s.device_details["ap2"].interfaces.radios.append(
        Radio(wlanStandard="802.11ax", frequencyGHz=2.4, channelWidthMHz=40, channel=0)
    )


def _gateway_saturation(s: Snapshot) -> None:
    uplink = s.device_stats["gw1"].uplink
    uplink.tx_rate_bps = 4_500_000_000   # against a 10 Gbps SFP+ port
    uplink.rx_rate_bps = 4_200_000_000   # -> 87% utilisation


def _no_wireless_clients(s: Snapshot) -> None:
    s.clients = [c for c in s.clients if c.type.upper() != "WIRELESS"]


def _rf_weak_and_roaming(s: Snapshot) -> None:
    s.rf = RfSnapshot(
        clients=[
            ClientRF(mac="aa:1", name="patio-cam", ap_mac=None, essid="Home",
                     signal_dbm=-88, tx_rate_kbps=None, rx_rate_kbps=None),
            ClientRF(mac="aa:2", name="laptop", ap_mac=None, essid="Home",
                     signal_dbm=-55, tx_rate_kbps=None, rx_rate_kbps=None),
        ],
        roam_counts={"flappy-phone": 14},
        roam_data_available=True,
    )


def _rf_slow_band_load(s: Snapshot) -> None:
    ap_mac = next(d for d in s.devices if d.id == "ap1").mac_address
    s.rf = RfSnapshot(
        clients=[
            # Strong signal but parked on 2.4 GHz: band steering isn't working.
            *[ClientRF(mac=f"bb:{i}", name=f"phone-{i}", ap_mac=ap_mac, essid="Home",
                       signal_dbm=-52, tx_rate_kbps=115_000, rx_rate_kbps=115_000,
                       channel=6)
              for i in range(3)],
            # Legacy rate: eats airtime out of proportion to the data it moves.
            ClientRF(mac="bb:old", name="old-printer", ap_mac=ap_mac, essid="Home",
                     signal_dbm=-60, tx_rate_kbps=24_000, rx_rate_kbps=24_000,
                     channel=6),
            *[ClientRF(mac=f"cc:{i}", name=f"laptop-{i}", ap_mac=ap_mac, essid="Home",
                       signal_dbm=-58, tx_rate_kbps=600_000, rx_rate_kbps=600_000,
                       channel=44)
              for i in range(35)],
        ],
        roam_counts={},
        roam_data_available=True,
    )


def _graded_upper_band(s: Snapshot) -> None:
    """Every graded rule pushed above its escalation threshold.

    Paired with _graded_lower_band. Severity grading is per-rule inline logic
    today and becomes an `escalate` block in the catalog; without both bands
    pinned, a broken escalation rule would still match the golden.
    """
    stats = s.device_stats["gw1"]
    stats.cpu_utilization_pct = 95.0          # >= 90 -> high
    stats.memory_utilization_pct = 95.0       # >= 90 -> high
    for radio in s.device_stats["ap1"].interfaces.radios:
        radio.tx_retries_pct = 35.0           # >= 30 -> high
    s.rf = RfSnapshot(
        clients=[
            ClientRF(mac="aa:1", name="far-cam", ap_mac=None, essid="Home",
                     signal_dbm=-90, tx_rate_kbps=12_000, rx_rate_kbps=12_000),
            ClientRF(mac="aa:2", name="old-tv", ap_mac=None, essid="Home",
                     signal_dbm=-86, tx_rate_kbps=24_000, rx_rate_kbps=24_000),
        ],
        roam_counts={}, roam_data_available=True,
    )
    s.wan = WanProbeResult(latency_ms=200.0, jitter_ms=50.0, loss_pct=15.0,
                           samples=15, per_target={})


def _graded_lower_band(s: Snapshot) -> None:
    """The same rules held just below their escalation thresholds."""
    stats = s.device_stats["gw1"]
    stats.cpu_utilization_pct = 80.0          # >= 75 but < 90 -> medium
    stats.memory_utilization_pct = 85.0       # >= 80 but < 90 -> medium
    for radio in s.device_stats["ap1"].interfaces.radios:
        radio.tx_retries_pct = 20.0           # >= 15 but < 30 -> medium
    s.rf = RfSnapshot(
        clients=[
            ClientRF(mac="aa:1", name="hall-cam", ap_mac=None, essid="Home",
                     signal_dbm=-80, tx_rate_kbps=48_000, rx_rate_kbps=48_000),
        ],
        roam_counts={}, roam_data_available=True,
    )
    s.wan = WanProbeResult(latency_ms=100.0, jitter_ms=10.0, loss_pct=3.0,
                           samples=15, per_target={})


def _wan_bad(s: Snapshot) -> None:
    s.wan = WanProbeResult(latency_ms=170.0, jitter_ms=40.0, loss_pct=12.0,
                           samples=15, per_target={})


def _wan_healthy(s: Snapshot) -> None:
    s.wan = WanProbeResult(latency_ms=12.0, jitter_ms=2.0, loss_pct=0.0,
                           samples=15, per_target={})


def _dns_bad(s: Snapshot) -> None:
    s.dns = DnsSnapshot(
        queries=1000, blocked=450,
        top_blocked=[{"domain": "tracker.evil", "queries": 300}],
        security_blocks=[{"reason": "Threat Intelligence Feeds", "queries": 5}],
    )


def _dns_healthy(s: Snapshot) -> None:
    s.dns = DnsSnapshot(queries=1000, blocked=80)
    s.rf = RfSnapshot(clients=[], roam_counts={}, roam_data_available=True)
    _wan_healthy(s)


# --- histories -------------------------------------------------------------

# prior[0] - prior[-1] is exactly 24h, which is the rule's inclusive boundary.
_REBOOT_HISTORY = RunHistory(runs=[
    HistoricalRun(run_id="r2", started_at=BASE_TIME - timedelta(hours=6),
                  device_uptimes={"gw1": 900}),
    HistoricalRun(run_id="r1", started_at=BASE_TIME - timedelta(hours=30),
                  device_uptimes={"gw1": 3000}),
])

_RETRIES_HISTORY = RunHistory(runs=[
    HistoricalRun(run_id="r1", started_at=BASE_TIME, radio_retries={"ap1:2.4": 5.0}),
])

_WAN_HISTORY = RunHistory(runs=[
    HistoricalRun(run_id="r1", started_at=BASE_TIME, site_metrics={"wan.latency_ms": 20.0}),
])

_DNS_HISTORY = RunHistory(runs=[
    HistoricalRun(run_id="r1", started_at=BASE_TIME, site_metrics={"dns.blocked_pct": 12.0}),
])


def _wifi_config_sins(s: Snapshot) -> None:
    """A config plane with the settings this catalog exists to catch.

    The demo baseline is a tidy site on purpose -- a fixture that trips every
    rule teaches nothing about which rule fired. The sins live here instead.
    """
    by_id = {w.id: w for w in s.config.wifi}
    guest = by_id["wl3"]
    guest.security_configuration.type = "OPEN"
    guest.security_configuration.encryption = None
    guest.broadcasting_frequencies_ghz = [2.4, 5]
    guest.band_steering_enabled = False
    guest.basic_data_rate_kbps = {"2.4": 5500, "5": 6000}

    iot = by_id["wl2"]
    iot.hide_name = True
    iot.bss_transition_enabled = False
    iot.basic_data_rate_kbps = {"2.4": 1000}

    by_id["wl4"].client_filtering_policy = WifiClientFiltering(
        action="ALLOW",
        macAddressFilter=["aa:00:00:00:00:04", "aa:00:00:00:00:0b"],
    )


def _network_config_sins(s: Snapshot) -> None:
    """Address space that collides with itself, and a subnet with no room."""
    by_id = {n.id: n for n in s.config.networks}
    lab = by_id["net4"]
    lab.ipv4_configuration.host_ip_address = "192.168.1.129"  # inside Default's /24
    lab.ipv4_configuration.prefix_length = 25
    lab.vlan_id = 30  # already Guest's tag
    by_id["net2"].ipv4_configuration.prefix_length = 29


def _dhcp_pool_pressure(s: Snapshot) -> None:
    """Squeeze the default network until the addresses in use fill it.

    The demo site is a /24 with ten clients, which no threshold will ever trip.
    Narrowing it to a /29 around the addresses those clients already hold is the
    smallest change that produces a genuinely full pool.
    """
    net = next(n for n in s.config.networks if n.id == "net1")
    net.ipv4_configuration.host_ip_address = "192.168.1.104"
    net.ipv4_configuration.prefix_length = 29  # .105-.110 assignable
    for client, ip in zip(
        [c for c in s.clients if c.type == "WIRED"],
        ["192.168.1.108", "192.168.1.109", "192.168.1.110"],
    ):
        client.ip_address = ip


def _no_config_plane(s: Snapshot) -> None:
    """A console too old for the config endpoints, or a key refused them."""
    s.config = None


def _series(metric: str, subject_id: str | None, name: str, values: list[float]) -> Series:
    """A metric series ending at BASE_TIME, one scan a day, oldest first."""
    last = len(values) - 1
    return Series(
        metric=metric, subject_id=subject_id, subject_name=name,
        points=[
            Point(at=BASE_TIME - timedelta(days=last - i), value=value)
            for i, value in enumerate(values)
        ],
    )


# One history covering all four shapes the trend rules look for: a spike that
# has no history behind it, a step that held, a climb with a ceiling ahead of
# it, and a slow decline.
_TREND_HISTORY = RunHistory(series=[
    _series("device.cpu_pct", "gw1", "Dream Machine Pro",
            [20, 21, 19, 22, 20, 21, 20, 19, 21, 85]),
    _series("radio.tx_retries_pct", "ap1:2.4", "Living Room AP 2.4 GHz",
            [4, 4.5, 4, 5, 4.2, 14, 14.5, 13.8, 15, 14.2, 14, 15.1]),
    _series("device.mem_pct", "sw1", "Rack Switch",
            [70, 72, 74, 76, 78, 80, 82, 84, 86, 88]),
    _series("site.health_score", None, "Home",
            [80, 79, 78, 77, 76, 74, 73, 72, 70, 69, 68, 66]),
])


SCENARIOS: list[Scenario] = [
    Scenario("demo_baseline"),
    Scenario("degraded_and_pending", _degraded_and_pending),
    Scenario("unsupported_device", _unsupported_device),
    Scenario("memory_load_recent_reboot", _memory_load_reboot),
    Scenario("stale_uptime", _stale_uptime),
    Scenario("reboot_loop", _reboot_loop, _REBOOT_HISTORY),
    Scenario("narrow_5_width", _narrow_5_width),
    Scenario("narrow_5_width_dense_stays_quiet", _narrow_5_width_dense),
    Scenario("legacy_radio_standard", _legacy_radio_standard),
    Scenario("legacy_radio_standard_5ghz", _legacy_radio_standard_5ghz),
    Scenario("mesh_two_hops", _mesh_two_hops),
    Scenario("mesh_topology_cycle", _mesh_cycle),
    Scenario("firmware_drift_cleared", _firmware_drift_cleared),
    Scenario("retries_worsening", None, _RETRIES_HISTORY),
    Scenario("disabled_radio_channel_zero", _disabled_radio_channel_zero),
    Scenario("gateway_saturation", _gateway_saturation),
    Scenario("no_wireless_clients", _no_wireless_clients),
    Scenario("rf_weak_and_roaming", _rf_weak_and_roaming),
    Scenario("rf_slow_band_load", _rf_slow_band_load),
    Scenario("graded_upper_band", _graded_upper_band),
    Scenario("graded_lower_band", _graded_lower_band),
    Scenario("wan_degraded", _wan_bad, _WAN_HISTORY),
    Scenario("wan_healthy_stays_quiet", _wan_healthy),
    Scenario("dns_spike", _dns_bad, _DNS_HISTORY),
    Scenario("all_enrichments_healthy", _dns_healthy),
    Scenario("wifi_config_sins", _wifi_config_sins),
    Scenario("network_config_sins", _network_config_sins),
    Scenario("dhcp_pool_pressure", _dhcp_pool_pressure),
    Scenario("no_config_plane", _no_config_plane),
    Scenario("metric_trends", None, _TREND_HISTORY),
]


async def build_snapshot(scenario: Scenario) -> Snapshot:
    """A fresh demo snapshot with this scenario's mutation applied."""
    from app.demo import DemoUnifiClient

    snapshot = await collect_snapshot(DemoUnifiClient())
    if scenario.mutate is not None:
        scenario.mutate(snapshot)
    return snapshot


def finding_dict(f) -> dict:
    """Every field that reaches the database, in a stable key order."""
    return {
        "rule_id": f.rule_id,
        "severity": str(f.severity),
        "category": str(f.category),
        "title": f.title,
        "summary": f.summary,
        "evidence": f.evidence,
        "recommendation": f.recommendation,
        "subject_type": f.subject_type,
        "subject_id": f.subject_id,
        "subject_name": f.subject_name,
    }


def sort_key(d: dict) -> tuple:
    """Total order over findings, independent of rule registration order."""
    return (d["rule_id"], d["subject_id"] or "", d["title"])


async def collect_all() -> dict:
    """Run every scenario and return the full serialisable result."""
    from app.rules import run_rules

    out: dict[str, dict] = {}
    for scenario in SCENARIOS:
        snapshot = await build_snapshot(scenario)
        findings, unsupported = run_rules(snapshot, scenario.history)
        out[scenario.name] = {
            # Emission order is user-visible: run_rules sorts by severity only,
            # and that sort is stable, so within a severity band the order is
            # the rule registration order. Pinned separately from the sorted
            # bodies so a reordering shows up as a one-line diff.
            "order": [f.rule_id for f in findings],
            "findings": sorted((finding_dict(f) for f in findings), key=sort_key),
            "unsupported": sorted(u.rule_id for u in unsupported),
        }
    return out
