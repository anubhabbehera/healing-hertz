import logging

from fastapi import APIRouter

from app.config import get_settings
from app.unifi.errors import UnifiAuthError, UnifiConnectionError

from .deps import make_unifi_client

logger = logging.getLogger(__name__)

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
    # The two typed failures below are the whole point of this endpoint: they
    # tell you *why* the console is unreachable, and their messages are composed
    # by app/unifi/client.py from a status code and an API path — never a
    # traceback, never a credential.
    except UnifiAuthError as exc:
        return {"ok": False, "error": f"Authentication failed: {exc}"}
    except UnifiConnectionError as exc:
        return {"ok": False, "error": f"Could not reach console: {exc}"}
    # Anything else is unbounded — a library error carrying a file path, a config
    # value, or a URL with something in it. The README promises keys are never
    # included in error messages, and `str(exc)` on an arbitrary exception cannot
    # promise that. The detail goes to the log, where the operator can read it;
    # the response names the failure type so the UI still says something useful.
    except Exception as exc:
        logger.exception("Connection test failed")
        return {
            "ok": False,
            "error": f"Connection test failed ({type(exc).__name__}). "
                     "See the server log for details.",
        }
    finally:
        await client.aclose()
