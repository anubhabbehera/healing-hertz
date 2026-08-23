"""What a snapshot contributes to the stored time series.

One definition, two readers. The database writes these after a scan so future
scans have history; the trend source reads the same list to put the current
scan's own numbers at the end of that history. Without the second reader a
trend rule would be judging the *previous* run — metrics are written after the
rules have already run, so the newest reading the database can offer is one
scan behind whatever is being scanned right now.

The health score is deliberately absent: it is a function of the findings, and
the findings do not exist yet at the point the rules are still running.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.collectors.snapshot import Snapshot

from . import subnets


@dataclass(frozen=True)
class Reading:
    metric: str
    subject_type: str
    subject_id: str | None
    subject_name: str | None
    value: float


def readings(snapshot: Snapshot) -> list[Reading]:
    """Every metric this snapshot can supply, in a stable order."""
    site = snapshot.site.name if snapshot.site else None
    out: list[Reading] = [
        Reading("site.client_count", "site", None, site, float(len(snapshot.clients))),
        Reading("site.device_online_count", "site", None, site,
                float(sum(1 for d in snapshot.devices if d.state == "ONLINE"))),
    ]

    for dev_id, stats in snapshot.device_stats.items():
        detail = snapshot.device_details.get(dev_id)
        name = detail.name if detail else dev_id
        for metric, value in (
            ("device.cpu_pct", stats.cpu_utilization_pct),
            ("device.mem_pct", stats.memory_utilization_pct),
            ("device.uptime_sec", stats.uptime_sec),
        ):
            if value is not None:
                out.append(Reading(metric, "device", dev_id, name, float(value)))
        for radio in stats.interfaces.radios:
            if radio.tx_retries_pct is not None:
                out.append(Reading(
                    "radio.tx_retries_pct", "radio", f"{dev_id}:{radio.frequency_ghz}",
                    f"{name} {radio.frequency_ghz} GHz", float(radio.tx_retries_pct),
                ))

    for dev_id, detail in snapshot.device_details.items():
        for port in detail.interfaces.ports:
            if port.state == "UP" and port.speed_mbps is not None:
                out.append(Reading(
                    "port.speed_mbps", "port", f"{dev_id}:{port.idx}",
                    f"{detail.name} port {port.idx}", float(port.speed_mbps),
                ))

    # Address-pool occupancy: a pool at 60% is fine, a pool that has climbed
    # three points a week is a date.
    if snapshot.config is not None:
        client_ips = [c.ip_address for c in snapshot.clients]
        for net in snapshot.config.networks:
            ipv4 = net.ipv4_configuration
            cidr = subnets.network_of(
                ipv4.host_ip_address if ipv4 else None,
                ipv4.prefix_length if ipv4 else None,
            )
            pressure = subnets.pool_pressure(
                subnets.hosts_in(cidr, client_ips), subnets.usable_hosts(cidr)
            )
            if pressure is not None:
                out.append(Reading("network.pool_pressure_pct", "network", net.id,
                                   net.name, round(pressure * 100, 2)))

    if snapshot.wan is not None:
        out.append(Reading("wan.latency_ms", "site", None, "WAN", snapshot.wan.latency_ms))
        out.append(Reading("wan.loss_pct", "site", None, "WAN", snapshot.wan.loss_pct))

    if snapshot.dns is not None:
        out.append(Reading("dns.blocked_pct", "site", None, "DNS",
                           round(snapshot.dns.blocked_pct, 2)))
        out.append(Reading("dns.queries_24h", "site", None, "DNS",
                           float(snapshot.dns.queries)))
    return out
