from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.engine import get_session_factory
from app.unifi.client import UnifiClient, UnifiClientProtocol


def make_unifi_client(settings: Settings | None = None) -> UnifiClientProtocol:
    settings = settings or get_settings()
    if settings.demo_mode:
        from app.demo import DemoUnifiClient

        return DemoUnifiClient()
    return UnifiClient(
        settings.unifi_host,
        settings.unifi_api_key,
        port=settings.unifi_port,
        verify=settings.unifi_tls_verify,
        prefix=settings.unifi_api_prefix,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session
