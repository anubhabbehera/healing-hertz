"""Golden-output equivalence for the rule engine.

Every rule's complete Finding output is pinned to tests/golden/findings.json.
The rule engine is being refactored from hand-written Python classes to a
declarative YAML catalog; this file is what proves the refactor changes how
findings are *produced* without changing what is produced.

If a diff here is intentional, regenerate with:

    uv run python -m tests.golden.generate
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.rule_scenarios import SCENARIOS, build_snapshot, finding_dict, sort_key

GOLDEN = json.loads((Path(__file__).parent / "golden" / "findings.json").read_text())


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_findings_match_golden(scenario):
    from app.rules import run_rules

    snapshot = await build_snapshot(scenario)
    findings, unsupported = run_rules(snapshot, scenario.history)

    expected = GOLDEN[scenario.name]
    actual = sorted((finding_dict(f) for f in findings), key=sort_key)

    assert [f.rule_id for f in findings] == expected["order"], "emission order changed"

    # Compare rule-by-rule first: a wording change should point at the rule that
    # changed, not dump 30 findings of unrelated context.
    assert [f["rule_id"] for f in actual] == [f["rule_id"] for f in expected["findings"]]
    for got, want in zip(actual, expected["findings"], strict=True):
        assert got == want, f"{scenario.name}: {want['rule_id']} changed"

    assert sorted(u.rule_id for u in unsupported) == expected["unsupported"]


def test_golden_covers_every_registered_rule():
    """A rule with no scenario is a rule the refactor could silently break."""
    from app.rules import RULES

    registered = {r.id for r in RULES}
    covered = {f["rule_id"] for s in GOLDEN.values() for f in s["findings"]}
    assert registered - covered == set(), "registered rules with no golden coverage"


def test_golden_is_not_empty():
    assert len(GOLDEN) == len(SCENARIOS)
    assert sum(len(s["findings"]) for s in GOLDEN.values()) > 300
