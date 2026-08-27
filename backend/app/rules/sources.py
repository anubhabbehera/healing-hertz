"""Named iterables over a Snapshot.

A source flattens whatever joins, lookups and None-guards a family of rules
needs into a plain dict of primitives. That flattening is the whole reason the
catalog never needs attribute traversal: a predicate or template names a key in
this dict and nothing else, so it cannot reach into an object, and validating a
rule against a source is a set-membership test rather than a path walk.

Adding a binding here is cheap. Adding one that is not a str/int/float/bool/None
is not allowed -- see ``test_sources`` -- because template rendering would then
have an object to traverse.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from app.analytics import capacity, metrics, subnets, timeseries, topology
from app.collectors.snapshot import Snapshot

from .base import RunHistory


@dataclass(frozen=True)
class Row:
    """One candidate a rule can fire on."""

    vars: dict[str, Any]
    subject_type: str = "site"
    subject_id: str | None = None
    subject_name: str | None = None


@dataclass(frozen=True)
class Source:
    name: str
    # Declared up front so a rule can be validated without running a scan.
    bindings: frozenset[str]
    iterate: Callable[[Snapshot, RunHistory], Iterator[Row]]
    doc: str = ""


REGISTRY: dict[str, Source] = {}


def register(name: str, bindings: set[str], doc: str = "") -> Callable:
    def wrap(fn: Callable[[Snapshot, RunHistory], Iterator[Row]]) -> Callable:
        REGISTRY[name] = Source(name=name, bindings=frozenset(bindings), iterate=fn, doc=doc)
        return fn

    return wrap


def get(name: str) -> Source:
    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY)) or "none registered"
        raise KeyError(f"unknown source {name!r}; known sources: {known}") from None


# --- devices ---------------------------------------------------------------

# Rules display a device by whichever identifier it actually has, so the
# fallbacks are bindings rather than something templates try to express.
_DEVICE_BINDINGS = {
    "device_id", "device_name", "device_model", "device_mac", "device_ip",
    "device_state", "device_supported",
    "device_firmware_version", "device_firmware_updatable",
    "is_access_point", "is_switch", "is_gateway",
    "name_or_model", "name_or_mac",
}


@register(
    "devices",
    _DEVICE_BINDINGS,
    doc="Every adopted device, from the Integration API device list.",
)
def _devices(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    for d in snapshot.devices:
        yield Row(
            vars={
                "device_id": d.id,
                "device_name": d.name,
                "device_model": d.model,
                "device_mac": d.mac_address,
                "device_ip": d.ip_address,
                "device_state": d.state,
                "device_supported": d.supported,
                "device_firmware_version": d.firmware_version,
                "device_firmware_updatable": d.firmware_updatable,
                # Booleans rather than the raw feature list: a list is not a
                # primitive, and every rule only ever asks "is it one of these".
                "is_access_point": "accessPoint" in d.features,
                "is_switch": "switching" in d.features,
                "is_gateway": "gateway" in d.features,
                "name_or_model": d.name or d.model,
                "name_or_mac": d.name or d.mac_address,
            },
            subject_type="device",
            subject_id=d.id,
            subject_name=d.name,
        )


# --- pending devices -------------------------------------------------------

_PENDING_BINDINGS = {
    "pending_id", "pending_name", "pending_model", "pending_mac",
    "name_or_model_or_mac",
}


@register(
    "pending_devices",
    _PENDING_BINDINGS,
    doc="Devices visible on the network but not adopted into this site.",
)
def _pending_devices(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    for p in snapshot.pending_devices:
        yield Row(
            vars={
                "pending_id": p.id,
                "pending_name": p.name,
                "pending_model": p.model,
                "pending_mac": p.mac_address,
                "name_or_model_or_mac": p.name or p.model or p.mac_address,
            },
            subject_type="device",
            subject_id=p.id,
            subject_name=p.name,
        )


def band_label(frequency_ghz: float | None) -> str | None:
    """A radio's band as it should read in prose.

    frequency_ghz is a float and the API reports 5 GHz as an integer, so pydantic
    hands back 5.0 -- which interpolates as "5.0 GHz". Formatting it here rather
    than in each template keeps the decision in one place and copes with a
    missing reading, which a format spec in a template would not.
    """
    return None if frequency_ghz is None else f"{frequency_ghz:g}"


# --- clients ---------------------------------------------------------------

_CLIENT_BINDINGS = {
    "client_id", "client_name", "client_mac", "client_ip", "client_type",
    "access_type", "access_authorized",
}


@register(
    "clients",
    _CLIENT_BINDINGS,
    doc="Connected clients as the Integration API reports them.",
)
def _clients(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    for c in snapshot.clients:
        access = c.access
        yield Row(
            vars={
                "client_id": c.id,
                "client_name": c.name,
                "client_mac": c.mac_address,
                "client_ip": c.ip_address,
                "client_type": (c.type or "").upper(),
                "access_type": access.type if access else None,
                "access_authorized": access.authorized if access else None,
            },
            subject_type="client",
            subject_id=c.id,
            subject_name=c.name,
        )


_RF_CLIENT_BINDINGS = {
    "client_mac", "client_name", "client_ssid", "client_ap_mac",
    "signal_dbm", "tx_rate_kbps", "rx_rate_kbps", "channel", "band_ghz",
}


@register(
    "rf_clients",
    _RF_CLIENT_BINDINGS,
    doc=(
        "Per-client RF detail from the legacy controller API. Yields nothing "
        "when that integration is not configured, which is how every rule over "
        "it stays quiet without its own guard."
    ),
)
def _rf_clients(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    if snapshot.rf is None:
        return
    for c in snapshot.rf.clients:
        yield Row(
            vars={
                "client_mac": c.mac,
                "client_name": c.name,
                "client_ssid": c.essid,
                "client_ap_mac": c.ap_mac,
                "signal_dbm": c.signal_dbm,
                "tx_rate_kbps": c.tx_rate_kbps,
                "rx_rate_kbps": c.rx_rate_kbps,
                "channel": c.channel,
                "band_ghz": c.band_ghz,
            },
            subject_type="client",
            subject_id=c.mac,
            subject_name=c.name,
        )


# --- device telemetry ------------------------------------------------------

_DEVICE_STATS_BINDINGS = {
    "device_id", "device_name",
    "uptime_sec", "cpu_pct", "memory_pct",
    "load_1_min", "load_5_min", "load_15_min",
    "uplink_tx_bps", "uplink_rx_bps",
}


@register(
    "device_stats",
    _DEVICE_STATS_BINDINGS,
    doc="Per-device telemetry, joined to device details for a display name.",
)
def _device_stats(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    for dev_id, stats in snapshot.device_stats.items():
        detail = snapshot.device_details.get(dev_id)
        # Stats can arrive for a device the detail fetch missed; the id is a
        # worse name than the real one but better than nothing.
        name = detail.name if detail else dev_id
        uplink = stats.uplink
        yield Row(
            vars={
                "device_id": dev_id,
                "device_name": name,
                "uptime_sec": stats.uptime_sec,
                "cpu_pct": stats.cpu_utilization_pct,
                "memory_pct": stats.memory_utilization_pct,
                "load_1_min": stats.load_average_1_min,
                "load_5_min": stats.load_average_5_min,
                "load_15_min": stats.load_average_15_min,
                "uplink_tx_bps": uplink.tx_rate_bps if uplink else None,
                "uplink_rx_bps": uplink.rx_rate_bps if uplink else None,
            },
            subject_type="device",
            subject_id=dev_id,
            subject_name=name,
        )


# --- radios of online access points ----------------------------------------

_AP_RADIO_BINDINGS = {
    "device_id", "device_name", "device_model",
    "radio_channel", "radio_width_mhz", "radio_frequency_ghz", "radio_band",
    "radio_standard", "radio_standard_normalized",
}


@register(
    "online_ap_radios",
    _AP_RADIO_BINDINGS,
    doc=(
        "Broadcasting radios of online access points. Joins the device list to "
        "device details for ONLINE state, and skips disabled radios."
    ),
)
def _online_ap_radios(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    online = {d.id for d in snapshot.devices if d.state == "ONLINE"}
    for dev_id, detail in snapshot.device_details.items():
        if dev_id not in online or not detail.is_access_point:
            continue
        for radio in detail.interfaces.radios:
            # A disabled radio is reported with channel 0 (or no channel) by the
            # Integration API. It can't cause RF problems, so it isn't a row.
            if not radio.channel:
                continue
            standard = radio.wlan_standard
            yield Row(
                vars={
                    "device_id": dev_id,
                    "device_name": detail.name,
                    "device_model": detail.model,
                    "radio_channel": radio.channel,
                    "radio_width_mhz": radio.channel_width_mhz,
                    "radio_frequency_ghz": radio.frequency_ghz,
                    "radio_band": band_label(radio.frequency_ghz),
                    "radio_standard": standard,
                    # Normalised here so the "match exactly, never by prefix"
                    # rule stays next to the reason for it.
                    "radio_standard_normalized": (
                        (standard or "").upper().replace("-", "").replace(" ", "")
                    ),
                },
                subject_type="device",
                subject_id=dev_id,
                subject_name=detail.name,
            )


# --- radio telemetry -------------------------------------------------------

_AP_RADIO_STATS_BINDINGS = {
    "device_id", "device_name", "radio_frequency_ghz", "radio_band", "tx_retries_pct",
}


@register(
    "ap_radio_stats",
    _AP_RADIO_STATS_BINDINGS,
    doc=(
        "Per-radio counters for access points. Unlike online_ap_radios this "
        "reads telemetry rather than configuration, and does not filter on "
        "state -- a device reporting stats is reporting them."
    ),
)
def _ap_radio_stats(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    for dev_id, stats in snapshot.device_stats.items():
        detail = snapshot.device_details.get(dev_id)
        if detail is None or not detail.is_access_point:
            continue
        for radio in stats.interfaces.radios:
            yield Row(
                vars={
                    "device_id": dev_id,
                    "device_name": detail.name,
                    "radio_frequency_ghz": radio.frequency_ghz,
                    "radio_band": band_label(radio.frequency_ghz),
                    "tx_retries_pct": radio.tx_retries_pct,
                },
                subject_type="device",
                subject_id=dev_id,
                subject_name=detail.name,
            )


# --- device ports ----------------------------------------------------------

_DEVICE_PORT_BINDINGS = {
    "device_id", "device_name", "device_model",
    "port_idx", "port_state", "port_connector",
    "port_speed_mbps", "port_max_speed_mbps",
    "poe_state", "poe_standard", "poe_type", "poe_enabled",
}


@register(
    "device_ports",
    _DEVICE_PORT_BINDINGS,
    doc="Every switch/gateway port on every device, flattened with its PoE state.",
)
def _device_ports(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    for dev_id, detail in snapshot.device_details.items():
        for port in detail.interfaces.ports:
            poe = port.poe
            yield Row(
                vars={
                    "device_id": dev_id,
                    "device_name": detail.name,
                    "device_model": detail.model,
                    "port_idx": port.idx,
                    "port_state": port.state,
                    "port_connector": port.connector,
                    "port_speed_mbps": port.speed_mbps,
                    "port_max_speed_mbps": port.max_speed_mbps,
                    "poe_state": poe.state if poe else None,
                    "poe_standard": poe.standard if poe else None,
                    "poe_type": poe.type if poe else None,
                    "poe_enabled": poe.enabled if poe else None,
                },
                subject_type="device",
                subject_id=dev_id,
                subject_name=detail.name,
            )


# --- configuration plane ---------------------------------------------------

_NETWORK_BINDINGS = {
    "network_id", "network_name", "network_enabled", "network_vlan_id",
    "network_management", "network_is_default", "network_isolation_enabled",
    "network_internet_access_enabled", "network_dhcp_mode",
    "network_cidr", "network_prefix_length", "network_usable_hosts",
    "network_client_count", "network_pool_pressure",
    "network_trusted_dhcp_servers",
}


@register(
    "networks",
    _NETWORK_BINDINGS,
    doc=(
        "Configured networks (VLANs) with their address space, joined to the "
        "clients addressed inside it. Yields nothing when the config plane is "
        "unreadable, which is how every rule over it stays quiet."
    ),
)
def _networks(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    if snapshot.config is None:
        return
    client_ips = [c.ip_address for c in snapshot.clients]
    for net in snapshot.config.networks:
        ipv4 = net.ipv4_configuration
        cidr = subnets.network_of(
            ipv4.host_ip_address if ipv4 else None,
            ipv4.prefix_length if ipv4 else None,
        )
        usable = subnets.usable_hosts(cidr)
        clients = subnets.hosts_in(cidr, client_ips)
        guarding = net.dhcp_guarding
        yield Row(
            vars={
                "network_id": net.id,
                "network_name": net.name,
                "network_enabled": net.enabled,
                "network_vlan_id": net.vlan_id,
                "network_management": net.management,
                "network_is_default": net.default,
                "network_isolation_enabled": net.isolation_enabled,
                "network_internet_access_enabled": net.internet_access_enabled,
                "network_dhcp_mode": (
                    ipv4.dhcp_configuration.mode
                    if ipv4 and ipv4.dhcp_configuration else None
                ),
                "network_cidr": str(cidr) if cidr else None,
                "network_prefix_length": cidr.prefixlen if cidr else None,
                "network_usable_hosts": usable,
                "network_client_count": clients,
                "network_pool_pressure": subnets.pool_pressure(clients, usable),
                "network_trusted_dhcp_servers": (
                    len(guarding.trusted_dhcp_server_ip_addresses) if guarding else 0
                ),
            },
            subject_type="site",
            subject_id=net.id,
            subject_name=net.name,
        )


_WIFI_BINDINGS = {
    "wifi_id", "wifi_name", "wifi_enabled", "wifi_type", "wifi_security",
    "wifi_encryption", "wifi_pmf_mode", "wifi_fast_roaming_enabled",
    "wifi_hidden", "wifi_client_isolation_enabled", "wifi_band_steering_enabled",
    "wifi_bss_transition_enabled", "wifi_mlo_enabled", "wifi_uapsd_enabled",
    "wifi_bands", "wifi_band_count", "wifi_on_24", "wifi_on_5", "wifi_on_6",
    "wifi_basic_rate_24_kbps", "wifi_basic_rate_5_kbps",
    "wifi_mac_filter_action", "wifi_mac_filter_count",
}


@register(
    "wifi_broadcasts",
    _WIFI_BINDINGS,
    doc=(
        "Each broadcast WiFi network with its security and roaming settings, "
        "from the Integration API config plane (Network 10.x and later)."
    ),
)
def _wifi_broadcasts(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    if snapshot.config is None:
        return
    for wifi in snapshot.config.wifi:
        security = wifi.security_configuration
        bands = sorted(wifi.broadcasting_frequencies_ghz)
        mac_filter = wifi.client_filtering_policy
        yield Row(
            vars={
                "wifi_id": wifi.id,
                "wifi_name": wifi.name,
                "wifi_enabled": wifi.enabled,
                "wifi_type": wifi.type,
                "wifi_security": security.type if security else None,
                "wifi_encryption": security.encryption if security else None,
                "wifi_pmf_mode": security.pmf_mode if security else None,
                "wifi_fast_roaming_enabled": (
                    security.fast_roaming_enabled if security else None
                ),
                "wifi_hidden": wifi.hide_name,
                "wifi_client_isolation_enabled": wifi.client_isolation_enabled,
                "wifi_band_steering_enabled": wifi.band_steering_enabled,
                "wifi_bss_transition_enabled": wifi.bss_transition_enabled,
                "wifi_mlo_enabled": wifi.mlo_enabled,
                "wifi_uapsd_enabled": wifi.uapsd_enabled,
                # Rendered rather than raw: a template cannot format a list, and
                # "2.4, 5" is what the prose wants to say.
                "wifi_bands": ", ".join(f"{b:g}" for b in bands) or None,
                "wifi_band_count": len(bands),
                "wifi_on_24": 2.4 in bands,
                "wifi_on_5": 5 in bands,
                "wifi_on_6": 6 in bands,
                "wifi_basic_rate_24_kbps": wifi.basic_data_rate_kbps.get("2.4"),
                "wifi_basic_rate_5_kbps": wifi.basic_data_rate_kbps.get("5"),
                "wifi_mac_filter_action": mac_filter.action if mac_filter else None,
                "wifi_mac_filter_count": (
                    len(mac_filter.mac_address_filter) if mac_filter else 0
                ),
            },
            subject_type="site",
            subject_id=wifi.id,
            subject_name=wifi.name,
        )


# --- trends ----------------------------------------------------------------

# Metrics whose movement is worth reporting on its own. Everything else is
# still stored and still charted; it just doesn't raise a finding when it
# wanders, because a port renegotiating or a client count rising is not news.
_WATCHED_METRICS = {
    "device.cpu_pct": "CPU",
    "device.mem_pct": "memory",
    "radio.tx_retries_pct": "TX retries",
    "wan.latency_ms": "WAN latency",
    "wan.loss_pct": "WAN packet loss",
    "dns.blocked_pct": "DNS blocks",
    "network.pool_pressure_pct": "address pool use",
    "site.health_score": "health score",
}

# Where a metric becomes a problem, for the rules that ask when the trend gets
# there. Only metrics with a real ceiling appear: "days until the client count
# reaches 100" is arithmetic, not a warning.
_FORECAST_TARGET = {
    "device.cpu_pct": 90.0,
    "device.mem_pct": 90.0,
    "radio.tx_retries_pct": 20.0,
    "wan.loss_pct": 5.0,
    "network.pool_pressure_pct": 100.0,
}

_TREND_BINDINGS = {
    "metric", "metric_label", "metric_watched", "subject_id", "subject_name",
    "sample_count", "latest", "median", "mad", "zscore", "zscore_abs", "ewma",
    "slope_per_day", "forecast_target", "days_to_target",
    "changepoint_at", "changepoint_direction", "changepoint_before", "changepoint_after",
}


def _with_current_scan(snapshot: Snapshot, history: RunHistory) -> list[timeseries.Series]:
    """Stored history with this scan's own readings appended.

    Metrics are written to the database after the rules have run, so the
    history a rule is handed stops one scan short. Judging the newest reading
    is most of the point of having a baseline, so it is added here rather than
    left for the next scan to notice.
    """
    at = snapshot.collected_at
    live = {(r.metric, r.subject_id): r for r in metrics.readings(snapshot)}
    merged: list[timeseries.Series] = []
    for series in history.series:
        reading = live.pop((series.metric, series.subject_id), None)
        points = list(series.points)
        if reading is not None:
            points.append(timeseries.Point(at=at, value=reading.value))
        merged.append(timeseries.Series(
            metric=series.metric,
            subject_id=series.subject_id,
            subject_name=reading.subject_name if reading else series.subject_name,
            points=points,
        ))
    # Anything measured for the first time this scan: one point, no statistics,
    # and every rule requires a sample count it cannot yet meet.
    merged.extend(
        timeseries.Series(
            metric=reading.metric, subject_id=reading.subject_id,
            subject_name=reading.subject_name,
            points=[timeseries.Point(at=at, value=reading.value)],
        )
        for reading in live.values()
    )
    return merged


@register(
    "metric_trends",
    _TREND_BINDINGS,
    doc=(
        "One row per stored metric and subject, with robust statistics over its "
        "history: deviation from its own normal, trend per day, time to a "
        "threshold, and any sustained step change."
    ),
)
def _metric_trends(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    for series in _with_current_scan(snapshot, history):
        values = series.values
        latest = series.latest
        if latest is None:
            continue
        # The point being judged is not part of the baseline it is judged
        # against; leaving it in drags the median toward it and hides exactly
        # the reading worth reporting.
        baseline = values[:-1]
        zscore = timeseries.modified_zscore(latest.value, baseline)
        target = _FORECAST_TARGET.get(series.metric)
        changepoint = timeseries.cusum_changepoint(series.points)
        yield Row(
            vars={
                "metric": series.metric,
                "metric_label": _WATCHED_METRICS.get(series.metric, series.metric),
                "metric_watched": series.metric in _WATCHED_METRICS,
                "subject_id": series.subject_id,
                "subject_name": series.subject_name,
                "sample_count": len(values),
                "latest": latest.value,
                "median": statistics.median(values),
                "mad": timeseries.mad(values),
                "zscore": zscore,
                # Predicates compare, they do not compute -- and "how far out,
                # either way" is the comparison every anomaly rule wants.
                "zscore_abs": abs(zscore) if zscore is not None else None,
                "ewma": timeseries.ewma(values),
                "slope_per_day": timeseries.theil_sen_slope(series.points),
                "forecast_target": target,
                "days_to_target": (
                    timeseries.days_until(series.points, target)
                    if target is not None else None
                ),
                "changepoint_at": (
                    changepoint.at.strftime("%Y-%m-%d %H:%M UTC") if changepoint else None
                ),
                "changepoint_direction": changepoint.direction if changepoint else None,
                "changepoint_before": changepoint.before if changepoint else None,
                "changepoint_after": changepoint.after if changepoint else None,
            },
            # Device-scoped metrics keep their device subject so a finding lands
            # on the hardware; everything else is site-scoped, including the
            # per-network pool metrics, which have no device behind them.
            subject_type=(
                "device"
                if series.metric.split(".")[0] in ("device", "port", "radio")
                else "site"
            ),
            subject_id=series.subject_id,
            subject_name=series.subject_name,
        )


# --- topology and wired capacity -------------------------------------------


def _topology(snapshot: Snapshot) -> topology.Topology:
    """The uplink tree for this snapshot, built from the device details."""
    by_id = {d.id: d for d in snapshot.devices}
    return topology.build([
        topology.Link(
            device_id=dev_id,
            name=detail.name or detail.model or dev_id,
            uplink_id=detail.uplink.device_id if detail.uplink else None,
            model=detail.model,
            kind=topology.device_kind(by_id[dev_id], detail) if dev_id in by_id else "other",
        )
        for dev_id, detail in snapshot.device_details.items()
    ])


_TOPOLOGY_BINDINGS = {
    "device_id", "device_name", "device_model", "device_kind",
    "uplink_name", "uplink_depth", "is_root", "in_uplink_cycle",
    "downstream_devices", "downstream_aps", "downstream_names",
}


@register(
    "device_topology",
    _TOPOLOGY_BINDINGS,
    doc=(
        "Every device with its place in the uplink tree: how many hops from the "
        "gateway it sits, and what loses its path if it stops."
    ),
)
def _device_topology(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    tree = _topology(snapshot)
    for dev_id, link in tree.links.items():
        downstream = tree.descendants(dev_id)
        uplink = tree.links.get(link.uplink_id) if link.uplink_id else None
        yield Row(
            vars={
                "device_id": dev_id,
                "device_name": link.name,
                "device_model": link.model,
                "device_kind": link.kind,
                "uplink_name": uplink.name if uplink else None,
                "uplink_depth": tree.depth(dev_id),
                "is_root": dev_id in tree.roots,
                "in_uplink_cycle": dev_id in tree.cyclic,
                "downstream_devices": len(downstream),
                "downstream_aps": sum(
                    1 for d in downstream if tree.links[d].kind == "access_point"
                ),
                # Rendered here because a template cannot join a list, and the
                # names are what makes a blast radius mean something.
                "downstream_names": ", ".join(
                    sorted(tree.links[d].name for d in downstream)
                ) or None,
            },
            subject_type="device",
            subject_id=dev_id,
            subject_name=link.name,
        )


_SWITCH_CAPACITY_BINDINGS = {
    "device_id", "device_name", "device_model",
    "uplink_speed_mbps", "downstream_speed_mbps", "oversubscription_ratio",
    "active_ports", "poe_powered_ports", "poe_demand_w", "poe_budget_w",
    "poe_utilization",
}


@register(
    "switch_capacity",
    _SWITCH_CAPACITY_BINDINGS,
    doc=(
        "Per switching device: link speed behind the uplink against the uplink "
        "itself, and committed PoE against the model's published budget. The "
        "PoE bindings are null for a model whose budget is not known."
    ),
)
def _switch_capacity(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    tree = _topology(snapshot)
    for dev_id, detail in snapshot.device_details.items():
        link = tree.links.get(dev_id)
        if link is None or link.kind not in ("switch", "gateway"):
            continue
        up_ports = [p for p in detail.interfaces.ports if p.state == "UP"]
        # The uplink is the fastest connected port: the API does not say which
        # port faces the parent, and on a correctly wired switch the widest
        # link is the one that does.
        uplink_mbps = max((p.speed_mbps or 0 for p in up_ports), default=0)
        downstream_mbps = sum(p.speed_mbps or 0 for p in up_ports) - uplink_mbps
        over = capacity.oversubscription(uplink_mbps, downstream_mbps)
        # Only ports actually delivering power count. A PoE-capable port with
        # nothing attached reports enabled with its state DOWN, and counting
        # those would have every populated switch claiming several times its
        # own budget.
        poe = capacity.poe_load(
            [
                (p.poe.standard, p.poe.type, p.poe.state in ("UP", "LIMITED"))
                for p in detail.interfaces.ports if p.poe is not None
            ],
            detail.model,
        )
        yield Row(
            vars={
                "device_id": dev_id,
                "device_name": link.name,
                "device_model": detail.model,
                "uplink_speed_mbps": over.uplink_mbps,
                "downstream_speed_mbps": over.downstream_mbps,
                "oversubscription_ratio": over.ratio,
                "active_ports": len(up_ports),
                "poe_powered_ports": poe.powered_ports,
                "poe_demand_w": poe.demand_w,
                "poe_budget_w": poe.budget_w,
                "poe_utilization": poe.utilization,
            },
            subject_type="device",
            subject_id=dev_id,
            subject_name=link.name,
        )


_STACK_BINDINGS = {
    "stack_id", "stack_name", "stack_unit_count",
    "stack_active_controllers", "stack_backup_controllers",
}


@register(
    "switch_stacks",
    _STACK_BINDINGS,
    doc=(
        "Configured switch stacks and the role each member holds. Yields "
        "nothing when the config plane is unreadable or nothing is stacked."
    ),
)
def _switch_stacks(snapshot: Snapshot, history: RunHistory) -> Iterator[Row]:
    if snapshot.config is None:
        return
    for stack in snapshot.config.switch_stacks:
        roles = [u.role for u in stack.units]
        yield Row(
            vars={
                "stack_id": stack.id,
                "stack_name": stack.name or stack.id,
                "stack_unit_count": len(stack.units),
                "stack_active_controllers": roles.count("ACTIVE_CONTROLLER"),
                "stack_backup_controllers": roles.count("BACKUP_CONTROLLER"),
            },
            subject_type="device",
            subject_id=stack.device_id or stack.id,
            subject_name=stack.name or stack.id,
        )
