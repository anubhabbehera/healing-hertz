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
    assert "wired.uplink_negotiation" in ids  # Patio AP 100 Mbps link
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
