import logging
from collections.abc import Iterable
from typing import Any

from app.collectors.snapshot import Snapshot

from .base import Finding, RunHistory, Severity, UnsupportedCheck
from .loader import load_catalog
from .unsupported import unsupported_checks

logger = logging.getLogger(__name__)

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
    failed: list[UnsupportedCheck] = []

    for rule in load_catalog():
        try:
            findings.extend(rule.evaluate(snapshot, history))
        except Exception as exc:
            # One rule must not cost the operator the whole scan. A rule that
            # cannot run is reported through the existing "not checkable"
            # channel, which is already persisted and already rendered -- that
            # is exactly what a rule which failed to evaluate is.
            logger.exception("rule %s failed to evaluate", rule.provenance)
            failed.append(UnsupportedCheck(
                rule_id=rule.id,
                title=rule.id,
                reason=f"The check failed to run: {type(exc).__name__}: {exc}",
            ))

    findings.sort(key=lambda f: _SEVERITY_ORDER.index(f.severity))

    unsupported = unsupported_checks(snapshot)
    already = {u.rule_id for u in unsupported}
    unsupported.extend(f for f in failed if f.rule_id not in already)
    return findings, unsupported


def health_score(findings: list[Finding]) -> int:
    """Score out of 100. Dismissed findings are reported but don't cost points."""
    return score_from_severities(f.severity.value for f in findings if not f.dismissed)


def score_from_severities(severities: Iterable[str]) -> int:
    """Same scoring, from persisted severity strings (used when re-scoring runs).

    An unrecognised severity scores zero rather than raising. This runs over
    every stored run each time a dismissal is added or removed (see
    repo.apply_dismissals), so one bad string in the findings table would
    otherwise make dismissals permanently unusable across all history, with no
    way to recover from the UI.
    """
    total = 0
    for s in severities:
        try:
            total += _PENALTY[Severity(s)]
        except ValueError:
            logger.warning("ignoring unrecognised severity %r while scoring", s)
    return max(100 - total, 0)
