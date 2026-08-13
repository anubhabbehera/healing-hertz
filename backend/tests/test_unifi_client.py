import httpx
import pytest
import respx

from app.unifi.client import UnifiClient
from app.unifi.errors import UnifiAuthError

BASE = "https://console.local:443/proxy/network/integration"


@pytest.fixture
async def client():
    c = UnifiClient("console.local", "test-key")
    yield c
    await c.aclose()


@respx.mock
async def test_pagination_across_pages(client):
    def pager(request):
        offset = int(request.url.params["offset"])
        total = 450
        page = [{"id": f"c{i}", "macAddress": "", "type": "WIRELESS"}
                for i in range(offset, min(offset + 200, total))]
        return httpx.Response(200, json={
            "offset": offset, "limit": 200, "count": len(page),
            "totalCount": total, "data": page,
        })

    respx.get(f"{BASE}/v1/sites/s1/clients").mock(side_effect=pager)
    clients = await client.list_clients("s1")
    assert len(clients) == 450
    assert clients[0].id == "c0" and clients[-1].id == "c449"


@respx.mock
async def test_auth_error_maps_to_unifi_auth_error(client):
    respx.get(f"{BASE}/v1/info").mock(return_value=httpx.Response(401))
    with pytest.raises(UnifiAuthError):
        await client.get_info()


@respx.mock
async def test_429_retries_then_succeeds(client):
    route = respx.get(f"{BASE}/v1/info")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"applicationVersion": "10.1.68"}),
    ]
    info = await client.get_info()
    assert info.application_version == "10.1.68"
    assert route.call_count == 2


@respx.mock
async def test_api_key_header_sent(client):
    route = respx.get(f"{BASE}/v1/info").mock(
        return_value=httpx.Response(200, json={"applicationVersion": "10.0.0"})
    )
    await client.get_info()
    assert route.calls.last.request.headers["X-API-KEY"] == "test-key"


@respx.mock
async def test_device_stats_404_returns_none(client):
    respx.get(f"{BASE}/v1/sites/s1/devices/d1/statistics/latest").mock(
        return_value=httpx.Response(404)
    )
    assert await client.get_device_stats("s1", "d1") is None


def test_self_hosted_prefix():
    c = UnifiClient("nvr.local", "k", port=8443, prefix="/integration")
    assert str(c._http.base_url).rstrip("/") == "https://nvr.local:8443/integration"
