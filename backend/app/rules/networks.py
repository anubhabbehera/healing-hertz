"""Rules over the configured address space.

Both checks are pairwise: they compare every network against every other one,
which is a shape the declarative form cannot express -- a source row is one
network, and no predicate over a single row can see a collision with another.
The arithmetic itself lives in app/analytics/subnets.py.
"""

from __future__ import annotations

from app.analytics import subnets
from app.collectors.snapshot import Snapshot

from .base import Binding, RunHistory


def _configured_networks(snapshot: Snapshot) -> list:
    return list(snapshot.config.networks) if snapshot.config else []


class SubnetOverlap:
    """Configured networks whose IPv4 ranges intersect.

    Overlapping ranges make routing and firewall rules ambiguous: a destination
    inside the shared space matches two networks, and which one wins is not
    something the operator chose.
    """

    id = "network.subnet_overlap"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        items = []
        for net in _configured_networks(snapshot):
            ipv4 = net.ipv4_configuration
            cidr = subnets.network_of(
                ipv4.host_ip_address if ipv4 else None,
                ipv4.prefix_length if ipv4 else None,
            )
            if cidr is not None:
                items.append((net.id, net.name or net.id, cidr))

        return [
            Binding(
                vars={
                    "network_a": overlap.a_name,
                    "cidr_a": overlap.a_cidr,
                    "network_b": overlap.b_name,
                    "cidr_b": overlap.b_cidr,
                },
                subject_type="site",
                # Stable across scans, so a dismissal keyed to this pair keeps
                # applying while the pair still exists.
                subject_id=f"{overlap.a_id}:{overlap.b_id}",
                subject_name=f"{overlap.a_name} / {overlap.b_name}",
            )
            for overlap in subnets.overlapping(items)
        ]


class DuplicateVlanId:
    """One VLAN id configured on more than one network.

    The tag is what the switch forwards on, so two networks sharing an id are
    one broadcast domain wearing two names -- and any isolation configured on
    one of them is not the isolation the other one gets.
    """

    id = "network.vlan_id_reuse"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Binding]:
        items = [
            (net.id, net.name or net.id, net.vlan_id)
            for net in _configured_networks(snapshot)
        ]
        return [
            Binding(
                vars={
                    "vlan_id": vlan_id,
                    "networks": ", ".join(names),
                    "network_count": len(names),
                },
                subject_type="site",
                subject_id=f"vlan-{vlan_id}",
                subject_name=f"VLAN {vlan_id}",
            )
            for vlan_id, names in sorted(subnets.duplicate_vlan_ids(items).items())
        ]
