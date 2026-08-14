from collections.abc import Iterable
from typing import Any

from app.collectors.snapshot import Snapshot

from .base import Finding, RunHistory, Severity, UnsupportedCheck
from .loader import load_catalog
from .unsupported import unsupported_checks

_SEVERITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
_PENALTY = {
    Severity.CRITICAL: 25,
    Severity.HIGH: 10,
    Severity.MEDIUM: 4,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


def __getattr__(name: str) -> Any:
    """Expose RULES without building the catalog at import time.

    Loading eagerly would turn a malformed catalog into an ImportError that
    breaks unrelated tests confusingly; this way it surfaces where it belongs,
    as an error from the scan that needed it.
    """
    if name == "RULES":
        return load_catalog()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def run_rules(
    snapshot: Snapshot, history: RunHistory | None = None
) -> tuple[list[Finding], list[UnsupportedCheck]]:
    history = history or RunHistory()
    findings: list[Finding] = []
    for rule in load_catalog():
        findings.extend(rule.evaluate(snapshot, history))
    findings.sort(key=lambda f: _SEVERITY_ORDER.index(f.severity))
    return findings, unsupported_checks(snapshot)


def health_score(findings: list[Finding]) -> int:
    """Score out of 100. Dismissed findings are reported but don't cost points."""
    return score_from_severities(f.severity.value for f in findings if not f.dismissed)


def score_from_severities(severities: Iterable[str]) -> int:
    """Same scoring, from persisted severity strings (used when re-scoring runs)."""
    return max(100 - sum(_PENALTY[Severity(s)] for s in severities), 0)
