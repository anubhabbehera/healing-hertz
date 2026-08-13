import anthropic
import httpx
import pytest

from app.advisor.llm import generate_advice
from app.advisor.prompts import PAYLOAD_CHAR_BUDGET, build_payload
from app.advisor.schema import AdviceItem, AdvicePlan
from app.config import get_settings
from app.rules import run_rules
from app.rules.base import Category, Finding, RunHistory, Severity

PLAN = AdvicePlan(
    overall_assessment="Network is mostly healthy with a few RF issues.",
    items=[AdviceItem(priority=1, title="Fix channel plan",
                      related_rule_ids=["wifi.channel_overlap"],
                      rationale="Two APs share channel 6.",
                      steps=["Open Radios page", "Set Patio AP to channel 11"],
                      effort="low")],
    quick_wins=["Update firmware"],
)


class FakeMessages:
    def __init__(self, error=None):
        self.error = error

    async def parse(self, **kwargs):
        if self.error:
            raise self.error
        return type("Resp", (), {"parsed_output": PLAN})()


class FakeAnthropic:
    error = None

    def __init__(self, **kwargs):
        self.messages = FakeMessages(error=type(self).error)

    async def close(self):
        pass


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    get_settings.cache_clear()


async def test_no_key_skips(snapshot):
    findings, _ = run_rules(snapshot)
    plan, status, _error = await generate_advice(findings, snapshot, RunHistory(), get_settings())
    assert plan is None and status == "skipped"


async def test_success(snapshot, with_key, monkeypatch):
    FakeAnthropic.error = None
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    findings, _ = run_rules(snapshot)
    plan, status, _error = await generate_advice(findings, snapshot, RunHistory(), get_settings())
    assert status == "ok"
    assert plan.items[0].title == "Fix channel plan"


async def test_api_error_degrades(snapshot, with_key, monkeypatch):
    FakeAnthropic.error = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    findings, _ = run_rules(snapshot)
    plan, status, _error = await generate_advice(findings, snapshot, RunHistory(), get_settings())
    assert plan is None and status == "failed"


async def test_payload_stays_under_budget(snapshot):
    # Synthesize a large finding set: many LOW findings with bulky evidence
    findings = [
        Finding(
            rule_id=f"test.bulk_{sev.value}",
            severity=sev,
            category=Category.DEVICE_HEALTH,
            title=f"Bulk finding {i}",
            summary="x" * 200,
            evidence={"blob": "y" * 500, "index": i},
            recommendation="z" * 200,
            subject_type="device",
            subject_id=f"dev{i}",
            subject_name=f"Device {i}",
        )
        for i in range(300)
        for sev in (Severity.LOW, Severity.MEDIUM)
    ]
    payload = build_payload(findings, snapshot, RunHistory())
    assert len(payload) <= PAYLOAD_CHAR_BUDGET


class FakeGatewayAnthropic:
    """Rejects output_format (like most gateways), accepts plain create."""

    last_base_url = None

    def __init__(self, **kwargs):
        type(self).last_base_url = kwargs.get("base_url")
        self.messages = self

    async def parse(self, **kwargs):
        req = httpx.Request("POST", "https://gateway.local/v1/messages")
        raise anthropic.BadRequestError(
            "output_format is not supported",
            response=httpx.Response(400, request=req),
            body=None,
        )

    async def create(self, **kwargs):
        text = PLAN.model_dump_json()
        block = type("Block", (), {"type": "text", "text": f"```json\n{text}\n```"})()
        return type("Resp", (), {"content": [block]})()

    async def close(self):
        pass


async def test_custom_base_url_falls_back_to_plain_json(snapshot, with_key, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.local")
    get_settings.cache_clear()
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeGatewayAnthropic)
    findings, _ = run_rules(snapshot)
    plan, status, _error = await generate_advice(findings, snapshot, RunHistory(), get_settings())
    assert status == "ok"
    assert plan.items[0].title == "Fix channel plan"
    assert FakeGatewayAnthropic.last_base_url == "https://gateway.local"


async def test_official_api_does_not_fall_back(snapshot, with_key, monkeypatch):
    # No base_url: a 400 from the official API is a real error, not a compat issue
    FakeAnthropic.error = anthropic.BadRequestError(
        "bad request",
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com")),
        body=None,
    )
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    findings, _ = run_rules(snapshot)
    plan, status, _error = await generate_advice(findings, snapshot, RunHistory(), get_settings())
    assert plan is None and status == "failed"


async def test_payload_is_sanitized_and_compact(snapshot):
    from app.advisor.prompts import IPV4_RE, MAC_RE
    from app.integrations.legacy_unifi import ClientRF, RfSnapshot

    snapshot.rf = RfSnapshot(
        clients=[ClientRF(mac="aa:bb:cc:dd:ee:01", name="Personal-Phone",
                          ap_mac="aa:bb:cc:dd:ee:ff", essid="HomeWifi",
                          signal_dbm=-88, tx_rate_kbps=None, rx_rate_kbps=None)],
        roam_counts={"Personal-Phone": 15},
        roam_data_available=True,
    )
    findings, _ = run_rules(snapshot)
    payload = build_payload(findings, snapshot, RunHistory())

    # No network addresses leave the machine
    assert not MAC_RE.search(payload)
    assert not IPV4_RE.search(payload)
    # No client hostnames or SSIDs; pseudonyms instead
    assert "Personal-Phone" not in payload
    assert "HomeWifi" not in payload
    assert "Guest Phone" not in payload  # demo fixture guest client name
    assert "client-1" in payload
    # Infrastructure names are deliberately kept
    assert "Office AP" in payload
    # Compact text, not JSON
    assert not payload.lstrip().startswith("{")
    assert "FINDINGS" in payload and "RADIOS" in payload


async def test_ap_names_not_pseudonymized_in_overlap_evidence(snapshot):
    findings, _ = run_rules(snapshot)
    payload = build_payload(findings, snapshot, RunHistory())
    # 5 GHz overlap (Living Room AP + Patio AP on ch149) must keep AP names
    assert "ap=Living Room AP" in payload
    assert "client-" not in payload  # no rf enrichment here -> no pseudonyms at all
