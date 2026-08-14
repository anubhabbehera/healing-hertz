"""Pydantic models for the rule catalog.

The catalog is the registry of every diagnostic check: its identity, its
category, and (as rules are converted) its thresholds and prose. Today every
entry still delegates its predicate to a Python class; the schema grows a
declarative form as rules move across.

These models set ``extra="forbid"``, deliberately the opposite of ``UnifiModel``
(``app/unifi/models.py``). An unknown key in an upstream API response is routine
and must not break parsing; an unknown key in a catalog file is a typo, and a
misspelled ``recomendation:`` silently becoming an empty recommendation in the
UI is exactly the failure this schema exists to prevent.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .base import Category

# An impl may only name a class inside this package. A catalog file is data,
# and data must never choose an arbitrary import target -- this is the boundary
# that will let user-supplied catalog files exist at all.
_IMPL_RE = re.compile(r"^app\.rules\.[a-z_]+:[A-Za-z_][A-Za-z0-9_]*$")

# Rule ids are the durable key for dismissals, run diffs and LLM suggestion
# linking (see db/models.py Dismissal), so the grammar is deliberately narrow:
# dotted lowercase segments, nothing that needs quoting or escaping downstream.
_RULE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CatalogEntry(CatalogModel):
    """One rule as declared in the catalog."""

    id: str
    kind: Literal["python"]
    impl: str
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

    @field_validator("impl")
    @classmethod
    def _validate_impl(cls, v: str) -> str:
        if not _IMPL_RE.match(v):
            raise ValueError(
                f"{v!r} is not an allowed impl: expected 'app.rules.<module>:<ClassName>'"
            )
        return v


class CatalogFile(CatalogModel):
    """The top level of one catalog YAML file."""

    rules: list[CatalogEntry]
