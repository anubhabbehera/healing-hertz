from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env from the project root and backend/ regardless of the working
# directory; later entries (and real environment variables) take precedence.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_PROJECT_ROOT / ".env", _BACKEND_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # UniFi console connection
    unifi_host: str = ""
    unifi_api_key: str = ""
    unifi_port: int = 443
    unifi_tls_verify: bool = False
    # UniFi OS consoles (UDM/UDR/Cloud Key): "/proxy/network/integration"
    # Self-hosted Network Server (port 8443): "/integration"
    unifi_api_prefix: str = "/proxy/network/integration"
    unifi_site: str = ""  # site name; empty = first site

    # Optional legacy-API enrichment (per-client RSSI + roaming events).
    # Use a dedicated read-only local admin; leave empty to disable.
    unifi_username: str = ""
    unifi_password: str = ""

    # Optional NextDNS integration (DNS analytics + anomaly rules)
    nextdns_api_key: str = ""
    nextdns_profile_id: str = ""

    # WAN active probe (latency/jitter/loss measured from this host)
    wan_probe: bool = True

    # Advisor (optional)
    anthropic_api_key: str = ""
    # Any endpoint speaking the Anthropic Messages API (e.g. OpenRouter, LiteLLM).
    # Empty = official Anthropic API.
    anthropic_base_url: str = ""
    advisor_model: str = "claude-sonnet-5"
    advisor_max_tokens: int = 8192

    db_path: str = "./healing_hertz.db"
    demo_mode: bool = False

    @property
    def unifi_base_url(self) -> str:
        return f"https://{self.unifi_host}:{self.unifi_port}{self.unifi_api_prefix}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
