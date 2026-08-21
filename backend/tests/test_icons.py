import json

import httpx
import pytest
import respx

from app.integrations.ui_icons import (
    CATALOGUE_URL,
    ICON_SIZE,
    IconCatalogue,
    normalize,
)

ICON_ID = "1064f3f6-8323-4325-af99-ee8bfe599109"
CONSOLE_ICON_ID = "aba783b2-f0c3-4c03-8f9e-005e6bebd67d"
PNG = b"\x89PNG\r\n\x1a\n-fake-"

CATALOGUE = {
    "devices": [
        {
            "sku": "U7-Pro-Wall",
            "product": {"name": "Access Point U7 Pro Wall"},
            "compliance": {"modelName": "U7-Pro-Wall"},
            "shortnames": ["U7-Pro-IW", "U7PIW"],
            "icon": {"id": ICON_ID},
        },
        {
            "sku": "UX7",
            "product": {"name": "Express 7"},
            "shortnames": ["UX7", "UX-Max"],
            "icon": {"id": CONSOLE_ICON_ID},
        },
        # Real catalogue entries do exist without an icon; they must not
        # poison the index with a None value.
        {"sku": "NO-ICON", "icon": {}},
    ]
}


def icon_url(icon_id: str) -> str:
    return f"https://static.ui.com/fingerprint/ui/icons/{icon_id}_{ICON_SIZE}x{ICON_SIZE}.png"


@pytest.fixture
def catalogue(tmp_path):
    return IconCatalogue(tmp_path / "icons")


@pytest.fixture
async def api(tmp_path, monkeypatch):
    from app.api import routes_icons
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("ICON_CACHE_DIR", str(tmp_path / "route-icons"))
    get_settings.cache_clear()
    routes_icons._catalogue.cache_clear()

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    routes_icons._catalogue.cache_clear()


def test_normalize_folds_spelling_differences():
    assert normalize("U7-Pro-Wall") == normalize("u7 pro wall") == "u7prowall"
    assert normalize("Express 7") == "express7"


def test_index_skips_entries_without_an_icon():
    index = IconCatalogue.build_index(CATALOGUE)
    assert index["u7prowall"] == ICON_ID
    # The API reports the product name for consoles, not the SKU.
    assert index["express7"] == CONSOLE_ICON_ID
    assert index["ux7"] == CONSOLE_ICON_ID
    assert "noicon" not in index


def test_index_rejects_a_non_uuid_icon_id():
    hostile = {"devices": [{"sku": "X", "icon": {"id": "../../etc/passwd"}}]}
    assert IconCatalogue.build_index(hostile) == {}


@respx.mock
async def test_fetches_then_serves_from_disk(catalogue, tmp_path):
    cat_route = respx.get(CATALOGUE_URL).mock(return_value=httpx.Response(200, json=CATALOGUE))
    png_route = respx.get(icon_url(ICON_ID)).mock(return_value=httpx.Response(200, content=PNG))

    assert await catalogue.png("U7-Pro-Wall") == PNG
    assert (tmp_path / "icons" / f"{ICON_ID}_{ICON_SIZE}.png").read_bytes() == PNG

    # Second call for the same model is served from memory and disk.
    assert await catalogue.png("U7-Pro-Wall") == PNG
    assert cat_route.call_count == 1
    assert png_route.call_count == 1


@respx.mock
async def test_second_process_reads_the_cached_catalogue(catalogue, tmp_path):
    respx.get(CATALOGUE_URL).mock(return_value=httpx.Response(200, json=CATALOGUE))
    respx.get(icon_url(ICON_ID)).mock(return_value=httpx.Response(200, content=PNG))
    await catalogue.png("U7-Pro-Wall")

    # A restart: fresh instance, same cache directory, and the catalogue
    # endpoint now fails. The on-disk copy has to carry it.
    respx.get(CATALOGUE_URL).mock(return_value=httpx.Response(503))
    restarted = IconCatalogue(tmp_path / "icons")
    assert await restarted.png("U7-Pro-Wall") == PNG


@respx.mock
async def test_unknown_model_and_upstream_failure_return_none(catalogue):
    respx.get(CATALOGUE_URL).mock(return_value=httpx.Response(200, json=CATALOGUE))
    assert await catalogue.png("Some Other Vendor AP") is None

    respx.get(icon_url(CONSOLE_ICON_ID)).mock(return_value=httpx.Response(404))
    assert await catalogue.png("Express 7") is None


@respx.mock
async def test_catalogue_outage_is_not_fatal(catalogue):
    respx.get(CATALOGUE_URL).mock(side_effect=httpx.ConnectError("offline"))
    assert await catalogue.png("U7-Pro-Wall") is None


async def test_oversized_model_is_not_looked_up(catalogue):
    # No respx mock registered: reaching the network here would error.
    assert await catalogue.png("x" * 200) is None
    assert await catalogue.png("") is None


@respx.mock
async def test_route_serves_a_png_and_404s_for_an_unknown_model(api):
    respx.get(CATALOGUE_URL).mock(return_value=httpx.Response(200, json=CATALOGUE))
    respx.get(icon_url(ICON_ID)).mock(return_value=httpx.Response(200, content=PNG))

    resp = await api.get("/api/device-icons/U7-Pro-Wall")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert "max-age" in resp.headers["cache-control"]
    assert resp.content == PNG

    assert (await api.get("/api/device-icons/Nothing%20Like%20This")).status_code == 404


@respx.mock
async def test_route_404s_when_icons_are_disabled(api, monkeypatch):
    from app.config import get_settings

    respx.get(CATALOGUE_URL).mock(return_value=httpx.Response(200, json=CATALOGUE))
    monkeypatch.setenv("DEVICE_ICONS", "false")
    get_settings.cache_clear()

    # Nothing reaches ui.com either: the check happens before any lookup.
    assert (await api.get("/api/device-icons/U7-Pro-Wall")).status_code == 404
    assert not respx.calls


def test_catalogue_json_on_disk_is_valid(tmp_path):
    # The cached copy is re-parsed on restart; keep it a plain JSON document.
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(CATALOGUE))
    assert IconCatalogue.build_index(json.loads(path.read_text()))["ux7"] == CONSOLE_ICON_ID
