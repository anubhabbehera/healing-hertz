from __future__ import annotations

from app.collectors.snapshot import Snapshot

from .base import Binding, RunHistory


class GatewaySaturation:
    """Uplink throughput against the gateway's fastest connected port.

    Not declarative: capacity comes from the widest UP port on the device
    detail, while throughput comes from the stats record, so the rule has to
    join two collections and reduce one of them before it can compare anything.
    """

    id = "site.gateway_saturation"
    _threshold = 0.85

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        bindings = []
        for dev_id, detail in snapshot.device_details.items():
            if not detail.is_gateway:
                continue
            stats = snapshot.device_stats.get(dev_id)
            if stats is None or stats.uplink is None:
                continue
            tx, rx = stats.uplink.tx_rate_bps or 0, stats.uplink.rx_rate_bps or 0
            up_ports = [p.speed_mbps for p in detail.interfaces.ports
                        if p.state == "UP" and p.speed_mbps]
            if not up_ports:
                continue
            capacity_mbps = max(up_ports)
            capacity_bps = capacity_mbps * 1_000_000
            utilization = (tx + rx) / capacity_bps if capacity_bps else 0
            if utilization < self._threshold:
                continue
            bindings.append(Binding(
                vars={
                    "device_name": detail.name,
                    "utilization": utilization,
                    "throughput_mbps": (tx + rx) / 1e6,
                    "capacity_mbps": capacity_mbps,
                    "tx_rate_bps": tx,
                    "rx_rate_bps": rx,
                },
                subject_type="device",
                subject_id=dev_id,
                subject_name=detail.name,
            ))
        return bindings
