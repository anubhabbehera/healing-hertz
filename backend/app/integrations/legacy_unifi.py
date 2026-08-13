"""Optional legacy UniFi controller API enrichment.

The unofficial controller API exposes what the Integration API doesn't:
per-client RF stats (RSSI/signal/PHY rates) and roaming events. Uses a
username/password local admin login — create a dedicated View Only admin.
Being unofficial, endpoints can shift across firmware; every call here is
treated as best-effort by the caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

ROAM_EVENT_KEYS = ("EVT_WU_Roam", "EVT_WU_RoamRadio")
EVENT_WINDOW_HOURS = 24


@dataclass
class ClientRF:
    mac: str
    name: str
    ap_mac: str | None
    essid: str | None
    signal_dbm: int | None  # negative dBm
    tx_rate_kbps: int | None
    rx_rate_kbps: int | None


@dataclass
class RfSnapshot:
    clients: list[ClientRF]
    roam_counts: dict[str, int]  # client name/mac -> roam events in last 24h
    roam_data_available: bool


class LegacyUnifiClient:
    """UniFi OS consoles (/proxy/network/...) and self-hosted servers (/api/...)."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 443,
        verify: bool = False,
        unifi_os: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self._unifi_os = unifi_os
        self._username = username
        self._password = password
        self._csrf: str | None = None
        self._http = httpx.AsyncClient(
            base_url=f"https://{host}:{port}", verify=verify, timeout=timeout
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _api(self, site: str, path: str) -> str:
        prefix = "/proxy/network/api" if self._unifi_os else "/api"
        return f"{prefix}/s/{site}/{path}"

    async def login(self) -> None:
        login_path = "/api/auth/login" if self._unifi_os else "/api/login"
        resp = await self._http.post(
            login_path, json={"username": self._username, "password": self._password}
        )
        resp.raise_for_status()
        self._csrf = resp.headers.get("x-csrf-token") or resp.headers.get(
            "x-updated-csrf-token"
        )

    def _headers(self) -> dict:
        return {"x-csrf-token": self._csrf} if self._csrf else {}

    async def client_rf(self, site: str) -> list[ClientRF]:
        resp = await self._http.get(self._api(site, "stat/sta"), headers=self._headers())
        resp.raise_for_status()
        clients = []
        for sta in resp.json().get("data", []):
            if sta.get("is_wired", False):
                continue
            signal = sta.get("signal")
            if signal is None and sta.get("rssi") is not None:
                # Some firmwares report only positive SNR-style rssi;
                # approximate dBm assuming a -95 dBm noise floor.
                signal = int(sta["rssi"]) - 95
            clients.append(ClientRF(
                mac=sta.get("mac", ""),
                name=sta.get("name") or sta.get("hostname") or sta.get("mac", "?"),
                ap_mac=sta.get("ap_mac"),
                essid=sta.get("essid"),
                signal_dbm=int(signal) if signal is not None else None,
                tx_rate_kbps=sta.get("tx_rate"),
                rx_rate_kbps=sta.get("rx_rate"),
            ))
        return clients

    async def roam_counts(self, site: str) -> dict[str, int]:
        resp = await self._http.post(
            self._api(site, "stat/event"),
            json={"within": EVENT_WINDOW_HOURS, "_limit": 3000},
            headers=self._headers(),
        )
        resp.raise_for_status()
        counts: dict[str, int] = {}
        for event in resp.json().get("data", []):
            if event.get("key") not in ROAM_EVENT_KEYS:
                continue
            who = event.get("hostname") or event.get("user") or "unknown"
            counts[who] = counts.get(who, 0) + 1
        return counts

    async def collect(self, site: str) -> RfSnapshot:
        await self.login()
        clients = await self.client_rf(site)
        try:
            roams = await self.roam_counts(site)
            roam_ok = True
        except httpx.HTTPError as exc:
            logger.info("Legacy API roam events unavailable: %s", exc)
            roams, roam_ok = {}, False
        return RfSnapshot(clients=clients, roam_counts=roams, roam_data_available=roam_ok)
