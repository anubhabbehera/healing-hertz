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
        "impl": "app.rules.wifi:ChannelOverlap",
        "category": "wifi",
    }
    return {**base, **over}


def _compile(*entries):
    from pathlib import Path

    from app.rules.loader import compile_entries
    from app.rules.schema import CatalogEntryAdapter

    return compile_entries(
        [(Path("test.yaml"), CatalogEntryAdapter.validate_python(e)) for e in entries]
    )


def test_catalog_declares_every_rule_exactly_once():
    from app.rules.loader import load_catalog

    ids = [r.id for r in load_catalog().rules]
    assert len(ids) == len(set(ids)) == 47


def test_catalog_entry_rejects_unknown_field():
    from pydantic import ValidationError

    from app.rules.schema import CatalogEntryAdapter

    # A typo'd key must fail loudly rather than ship an empty field to the UI.
    with pytest.raises(ValidationError):
        CatalogEntryAdapter.validate_python(_entry(recomendation="oops"))


@pytest.mark.parametrize(
    "bad_impl",
    [
        "os:system",                     # outside the package entirely
        "app.evil:Thing",                # right prefix, wrong package
        "app.rules.wifi.ChannelOverlap", # dot instead of colon
        "app.rules..wifi:ChannelOverlap",
        "builtins:eval",
    ],
)
def test_catalog_entry_rejects_impl_outside_package(bad_impl):
    """impl is an import target, so the catalog must never reach outside app.rules."""
    from pydantic import ValidationError

    from app.rules.schema import CatalogEntryAdapter

    with pytest.raises(ValidationError):
        CatalogEntryAdapter.validate_python(_entry(impl=bad_impl))


@pytest.mark.parametrize("bad_id", ["nodots", "Wifi.Upper", "wifi..double", "1wifi.x", ""])
def test_catalog_entry_rejects_malformed_rule_id(bad_id):
    from pydantic import ValidationError

    from app.rules.schema import CatalogEntryAdapter

    with pytest.raises(ValidationError):
        CatalogEntryAdapter.validate_python(_entry(id=bad_id))


def test_catalog_entry_rejects_unknown_category():
    from pydantic import ValidationError

    from app.rules.schema import CatalogEntryAdapter

    with pytest.raises(ValidationError):
        CatalogEntryAdapter.validate_python(_entry(category="not_a_category"))


def test_duplicate_rule_id_is_rejected():
    """Two rules sharing an id would collide in dismissals and run diffs."""
    from app.rules.loader import CatalogError

    with pytest.raises(CatalogError, match="duplicate rule id"):
        _compile(_entry(), _entry(impl="app.rules.wifi:Narrow5Width"))


def test_unknown_class_is_rejected():
    from app.rules.loader import CatalogError

    with pytest.raises(CatalogError, match="no attribute"):
        _compile(_entry(impl="app.rules.wifi:NoSuchRule"))


def test_unknown_severity_does_not_break_scoring():
    """apply_dismissals re-scores every stored run; one bad string must not brick it."""
    from app.rules import score_from_severities

    assert score_from_severities(["critical", "high"]) == 100 - 25 - 10
    # A severity the current code doesn't recognise costs nothing, but scoring
    # still completes for the rest of the run.
    assert score_from_severities(["critical", "wat", "high"]) == 100 - 25 - 10


async def test_a_failing_rule_does_not_abort_the_scan(snapshot, monkeypatch):
    import app.rules as rules_pkg
    from app.rules import run_rules
    from app.rules.base import Category
    from app.rules.loader import Catalog, CatalogRule, Provenance, load_catalog

    class Exploding:
        def evaluate(self, snapshot, history):
            raise RuntimeError("boom")

    broken = CatalogRule(
        id="wifi.exploding",
        category=Category.WIFI,
        provenance=Provenance("test.yaml", "wifi.exploding"),
        impl=Exploding(),
    )
    patched = Catalog(rules=[*load_catalog().rules, broken], problems=[])
    monkeypatch.setattr(rules_pkg, "load_catalog", lambda: patched)

    findings, unsupported = run_rules(snapshot)

    assert findings, "the other rules must still have run"
    failed = next(u for u in unsupported if u.rule_id == "wifi.exploding")
    assert "boom" in failed.reason


def test_extending_unsupported_does_not_mutate_shared_state():
    """unsupported_checks() must hand back a fresh list, not a module-level one."""
    from app.rules.unsupported import unsupported_checks

    first = unsupported_checks()
    first.append("scribble")
    assert "scribble" not in unsupported_checks()


def test_disabled_entry_is_not_loaded():
    """Disabling beats deleting: the id stays declared, so dismissals survive."""
    assert _compile(_entry(enabled=False)) == []
    assert len(_compile(_entry())) == 1


# --- disabled rules --------------------------------------------------------


def _disabled_entry(**over):
    return _entry(enabled=False, **over)


def test_disabled_entries_are_retained_but_not_compiled():
    """Retiring a check keeps its id reserved without running it."""
    from pathlib import Path

    from app.rules.loader import compile_entries
    from app.rules.schema import CatalogEntryAdapter

    disabled = []
    rules = compile_entries(
        [(Path("test.yaml"), CatalogEntryAdapter.validate_python(_disabled_entry()))],
        disabled=disabled,
    )
    assert rules == []
    assert [d.entry.id for d in disabled] == ["wifi.dfs_channel"]
    # Never compiled, so it structurally cannot run.
    assert not hasattr(disabled[0].entry, "evaluate")


def test_a_disabled_entry_still_reserves_its_id():
    """Otherwise retiring a rule would silently allow a duplicate."""
    from pathlib import Path

    from app.rules.loader import CatalogError, compile_entries
    from app.rules.schema import CatalogEntryAdapter

    entries = [
        (Path("a.yaml"), CatalogEntryAdapter.validate_python(_disabled_entry())),
        (Path("b.yaml"), CatalogEntryAdapter.validate_python(_entry())),
    ]
    with pytest.raises(CatalogError, match="duplicate rule id"):
        compile_entries(entries, disabled=[])


async def test_a_disabled_user_rule_produces_no_findings(tmp_path, monkeypatch, snapshot):
    """The end-to-end guarantee: disabled means it does not run."""
    from app.config import get_settings
    from app.rules import run_rules
    from app.rules.loader import load_catalog

    directory = tmp_path / "rules.d"
    directory.mkdir()
    (directory / "off.yaml").write_text("""
rules:
  - id: custom.switched_off
    kind: declarative
    category: wired
    enabled: false
    emits:
      - source: device_ports
        severity: info
        title: "{device_name} port {port_idx}"
        summary: "s"
        recommendation: "r"
""")
    monkeypatch.setenv("RULES_DIR", str(directory))
    get_settings.cache_clear()
    load_catalog.cache_clear()
    try:
        catalog = load_catalog()
        assert "custom.switched_off" in {d.entry.id for d in catalog.disabled}
        assert "custom.switched_off" not in {r.id for r in catalog.rules}

        findings, _ = run_rules(snapshot)
        assert "custom.switched_off" not in {f.rule_id for f in findings}
    finally:
        get_settings.cache_clear()
        load_catalog.cache_clear()


def test_every_loaded_rule_resolves_to_a_file_that_exists():
    """Catches path-derivation drift the moment a directory moves."""
    from app.rules.loader import CATALOG_DIR, load_catalog

    for rule in load_catalog().rules:
        if rule.id.startswith("custom."):
            continue
        assert (CATALOG_DIR / rule.provenance.file).is_file(), rule.id
