from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UnifiModel(BaseModel):
    """New API fields must never break parsing."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Page[T](UnifiModel):
    offset: int = 0
    limit: int = 0
    count: int = 0
    total_count: int = Field(0, alias="totalCount")
    data: list[T] = []


class AppInfo(UnifiModel):
    application_version: str = Field("", alias="applicationVersion")


class Site(UnifiModel):
    id: str
    internal_reference: str | None = Field(None, alias="internalReference")
    name: str = ""


class PortPoe(UnifiModel):
    standard: str | None = None
    type: int | None = None
    enabled: bool = False
    state: str | None = None  # UP | DOWN | LIMITED


class Port(UnifiModel):
    idx: int
    state: str = ""  # UP | DOWN
    connector: str | None = None  # RJ45 | SFP | SFPPLUS | ...
    max_speed_mbps: int | None = Field(None, alias="maxSpeedMbps")
    speed_mbps: int | None = Field(None, alias="speedMbps")
    poe: PortPoe | None = None


class Radio(UnifiModel):
    wlan_standard: str | None = Field(None, alias="wlanStandard")
    frequency_ghz: float | None = Field(None, alias="frequencyGHz")
    channel_width_mhz: int | None = Field(None, alias="channelWidthMHz")
    channel: int | None = None


class DeviceInterfaces(UnifiModel):
    ports: list[Port] = []
    radios: list[Radio] = []


class DeviceFeatures(UnifiModel):
    switching: object | None = None
    access_point: object | None = Field(None, alias="accessPoint")
    gateway: object | None = None


class DeviceOverview(UnifiModel):
    id: str
    mac_address: str = Field("", alias="macAddress")
    ip_address: str | None = Field(None, alias="ipAddress")
    name: str = ""
    model: str = ""
    state: str = ""  # ONLINE | OFFLINE | PENDING_ADOPTION | ...
    supported: bool = True
    firmware_version: str | None = Field(None, alias="firmwareVersion")
    firmware_updatable: bool = Field(False, alias="firmwareUpdatable")
    features: list[str] = []  # ["switching", "accessPoint", "gateway"]


class DeviceUplink(UnifiModel):
    device_id: str | None = Field(None, alias="deviceId")


class DeviceDetail(UnifiModel):
    id: str
    mac_address: str = Field("", alias="macAddress")
    ip_address: str | None = Field(None, alias="ipAddress")
    name: str = ""
    model: str = ""
    state: str = ""
    supported: bool = True
    firmware_version: str | None = Field(None, alias="firmwareVersion")
    firmware_updatable: bool = Field(False, alias="firmwareUpdatable")
    features: DeviceFeatures | None = None
    uplink: DeviceUplink | None = None
    interfaces: DeviceInterfaces = DeviceInterfaces()

    @property
    def is_access_point(self) -> bool:
        return self.features is not None and self.features.access_point is not None

    @property
    def is_gateway(self) -> bool:
        return self.features is not None and self.features.gateway is not None


class RadioStats(UnifiModel):
    frequency_ghz: float | None = Field(None, alias="frequencyGHz")
    tx_retries_pct: float | None = Field(None, alias="txRetriesPct")


class UplinkStats(UnifiModel):
    tx_rate_bps: int | None = Field(None, alias="txRateBps")
    rx_rate_bps: int | None = Field(None, alias="rxRateBps")


class StatsInterfaces(UnifiModel):
    radios: list[RadioStats] = []


class DeviceStats(UnifiModel):
    uptime_sec: int | None = Field(None, alias="uptimeSec")
    last_heartbeat_at: datetime | None = Field(None, alias="lastHeartbeatAt")
    load_average_1_min: float | None = Field(None, alias="loadAverage1Min")
    load_average_5_min: float | None = Field(None, alias="loadAverage5Min")
    load_average_15_min: float | None = Field(None, alias="loadAverage15Min")
    cpu_utilization_pct: float | None = Field(None, alias="cpuUtilizationPct")
    memory_utilization_pct: float | None = Field(None, alias="memoryUtilizationPct")
    uplink: UplinkStats | None = None
    interfaces: StatsInterfaces = StatsInterfaces()


class ClientAccess(UnifiModel):
    type: str | None = None  # DEFAULT | GUEST
    authorized: bool | None = None


class ClientOverview(UnifiModel):
    id: str
    name: str | None = None
    type: str = ""  # WIRED | WIRELESS | VPN
    mac_address: str = Field("", alias="macAddress")
    ip_address: str | None = Field(None, alias="ipAddress")
    connected_at: datetime | None = Field(None, alias="connectedAt")
    access: ClientAccess | None = None


class PendingDevice(UnifiModel):
    id: str
    mac_address: str = Field("", alias="macAddress")
    name: str = ""
    model: str = ""


# --- configuration plane ---------------------------------------------------
#
# Networks and WiFi broadcasts are configuration, not telemetry: they change
# when an operator changes them, not between scans. Network 10.x exposes them
# read-only, which is what makes a config audit possible without a local admin.


class NetworkDhcp(UnifiModel):
    mode: str | None = None  # SERVER | RELAY


class NetworkIpv4(UnifiModel):
    auto_scale_enabled: bool | None = Field(None, alias="autoScaleEnabled")
    host_ip_address: str | None = Field(None, alias="hostIpAddress")
    prefix_length: int | None = Field(None, alias="prefixLength")
    dhcp_configuration: NetworkDhcp | None = Field(None, alias="dhcpConfiguration")


class NetworkDhcpGuarding(UnifiModel):
    trusted_dhcp_server_ip_addresses: list[str] = Field(
        default_factory=list, alias="trustedDhcpServerIpAddresses"
    )


class Network(UnifiModel):
    id: str
    name: str = ""
    enabled: bool = True
    management: str | None = None  # UNMANAGED | GATEWAY | SWITCH
    vlan_id: int | None = Field(None, alias="vlanId")
    default: bool = False
    # Only the gateway/switch-managed variants carry these; an unmanaged
    # network leaves them null, which every rule over them treats as "unknown".
    isolation_enabled: bool | None = Field(None, alias="isolationEnabled")
    internet_access_enabled: bool | None = Field(None, alias="internetAccessEnabled")
    ipv4_configuration: NetworkIpv4 | None = Field(None, alias="ipv4Configuration")
    dhcp_guarding: NetworkDhcpGuarding | None = Field(None, alias="dhcpGuarding")


class WifiSecurity(UnifiModel):
    # OPEN | WPA2_PERSONAL | WPA3_PERSONAL | WPA2_WPA3_PERSONAL | *_ENTERPRISE
    type: str | None = None
    # Only on an OPEN network: ENHANCED_OPEN(_WITH_TRANSITION) is OWE, which is
    # encrypted despite the type saying open.
    encryption: str | None = None
    pmf_mode: str | None = Field(None, alias="pmfMode")
    fast_roaming_enabled: bool | None = Field(None, alias="fastRoamingEnabled")


class WifiClientFiltering(UnifiModel):
    action: str | None = None  # ALLOW | BLOCK
    mac_address_filter: list[str] = Field(default_factory=list, alias="macAddressFilter")


class WifiBroadcast(UnifiModel):
    id: str
    name: str = ""
    enabled: bool = True
    type: str | None = None  # STANDARD | IOT_OPTIMIZED
    security_configuration: WifiSecurity | None = Field(None, alias="securityConfiguration")
    hide_name: bool | None = Field(None, alias="hideName")
    client_isolation_enabled: bool | None = Field(None, alias="clientIsolationEnabled")
    band_steering_enabled: bool | None = Field(None, alias="bandSteeringEnabled")
    bss_transition_enabled: bool | None = Field(None, alias="bssTransitionEnabled")
    mlo_enabled: bool | None = Field(None, alias="mloEnabled")
    uapsd_enabled: bool | None = Field(None, alias="uapsdEnabled")
    broadcasting_frequencies_ghz: list[float] = Field(
        default_factory=list, alias="broadcastingFrequenciesGHz"
    )
    # Keyed by band as a string, exactly as the API sends it ("2.4", "5").
    basic_data_rate_kbps: dict[str, int] = Field(
        default_factory=dict, alias="basicDataRateKbpsByFrequencyGHz"
    )
    client_filtering_policy: WifiClientFiltering | None = Field(
        None, alias="clientFilteringPolicy"
    )
