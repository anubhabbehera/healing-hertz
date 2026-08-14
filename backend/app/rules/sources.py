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
