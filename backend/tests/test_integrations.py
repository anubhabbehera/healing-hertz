from datetime import UTC, datetime

import httpx
import respx

from app.integrations.legacy_unifi import ClientRF, LegacyUnifiClient, RfSnapshot
from app.integrations.nextdns import DnsSnapshot, NextDnsClient
from app.integrations.wan_probe import WanProbeResult, aggregate
from app.rules import run_rules
from app.rules.base import HistoricalRun, RunHistory


def rule_ids(findings):
    return {f.rule_id for f in findings}


# --- WAN probe aggregation ---

def test_wan_aggregate_healthy():
    result = aggregate({"1.1.1.1": [10.0, 12.0, 11.0], "8.8.8.8": [14.0, 13.0, 15.0]})
    assert result.loss_pct == 0
    assert 10 <= result.latency_ms <= 15


def test_wan_aggregate_with_failures():
    result = aggregate({"1.1.1.1": [10.0, None, 12.0, None]})
    assert result.loss_pct == 50.0
    assert result.per_target["1.1.1.1"]["failed"] == 2


# --- NextDNS client (mocked HTTP) ---

@respx.mock
async def test_nextdns_collect():
    base = "https://api.nextdns.io/profiles/abc123"
    respx.get(f"{base}/analytics/status").mock(return_value=httpx.Response(200, json={
        "data": [{"status": "default", "queries": 800},
                 {"status": "blocked", "queries": 200}],
    }))
    respx.get(f"{base}/analytics/domains").mock(return_value=httpx.Response(200, json={
        "data": [{"domain": "ads.example.com", "queries": 120}],
    }))
    respx.get(f"{base}/analytics/reasons").mock(return_value=httpx.Response(200, json={
        "data": [
            {"id": "blocklist:ads", "name": "Ads & Trackers", "queries": 180},
            {"id": "security:threat-intelligence-feeds",
             "name": "Threat Intelligence Feeds", "queries": 7},
        ],
    }))
    client = NextDnsClient("key", "abc123")
    dns = await client.collect()
    await client.aclose()
    assert dns.queries == 1000
    assert dns.blocked == 200
    assert dns.blocked_pct == 20.0
    assert dns.security_block_count == 7
    assert dns.top_blocked[0]["domain"] == "ads.example.com"


# --- Legacy UniFi client (mocked HTTP) ---

@respx.mock
async def test_legacy_client_rf_and_roams():
    respx.post("https://gw.local:443/api/auth/login").mock(
        return_value=httpx.Response(200, headers={"x-csrf-token": "tok"})
    )
    respx.get("https://gw.local:443/proxy/network/api/s/default/stat/sta").mock(
        return_value=httpx.Response(200, json={"data": [
            {"mac": "aa:1", "hostname": "phone", "signal": -82, "essid": "Home",
             "ap_mac": "ap:1", "is_wired": False},
            {"mac": "aa:2", "name": "nas", "is_wired": True},
            {"mac": "aa:3", "hostname": "cam", "rssi": 15, "is_wired": False},
        ]})
    )
    respx.post("https://gw.local:443/proxy/network/api/s/default/stat/event").mock(
        return_value=httpx.Response(200, json={"data": [
            {"key": "EVT_WU_Roam", "hostname": "phone"},
            {"key": "EVT_WU_Roam", "hostname": "phone"},
            {"key": "EVT_AP_Restarted", "hostname": "ap"},
        ]})
    )
    client = LegacyUnifiClient("gw.local", "ro-admin", "pw")
    rf = await client.collect("default")
    await client.aclose()
    assert len(rf.clients) == 2  # wired client excluded
    phone = next(c for c in rf.clients if c.name == "phone")
    assert phone.signal_dbm == -82
    cam = next(c for c in rf.clients if c.name == "cam")
    assert cam.signal_dbm == 15 - 95  # derived from positive rssi
    assert rf.roam_counts == {"phone": 2}


# --- Rules over enrichment data ---

async def test_rf_rules_and_unsupported_shrinks(snapshot):
    findings, unsupported = run_rules(snapshot)
    assert {u["rule_id"] if isinstance(u, dict) else u.rule_id for u in unsupported} == {
        "wifi.weak_rssi_clients", "clients.excessive_roaming",
        "wan.latency_loss", "dns.anomalies",
    }

    snapshot.rf = RfSnapshot(
        clients=[
            ClientRF(mac="aa:1", name="patio-cam", ap_mac=None, essid="Home",
                     signal_dbm=-88, tx_rate_kbps=None, rx_rate_kbps=None),
            ClientRF(mac="aa:2", name="laptop", ap_mac=None, essid="Home",
                     signal_dbm=-55, tx_rate_kbps=None, rx_rate_kbps=None),
        ],
        roam_counts={"flappy-phone": 14},
        roam_data_available=True,
    )
    findings, unsupported = run_rules(snapshot)
    ids = rule_ids(findings)
    assert "wifi.weak_rssi_clients" in ids
    assert "clients.excessive_roaming" in ids
    remaining = {u.rule_id for u in unsupported}
    assert "wifi.weak_rssi_clients" not in remaining
    assert "clients.excessive_roaming" not in remaining


async def test_wan_rules(snapshot):
    snapshot.wan = WanProbeResult(latency_ms=170.0, jitter_ms=40.0, loss_pct=12.0,
                                  samples=15, per_target={})
    history = RunHistory(runs=[HistoricalRun(
        run_id="r1", started_at=datetime.now(UTC),
        site_metrics={"wan.latency_ms": 20.0},
    )])
    findings, unsupported = run_rules(snapshot, history)
    ids = rule_ids(findings)
    assert "wan.latency_loss" in ids
    assert "wan.latency_worsening" in ids
    assert "wan.latency_loss" not in {u.rule_id for u in unsupported}


async def test_dns_rules(snapshot):
    snapshot.dns = DnsSnapshot(
        queries=1000, blocked=450,
        top_blocked=[{"domain": "tracker.evil", "queries": 300}],
        security_blocks=[{"reason": "Threat Intelligence Feeds", "queries": 5}],
    )
    history = RunHistory(runs=[HistoricalRun(
        run_id="r1", started_at=datetime.now(UTC),
        site_metrics={"dns.blocked_pct": 12.0},
    )])
    findings, unsupported = run_rules(snapshot, history)
    ids = rule_ids(findings)
    assert "dns.security_blocks" in ids
    assert "dns.anomalies" in ids  # spike (12% -> 45%) and high-ratio
    assert "dns.anomalies" not in {u.rule_id for u in unsupported}


async def test_healthy_enrichments_stay_quiet(snapshot):
    snapshot.wan = WanProbeResult(latency_ms=12.0, jitter_ms=2.0, loss_pct=0.0,
                                  samples=15, per_target={})
    snapshot.dns = DnsSnapshot(queries=1000, blocked=80)
    snapshot.rf = RfSnapshot(clients=[], roam_counts={}, roam_data_available=True)
    findings, unsupported = run_rules(snapshot)
    ids = rule_ids(findings)
    assert not ids & {"wan.latency_loss", "dns.anomalies", "dns.security_blocks",
                      "wifi.weak_rssi_clients", "clients.excessive_roaming"}
    assert unsupported == []
