import pytest

from app.config import get_settings
from app.db.engine import dispose, init_db
from app.rules.loader import load_catalog


@pytest.fixture(autouse=True)
async def app_env(tmp_path, monkeypatch):
    """Isolated demo-mode settings + fresh SQLite DB per test."""
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("RULES_DIR", "")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
    monkeypatch.setenv("UNIFI_HOST", "")
    monkeypatch.setenv("UNIFI_API_KEY", "")
    monkeypatch.setenv("UNIFI_USERNAME", "")
    monkeypatch.setenv("UNIFI_PASSWORD", "")
    monkeypatch.setenv("NEXTDNS_API_KEY", "")
    monkeypatch.setenv("NEXTDNS_PROFILE_ID", "")
    monkeypatch.setenv("WAN_PROBE", "false")
    get_settings.cache_clear()
    # The catalog reads rules_dir, so it has to be rebuilt whenever settings are.
    load_catalog.cache_clear()
    await dispose()
    yield
    await dispose()
    get_settings.cache_clear()
    load_catalog.cache_clear()


@pytest.fixture
async def db():
    await init_db()
    yield


@pytest.fixture
def demo_client():
    from app.demo import DemoUnifiClient

    return DemoUnifiClient()


@pytest.fixture
async def snapshot(demo_client):
    from app.collectors.snapshot import collect_snapshot

    return await collect_snapshot(demo_client)
