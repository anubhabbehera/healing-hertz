"""NextDNS analytics integration (https://nextdns.github.io/api/).

Pulls the last 24h of per-profile analytics so DNS anomalies become
diagnosable: blocked ratio, security-category blocks, top blocked domains.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.nextdns.io"
WINDOW = "-1d"

# Keywords identifying security (vs ads/tracker/parental) block reasons.
_SECURITY_KEYWORDS = (
    "threat", "malware", "phishing", "security", "safe-browsing", "safebrowsing",
    "cryptojacking", "dga", "typosquat", "rebinding", "nrd", "parked",
)


@dataclass
class DnsSnapshot:
    queries: int = 0
    blocked: int = 0
    top_blocked: list[dict] = field(default_factory=list)  # [{domain, queries}]
    security_blocks: list[dict] = field(default_factory=list)  # [{reason, queries}]

    @property
    def blocked_pct(self) -> float:
        return (self.blocked / self.queries * 100) if self.queries else 0.0

    @property
    def security_block_count(self) -> int:
        return sum(r.get("queries", 0) for r in self.security_blocks)


class NextDnsClient:
    def __init__(self, api_key: str, profile_id: str, timeout: float = 15.0) -> None:
        self._profile = profile_id
        self._http = httpx.AsyncClient(
            base_url=BASE_URL, headers={"X-Api-Key": api_key}, timeout=timeout
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get_data(self, path: str, params: dict | None = None) -> list[dict]:
        resp = await self._http.get(
            f"/profiles/{self._profile}{path}", params={"from": WINDOW, **(params or {})}
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", payload)
        return data if isinstance(data, list) else []

    async def collect(self) -> DnsSnapshot:
        snapshot = DnsSnapshot()

        for entry in await self._get_data("/analytics/status"):
            status = entry.get("status")
            queries = int(entry.get("queries", 0))
            snapshot.queries += queries
            if status == "blocked":
                snapshot.blocked = queries

        try:
            snapshot.top_blocked = [
                {"domain": d.get("domain"), "queries": d.get("queries", 0)}
                for d in await self._get_data("/analytics/domains",
                                              {"status": "blocked", "limit": 10})
            ]
        except httpx.HTTPError as exc:
            logger.info("NextDNS top-blocked fetch failed: %s", exc)

        try:
            for reason in await self._get_data("/analytics/reasons"):
                haystack = f"{reason.get('id', '')} {reason.get('name', '')}".lower()
                if any(k in haystack for k in _SECURITY_KEYWORDS):
                    snapshot.security_blocks.append({
                        "reason": reason.get("name") or reason.get("id"),
                        "queries": int(reason.get("queries", 0)),
                    })
        except httpx.HTTPError as exc:
            logger.info("NextDNS reasons fetch failed: %s", exc)

        return snapshot
