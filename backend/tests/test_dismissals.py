import asyncio

import httpx
import pytest

from app.main import create_app


@pytest.fixture
async def api(db):
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def run_scan(api, timeout=15.0):
    run_id = (await api.post("/api/scans")).json()["run_id"]
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        status = (await api.get(f"/api/scans/{run_id}")).json()
        if status["status"] in ("completed", "failed"):
            return run_id
        assert asyncio.get_event_loop().time() < deadline, "scan timed out"
        await asyncio.sleep(0.05)


async def test_dismissing_a_finding_raises_the_score(api):
    await run_scan(api)
    before = (await api.get("/api/runs/latest")).json()
    offline = next(f for f in before["findings"] if f["rule_id"] == "device.offline")
    assert offline["dismissed"] is False

    resp = await api.post("/api/dismissals", json={
        "rule_id": offline["rule_id"],
        "subject_id": offline["subject_id"],
        "subject_name": offline["subject_name"],
        "title": offline["title"],
        "reason": "Garage AP is unplugged on purpose",
    })
    assert resp.status_code == 201

    after = (await api.get("/api/runs/latest")).json()
    # device.offline is CRITICAL = 25 points
    assert after["health_score"] == before["health_score"] + 25
    dismissed = next(f for f in after["findings"] if f["rule_id"] == "device.offline")
    assert dismissed["dismissed"] is True, "finding should remain visible, just not counted"


async def test_restore_lowers_the_score_again(api):
    await run_scan(api)
    before = (await api.get("/api/runs/latest")).json()
    finding = next(f for f in before["findings"] if f["rule_id"] == "device.high_cpu")

    created = (await api.post("/api/dismissals", json={
        "rule_id": finding["rule_id"], "subject_id": finding["subject_id"],
    })).json()
    raised = (await api.get("/api/runs/latest")).json()["health_score"]
    assert raised == before["health_score"] + 10  # HIGH = 10

    assert (await api.delete(f"/api/dismissals/{created['id']}")).status_code == 204
    restored = (await api.get("/api/runs/latest")).json()
    assert restored["health_score"] == before["health_score"]
    assert next(
        f for f in restored["findings"] if f["rule_id"] == "device.high_cpu"
    )["dismissed"] is False


async def test_dismissal_applies_to_later_scans(api):
    await run_scan(api)
    first = (await api.get("/api/runs/latest")).json()
    await api.post("/api/dismissals", json={
        "rule_id": "device.offline",
        "subject_id": next(
            f["subject_id"] for f in first["findings"] if f["rule_id"] == "device.offline"
        ),
    })

    await run_scan(api)  # a fresh scan must honour the standing dismissal
    latest = (await api.get("/api/runs/latest")).json()
    offline = next(f for f in latest["findings"] if f["rule_id"] == "device.offline")
    assert offline["dismissed"] is True
    assert latest["health_score"] == first["health_score"] + 25


async def test_site_wide_dismissal_covers_every_subject(api):
    await run_scan(api)
    before = (await api.get("/api/runs/latest")).json()
    firmware = [f for f in before["findings"] if f["rule_id"] == "firmware.update_available"]
    assert len(firmware) >= 2, "fixture should have multiple firmware findings"

    # subject_id omitted -> dismiss the rule for all subjects
    await api.post("/api/dismissals", json={"rule_id": "firmware.update_available"})

    after = (await api.get("/api/runs/latest")).json()
    assert all(
        f["dismissed"]
        for f in after["findings"]
        if f["rule_id"] == "firmware.update_available"
    )
    assert after["health_score"] == before["health_score"] + 4 * len(firmware)  # MEDIUM = 4


async def test_trend_metric_is_rescored_too(api):
    await run_scan(api)
    await api.post("/api/dismissals", json={"rule_id": "device.offline"})
    latest = (await api.get("/api/runs/latest")).json()
    trends = (await api.get("/api/trends", params={"metric": "site.health_score"})).json()
    assert trends["points"][-1]["value"] == latest["health_score"], (
        "trend line must match the re-scored run, not the original score"
    )


async def test_duplicate_dismissal_is_idempotent(api):
    await run_scan(api)
    body = {"rule_id": "device.offline", "subject_id": "ap3"}
    first = (await api.post("/api/dismissals", json=body)).json()
    second = (await api.post("/api/dismissals", json=body)).json()
    assert first["id"] == second["id"]
    assert len((await api.get("/api/dismissals")).json()) == 1


async def test_delete_unknown_dismissal_404s(api):
    assert (await api.delete("/api/dismissals/9999")).status_code == 404


async def test_dismissed_excluded_from_severity_counts(api):
    await run_scan(api)
    before = (await api.get("/api/runs/latest")).json()
    assert before["dismissed_count"] == 0
    critical_before = before["severity_counts"].get("critical", 0)
    assert critical_before == 1

    offline = next(f for f in before["findings"] if f["rule_id"] == "device.offline")
    await api.post("/api/dismissals", json={
        "rule_id": offline["rule_id"], "subject_id": offline["subject_id"],
    })

    after = (await api.get("/api/runs/latest")).json()
    # The dashboard's severity tiles read these counts; leaving the dismissed
    # finding in would contradict the health score displayed beside them.
    assert after["severity_counts"].get("critical", 0) == 0
    assert after["dismissed_count"] == 1
    # …but the finding itself is still returned, flagged, for the Findings page.
    assert any(f["rule_id"] == "device.offline" and f["dismissed"] for f in after["findings"])


async def test_suggestions_for_dismissed_findings_are_hidden(api, db):
    """Advice written before a dismissal must stop recommending waived work."""
    from app.db import repo
    from app.db.engine import get_session_factory

    await run_scan(api)
    run_id = (await api.get("/api/runs/latest")).json()["id"]

    # Stand in for an LLM plan: one suggestion about a rule we will dismiss,
    # one about a rule we keep, and one piece of general advice.
    factory = get_session_factory()
    async with factory() as session:
        run = await repo.get_run(session, run_id)
        from app.db.models import SuggestionRow
        session.add_all([
            SuggestionRow(run_id=run.id, priority=1, title="Fix the offline AP",
                          rationale="…", steps_json='["a"]', effort="low",
                          related_rule_ids_json='["device.offline"]'),
            SuggestionRow(run_id=run.id, priority=2, title="Fix the channel plan",
                          rationale="…", steps_json='["b"]', effort="low",
                          related_rule_ids_json='["wifi.bad_24_channel"]'),
            SuggestionRow(run_id=run.id, priority=3, title="General hygiene",
                          rationale="…", steps_json='["c"]', effort="low",
                          related_rule_ids_json='[]'),
        ])
        await session.commit()

    def titles(detail):
        return {s["title"] for s in detail["suggestions"]}

    assert titles((await api.get("/api/runs/latest")).json()) == {
        "Fix the offline AP", "Fix the channel plan", "General hygiene",
    }

    await api.post("/api/dismissals", json={"rule_id": "device.offline"})

    after = titles((await api.get("/api/runs/latest")).json())
    assert "Fix the offline AP" not in after, "advice for a dismissed finding must be hidden"
    assert "Fix the channel plan" in after, "unrelated advice must survive"
    assert "General hygiene" in after, "advice with no rule reference is always kept"


async def test_advisor_is_not_given_dismissed_findings(api, snapshot, monkeypatch):
    """Root cause: the advisor used to receive dismissed findings and write
    plans for problems the operator had already waived."""
    import app.scan.orchestrator as orch

    seen: list[list] = []

    async def spy(findings, snap, history, settings):
        seen.append(list(findings))
        return None, "skipped", None

    monkeypatch.setattr(orch, "generate_advice", spy)

    await run_scan(api)
    assert seen and any(f.rule_id == "device.offline" for f in seen[0])

    await api.post("/api/dismissals", json={"rule_id": "device.offline"})
    seen.clear()
    await run_scan(api)

    assert seen, "advisor should still be called"
    assert all(not f.dismissed for f in seen[0]), "dismissed findings must not be sent"
    assert not any(f.rule_id == "device.offline" for f in seen[0])
