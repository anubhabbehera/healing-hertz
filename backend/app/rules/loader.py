"""Read the rule catalog and compile it into runnable rules.

Catalog files are numbered so glob order is evaluation order. That order is
user-visible: ``run_rules`` sorts findings by severity with a stable sort, so
within a severity band findings appear in the order their rules ran.

Built-in catalog failures are fatal — a malformed catalog that ships is a bug,
and running with a silently-reduced rule set would misreport a network as
healthy. (User-supplied catalogs, added later, fail soft instead.)
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.collectors.snapshot import Snapshot

from .base import Category, Finding, RunHistory
from .declarative import RuleCompileError, compile_declarative
from .schema import CatalogEntry, CatalogFile, DeclarativeEntry, PythonEntry

CATALOG_DIR = Path(__file__).parent / "catalog"
CONSTANTS_FILE = CATALOG_DIR / "_constants.yaml"


class CatalogError(Exception):
    """A catalog file is malformed or names something that does not exist."""


def _load_constants() -> dict[str, object]:
    if not CONSTANTS_FILE.exists():
        return {}
    try:
        raw = yaml.safe_load(CONSTANTS_FILE.read_text()) or {}
    except yaml.YAMLError as exc:
        raise CatalogError(f"{CONSTANTS_FILE.name}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise CatalogError(f"{CONSTANTS_FILE.name}: expected a mapping of name to value")
    return raw


def _substitute(node: object, constants: dict[str, object], where: str) -> object:
    """Replace "$NAME" with its constant, anywhere in a parsed catalog file.

    Named constants keep sets like the DFS channel list in one place instead of
    inline in each rule that reads them, and give them a name a reader
    recognises.
    """
    if isinstance(node, str) and node.startswith("$"):
        name = node[1:]
        if name not in constants:
            known = ", ".join(sorted(constants)) or "none defined"
            raise CatalogError(f"{where}: unknown constant {node!r}; defined: {known}")
        return constants[name]
    if isinstance(node, dict):
        return {k: _substitute(v, constants, where) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, constants, where) for v in node]
    return node


@dataclass(frozen=True)
class Provenance:
    """Where a compiled rule came from, for error messages.

    A bad rule in Python is a traceback with a line number; a bad rule in the
    catalog is otherwise an anonymous failure deep in the engine. Every raised
    error carries this.
    """

    file: str
    rule_id: str

    def __str__(self) -> str:
        return f"{self.file}[{self.rule_id}]"


@dataclass
class CatalogRule:
    """A catalog entry bound to its implementation.

    Satisfies the ``Rule`` protocol in base.py, so it drops into ``RULES``
    with no change to the engine.
    """

    id: str
    category: Category
    provenance: Provenance
    impl: object

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        return self.impl.evaluate(snapshot, history)


def _resolve_impl(entry: PythonEntry, provenance: Provenance) -> object:
    """Import and instantiate the class an entry names.

    The module path is already constrained to ``app.rules.*`` by the schema, so
    this cannot reach outside the package however the catalog was written.
    """
    module_name, _, class_name = entry.impl.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise CatalogError(f"{provenance}: cannot import {module_name!r}: {exc}") from exc

    cls = getattr(module, class_name, None)
    if cls is None:
        raise CatalogError(f"{provenance}: {module_name!r} has no attribute {class_name!r}")

    instance = cls()
    if not callable(getattr(instance, "evaluate", None)):
        raise CatalogError(f"{provenance}: {entry.impl} has no evaluate() method")
    return instance


def _read_file(path: Path, constants: dict[str, object]) -> list[CatalogEntry]:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise CatalogError(f"{path.name}: invalid YAML: {exc}") from exc

    if raw is None:
        return []
    raw = _substitute(raw, constants, path.name)
    try:
        return CatalogFile.model_validate(raw).rules
    except ValidationError as exc:
        raise CatalogError(f"{path.name}: {exc}") from exc


def compile_entries(entries: list[tuple[Path, object]]) -> list[object]:
    """Compile parsed entries into runnable rules, rejecting duplicate ids."""
    seen: dict[str, Provenance] = {}
    rules: list[object] = []
    for path, entry in entries:
        provenance = Provenance(path.name, entry.id)
        if entry.id in seen:
            raise CatalogError(
                f"{provenance}: duplicate rule id, already declared in {seen[entry.id]}"
            )
        seen[entry.id] = provenance
        if not entry.enabled:
            continue

        if isinstance(entry, DeclarativeEntry):
            try:
                rules.append(compile_declarative(entry, provenance))
            except RuleCompileError as exc:
                raise CatalogError(str(exc)) from exc
        else:
            rules.append(
                CatalogRule(
                    id=entry.id,
                    category=entry.category,
                    provenance=provenance,
                    impl=_resolve_impl(entry, provenance),
                )
            )
    return rules


@lru_cache(maxsize=1)
def load_catalog() -> list[CatalogRule]:
    """Every built-in rule, in catalog order.

    Cached rather than built at import so a malformed catalog surfaces as a
    CatalogError from the first scan, not as an ImportError that breaks
    unrelated tests confusingly.
    """
    # Leading underscore means "not a rule file" -- _constants.yaml is data the
    # rule files reference, not a source of entries.
    paths = sorted(p for p in CATALOG_DIR.glob("*.yaml") if not p.name.startswith("_"))
    if not paths:
        raise CatalogError(f"no catalog files found in {CATALOG_DIR}")
    constants = _load_constants()
    entries = [(path, entry) for path in paths for entry in _read_file(path, constants)]
    return compile_entries(entries)
