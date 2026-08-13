import json
from pathlib import Path

from app.unifi.models import (
    AppInfo,
    ClientOverview,
    DeviceDetail,
    DeviceOverview,
    DeviceStats,
    PendingDevice,
    Site,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


class DemoUnifiClient:
    """UnifiClientProtocol implementation backed by bundled fixture JSON."""

    async def get_info(self) -> AppInfo:
        return AppInfo.model_validate(_load("info.json"))

    async def list_sites(self) -> list[Site]:
        return [Site.model_validate(s) for s in _load("sites.json")]

    async def list_devices(self, site_id: str) -> list[DeviceOverview]:
        return [DeviceOverview.model_validate(d) for d in _load("devices.json")]

    async def get_device(self, site_id: str, device_id: str) -> DeviceDetail:
        return DeviceDetail.model_validate(_load("device_details.json")[device_id])

    async def get_device_stats(self, site_id: str, device_id: str) -> DeviceStats | None:
        raw = _load("device_stats.json").get(device_id)
        return DeviceStats.model_validate(raw) if raw else None

    async def list_clients(self, site_id: str) -> list[ClientOverview]:
        return [ClientOverview.model_validate(c) for c in _load("clients.json")]

    async def list_pending_devices(self) -> list[PendingDevice]:
        return [PendingDevice.model_validate(d) for d in _load("pending_devices.json")]

    async def aclose(self) -> None:
        return None
