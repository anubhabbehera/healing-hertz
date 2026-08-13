"""Optional enrichment collectors: legacy UniFi RF data, NextDNS, WAN probe.

Each runs only when configured and fails soft — a broken integration logs a
warning and leaves its slot None; the scan itself never fails because of it.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import Settings
from app.integrations import wan_probe
from app.integrations.legacy_unifi import LegacyUnifiClient
from app.integrations.nextdns import NextDnsClient

from .snapshot import ProgressFn, Snapshot

logger = logging.getLogger(__name__)


async def _collect_rf(snapshot: Snapshot, settings: Settings) -> None:
    client = LegacyUnifiClient(
        settings.unifi_host,
        settings.unifi_username,
        settings.unifi_password,
        port=settings.unifi_port,
        verify=settings.unifi_tls_verify,
        unifi_os=settings.unifi_api_prefix.startswith("/proxy"),
    )
    try:
        site = snapshot.site.internal_reference or "default"
        snapshot.rf = await client.collect(site)
    finally:
        await client.aclose()


async def _collect_dns(snapshot: Snapshot, settings: Settings) -> None:
    client = NextDnsClient(settings.nextdns_api_key, settings.nextdns_profile_id)
    try:
        snapshot.dns = await client.collect()
    finally:
        await client.aclose()


async def _collect_wan(snapshot: Snapshot) -> None:
    snapshot.wan = await wan_probe.probe()


async def enrich_snapshot(
    snapshot: Snapshot, settings: Settings, progress: ProgressFn | None = None
) -> None:
    if settings.demo_mode:
        return

    tasks: dict[str, asyncio.Task] = {}
    if settings.unifi_username and settings.unifi_password:
        tasks["legacy UniFi API"] = asyncio.create_task(_collect_rf(snapshot, settings))
    if settings.nextdns_api_key and settings.nextdns_profile_id:
        tasks["NextDNS"] = asyncio.create_task(_collect_dns(snapshot, settings))
    if settings.wan_probe:
        tasks["WAN probe"] = asyncio.create_task(_collect_wan(snapshot))
    if not tasks:
        return

    if progress:
        await progress(f"Enriching: {', '.join(tasks)}")
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for name, result in zip(tasks, results):
        if isinstance(result, BaseException):
            logger.warning("Enrichment '%s' failed (continuing without it): %s", name, result)
