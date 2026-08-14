"""Compile a declarative catalog entry into a runnable rule.

Everything a rule references -- predicate bindings, template fields, evidence
values -- is checked against the source's declared bindings at compile time, so
a typo is a load error naming the rule and not a KeyError mid-scan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.collectors.snapshot import Snapshot

from . import sources
from .base import Category, Finding, RunHistory
from .render import make_finding, render, template_fields, validate_template
from .schema import AllOf, AnyOf, Comparison, DeclarativeEntry, EmitBlock, NotOf


class RuleCompileError(Exception):
    """A declarative entry references something that does not exist."""


# --- predicate evaluation --------------------------------------------------

_ORDERED = {
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
}


def _compare(op: str, left: Any, value: Any) -> bool:
    if op == "is_null":
        return left is None
    if op == "is_not_null":
        return left is not None
    if op == "eq":
        return left == value
    if op == "ne":
        return left != value
    if op in _ORDERED:
        # A missing reading is not a match. The Python rules guard on presence
        # explicitly before comparing; this makes that the default so a
        # declarative rule can't blow up on None < int.
        if left is None or value is None:
            return False
        return _ORDERED[op](left, value)
    if op == "in":
        return left is not None and left in value
    if op == "not_in":
        return left is None or left not in value
    if op == "contains":
        return left is not None and value in left
    raise RuleCompileError(f"unknown operator {op!r}")  # pragma: no cover - schema guards


def matches(node: Any, bindings: dict[str, Any]) -> bool:
    if isinstance(node, Comparison):
        return _compare(node.op, bindings.get(node.binding), node.value)
    if isinstance(node, AllOf):
        return all(matches(child, bindings) for child in node.all)
    if isinstance(node, AnyOf):
        return any(matches(child, bindings) for child in node.any)
    if isinstance(node, NotOf):
        return not matches(node.negate, bindings)
    raise RuleCompileError(f"unknown predicate node {node!r}")  # pragma: no cover


def _predicate_bindings(node: Any) -> set[str]:
    if isinstance(node, Comparison):
        return {node.binding}
    if isinstance(node, AllOf):
        return set().union(*(_predicate_bindings(c) for c in node.all))
    if isinstance(node, AnyOf):
        return set().union(*(_predicate_bindings(c) for c in node.any))
    if isinstance(node, NotOf):
        return _predicate_bindings(node.negate)
    return set()


# --- compiled form ---------------------------------------------------------


@dataclass(frozen=True)
class CompiledEmit:
    source: sources.Source
    block: EmitBlock


@dataclass
class DeclarativeRule:
    """Satisfies the Rule protocol in base.py."""

    id: str
    category: Category
    provenance: Any
    emits: list[CompiledEmit]

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings: list[Finding] = []
        for emit in self.emits:
            block = emit.block
            for row in emit.source.iterate(snapshot, history):
                if block.where is not None and not matches(block.where, row.vars):
                    continue
                findings.append(self._build(block, row))
        return findings

    def _build(self, block: EmitBlock, row: sources.Row) -> Finding:
        where = f"{self.provenance}"
        site_scoped = block.subject == "site"
        return make_finding(
            rule_id=self.id,
            category=self.category,
            severity=block.severity,
            title=render(block.title, row.vars, where),
            summary=render(block.summary, row.vars, where),
            recommendation=render(block.recommendation, row.vars, where),
            evidence={k: row.vars[v.raw] for k, v in block.evidence.items()},
            subject_type="site" if site_scoped else row.subject_type,
            subject_id=None if site_scoped else row.subject_id,
            subject_name=None if site_scoped else row.subject_name,
        )


def compile_declarative(entry: DeclarativeEntry, provenance: Any) -> DeclarativeRule:
    emits: list[CompiledEmit] = []
    for index, block in enumerate(entry.emits):
        where = f"{provenance} emit[{index}]"
        try:
            source = sources.get(block.source)
        except KeyError as exc:
            raise RuleCompileError(f"{where}: {exc}") from None

        available = source.bindings
        unknown = _predicate_bindings(block.where) - available
        if unknown:
            raise RuleCompileError(
                f"{where}: predicate reads {sorted(unknown)}, "
                f"which source {source.name!r} does not provide"
            )

        for label, template in (("title", block.title), ("summary", block.summary),
                                ("recommendation", block.recommendation)):
            validate_template(template, set(available), f"{where}.{label}")

        for key, value in block.evidence.items():
            if value.raw not in available:
                raise RuleCompileError(
                    f"{where}: evidence {key!r} reads {value.raw!r}, "
                    f"which source {source.name!r} does not provide"
                )

        emits.append(CompiledEmit(source=source, block=block))

    return DeclarativeRule(
        id=entry.id, category=entry.category, provenance=provenance, emits=emits
    )


def used_template_fields(block: EmitBlock) -> set[str]:
    """Every binding a block's prose depends on (used by tests)."""
    return (
        template_fields(block.title)
        | template_fields(block.summary)
        | template_fields(block.recommendation)
    )
