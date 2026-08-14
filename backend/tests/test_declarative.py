"""The declarative rule machinery: templates, predicates and sources.

The template tests are the security-relevant ones. str.format can walk
attributes -- "{a.__class__.__init__.__globals__}".format(a=obj) is a real
sandbox escape and format_map does not close it -- so the validator rejecting
anything that is not a bare name is what makes catalog files safe to accept
from an operator.
"""

from __future__ import annotations

import pytest

from app.rules import sources
from app.rules.declarative import compile_declarative, matches
from app.rules.render import PRIMITIVES, TemplateError, render, validate_template
from app.rules.schema import CatalogEntryAdapter, DeclarativeEntry

BINDINGS = {"name", "pct"}


# --- template validation ---------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        "{name.__class__}",                       # attribute walk
        "{name.__class__.__init__.__globals__}",  # the full escape
        "{name[0]}",                              # subscript
        "{0}",                                    # positional
        "{}",                                     # auto-numbered
    ],
)
def test_template_rejects_anything_but_a_bare_name(template):
    with pytest.raises(TemplateError):
        validate_template(template, BINDINGS, "test")


def test_template_rejects_unknown_binding():
    with pytest.raises(TemplateError, match="not available"):
        validate_template("{nope}", BINDINGS, "test")


def test_template_rejects_absurd_width():
    """A huge width is a cheap way to exhaust memory at render time."""
    with pytest.raises(TemplateError, match="format spec"):
        validate_template("{pct:>999999999}", BINDINGS, "test")


@pytest.mark.parametrize("template", ["{name}", "{pct:.0f}%", "{pct:.1f}", "{name} is {pct:.0%}"])
def test_template_accepts_the_specs_rules_actually_use(template):
    validate_template(template, BINDINGS, "test")


def test_render_matches_fstring_formatting():
    """format and f-strings share __format__, so output must be identical."""
    cpu = 93.4
    assert render("{cpu:.0f}%", {"cpu": cpu}, "t") == f"{cpu:.0f}%"
    assert render("{cpu:.1f}", {"cpu": cpu}, "t") == f"{cpu:.1f}"


def test_render_preserves_float_rendering_quirks():
    """5 parsed as a float renders "5.0"; the catalog must not quietly fix that."""
    assert render("{freq} GHz", {"freq": 5.0}, "t") == "5.0 GHz"


# --- predicate semantics ---------------------------------------------------


def _pred(raw):
    """Build a predicate through the real schema path."""
    entry = CatalogEntryAdapter.validate_python({
        "id": "test.rule",
        "kind": "declarative",
        "category": "wifi",
        "emits": [{
            "source": "device_ports",
            "where": raw,
            "severity": "low",
            "title": "t",
            "summary": "s",
            "recommendation": "r",
        }],
    })
    return entry.emits[0].where


@pytest.mark.parametrize(
    ("where", "row", "expected"),
    [
        (["port_state", "eq", "UP"], {"port_state": "UP"}, True),
        (["port_state", "eq", "UP"], {"port_state": "DOWN"}, False),
        (["port_idx", "lte", 100], {"port_idx": 50}, True),
        (["port_idx", "gt", 100], {"port_idx": 50}, False),
        (["port_idx", "in", [1, 6, 11]], {"port_idx": 6}, True),
        (["port_idx", "not_in", [1, 6, 11]], {"port_idx": 3}, True),
        (["poe_state", "is_null"], {"poe_state": None}, True),
        (["poe_state", "is_not_null"], {"poe_state": None}, False),
    ],
)
def test_comparison_operators(where, row, expected):
    assert matches(_pred(where), row) is expected


@pytest.mark.parametrize("op", ["lt", "lte", "gt", "gte"])
def test_ordered_comparison_against_none_is_false_not_an_error(op):
    """The Python rules guard on presence before comparing; None must not raise."""
    assert matches(_pred(["port_speed_mbps", op, 100]), {"port_speed_mbps": None}) is False


def test_missing_binding_is_treated_as_absent():
    assert matches(_pred(["port_idx", "eq", 5]), {}) is False


def test_boolean_combinators():
    row = {"port_state": "UP", "port_idx": 5}
    assert matches(_pred({"all": [["port_state", "eq", "UP"], ["port_idx", "eq", 5]]}), row)
    assert not matches(_pred({"all": [["port_state", "eq", "UP"], ["port_idx", "eq", 9]]}), row)
    assert matches(_pred({"any": [["port_idx", "eq", 9], ["port_idx", "eq", 5]]}), row)
    assert matches(_pred({"not": ["port_idx", "eq", 9]}), row)


def test_compact_predicate_must_be_two_or_three_items():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _pred(["port_idx", "eq", 1, "extra"])


# --- compile-time validation -----------------------------------------------


def _entry(**over):
    emit = {
        "source": "device_ports",
        "severity": "low",
        "title": "t",
        "summary": "s",
        "recommendation": "r",
        **over.pop("emit", {}),
    }
    return CatalogEntryAdapter.validate_python({
        "id": "test.rule", "kind": "declarative", "category": "wifi",
        "emits": [emit], **over,
    })


def test_unknown_source_is_rejected_at_compile_time():
    from app.rules.declarative import RuleCompileError

    entry = _entry(emit={"source": "not_a_source"})
    with pytest.raises(RuleCompileError, match="unknown source"):
        compile_declarative(entry, "test.yaml[test.rule]")


def test_predicate_reading_an_unknown_binding_is_rejected():
    from app.rules.declarative import RuleCompileError

    entry = _entry(emit={"where": ["not_a_binding", "eq", 1]})
    with pytest.raises(RuleCompileError, match="does not provide"):
        compile_declarative(entry, "test.yaml[test.rule]")


def test_evidence_reading_an_unknown_binding_is_rejected():
    from app.rules.declarative import RuleCompileError

    entry = _entry(emit={"evidence": {"x": {"raw": "not_a_binding"}}})
    with pytest.raises(RuleCompileError, match="not available here"):
        compile_declarative(entry, "test.yaml[test.rule]")


# --- aggregation -----------------------------------------------------------


def _agg_entry(**emit_over):
    emit = {
        "source": "rf_clients",
        "where": ["signal_dbm", "lte", -75],
        "aggregate": {"into": "site", "compute": {"count": {"op": "count"}}},
        "severity": "low",
        "title": "{count} weak clients",
        "summary": "s",
        "recommendation": "r",
        **emit_over,
    }
    return CatalogEntryAdapter.validate_python({
        "id": "test.rule", "kind": "declarative", "category": "wifi", "emits": [emit],
    })


def test_aggregated_prose_cannot_read_a_row_binding():
    """Rows are folded away, so a per-row binding is genuinely gone by then."""
    entry = _agg_entry(title="{signal_dbm}")
    with pytest.raises(TemplateError):
        compile_declarative(entry, "test.yaml[test.rule]")


def test_aggregate_compute_must_read_a_real_binding():
    from app.rules.declarative import RuleCompileError

    entry = _agg_entry(aggregate={
        "into": "site",
        "compute": {"worst": {"op": "min_of", "of": "not_a_binding"}},
    })
    with pytest.raises(RuleCompileError, match="does not provide"):
        compile_declarative(entry, "test.yaml[test.rule]")


def test_top_projection_requires_an_aggregated_block():
    from app.rules.declarative import RuleCompileError

    entry = _entry(emit={"evidence": {
        "x": {"op": "top", "project": {"n": "device_name"}},
    }})
    with pytest.raises(RuleCompileError, match="only makes sense in an aggregated block"):
        compile_declarative(entry, "test.yaml[test.rule]")


def test_top_projection_must_read_real_bindings():
    from app.rules.declarative import RuleCompileError

    entry = _agg_entry(evidence={"x": {"op": "top", "project": {"n": "nope"}}})
    with pytest.raises(RuleCompileError, match="does not provide"):
        compile_declarative(entry, "test.yaml[test.rule]")


async def test_min_matches_keeps_a_rule_quiet(snapshot):
    """band_steering_ineffective needs three matches; two must stay silent."""
    from app.integrations.legacy_unifi import ClientRF, RfSnapshot
    from app.rules import run_rules

    def strong(n):
        return [ClientRF(mac=f"aa:{i}", name=f"c{i}", ap_mac=None, essid="Home",
                         signal_dbm=-50, tx_rate_kbps=200_000, rx_rate_kbps=200_000,
                         channel=6) for i in range(n)]

    snapshot.rf = RfSnapshot(clients=strong(2), roam_counts={}, roam_data_available=True)
    assert "wifi.band_steering_ineffective" not in {f.rule_id for f in run_rules(snapshot)[0]}

    snapshot.rf = RfSnapshot(clients=strong(3), roam_counts={}, roam_data_available=True)
    assert "wifi.band_steering_ineffective" in {f.rule_id for f in run_rules(snapshot)[0]}


async def test_a_source_over_a_missing_enrichment_yields_nothing(snapshot):
    """rf_clients has no guard in any rule; the source absorbs it."""
    from app.rules.base import RunHistory

    snapshot.rf = None
    assert list(sources.get("rf_clients").iterate(snapshot, RunHistory())) == []


def test_template_naming_an_unknown_binding_is_rejected():
    entry = _entry(emit={"title": "{not_a_binding}"})
    with pytest.raises(TemplateError):
        compile_declarative(entry, "test.yaml[test.rule]")


# --- source contract -------------------------------------------------------


async def _rows_by_source():
    """Every row every source produces, across all scenarios."""
    from tests.rule_scenarios import SCENARIOS, build_snapshot

    out: dict[str, list] = {name: [] for name in sources.REGISTRY}
    for scenario in SCENARIOS:
        snapshot = await build_snapshot(scenario)
        for name, source in sources.REGISTRY.items():
            out[name].extend(source.iterate(snapshot, scenario.history))
    return out


async def test_sources_yield_only_their_declared_bindings():
    """A source's declared bindings are what rules validate against, so they
    must match what it actually yields -- otherwise validation passes and the
    scan KeyErrors."""
    for name, rows in (await _rows_by_source()).items():
        declared = set(sources.REGISTRY[name].bindings)
        assert rows, f"source {name!r} yields nothing in any scenario"
        for row in rows:
            assert set(row.vars) == declared, (
                f"source {name!r} yields keys that differ from its declared bindings"
            )


async def test_source_bindings_are_primitives():
    """Templates must have nothing to traverse, even if the validator is bypassed."""
    for name, rows in (await _rows_by_source()).items():
        for row in rows:
            for key, value in row.vars.items():
                assert isinstance(value, PRIMITIVES), f"{name}.{key} is {type(value)}"


def test_declarative_entry_requires_at_least_one_emit():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CatalogEntryAdapter.validate_python({
            "id": "test.rule", "kind": "declarative", "category": "wifi", "emits": [],
        })


def test_declarative_entry_rejects_impl():
    """A declarative rule must not be able to smuggle in an import target."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CatalogEntryAdapter.validate_python({
            "id": "test.rule", "kind": "declarative", "category": "wifi",
            "impl": "app.rules.wifi:ChannelOverlap",
            "emits": [{"source": "device_ports", "severity": "low",
                       "title": "t", "summary": "s", "recommendation": "r"}],
        })


def test_the_converted_wired_rules_are_declarative():
    from app.rules.declarative import DeclarativeRule
    from app.rules.loader import load_catalog

    by_id = {r.id: r for r in load_catalog().rules}
    assert isinstance(by_id["wired.poe_limited"], DeclarativeRule)
    assert isinstance(by_id["wired.uplink_negotiation"], DeclarativeRule)


def test_declarative_entry_type_is_exported():
    assert DeclarativeEntry.__name__ == "DeclarativeEntry"
