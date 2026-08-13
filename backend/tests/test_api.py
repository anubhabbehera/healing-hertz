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


async def run_scan_and_wait(api, timeout=15.0):
    resp = await api.post("/api/scans")
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        status = (await api.get(f"/api/scans/{run_id}")).json()
        if status["status"] in ("completed", "failed"):
            return run_id, status
        assert asyncio.get_event_loop().time() < deadline, "scan timed out"
        await asyncio.sleep(0.05)


async def test_health(api):
    resp = await api.get("/api/health")
    assert resp.json() == {"status": "ok"}


async def test_scan_lifecycle_and_reports(api):
    run_id, status = await run_scan_and_wait(api)
    assert status["status"] == "completed"

    detail = (await api.get(f"/api/runs/{run_id}")).json()
    assert detail["health_score"] < 100
    assert detail["advice_status"] == "skipped"
    assert any(f["rule_id"] == "device.offline" for f in detail["findings"])
    assert detail["unsupported_checks"]

    latest = (await api.get("/api/runs/latest")).json()
    assert latest["id"] == run_id

    runs = (await api.get("/api/runs")).json()
    assert len(runs) == 1


async def test_trends_and_compare_across_two_runs(api):
    run_a, _ = await run_scan_and_wait(api)
    run_b, _ = await run_scan_and_wait(api)

    trends = (await api.get("/api/trends", params={"metric": "site.health_score"})).json()
    assert len(trends["points"]) == 2

    diff = (await api.get("/api/runs/compare", params={"a": run_a, "b": run_b})).json()
    assert diff["new"] == []
    assert diff["resolved"] == []
    assert len(diff["persisting"]) > 5


async def test_settings_masked(api):
    settings = (await api.get("/api/settings")).json()
    assert settings["demo_mode"] is True
    assert settings["anthropic_api_key_set"] is False
    assert "unifi_api_key" not in settings
