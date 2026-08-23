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


# --- config plane ----------------------------------------------------------
#
# These endpoints arrived in Network 10.x, and a key made under a restricted
# admin can be refused them outright. Either way the scan has to continue.


def _page(items):
    return httpx.Response(200, json={
        "offset": 0, "limit": 200, "count": len(items),
        "totalCount": len(items), "data": items,
    })


@respx.mock
async def test_networks_are_fetched_in_detail_form(client):
    respx.get(f"{BASE}/v1/sites/s1/networks").mock(
        return_value=_page([{"id": "n1", "name": "Default"}])
    )
    respx.get(f"{BASE}/v1/sites/s1/networks/n1").mock(return_value=httpx.Response(200, json={
        "id": "n1", "name": "Default", "vlanId": 1,
        "ipv4Configuration": {"hostIpAddress": "192.168.1.1", "prefixLength": 24},
    }))
    networks = await client.list_networks("s1")
    assert networks[0].vlan_id == 1
    assert networks[0].ipv4_configuration.prefix_length == 24


@respx.mock
async def test_a_network_whose_detail_fails_falls_back_to_the_overview(client):
    respx.get(f"{BASE}/v1/sites/s1/networks").mock(
        return_value=_page([{"id": "n1", "name": "Default", "vlanId": 7}])
    )
    respx.get(f"{BASE}/v1/sites/s1/networks/n1").mock(return_value=httpx.Response(500))
    networks = await client.list_networks("s1")
    assert [(n.id, n.vlan_id) for n in networks] == [("n1", 7)]


@respx.mock
@pytest.mark.parametrize("status", [403, 404])
async def test_config_endpoints_absent_or_forbidden_yield_nothing(client, status):
    respx.get(f"{BASE}/v1/sites/s1/networks").mock(return_value=httpx.Response(status))
    respx.get(f"{BASE}/v1/sites/s1/wifi/broadcasts").mock(return_value=httpx.Response(status))
    assert await client.list_networks("s1") == []
    assert await client.list_wifi_broadcasts("s1") == []


@respx.mock
async def test_wifi_broadcasts_parse_their_settings(client):
    respx.get(f"{BASE}/v1/sites/s1/wifi/broadcasts").mock(
        return_value=_page([{"id": "w1", "name": "Home"}])
    )
    respx.get(f"{BASE}/v1/sites/s1/wifi/broadcasts/w1").mock(
        return_value=httpx.Response(200, json={
            "id": "w1", "name": "Home", "enabled": True,
            "securityConfiguration": {"type": "WPA3_PERSONAL", "pmfMode": "REQUIRED"},
            "broadcastingFrequenciesGHz": [2.4, 5],
            "basicDataRateKbpsByFrequencyGHz": {"2.4": 6000, "5": 6000},
            "bandSteeringEnabled": True,
        })
    )
    wifi = await client.list_wifi_broadcasts("s1")
    assert wifi[0].security_configuration.type == "WPA3_PERSONAL"
    assert wifi[0].broadcasting_frequencies_ghz == [2.4, 5]
    assert wifi[0].basic_data_rate_kbps["2.4"] == 6000
