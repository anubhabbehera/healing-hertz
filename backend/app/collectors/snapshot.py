from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.integrations.legacy_unifi import RfSnapshot
from app.integrations.nextdns import DnsSnapshot
from app.integrations.wan_probe import WanProbeResult
from app.unifi.client import UnifiClientProtocol
from app.unifi.models import (
    ClientOverview,
    DeviceDetail,
    DeviceOverview,
    DeviceStats,
    Network,
    PendingDevice,
    Site,
    SwitchStack,
    WifiBroadcast,
)

ProgressFn = Callable[[str], Awaitable[None]]

DETAIL_CONCURRENCY = 5


@dataclass
class ConfigSnapshot:
    """The site's configuration as the Integration API reports it.

    Separate from the telemetry above because it can be absent: the endpoints
    arrived in Network 10.x, and a key made under a restricted admin may be
    refused them. None means "not readable here", which the unsupported-checks
    list turns into an explanation rather than a silent gap.
    """

    networks: list[Network] = field(default_factory=list)
    wifi: list[WifiBroadcast] = field(default_factory=list)
    switch_stacks: list[SwitchStack] = field(default_factory=list)


@dataclass
class Snapshot:
    collected_at: datetime
    application_version: str
    site: Site
    devices: list[DeviceOverview]
    device_details: dict[str, DeviceDetail] = field(default_factory=dict)
    device_stats: dict[str, DeviceStats] = field(default_factory=dict)
    clients: list[ClientOverview] = field(default_factory=list)
    pending_devices: list[PendingDevice] = field(default_factory=list)
    config: ConfigSnapshot | None = None
    # Optional enrichments (None = the integration is not configured/available)
    rf: RfSnapshot | None = None
    dns: DnsSnapshot | None = None
    wan: WanProbeResult | None = None


async def collect_snapshot(
    client: UnifiClientProtocol,
    site_name: str = "",
    progress: ProgressFn | None = None,
) -> Snapshot:
    async def emit(detail: str) -> None:
        if progress:
            await progress(detail)

    await emit("Connecting to console")
    info = await client.get_info()

    await emit("Listing sites")
    sites = await client.list_sites()
    if not sites:
        raise RuntimeError("No sites found on this console")
    site = next((s for s in sites if site_name and s.name == site_name), sites[0])

    await emit(f"Listing devices for site '{site.name or site.id}'")
    devices = await client.list_devices(site.id)

    sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
    details: dict[str, DeviceDetail] = {}
    stats: dict[str, DeviceStats] = {}
    done = 0

    async def fetch_device(dev: DeviceOverview) -> None:
        nonlocal done
        async with sem:
            details[dev.id] = await client.get_device(site.id, dev.id)
            if dev.state == "ONLINE":
                s = await client.get_device_stats(site.id, dev.id)
                if s is not None:
                    stats[dev.id] = s
        done += 1
        await emit(f"Devices {done}/{len(devices)}")

    await asyncio.gather(*(fetch_device(d) for d in devices))

    await emit("Listing clients")
    clients = await client.list_clients(site.id)

    await emit("Checking pending devices")
    pending = await client.list_pending_devices()

    await emit("Reading site configuration")
    networks = await client.list_networks(site.id)
    wifi = await client.list_wifi_broadcasts(site.id)
    stacks = await client.list_switch_stacks(site.id)
    # Both empty means the config plane is unreadable on this console, not that
    # the site has no networks -- every site has at least one.
    config = (
        ConfigSnapshot(networks=networks, wifi=wifi, switch_stacks=stacks)
        if (networks or wifi) else None
    )

    return Snapshot(
        collected_at=datetime.now(UTC),
        application_version=info.application_version,
        site=site,
        devices=devices,
        device_details=details,
        device_stats=stats,
        clients=clients,
        pending_devices=pending,
        config=config,
    )
