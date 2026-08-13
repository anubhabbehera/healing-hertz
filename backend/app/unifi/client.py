from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from .errors import UnifiAuthError, UnifiConnectionError, UnifiRateLimited
from .models import (
    AppInfo,
    ClientOverview,
    DeviceDetail,
    DeviceOverview,
    DeviceStats,
    PendingDevice,
    Site,
)

MAX_PAGE_SIZE = 200
MAX_RETRIES = 3


class UnifiClientProtocol(Protocol):
    async def get_info(self) -> AppInfo: ...
    async def list_sites(self) -> list[Site]: ...
    async def list_devices(self, site_id: str) -> list[DeviceOverview]: ...
    async def get_device(self, site_id: str, device_id: str) -> DeviceDetail: ...
    async def get_device_stats(self, site_id: str, device_id: str) -> DeviceStats | None: ...
    async def list_clients(self, site_id: str) -> list[ClientOverview]: ...
    async def list_pending_devices(self) -> list[PendingDevice]: ...
    async def aclose(self) -> None: ...


class UnifiClient:
    """Thin async wrapper around the UniFi Network Integration API v1."""

    def __init__(
        self,
        host: str,
        api_key: str,
        *,
        port: int = 443,
        verify: bool = False,
        prefix: str = "/proxy/network/integration",
        timeout: float = 15.0,
    ) -> None:
        self._base = f"https://{host}:{port}{prefix}"
        self._http = httpx.AsyncClient(
            base_url=self._base,
            headers={"X-API-KEY": api_key, "Accept": "application/json"},
            verify=verify,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await self._http.get(path, params=params)
            except httpx.HTTPError as exc:
                last_exc = UnifiConnectionError(f"GET {path}: {exc}")
                await asyncio.sleep(min(2**attempt, 8))
                continue

            if resp.status_code in (401, 403):
                raise UnifiAuthError(f"API key rejected ({resp.status_code}) for {path}")
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 2**attempt))
                last_exc = UnifiRateLimited(f"429 on {path}")
                await asyncio.sleep(min(retry_after, 30))
                continue
            if resp.status_code >= 500:
                last_exc = UnifiConnectionError(f"{resp.status_code} on {path}")
                await asyncio.sleep(min(2**attempt, 8))
                continue

            resp.raise_for_status()
            return resp.json()

        raise last_exc if last_exc else UnifiConnectionError(f"GET {path} failed")

    async def _paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        items: list[dict] = []
        offset = 0
        while True:
            page_params = dict(params or {}) | {"offset": offset, "limit": MAX_PAGE_SIZE}
            payload = await self._get(path, params=page_params)
            data = payload.get("data", [])
            items.extend(data)
            total = payload.get("totalCount", len(items))
            offset += len(data)
            if offset >= total or not data:
                return items

    async def get_info(self) -> AppInfo:
        return AppInfo.model_validate(await self._get("/v1/info"))

    async def list_sites(self) -> list[Site]:
        return [Site.model_validate(s) for s in await self._paginate("/v1/sites")]

    async def list_devices(self, site_id: str) -> list[DeviceOverview]:
        raw = await self._paginate(f"/v1/sites/{site_id}/devices")
        return [DeviceOverview.model_validate(d) for d in raw]

    async def get_device(self, site_id: str, device_id: str) -> DeviceDetail:
        raw = await self._get(f"/v1/sites/{site_id}/devices/{device_id}")
        return DeviceDetail.model_validate(raw)

    async def get_device_stats(self, site_id: str, device_id: str) -> DeviceStats | None:
        # Offline devices have no latest statistics; tolerate 404.
        try:
            raw = await self._get(f"/v1/sites/{site_id}/devices/{device_id}/statistics/latest")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return DeviceStats.model_validate(raw)

    async def list_clients(self, site_id: str) -> list[ClientOverview]:
        raw = await self._paginate(f"/v1/sites/{site_id}/clients")
        return [ClientOverview.model_validate(c) for c in raw]

    async def list_pending_devices(self) -> list[PendingDevice]:
        try:
            raw = await self._paginate("/v1/pending-devices")
        except httpx.HTTPStatusError as exc:
            # Older Network versions lack this endpoint.
            if exc.response.status_code == 404:
                return []
            raise
        return [PendingDevice.model_validate(d) for d in raw]
