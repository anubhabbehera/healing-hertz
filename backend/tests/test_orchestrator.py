from app.config import get_settings
from app.db import repo
from app.db.engine import get_session_factory
from app.scan.orchestrator import run_scan
from app.scan.progress import create_progress


async def test_scan_end_to_end_demo_mode(db, demo_client):
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        await repo.create_run(session, "run1")

    progress = create_progress("run1")
    await run_scan("run1", demo_client, settings, progress)

    assert progress.finished
    assert progress.events[-1].phase == "done"

    async with factory() as session:
        run = await repo.get_run(session, "run1")
        assert run.status == "completed"
        assert run.advice_status == "skipped"  # no anthropic key in tests
        assert run.health_score is not None and run.health_score < 100
        assert run.device_count == 7
        assert run.client_count == 12
        assert len(run.findings) > 5
        assert any(m.metric == "site.health_score" for m in run.metrics)


async def test_failed_collect_marks_run_failed(db, demo_client, monkeypatch):
    async def boom(site_id):
        raise RuntimeError("console unreachable")

    monkeypatch.setattr(demo_client, "list_devices", boom)
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        await repo.create_run(session, "run2")

    progress = create_progress("run2")
    await run_scan("run2", demo_client, settings, progress)

    assert progress.events[-1].phase == "error"
    async with factory() as session:
        run = await repo.get_run(session, "run2")
        assert run.status == "failed"
        assert "console unreachable" in run.error


async def test_history_load_after_run(db, demo_client):
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        await repo.create_run(session, "run3")
    await run_scan("run3", demo_client, settings, create_progress("run3"))

    async with factory() as session:
        history = await repo.load_history(session)
    assert len(history.runs) == 1
    assert history.runs[0].device_uptimes  # uptime metrics captured
    assert any(":" in k for k in history.runs[0].radio_retries)
