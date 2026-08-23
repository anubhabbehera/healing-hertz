"""IPv4 address-space arithmetic for the configured networks.

The console reports a network as a host address plus a prefix length, which is
not directly comparable to anything: two networks overlap or they do not, and a
DHCP pool is under pressure or it is not, only once both are expressed as
address ranges. That conversion, and the counting on top of it, is all this
module does.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

IPv4Network = ipaddress.IPv4Network


def network_of(host_ip: str | None, prefix_length: int | None) -> IPv4Network | None:
    """The subnet a gateway host address sits in, or None if unusable.

    ``192.168.1.1/24`` describes the network ``192.168.1.0/24``; strict=False is
    what does that truncation, and is the whole reason this is not a one-liner
    at each call site.
    """
    if not host_ip or prefix_length is None:
        return None
    try:
        return ipaddress.ip_network(f"{host_ip}/{prefix_length}", strict=False)  # type: ignore[return-value]
    except ValueError:
        return None


def usable_hosts(network: IPv4Network | None) -> int | None:
    """Addresses a client can actually be given.

    The network and broadcast addresses are not assignable, except on /31 and
    /32, which have neither in the usual sense (RFC 3021 point-to-point links).
    """
    if network is None:
        return None
    if network.prefixlen >= 31:
        return network.num_addresses
    return network.num_addresses - 2


def hosts_in(network: IPv4Network | None, addresses: list[str | None]) -> int:
    """How many of these client addresses fall inside this network."""
    if network is None:
        return 0
    count = 0
    for addr in addresses:
        if not addr:
            continue
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.version == 4 and ip in network:
            count += 1
    return count


def pool_pressure(clients: int, usable: int | None) -> float | None:
    """Fraction of the assignable address space currently in use."""
    if not usable or usable <= 0:
        return None
    return clients / usable


@dataclass(frozen=True)
class Overlap:
    """Two configured networks whose address ranges intersect.

    Always a containment: two CIDR blocks are either disjoint or one is wholly
    inside the other, so there is no partial-overlap case to distinguish. The
    wider network is always ``a``.
    """

    a_id: str
    a_name: str
    a_cidr: str
    b_id: str
    b_name: str
    b_cidr: str


def overlapping(items: list[tuple[str, str, IPv4Network]]) -> list[Overlap]:
    """Every pair of (id, name, network) whose address space intersects.

    Each unordered pair is reported once, wider network first -- the wider one
    is the context, the narrower one is what got carved out of it.
    """
    found: list[Overlap] = []
    for i, (a_id, a_name, a_net) in enumerate(items):
        for b_id, b_name, b_net in items[i + 1:]:
            if not a_net.overlaps(b_net):
                continue
            wider, narrower = (a_id, a_name, a_net), (b_id, b_name, b_net)
            if a_net.prefixlen > b_net.prefixlen:
                wider, narrower = narrower, wider
            found.append(Overlap(
                a_id=wider[0], a_name=wider[1], a_cidr=str(wider[2]),
                b_id=narrower[0], b_name=narrower[1], b_cidr=str(narrower[2]),
            ))
    return found


def duplicate_vlan_ids(items: list[tuple[str, str, int | None]]) -> dict[int, list[str]]:
    """VLAN id -> names of the networks sharing it, for ids used more than once."""
    by_vlan: dict[int, list[str]] = {}
    for _net_id, name, vlan_id in items:
        if vlan_id is None:
            continue
        by_vlan.setdefault(vlan_id, []).append(name)
    return {vlan: names for vlan, names in by_vlan.items() if len(names) > 1}
