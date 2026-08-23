"""Unit tests for the deterministic analysis helpers.

These are pure functions, so they are tested on values rather than through a
scan: the arithmetic is the thing being pinned, and a rule that quotes it can
only be as right as this.
"""

import ipaddress
from datetime import UTC, datetime, timedelta

import pytest

from app.analytics import subnets, timeseries

BASE = datetime(2026, 1, 15, 12, tzinfo=UTC)


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


# --- time series -----------------------------------------------------------


def points(values, step_days=1.0):
    """A series ending at BASE, one reading every `step_days`, oldest first."""
    last = len(values) - 1
    return [
        timeseries.Point(at=BASE - timedelta(days=(last - i) * step_days), value=v)
        for i, v in enumerate(values)
    ]


def test_mad_is_the_median_distance_from_the_median():
    assert timeseries.mad([1, 2, 3, 4, 100]) == 1
    assert timeseries.mad([5, 5, 5]) == 0
    assert timeseries.mad([]) is None


def test_modified_zscore_is_not_dragged_by_the_outlier_it_is_scoring():
    quiet = [20, 21, 19, 22, 20, 21, 20]
    assert timeseries.modified_zscore(21, quiet) < 3.5
    assert timeseries.modified_zscore(85, quiet) > 20
    # Sign carries the direction.
    assert timeseries.modified_zscore(2, quiet) < 0


def test_modified_zscore_needs_a_baseline_with_spread():
    assert timeseries.modified_zscore(5, []) is None
    assert timeseries.modified_zscore(5, [3]) is None
    assert timeseries.modified_zscore(5, [3, 3, 3, 3]) is None
    # Half the readings identical: MAD is 0, the mean-deviation fallback is not.
    assert timeseries.modified_zscore(9, [3, 3, 3, 5, 7]) is not None


def test_ewma_weights_the_recent_readings():
    rising = [10, 10, 10, 20, 20, 20]
    assert 10 < timeseries.ewma(rising) < 20
    assert timeseries.ewma(rising) > sum(rising) / len(rising)
    assert timeseries.ewma([]) is None
    with pytest.raises(ValueError):
        timeseries.ewma([1, 2], alpha=0)


def test_theil_sen_slope_is_per_day_and_ignores_one_bad_reading():
    clean = timeseries.theil_sen_slope(points([10, 12, 14, 16, 18]))
    assert clean == pytest.approx(2.0)
    # One absurd reading; least squares would tilt, the median of slopes holds.
    noisy = timeseries.theil_sen_slope(points([10, 12, 14, 900, 18]))
    assert noisy == pytest.approx(2.0, abs=0.6)


def test_theil_sen_slope_scales_with_the_gap_between_readings():
    # Same values twice as far apart in time is half the slope per day.
    assert timeseries.theil_sen_slope(points([10, 12, 14], step_days=2)) == pytest.approx(1.0)
    assert timeseries.theil_sen_slope(points([5])) is None


def test_days_until_extrapolates_the_trend():
    assert timeseries.days_until(points([70, 75, 80, 85]), 90) == pytest.approx(1.0)


@pytest.mark.parametrize("values,threshold", [
    ([70, 75, 80, 85], 60),   # already past it
    ([85, 80, 75, 70], 90),   # moving away from it
    ([70, 70, 70, 70], 90),   # not moving at all
])
def test_days_until_is_none_when_the_trend_never_arrives(values, threshold):
    assert timeseries.days_until(points(values), threshold) is None


def test_cusum_finds_a_step_and_names_both_levels():
    change = timeseries.cusum_changepoint(
        points([4, 4.5, 4, 5, 4.2, 14, 14.5, 13.8, 15, 14.2, 14, 15.1])
    )
    assert change.index == 5
    assert change.direction == "up"
    assert change.before == pytest.approx(4.2, abs=0.5)
    assert change.after == pytest.approx(14.2, abs=0.5)


def test_cusum_reports_a_drop_as_down():
    change = timeseries.cusum_changepoint(
        points([90, 91, 89, 92, 90, 60, 61, 59, 62, 60, 61, 60])
    )
    assert change.direction == "down"


def test_cusum_ignores_a_spike_with_no_readings_behind_it():
    # The same jump is a step change or a spike depending only on whether it
    # held, and at the tail of the series there is no way to know yet.
    assert timeseries.cusum_changepoint(
        points([20, 21, 19, 22, 20, 21, 20, 19, 21, 85])
    ) is None


@pytest.mark.parametrize("values", [
    [5, 5.1, 4.9, 5, 5.05, 4.95, 5, 5.1, 4.9, 5],  # noise, no shift
    [5, 5, 5, 5, 5, 5, 5, 5],                       # no spread at all
    [1, 2, 3],                                      # too short to judge
])
def test_cusum_stays_quiet_without_a_real_change(values):
    assert timeseries.cusum_changepoint(points(values)) is None
