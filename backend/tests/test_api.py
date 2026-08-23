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


# --- rules catalog ---------------------------------------------------------

# The same example the loader tests use, so endpoint and loader stay pinned to
# one definition of "a valid user rule".
from tests.test_user_rules import GOOD_RULE


async def test_list_rules(api):
    body = (await api.get("/api/rules")).json()

    ids = {r["id"] for r in body["rules"]}
    assert "wifi.dfs_channel" in ids
    assert "wifi.weak_rssi_clients" in ids            # a not-checkable entry
    assert body["counts"]["active"] == 47
    assert body["counts"]["not_checkable"] == 12
    assert len(body["sources"]) == 10
    assert body["categories"]


async def test_rules_payload_carries_no_internals(api):
    """A Source holds a callable and a frozenset; neither may reach the wire."""
    import json

    body = (await api.get("/api/rules")).json()
    blob = json.dumps(body)
    assert "iterate" not in blob
    assert "<function" not in blob
    for source in body["sources"]:
        assert source["bindings"] == sorted(source["bindings"])


async def test_predicate_uses_the_yaml_field_names(api):
    """`not` is aliased from `negate`; the API must speak the YAML's language."""
    import json

    blob = json.dumps((await api.get("/api/rules")).json())
    assert '"negate"' not in blob


async def test_validate_accepts_a_good_draft(api):
    body = (await api.post("/api/rules/validate", json={"yaml": GOOD_RULE})).json()
    assert body["ok"] is True
    assert body["errors"] == []
    assert [r["id"] for r in body["rules"]] == ["custom.spare_port"]
    # Ran against the bundled fixtures, not the operator's network.
    assert body["preview"]["basis"] == "demo_fixtures"


async def test_validate_rejects_a_user_rule_naming_python(api):
    draft = """
rules:
  - id: custom.sneaky
    kind: python
    impl: app.rules.wifi:MeshUplink
    category: wifi
    provides: [device_name]
    emits:
      - severity: low
        title: "{device_name}"
        summary: s
        recommendation: r
"""
    body = (await api.post("/api/rules/validate", json={"yaml": draft})).json()
    assert body["ok"] is False
    assert "only allowed in the built-in catalog" in body["errors"][0]["message"]


async def test_validate_rejects_an_unnamespaced_id(api):
    draft = GOOD_RULE.replace("custom.spare_port", "wifi.spare_port")
    body = (await api.post("/api/rules/validate", json={"yaml": draft})).json()
    assert body["ok"] is False
    assert "must start with 'custom.'" in body["errors"][0]["message"]


async def test_validate_rejects_an_unknown_binding(api):
    draft = GOOD_RULE.replace("port_state", "prot_state")
    body = (await api.post("/api/rules/validate", json={"yaml": draft})).json()
    assert body["ok"] is False
    assert body["errors"][0]["stage"] == "compile"
    assert "prot_state" in body["errors"][0]["message"]


async def test_validate_reports_malformed_yaml(api):
    body = (await api.post("/api/rules/validate", json={"yaml": "rules: [ broken"})).json()
    assert body["ok"] is False
    assert body["errors"][0]["stage"] == "yaml"


async def test_validate_rejects_an_alias_bomb(api):
    """A length limit does not contain an alias bomb; the parser must refuse it."""
    body = (await api.post("/api/rules/validate", json={"yaml": "rules: &a [*a]"})).json()
    assert body["ok"] is False
    assert "aliases are not supported" in body["errors"][0]["message"]


async def test_validate_rejects_an_oversized_draft(api):
    resp = await api.post("/api/rules/validate", json={"yaml": "x" * 70_000})
    assert resp.status_code == 422


async def test_validate_returns_200_even_when_the_draft_is_bad(api):
    """The request is well-formed; the content is what's being reported on."""
    resp = await api.post("/api/rules/validate", json={"yaml": "rules: [ broken"})
    assert resp.status_code == 200


async def test_reload_picks_up_a_file_written_after_startup(api, tmp_path, monkeypatch):
    """The whole reason the endpoint exists: the catalog is cached."""
    from app.config import get_settings
    from app.rules.loader import load_catalog

    directory = tmp_path / "rules.d"
    directory.mkdir()
    monkeypatch.setenv("RULES_DIR", str(directory))
    get_settings.cache_clear()
    load_catalog.cache_clear()
    try:
        before = (await api.get("/api/rules")).json()
        assert "custom.spare_port" not in {r["id"] for r in before["rules"]}

        (directory / "mine.yaml").write_text(GOOD_RULE)
        # Deliberately no cache_clear() here — the endpoint must do it.
        after = (await api.post("/api/rules/reload")).json()
        assert "custom.spare_port" in {r["id"] for r in after["rules"]}
    finally:
        get_settings.cache_clear()
        load_catalog.cache_clear()


# --- stale run cleanup -----------------------------------------------------


async def _seed_running_run(run_id: str) -> None:
    from app.db import repo
    from app.db.engine import get_session_factory

    async with get_session_factory()() as session:
        await repo.create_run(session, run_id)


async def test_clear_stale_marks_orphaned_runs_failed(api):
    await _seed_running_run("stale0000001")

    body = (await api.post("/api/scans/clear-stale")).json()
    assert body["cleared"] == 1

    status = (await api.get("/api/scans/stale0000001")).json()
    assert status["status"] == "failed"
    assert status["error"]

    # Nothing left to clear on a second press.
    assert (await api.post("/api/scans/clear-stale")).json()["cleared"] == 0


async def test_clear_stale_leaves_the_running_scan_alone(api, monkeypatch):
    await _seed_running_run("live00000001")
    await _seed_running_run("stale0000002")
    monkeypatch.setattr("app.api.routes_scan.active_run_id", lambda: "live00000001")

    assert (await api.post("/api/scans/clear-stale")).json()["cleared"] == 1
    assert (await api.get("/api/scans/live00000001")).json()["status"] == "running"
    assert (await api.get("/api/scans/stale0000002")).json()["status"] == "failed"


async def test_startup_clears_runs_left_over_from_a_restart(db):
    await _seed_running_run("crashed00001")

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            status = (await client.get("/api/scans/crashed00001")).json()
    assert status["status"] == "failed"
    assert "Interrupted" in status["error"]
