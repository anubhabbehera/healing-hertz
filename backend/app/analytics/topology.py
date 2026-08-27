"""The shape of the wired path each device depends on.

Every adopted device reports the device it uplinks through, which is enough to
rebuild the tree the site is actually wired as. What that tree answers is not
"is this device healthy" -- the device rules already do that -- but "what else
stops when this one does", and "how many hops from the gateway is this".

Uplinks form a tree, not a general graph: each device names exactly one parent,
so a cut anywhere disconnects precisely the subtree below it and no cycle
detection is needed for the common case. Consoles do occasionally report a
cycle anyway (a mesh AP pair that each thinks it uplinks via the other), so
every traversal here is written to terminate on one rather than to trust the
data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.unifi.models import DeviceDetail, DeviceOverview


def device_kind(overview: DeviceOverview, detail: DeviceDetail | None) -> str:
    """Coarse hardware class, from whichever feature shape the API returned."""
    if detail is not None and detail.features is not None:
        if detail.features.gateway is not None:
            return "gateway"
        if detail.features.access_point is not None:
            return "access_point"
        if detail.features.switching is not None:
            return "switch"
    features = set(overview.features)
    if "gateway" in features:
        return "gateway"
    if "accessPoint" in features:
        return "access_point"
    if "switching" in features:
        return "switch"
    return "other"


@dataclass(frozen=True)
class Link:
    """One device and the device it uplinks through."""

    device_id: str
    name: str
    uplink_id: str | None = None
    model: str = ""
    kind: str = "other"  # gateway | switch | access_point | other


@dataclass
class Topology:
    links: dict[str, Link] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=dict)
    # Devices with no uplink, or whose uplink names a device that is not
    # adopted here. Usually just the gateway.
    roots: list[str] = field(default_factory=list)
    # Devices whose uplink chain never reaches a root: a reported cycle.
    cyclic: set[str] = field(default_factory=set)

    def depth(self, device_id: str) -> int | None:
        """Uplink hops to a root. 0 for a root, None inside a cycle."""
        seen: set[str] = set()
        hops = 0
        current = device_id
        while True:
            if current in seen:
                return None
            seen.add(current)
            uplink = self.links[current].uplink_id if current in self.links else None
            if uplink is None or uplink not in self.links:
                return hops
            hops += 1
            current = uplink

    def descendants(self, device_id: str) -> set[str]:
        """Every device that loses its path to the root if this one goes."""
        out: set[str] = set()
        stack = list(self.children.get(device_id, ()))
        while stack:
            current = stack.pop()
            if current in out or current == device_id:
                continue
            out.add(current)
            stack.extend(self.children.get(current, ()))
        return out

    def path_to_root(self, device_id: str) -> list[str]:
        """This device, then each uplink above it, ending at a root."""
        path = [device_id]
        seen = {device_id}
        current = device_id
        while True:
            uplink = self.links[current].uplink_id if current in self.links else None
            if uplink is None or uplink not in self.links or uplink in seen:
                return path
            path.append(uplink)
            seen.add(uplink)
            current = uplink


def build(links: list[Link]) -> Topology:
    """Index the uplink reports into a tree that can be walked either way."""
    topology = Topology(links={link.device_id: link for link in links})
    for link in links:
        parent = link.uplink_id
        if parent is None or parent not in topology.links or parent == link.device_id:
            topology.roots.append(link.device_id)
            continue
        topology.children.setdefault(parent, []).append(link.device_id)

    for device_id in topology.links:
        if topology.depth(device_id) is None:
            topology.cyclic.add(device_id)
    return topology


@dataclass(frozen=True)
class BlastRadius:
    device_id: str
    name: str
    kind: str
    depth: int | None
    downstream: int
    downstream_names: list[str]
    # Access points below this device, counted separately: they are where the
    # clients are, so they are what an outage is felt through.
    downstream_aps: int


def blast_radius(topology: Topology) -> list[BlastRadius]:
    """Every device that has something behind it, widest blast radius first."""
    out = []
    for device_id, link in topology.links.items():
        downstream = topology.descendants(device_id)
        if not downstream:
            continue
        out.append(BlastRadius(
            device_id=device_id,
            name=link.name,
            kind=link.kind,
            depth=topology.depth(device_id),
            downstream=len(downstream),
            downstream_names=sorted(
                topology.links[d].name or d for d in downstream
            ),
            downstream_aps=sum(
                1 for d in downstream if topology.links[d].kind == "access_point"
            ),
        ))
    return sorted(out, key=lambda b: (-b.downstream, b.name))
