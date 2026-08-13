import httpx
import pytest

from app.db import repo
from app.main import create_app
from tests.test_api import run_scan_and_wait


@pytest.fixture
async def api(db):
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _by_name(rows: list[dict]) -> dict[str, dict]:
    return {r["name"]: r for r in rows}


async def test_device_rows_merge_overview_detail_and_stats(snapshot):
    rows = _by_name(repo._device_rows(snapshot))
    assert len(rows) == len(snapshot.devices)

    gateway = rows["Dream Machine Pro"]
    assert gateway["kind"] == "gateway"  # gateway wins over its switching feature
    assert gateway["model"] == "UDM-Pro"
    assert gateway["state"] == "ONLINE"
    assert gateway["cpu_pct"] == pytest.approx(93.4)
    assert gateway["mem_pct"] == pytest.approx(71.2)
    assert gateway["uptime_sec"] == 2592000
    assert gateway["last_heartbeat_at"].startswith("2026-08-12T09:59:30")
    assert gateway["ports_total"] == 2

    ap = rows["Office AP"]
    assert ap["kind"] == "access_point"
    assert len(ap["radios"]) == 2
    assert {r["frequency_ghz"] for r in ap["radios"]} == {2.4, 5.0}

    switch = rows["Office Switch"]
    assert switch["kind"] == "switch"
    assert switch["firmware_updatable"] is True


async def test_offline_device_has_no_stats_but_stays_listed(snapshot):
    offline = _by_name(repo._device_rows(snapshot))["Garage AP"]
    assert offline["state"] == "OFFLINE"
    assert offline["cpu_pct"] is None
    assert offline["mem_pct"] is None
    assert offline["uptime_sec"] is None
    assert offline["last_heartbeat_at"] is None
    assert offline["firmware_version"]  # inventory data survives the outage


async def test_run_detail_exposes_hardware_inventory(api):
    run_id, _ = await run_scan_and_wait(api)

    detail = (await api.get(f"/api/runs/{run_id}")).json()
    devices = detail["devices"]
    assert len(devices) == detail["device_count"]
    assert any(d["state"] == "OFFLINE" for d in devices)
    assert any(d["firmware_updatable"] for d in devices)
    assert any(d["cpu_pct"] is not None for d in devices)

    latest = (await api.get("/api/runs/latest")).json()
    assert latest["devices"] == devices


async def test_runs_predating_the_inventory_report_no_devices(api):
    """Runs stored before devices_json existed must not break the dashboard."""
    from app.db.engine import get_session_factory

    run_id, _ = await run_scan_and_wait(api)
    async with get_session_factory()() as session:
        run = await repo.get_run(session, run_id)
        run.devices_json = None
        await session.commit()

    detail = (await api.get(f"/api/runs/{run_id}")).json()
    assert detail["devices"] == []
