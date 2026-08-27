"""Robust statistics over a stored metric series.

Every scan writes its metrics to the database, so by the time a site has run
for a week there is a real series behind each number. What these functions do
with it is deliberately robust rather than conventional: a mean and a standard
deviation over network telemetry are dominated by the very spikes worth
noticing, so a single bad scan moves the "normal" it is being compared against.
Median, median absolute deviation and a median-of-slopes trend do not have that
problem, and none of them need a distribution assumption.

Pure functions over plain values: no snapshot, no database, no rule.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime

# Scales the median absolute deviation so it estimates the same spread as a
# standard deviation would on normally distributed data, which is what makes
# the resulting score comparable to the familiar "3 sigma".
_MAD_TO_SIGMA = 0.6745
# Same idea for the mean absolute deviation, used only when MAD is exactly zero
# (more than half the readings identical), where a MAD-based score cannot exist.
_MEANAD_TO_SIGMA = 0.7979

DAY_SECONDS = 86400.0


@dataclass(frozen=True)
class Point:
    at: datetime
    value: float


@dataclass(frozen=True)
class Series:
    """One metric for one subject, oldest point first."""

    metric: str
    subject_id: str | None = None
    subject_name: str | None = None
    points: list[Point] = field(default_factory=list)

    @property
    def values(self) -> list[float]:
        return [p.value for p in self.points]

    @property
    def latest(self) -> Point | None:
        return self.points[-1] if self.points else None


def mad(values: list[float]) -> float | None:
    """Median absolute deviation from the median."""
    if not values:
        return None
    centre = statistics.median(values)
    return statistics.median([abs(v - centre) for v in values])


def modified_zscore(value: float, baseline: list[float]) -> float | None:
    """How far `value` sits from the baseline, in robust standard deviations.

    None when the baseline is empty or has no spread at all -- with every prior
    reading identical there is no scale to measure a deviation against, and any
    number this returned would be an artefact of the fallback rather than a
    property of the data.
    """
    if len(baseline) < 2:
        return None
    centre = statistics.median(baseline)
    spread = mad(baseline)
    if spread and spread > 0:
        return _MAD_TO_SIGMA * (value - centre) / spread
    mean_ad = statistics.mean([abs(v - centre) for v in baseline])
    if mean_ad > 0:
        return _MEANAD_TO_SIGMA * (value - centre) / mean_ad
    return None


def ewma(values: list[float], alpha: float = 0.3) -> float | None:
    """Exponentially weighted mean: recent readings weigh more, old ones fade.

    Catches the drift a z-score misses -- a metric creeping up a little every
    scan never looks anomalous against a baseline that creeps with it, but the
    gap between the fast and slow view of the same series does open up.
    """
    if not values:
        return None
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")
    current = values[0]
    for value in values[1:]:
        current = alpha * value + (1 - alpha) * current
    return current


def theil_sen_slope(points: list[Point]) -> float | None:
    """Trend in units per day: the median of every pairwise slope.

    Least squares would let one outlying scan tilt the whole line; the median
    of slopes tolerates up to ~29% of the readings being nonsense before it
    starts to follow them.
    """
    usable = [p for p in points if p.at is not None]
    if len(usable) < 2:
        return None
    slopes: list[float] = []
    for i, a in enumerate(usable):
        for b in usable[i + 1:]:
            days = (b.at - a.at).total_seconds() / DAY_SECONDS
            if days > 0:
                slopes.append((b.value - a.value) / days)
    return statistics.median(slopes) if slopes else None


def days_until(points: list[Point], threshold: float) -> float | None:
    """Days until the trend reaches `threshold`, or None if it never does.

    Straight-line extrapolation of the Theil-Sen slope from the latest reading.
    That is a floor on the honesty of the answer, not a prediction: it assumes
    the trend holds, which is exactly the assumption a rule quoting it should
    make the operator aware of.
    """
    if not points:
        return None
    slope = theil_sen_slope(points)
    if slope is None or slope == 0:
        return None
    remaining = threshold - points[-1].value
    # Already past it, or moving away from it.
    if remaining == 0 or (remaining > 0) != (slope > 0):
        return None
    return remaining / slope


@dataclass(frozen=True)
class Changepoint:
    """Where a series stepped to a new level, and what it moved between."""

    index: int
    at: datetime
    before: float
    after: float
    direction: str  # "up" | "down"


def cusum_changepoint(
    points: list[Point],
    threshold: float = 5.0,
    slack: float = 0.5,
    min_after: int = 3,
) -> Changepoint | None:
    """The first sustained step change in the series, if there is one.

    A cumulative sum of robustly standardised deviations: `slack` is the drift
    tolerated for free each step, and the alarm fires once the accumulated
    excess passes `threshold`. Small persistent shifts trip it where a
    per-reading z-score never would, because the evidence adds up instead of
    being judged one reading at a time.

    An alarm needs `min_after` readings behind it to count. Without that a spike
    in the last scan or two reads as a step change, when the only thing that
    distinguishes the two is whether the new level holds -- and the series
    cannot yet say.
    """
    if len(points) < 4:
        return None
    values = [p.value for p in points]

    # The reference level is the start of the series, not its overall median.
    # Against a global median a series that steps once looks abnormal from its
    # very first reading -- the half that sits on the old level is as far from
    # the middle as the half on the new one, and the alarm lands at index 0.
    warmup = max(3, len(values) // 4)
    if len(values) - warmup < min_after:
        return None
    baseline = values[:warmup]
    centre = statistics.median(baseline)
    spread = mad(baseline) or mad(values)
    if not spread or spread <= 0:
        return None
    scale = spread / _MAD_TO_SIGMA

    high = low = 0.0
    for i in range(warmup, len(values)):
        standardised = (values[i] - centre) / scale
        high = max(0.0, high + standardised - slack)
        low = min(0.0, low + standardised + slack)
        if high > threshold or low < -threshold:
            if len(values) - i < min_after:
                return None
            after = statistics.median(values[i:])
            return Changepoint(
                index=i,
                at=points[i].at,
                before=statistics.median(values[:i]),
                after=after,
                direction="up" if after >= centre else "down",
            )
    return None
