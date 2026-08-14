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


# --- catalog loading and validation ---------------------------------------


def _entry(**over):
    base = {
        "id": "wifi.dfs_channel",
        "kind": "python",
        "impl": "app.rules.wifi:DfsChannel",
        "category": "wifi",
    }
    return {**base, **over}


def _compile(*entries):
    from pathlib import Path

    from app.rules.loader import compile_entries
    from app.rules.schema import CatalogEntry

    return compile_entries(
        [(Path("test.yaml"), CatalogEntry.model_validate(e)) for e in entries]
    )


def test_catalog_declares_every_rule_exactly_once():
    from app.rules.loader import load_catalog

    ids = [r.id for r in load_catalog()]
    assert len(ids) == len(set(ids)) == 35


def test_catalog_entry_rejects_unknown_field():
    from pydantic import ValidationError

    from app.rules.schema import CatalogEntry

    # A typo'd key must fail loudly rather than ship an empty field to the UI.
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_entry(recomendation="oops"))


@pytest.mark.parametrize(
    "bad_impl",
    [
        "os:system",                     # outside the package entirely
        "app.evil:Thing",                # right prefix, wrong package
        "app.rules.wifi.DfsChannel",     # dot instead of colon
        "app.rules..wifi:DfsChannel",
        "builtins:eval",
    ],
)
def test_catalog_entry_rejects_impl_outside_package(bad_impl):
    """impl is an import target, so the catalog must never reach outside app.rules."""
    from pydantic import ValidationError

    from app.rules.schema import CatalogEntry

    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_entry(impl=bad_impl))


@pytest.mark.parametrize("bad_id", ["nodots", "Wifi.Upper", "wifi..double", "1wifi.x", ""])
def test_catalog_entry_rejects_malformed_rule_id(bad_id):
    from pydantic import ValidationError

    from app.rules.schema import CatalogEntry

    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_entry(id=bad_id))


def test_catalog_entry_rejects_unknown_category():
    from pydantic import ValidationError

    from app.rules.schema import CatalogEntry

    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_entry(category="not_a_category"))


def test_duplicate_rule_id_is_rejected():
    """Two rules sharing an id would collide in dismissals and run diffs."""
    from app.rules.loader import CatalogError

    with pytest.raises(CatalogError, match="duplicate rule id"):
        _compile(_entry(), _entry(impl="app.rules.wifi:Wide24Width"))


def test_unknown_class_is_rejected():
    from app.rules.loader import CatalogError

    with pytest.raises(CatalogError, match="no attribute"):
        _compile(_entry(impl="app.rules.wifi:NoSuchRule"))


def test_disabled_entry_is_not_loaded():
    """Disabling beats deleting: the id stays declared, so dismissals survive."""
    assert _compile(_entry(enabled=False)) == []
    assert len(_compile(_entry())) == 1
