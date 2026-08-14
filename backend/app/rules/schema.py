"""Pydantic models for the rule catalog.

The catalog is the registry of every diagnostic check: its identity, category,
thresholds and prose. An entry is either ``kind: declarative`` -- predicate and
content both expressed here -- or ``kind: python``, whose algorithm is too
involved to express as data and stays in a class.

These models set ``extra="forbid"``, deliberately the opposite of ``UnifiModel``
(``app/unifi/models.py``). An unknown key in an upstream API response is routine
and must not break parsing; an unknown key in a catalog file is a typo, and a
misspelled ``recomendation:`` silently becoming an empty recommendation in the
UI is exactly the failure this schema exists to prevent.

Predicates are a structured tree, not an expression string. A comparison may be
written compactly as ``[binding, op, value]`` -- that is still the tree, just
the two-element list YAML already parsed, so there is no grammar and nothing to
parse defensively.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
)

from .base import Category, Severity

# An impl may only name a class inside this package. A catalog file is data,
# and data must never choose an arbitrary import target -- this is the boundary
# that lets user-supplied catalog files exist at all.
_IMPL_RE = re.compile(r"^app\.rules\.[a-z_]+:[A-Za-z_][A-Za-z0-9_]*$")

# Rule ids are the durable key for dismissals, run diffs and LLM suggestion
# linking (see db/models.py Dismissal), so the grammar is deliberately narrow:
# dotted lowercase segments, nothing that needs quoting or escaping downstream.
_RULE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# --- predicates ------------------------------------------------------------

Op = Literal[
    "eq", "ne", "lt", "lte", "gt", "gte",
    "in", "not_in", "contains", "is_null", "is_not_null",
]

_UNARY_OPS = {"is_null", "is_not_null"}


def _normalize_predicate(v: Any) -> Any:
    """Expand the compact list form into the mapping form."""
    if isinstance(v, list):
        if len(v) == 2:
            return {"binding": v[0], "op": v[1]}
        if len(v) == 3:
            return {"binding": v[0], "op": v[1], "value": v[2]}
        raise ValueError(
            f"compact predicate must be [binding, op] or [binding, op, value], got {v!r}"
        )
    return v


class Comparison(CatalogModel):
    binding: str
    op: Op
    value: Any = None

    @field_validator("binding")
    @classmethod
    def _plain_name(cls, v: str) -> str:
        # No dots or brackets: a predicate reads a source binding, never an
        # attribute chain. Sources do the flattening precisely so this holds.
        if not v.isidentifier():
            raise ValueError(f"{v!r} is not a binding name")
        return v


class AllOf(CatalogModel):
    all: list[Predicate]


class AnyOf(CatalogModel):
    any: list[Predicate]


class NotOf(CatalogModel):
    negate: Predicate = Field(alias="not")


Predicate = Annotated[
    AllOf | AnyOf | NotOf | Comparison,
    BeforeValidator(_normalize_predicate),
]


# --- emit blocks -----------------------------------------------------------


class RawValue(CatalogModel):
    """An evidence value bound straight from a source binding.

    Evidence is serialised to JSON and read by the advisor's payload sanitiser,
    so values keep their type -- a port index stays an int, not "5".
    """

    raw: str


class Escalation(CatalogModel):
    when: Predicate
    to: Severity


class SeveritySpec(CatalogModel):
    """A severity graded by the value that matched.

    Escalations are tried in order and the first match wins, falling back to
    ``base``.
    """

    base: Severity
    escalate: list[Escalation] = Field(default_factory=list)


# --- computed values -------------------------------------------------------
#
# Templates do no arithmetic. Anything a rule's prose needs beyond a raw
# binding is named here first, which is what keeps a replacement field a bare
# identifier and therefore safe to validate.
#
# This vocabulary is deliberately closed. A rule that needs an operation not
# listed is a rule whose logic is not data; it belongs in a Python impl. Resist
# adding one op per rule -- that is how a small set of names turns into an
# expression language by accretion.


class FloorDivOp(CatalogModel):
    """Integer division, e.g. seconds to whole days."""

    op: Literal["floordiv"]
    of: str
    by: float


class RatioOp(CatalogModel):
    """of / per. Null when either side is missing, or the denominator is <= 0."""

    op: Literal["ratio"]
    of: str
    per: str


class ScaleOp(CatalogModel):
    op: Literal["scale"]
    of: str
    by: float


class RoundOp(CatalogModel):
    op: Literal["round"]
    of: str
    digits: int = 0


RowCompute = Annotated[
    FloorDivOp | RatioOp | ScaleOp | RoundOp,
    Field(discriminator="op"),
]


# --- aggregation -----------------------------------------------------------


class CountOp(CatalogModel):
    op: Literal["count"]


class MinOfOp(CatalogModel):
    op: Literal["min_of"]
    of: str
    # Substituted for a missing reading, matching the `or 0` the rules use.
    null_as: float | None = None


class MaxOfOp(CatalogModel):
    op: Literal["max_of"]
    of: str
    null_as: float | None = None


GroupCompute = Annotated[CountOp | MinOfOp | MaxOfOp, Field(discriminator="op")]


class Aggregate(CatalogModel):
    """Fold every matching row into a single site-scoped finding.

    Several rules report a population rather than a device -- "4 clients on
    legacy rates" -- where naming each one separately would be noise.
    """

    into: Literal["site"]
    # Below this many matches the rule stays quiet.
    min_matches: int = 1
    compute: dict[str, GroupCompute] = Field(default_factory=dict)


class TopProjection(CatalogModel):
    """Evidence built from the matched rows, not from a single value."""

    op: Literal["top"]
    sort_by: str | None = None
    order: Literal["asc", "desc"] = "asc"
    null_as: float | None = None
    limit: int | None = None
    # Evidence key -> binding to read from each row.
    project: dict[str, str]


EvidenceValue = RawValue | TopProjection


class EmitBlock(CatalogModel):
    source: str
    # Computed before `where`, so a predicate can test a derived value too.
    compute: dict[str, RowCompute] = Field(default_factory=dict)
    where: Predicate | None = None
    aggregate: Aggregate | None = None
    severity: Severity | SeveritySpec
    # "source" takes the subject the source attached to the row (usually a
    # device); "site" makes the finding site-scoped. Aggregated blocks are
    # always site-scoped.
    subject: Literal["source", "site"] = "source"
    title: str
    summary: str
    recommendation: str
    evidence: dict[str, EvidenceValue] = Field(default_factory=dict)


# --- entries ---------------------------------------------------------------


class _BaseEntry(CatalogModel):
    id: str
    category: Category
    # Present so a check can be switched off without deleting its entry, which
    # would orphan any dismissals keyed to its rule_id.
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _RULE_ID_RE.match(v):
            raise ValueError(
                f"{v!r} is not a valid rule id: expected dotted lowercase "
                "segments, e.g. 'wifi.dfs_channel'"
            )
        return v


class PythonEmit(CatalogModel):
    """Prose for one shape of finding a Python rule produces.

    Unlike a declarative emit there is no source or predicate -- the class
    already decided what fires. This is only what to write about it.
    """

    # Matches Binding.key. Rules reporting one kind of thing can leave it.
    key: str = "default"
    severity: Severity | SeveritySpec
    title: str
    summary: str
    recommendation: str
    evidence: dict[str, RawValue] = Field(default_factory=dict)


class PythonEntry(_BaseEntry):
    kind: Literal["python"]
    impl: str
    # Bindings the impl guarantees to provide; templates are checked against them.
    provides: list[str] = Field(default_factory=list)
    # Empty means the impl builds its own Findings.
    emits: list[PythonEmit] = Field(default_factory=list)

    @field_validator("impl")
    @classmethod
    def _validate_impl(cls, v: str) -> str:
        if not _IMPL_RE.match(v):
            raise ValueError(
                f"{v!r} is not an allowed impl: expected 'app.rules.<module>:<ClassName>'"
            )
        return v


class DeclarativeEntry(_BaseEntry):
    kind: Literal["declarative"]
    emits: list[EmitBlock] = Field(min_length=1)


CatalogEntry = Annotated[
    PythonEntry | DeclarativeEntry,
    Field(discriminator="kind"),
]

# CatalogEntry is a union, so it has no .model_validate of its own.
CatalogEntryAdapter: TypeAdapter[CatalogEntry] = TypeAdapter(CatalogEntry)


class CatalogFile(CatalogModel):
    """The top level of one catalog YAML file."""

    rules: list[CatalogEntry]


AllOf.model_rebuild()
AnyOf.model_rebuild()
NotOf.model_rebuild()
