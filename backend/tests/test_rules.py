from datetime import UTC, datetime, timedelta

from app.rules import health_score, run_rules
from app.rules.base import HistoricalRun, RunHistory, Severity


def rule_ids(findings):
    return {f.rule_id for f in findings}


async def test_demo_snapshot_trips_seeded_rules(snapshot):
    findings, unsupported = run_rules(snapshot)
    ids = rule_ids(findings)
    assert "device.offline" in ids            # Garage AP OFFLINE
    assert "device.high_cpu" in ids           # gateway at 93%
    assert "firmware.update_available" in ids  # sw2 + ap2
    assert "wifi.bad_24_channel" in ids       # Office AP on channel 3
    assert "wifi.wide_24_width" in ids        # Office AP 40 MHz on 2.4
    assert "wifi.channel_overlap" in ids      # ap2 + ap4 share channel 6
    assert "wifi.high_retries" in ids         # Office AP 22.4% retries
    assert "wifi.dfs_channel" in ids          # Office AP 5 GHz on channel 100
    assert "wifi.mesh_uplink" in ids          # Patio AP uplinks via Living Room AP
    assert "firmware.version_drift" in ids    # ap2 on 6.6.50, ap1/ap4 on 6.6.55
    assert "wired.uplink_negotiation" in ids  # Office AP 100 Mbps link
    assert "wired.poe_limited" in ids         # Rack Switch port 5
    assert "clients.unauthorized_guests" in ids
    # Non-triggering rules stay quiet
    assert "device.reboot_loop" not in ids
    assert "site.gateway_saturation" not in ids
    assert "clients.none_on_ap" not in ids
    # Unsupported checks are declared
    assert {u.rule_id for u in unsupported} >= {"wan.latency_loss", "wifi.weak_rssi_clients"}


async def test_severities(snapshot):
    findings, _ = run_rules(snapshot)
    by_id = {f.rule_id: f for f in findings}
    assert by_id["device.offline"].severity == Severity.CRITICAL
    assert by_id["device.high_cpu"].severity == Severity.HIGH
    assert by_id["wired.uplink_negotiation"].severity == Severity.HIGH
    assert by_id["wifi.high_retries"].severity == Severity.MEDIUM  # 22.4% < 30


async def test_health_score(snapshot):
    findings, _ = run_rules(snapshot)
    score = health_score(findings)
    assert 0 <= score < 100
    assert health_score([]) == 100


async def test_narrow_5ghz_width_only_flagged_when_channels_are_free(snapshot):
    # Three online APs broadcast on 5 GHz; at that density 80 MHz is the right
    # call and a 40 MHz radio is leaving throughput on the table.
    snapshot.device_details["ap1"].interfaces.radios[1].channel_width_mhz = 40
    findings, _ = run_rules(snapshot)
    assert "wifi.narrow_5_width" in rule_ids(findings)

    # Add a fourth 5 GHz radio and the band is busy enough that narrow channels
    # are a deliberate trade, not a mistake.
    from app.unifi.models import Radio

    snapshot.device_details["ap2"].interfaces.radios.append(
        Radio(wlanStandard="802.11ax", frequencyGHz=5, channelWidthMHz=80, channel=36)
    )
    findings, _ = run_rules(snapshot)
    assert "wifi.narrow_5_width" not in rule_ids(findings)


async def test_mesh_hop_depth_escalates_severity(snapshot):
    # ap4 -> ap2 is one hop (already in the fixtures); chain ap2 -> ap1 to make
    # ap4 two hops from anything wired.
    snapshot.device_details["ap2"].uplink.device_id = "ap1"
    findings, _ = run_rules(snapshot)
    mesh = {f.subject_id: f for f in findings if f.rule_id == "wifi.mesh_uplink"}
    assert mesh["ap2"].severity == Severity.MEDIUM  # one hop, parent is wired
    assert mesh["ap4"].severity == Severity.HIGH    # two hops
    assert mesh["ap4"].evidence["wirelessHops"] == 2


async def test_mesh_uplink_survives_a_topology_cycle(snapshot):
    # A controller that reports each AP as the other's uplink must not hang the
    # scan; the walk stops as soon as it revisits a device.
    snapshot.device_details["ap2"].uplink.device_id = "ap4"
    findings, _ = run_rules(snapshot)
    assert {f.subject_id for f in findings if f.rule_id == "wifi.mesh_uplink"} == {"ap2", "ap4"}


async def test_legacy_radio_standard_flags_pre_n_only(snapshot):
    findings, _ = run_rules(snapshot)
    assert "wifi.legacy_radio_standard" not in rule_ids(findings)  # 11ac/11ax fleet

    snapshot.device_details["ap1"].interfaces.radios[0].wlan_standard = "802.11g"
    findings, _ = run_rules(snapshot)
    legacy = [f for f in findings if f.rule_id == "wifi.legacy_radio_standard"]
    assert [f.subject_id for f in legacy] == ["ap1"]


async def test_stale_uptime(snapshot):
    findings, _ = run_rules(snapshot)
    assert "device.stale_uptime" not in rule_ids(findings)  # fixtures top out at 30d

    snapshot.device_stats["gw1"].uptime_sec = 200 * 86400
    findings, _ = run_rules(snapshot)
    stale = next(f for f in findings if f.rule_id == "device.stale_uptime")
    assert stale.severity == Severity.LOW
    assert "200 days" in stale.title


async def test_firmware_drift_ignores_offline_and_single_version(snapshot):
    drift = next(f for f in run_rules(snapshot)[0] if f.rule_id == "firmware.version_drift")
    # ap3 is OFFLINE on 6.6.50 — its version must not count as drift.
    assert set(drift.evidence["versions"]) == {"6.6.50", "6.6.55"}
    assert "Garage AP" not in str(drift.evidence)

    for dev in snapshot.devices:
        if "accessPoint" in dev.features:
            dev.firmware_version = "6.6.55"
    assert "firmware.version_drift" not in rule_ids(run_rules(snapshot)[0])


async def test_reboot_loop_needs_history(snapshot):
    # Force low uptime on the gateway in the current snapshot
    snapshot.device_stats["gw1"].uptime_sec = 1200
    now = datetime.now(UTC)
    history = RunHistory(runs=[
        HistoricalRun(run_id="r2", started_at=now - timedelta(hours=6),
                      device_uptimes={"gw1": 900}),
        HistoricalRun(run_id="r1", started_at=now - timedelta(hours=30),
                      device_uptimes={"gw1": 3000}),
    ])
    findings, _ = run_rules(snapshot, history)
    assert "device.reboot_loop" in rule_ids(findings)


async def test_retries_worsening(snapshot):
    history = RunHistory(runs=[
        HistoricalRun(run_id="r1", started_at=datetime.now(UTC),
                      radio_retries={"ap1:2.4": 5.0}),
    ])
    findings, _ = run_rules(snapshot, history)  # current ap1 2.4 GHz is 22.4%
    assert "wifi.retries_worsening" in rule_ids(findings)


async def test_disabled_radio_channel_zero_not_flagged(snapshot):
    # A disabled 2.4 GHz radio is reported as channel 0 by the Integration API
    # (e.g. U7-Pro-Wall with 2.4 GHz turned off). It must trigger nothing —
    # not bad-channel, not wide-width, not overlap.
    from app.unifi.models import Radio

    detail = snapshot.device_details["ap2"]  # Living Room AP, currently clean on 2.4
    detail.interfaces.radios.append(
        Radio(wlanStandard="802.11ax", frequencyGHz=2.4, channelWidthMHz=40, channel=0)
    )
    findings, _ = run_rules(snapshot)
    for f in findings:
        if f.subject_id == "ap2" or "Living Room" in str(f.evidence):
            assert f.rule_id not in ("wifi.bad_24_channel", "wifi.wide_24_width"), f
    # And the disabled radio must not reach the LLM payload
    from app.advisor.prompts import build_payload
    from app.rules.base import RunHistory

    payload = build_payload(findings, snapshot, RunHistory())
    assert "ch0 " not in payload and "channel 0" not in payload
