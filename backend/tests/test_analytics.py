"""Unit tests for the deterministic analysis helpers.

These are pure functions, so they are tested on values rather than through a
scan: the arithmetic is the thing being pinned, and a rule that quotes it can
only be as right as this.
"""

import ipaddress

import pytest

from app.analytics import subnets


def net(cidr: str) -> ipaddress.IPv4Network:
    return ipaddress.ip_network(cidr)


# --- address space ---------------------------------------------------------


def test_network_of_truncates_a_host_address_to_its_subnet():
    assert subnets.network_of("192.168.1.1", 24) == net("192.168.1.0/24")
    assert subnets.network_of("192.168.1.129", 25) == net("192.168.1.128/25")


@pytest.mark.parametrize("host,prefix", [
    (None, 24), ("192.168.1.1", None), ("", 24),
    ("not-an-address", 24), ("192.168.1.1", 33),
])
def test_network_of_returns_none_for_anything_unusable(host, prefix):
    assert subnets.network_of(host, prefix) is None


@pytest.mark.parametrize("cidr,usable", [
    ("192.168.1.0/24", 254),
    ("192.168.1.0/25", 126),
    ("192.168.1.0/29", 6),
    # RFC 3021 point-to-point: no network/broadcast pair to subtract.
    ("192.168.1.0/31", 2),
    ("192.168.1.1/32", 1),
])
def test_usable_hosts_excludes_network_and_broadcast(cidr, usable):
    assert subnets.usable_hosts(net(cidr)) == usable


def test_usable_hosts_of_nothing_is_none():
    assert subnets.usable_hosts(None) is None


def test_hosts_in_counts_only_addresses_inside_the_network():
    inside = subnets.hosts_in(
        net("192.168.1.0/24"),
        ["192.168.1.10", "192.168.2.10", None, "", "garbage", "192.168.1.254"],
    )
    assert inside == 2


def test_hosts_in_ignores_ipv6():
    assert subnets.hosts_in(net("192.168.1.0/24"), ["fe80::1"]) == 0


def test_pool_pressure_is_a_fraction_and_none_without_a_denominator():
    assert subnets.pool_pressure(3, 6) == 0.5
    assert subnets.pool_pressure(3, 0) is None
    assert subnets.pool_pressure(3, None) is None


# --- overlaps --------------------------------------------------------------


def test_overlapping_reports_each_pair_once_widest_first():
    found = subnets.overlapping([
        ("a", "Default", net("192.168.1.0/24")),
        ("b", "Lab", net("192.168.1.128/25")),
    ])
    assert len(found) == 1
    overlap = found[0]
    assert (overlap.a_name, overlap.a_cidr) == ("Default", "192.168.1.0/24")
    assert (overlap.b_name, overlap.b_cidr) == ("Lab", "192.168.1.128/25")


def test_overlapping_orders_the_wider_network_first_whichever_way_round():
    for items in (
        [("a", "A", net("10.0.0.0/16")), ("b", "B", net("10.0.1.0/24"))],
        [("b", "B", net("10.0.1.0/24")), ("a", "A", net("10.0.0.0/16"))],
    ):
        found = subnets.overlapping(items)
        assert [(o.a_name, o.b_name) for o in found] == [("A", "B")]


def test_overlapping_is_quiet_when_nothing_intersects():
    assert subnets.overlapping([
        ("a", "A", net("192.168.1.0/24")),
        ("b", "B", net("192.168.2.0/24")),
        ("c", "C", net("10.0.0.0/8")),
    ]) == []


def test_overlapping_reports_every_pair_in_a_three_way_collision():
    found = subnets.overlapping([
        ("a", "A", net("10.0.0.0/8")),
        ("b", "B", net("10.1.0.0/16")),
        ("c", "C", net("10.1.1.0/24")),
    ])
    assert len(found) == 3


# --- VLAN ids --------------------------------------------------------------


def test_duplicate_vlan_ids_reports_only_reused_tags():
    dupes = subnets.duplicate_vlan_ids([
        ("a", "Default", 1),
        ("b", "Guest", 30),
        ("c", "Lab", 30),
        ("d", "Untagged", None),
        ("e", "Also untagged", None),
    ])
    assert dupes == {30: ["Guest", "Lab"]}
