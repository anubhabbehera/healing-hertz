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

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from app.analytics import subnets
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
