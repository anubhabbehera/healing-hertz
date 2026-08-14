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
