"""Operator-supplied rule files.

The boundary these tests defend: a user catalog is data, and data must not be
able to name code to import, take over a built-in rule's identity, or take down
a scan by being malformed.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.rules import run_rules
from app.rules.loader import load_catalog

GOOD_RULE = """
rules:
  - id: custom.spare_port
    kind: declarative
    category: wired
    emits:
      - source: device_ports
        where: [port_state, eq, DOWN]
        severity: info
        title: "{device_name} port {port_idx} is down"
        summary: "Port {port_idx} on {device_name} has no link."
        recommendation: "Nothing to do if the port is deliberately unused."
        evidence:
          device: {raw: device_name}
          port: {raw: port_idx}
"""


@pytest.fixture
def rules_dir(tmp_path, monkeypatch):
    """Point the loader at a writable directory of user rules."""
    directory = tmp_path / "rules.d"
    directory.mkdir()
    monkeypatch.setenv("RULES_DIR", str(directory))
    get_settings.cache_clear()
    load_catalog.cache_clear()
    yield directory
    get_settings.cache_clear()
    load_catalog.cache_clear()


def _write(directory, name, text):
    (directory / name).write_text(text)
    load_catalog.cache_clear()


def test_user_rules_are_disabled_by_default():
    """An unset RULES_DIR must add no surface at all."""
    assert get_settings().rules_dir == ""
    assert all(not r.id.startswith("custom.") for r in load_catalog().rules)


async def test_a_user_rule_runs_and_produces_findings(rules_dir, snapshot):
    _write(rules_dir, "mine.yaml", GOOD_RULE)

    assert "custom.spare_port" in {r.id for r in load_catalog().rules}
    findings, _ = run_rules(snapshot)
    mine = [f for f in findings if f.rule_id == "custom.spare_port"]
    assert mine, "the user rule should have fired on the demo fixtures"
    assert mine[0].severity == "info"
    assert "port" in mine[0].evidence


def test_user_rules_load_after_builtins(rules_dir, snapshot):
    """Appending keeps built-in evaluation order, and so finding order, unchanged."""
    _write(rules_dir, "mine.yaml", GOOD_RULE)

    ids = [r.id for r in load_catalog().rules]
    assert ids[-1] == "custom.spare_port"
    assert ids[:-1] == [r.id for r in load_catalog().rules if not r.id.startswith("custom.")]


# --- the sandbox boundary --------------------------------------------------


def test_a_user_rule_may_not_name_code_to_import(rules_dir, snapshot):
    """kind: python is the one field that turns data into an import."""
    _write(rules_dir, "evil.yaml", """
rules:
  - id: custom.sneaky
    kind: python
    impl: app.rules.wifi:MeshUplink
    category: wifi
""")
    _, unsupported = run_rules(snapshot)

    assert "custom.sneaky" not in {r.id for r in load_catalog().rules}
    problem = next(u for u in unsupported if u.rule_id == "custom.evil")
    assert "only allowed in the built-in catalog" in problem.reason


def test_a_user_rule_must_be_namespaced(rules_dir, snapshot):
    """An un-prefixed id could collide with a built-in and hijack its dismissals."""
    _write(rules_dir, "greedy.yaml", GOOD_RULE.replace("custom.spare_port", "wifi.spare_port"))
    _, unsupported = run_rules(snapshot)

    assert "wifi.spare_port" not in {r.id for r in load_catalog().rules}
    assert "must start with 'custom.'" in next(
        u for u in unsupported if u.rule_id == "custom.greedy"
    ).reason


def test_a_user_rule_cannot_shadow_a_builtin_id(rules_dir, snapshot):
    """Even inside the namespace, a duplicate id is rejected."""
    _write(rules_dir, "dupe.yaml", GOOD_RULE + GOOD_RULE.replace("rules:\n", ""))
    _, unsupported = run_rules(snapshot)

    assert any(u.rule_id == "custom.dupe" for u in unsupported)


def test_a_user_template_cannot_escape_the_bindings(rules_dir, snapshot):
    _write(rules_dir, "escape.yaml", GOOD_RULE.replace(
        '"{device_name} port {port_idx} is down"',
        '"{device_name.__class__.__init__.__globals__}"',
    ))
    _, unsupported = run_rules(snapshot)

    assert "custom.spare_port" not in {r.id for r in load_catalog().rules}
    assert any(u.rule_id == "custom.escape" for u in unsupported)


def test_a_user_rule_cannot_read_an_unknown_binding(rules_dir, snapshot):
    _write(rules_dir, "typo.yaml", GOOD_RULE.replace("port_state", "prot_state"))
    _, unsupported = run_rules(snapshot)

    assert any(u.rule_id == "custom.typo" for u in unsupported)


# --- failing soft ----------------------------------------------------------


async def test_malformed_yaml_is_reported_not_raised(rules_dir, snapshot):
    """An operator's half-finished edit must not take down their scan."""
    _write(rules_dir, "broken.yaml", "rules: [ this is not: valid: yaml")

    findings, unsupported = run_rules(snapshot)
    assert findings, "every built-in rule should still have run"
    assert any(u.rule_id == "custom.broken" for u in unsupported)


async def test_one_bad_file_does_not_stop_a_good_one(rules_dir, snapshot):
    _write(rules_dir, "aaa_broken.yaml", "rules: [ nope")
    _write(rules_dir, "zzz_good.yaml", GOOD_RULE)

    findings, unsupported = run_rules(snapshot)
    assert any(f.rule_id == "custom.spare_port" for f in findings)
    assert any(u.rule_id == "custom.aaa_broken" for u in unsupported)


async def test_unknown_field_in_a_user_rule_is_reported(rules_dir, snapshot):
    """A typo'd key must not silently ship an empty field to the UI."""
    _write(rules_dir, "typo.yaml", GOOD_RULE.replace("recommendation:", "recomendation:"))

    _, unsupported = run_rules(snapshot)
    assert any(u.rule_id == "custom.typo" for u in unsupported)


def test_a_missing_rules_dir_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("RULES_DIR", str(tmp_path / "does-not-exist"))
    get_settings.cache_clear()
    load_catalog.cache_clear()
    try:
        assert len(load_catalog().rules) == 47
    finally:
        get_settings.cache_clear()
        load_catalog.cache_clear()
