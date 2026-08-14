from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.collectors.snapshot import Snapshot


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Category(StrEnum):
    DEVICE_HEALTH = "device_health"
    WIFI = "wifi"
    WIRED = "wired"
    CLIENTS = "clients"
    FIRMWARE = "firmware"
    CAPACITY = "capacity"


@dataclass
class Finding:
    rule_id: str
    severity: Severity
    category: Category
    title: str
    summary: str
    evidence: dict
    recommendation: str
    subject_type: str = "site"  # device | client | site
    subject_id: str | None = None
    subject_name: str | None = None
    # Set when the operator has acknowledged this as won't-fix; such findings
    # are still reported but excluded from the health score.
    dismissed: bool = False

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.rule_id, self.subject_id)


@dataclass
class Binding:
    """What a ``kind: python`` rule returns instead of a Finding.

    The class works out *what is true* -- walking a mesh, taking a median over
    history, grouping by channel -- and hands back the values it computed. The
    catalog says what to write about them. That keeps a rule's prose in one
    place whether or not its logic could be expressed as data.

    ``key`` selects which emit block renders it, for the rules that report more
    than one kind of thing under a single id.
    """

    vars: dict
    key: str = "default"
    subject_type: str = "site"
    subject_id: str | None = None
    subject_name: str | None = None


@dataclass
class UnsupportedCheck:
    rule_id: str
    title: str
    reason: str


@dataclass
class HistoricalRun:
    """Read-model of a prior scan, built by db/repo.py for cross-run rules."""

    run_id: str
    started_at: datetime
    device_uptimes: dict[str, float] = field(default_factory=dict)  # device_id -> sec
    radio_retries: dict[str, float] = field(default_factory=dict)  # "dev_id:freq" -> pct
    site_metrics: dict[str, float] = field(default_factory=dict)  # metric name -> value
    finding_keys: set[tuple[str, str | None]] = field(default_factory=set)


@dataclass
class RunHistory:
    runs: list[HistoricalRun] = field(default_factory=list)  # newest first


class Rule(Protocol):
    id: str

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]: ...
