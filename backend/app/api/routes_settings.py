from fastapi import APIRouter

from app.config import get_settings
from app.unifi.errors import UnifiAuthError, UnifiConnectionError

from .deps import make_unifi_client

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_masked_settings() -> dict:
    s = get_settings()
    return {
        "unifi_host": s.unifi_host,
        "unifi_port": s.unifi_port,
        "unifi_tls_verify": s.unifi_tls_verify,
        "unifi_api_prefix": s.unifi_api_prefix,
        "unifi_site": s.unifi_site,
        "unifi_api_key_set": bool(s.unifi_api_key),
        "anthropic_api_key_set": bool(s.anthropic_api_key),
        "anthropic_base_url": s.anthropic_base_url,
        "advisor_model": s.advisor_model,
        "demo_mode": s.demo_mode,
        "legacy_api_enabled": bool(s.unifi_username and s.unifi_password),
        "nextdns_enabled": bool(s.nextdns_api_key and s.nextdns_profile_id),
        "wan_probe_enabled": s.wan_probe and not s.demo_mode,
    }


@router.post("/test-connection")
async def test_connection() -> dict:
    client = make_unifi_client()
    try:
        info = await client.get_info()
        sites = await client.list_sites()
        return {
            "ok": True,
            "application_version": info.application_version,
            "sites": [{"id": s.id, "name": s.name} for s in sites],
        }
    except UnifiAuthError as exc:
        return {"ok": False, "error": f"Authentication failed: {exc}"}
    except UnifiConnectionError as exc:
        return {"ok": False, "error": f"Could not reach console: {exc}"}
    except Exception as exc:  # noqa: BLE001 — connection test reports any failure to the UI
        return {"ok": False, "error": str(exc)}
    finally:
        await client.aclose()
