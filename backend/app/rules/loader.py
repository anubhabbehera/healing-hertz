"""Read the rule catalog and compile it into runnable rules.

Catalog files are numbered so glob order is evaluation order. That order is
user-visible: ``run_rules`` sorts findings by severity with a stable sort, so
within a severity band findings appear in the order their rules ran.

Built-in and user catalogs fail differently, on purpose.

A malformed built-in catalog is a bug that shipped, and running with a
silently-reduced rule set would misreport a network as healthy -- so it raises.
A malformed *user* catalog is an operator's half-finished edit, and taking down
their scan over it would be hostile -- so the file is skipped and reported
through the same "not checkable" channel a failing rule uses, which means the
parse error is visible in the UI rather than only in a log.

User catalogs are also the reason for the two hard restrictions enforced here:
ids must be under the ``custom.`` prefix, and ``kind: python`` is rejected
outside the built-in directory.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.collectors.snapshot import Snapshot
from app.config import get_settings

from .base import Binding, Category, Finding, RunHistory, UnsupportedCheck
from .declarative import (
    RuleCompileError,
    compile_declarative,
    predicate_bindings,
    severity_for,
)
from .render import TemplateError, make_finding, render, validate_template
from .schema import CatalogEntry, CatalogFile, DeclarativeEntry, PythonEmit, PythonEntry

logger = logging.getLogger(__name__)

CATALOG_DIR = Path(__file__).parent / "catalog"
CONSTANTS_FILE = CATALOG_DIR / "_constants.yaml"

# Every operator-supplied rule id lives under this prefix. That makes a
# collision with a built-in id structurally impossible, so a user rule can
# never hijack an existing dismissal row or poison a run diff, and an id in the
# database says where it came from.
USER_RULE_PREFIX = "custom"


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
    """A catalog entry bound to its Python implementation.

    Satisfies the ``Rule`` protocol in base.py, so it drops into ``RULES``
    with no change to the engine.

    With emit blocks the impl returns Bindings and its prose is rendered here,
    through the same factory declarative rules use. Without them the impl builds
    its own Findings -- still supported, but the catalog then knows nothing
    about what the rule says.
    """

    id: str
    category: Category
    provenance: Provenance
    impl: object
    emits: dict[str, PythonEmit] = field(default_factory=dict)

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        produced = self.impl.evaluate(snapshot, history)
        if not self.emits:
            return produced
        return [self._render(b) for b in produced]

    def _render(self, binding: Binding) -> Finding:
        try:
            block = self.emits[binding.key]
        except KeyError:
            raise CatalogError(
                f"{self.provenance}: returned a binding keyed {binding.key!r}, "
                f"which has no emit block (declared: {sorted(self.emits)})"
            ) from None
        where = str(self.provenance)
        site_scoped = binding.subject_type == "site"
        return make_finding(
            rule_id=self.id,
            category=self.category,
            severity=severity_for(block, binding.vars),
            title=render(block.title, binding.vars, where),
            summary=render(block.summary, binding.vars, where),
            recommendation=render(block.recommendation, binding.vars, where),
            evidence={k: binding.vars[v.raw] for k, v in block.evidence.items()},
            subject_type=binding.subject_type,
            subject_id=None if site_scoped else binding.subject_id,
            subject_name=None if site_scoped else binding.subject_name,
        )


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


def _compile_python_emits(entry: PythonEntry, provenance: Provenance) -> dict[str, PythonEmit]:
    """Check a Python rule's prose against the bindings it promises to provide."""
    if not entry.emits:
        if entry.provides:
            raise CatalogError(
                f"{provenance}: declares provides but no emits, so nothing reads them"
            )
        return {}

    available = set(entry.provides)
    blocks: dict[str, PythonEmit] = {}
    for block in entry.emits:
        if block.key in blocks:
            raise CatalogError(f"{provenance}: duplicate emit key {block.key!r}")
        where = f"{provenance} emit[{block.key}]"
        for label, template in (("title", block.title), ("summary", block.summary),
                                ("recommendation", block.recommendation)):
            validate_template(template, available, f"{where}.{label}")
        for name, value in block.evidence.items():
            if value.raw not in available:
                raise CatalogError(
                    f"{where}: evidence {name!r} reads {value.raw!r}, "
                    f"which {entry.impl} does not declare in provides"
                )
        for step in getattr(block.severity, "escalate", []):
            missing = predicate_bindings(step.when) - available
            if missing:
                raise CatalogError(
                    f"{where}: severity escalation reads {sorted(missing)}, "
                    "which is not in provides"
                )
        blocks[block.key] = block
    return blocks


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


def compile_entries(
    entries: list[tuple[Path, object]],
    *,
    trusted: bool = True,
    seen: dict[str, Provenance] | None = None,
) -> list[object]:
    """Compile parsed entries into runnable rules, rejecting duplicate ids.

    ``trusted`` is False for operator-supplied catalogs, which may not name a
    Python implementation and must stay inside the ``custom.`` namespace.
    """
    seen = {} if seen is None else seen
    rules: list[object] = []
    for path, entry in entries:
        provenance = Provenance(path.name, entry.id)

        if not trusted:
            if not entry.id.startswith(f"{USER_RULE_PREFIX}."):
                raise CatalogError(
                    f"{provenance}: a user rule id must start with "
                    f"'{USER_RULE_PREFIX}.' so it can never collide with a built-in "
                    "one and orphan its dismissals"
                )
            if isinstance(entry, PythonEntry):
                raise CatalogError(
                    f"{provenance}: kind 'python' is only allowed in the built-in "
                    "catalog -- a user rule may not name code to import"
                )

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
            except (RuleCompileError, TemplateError) as exc:
                # Both mean the same thing to a caller -- this entry does not
                # compile -- and a user catalog needs a single type to catch if
                # it is to fail soft.
                raise CatalogError(str(exc)) from exc
        else:
            rules.append(
                CatalogRule(
                    id=entry.id,
                    category=entry.category,
                    provenance=provenance,
                    impl=_resolve_impl(entry, provenance),
                    emits=_compile_python_emits(entry, provenance),
                )
            )
    return rules


def _rule_files(directory: Path) -> list[Path]:
    # Leading underscore means "not a rule file" -- _constants.yaml is data the
    # rule files reference, not a source of entries.
    return sorted(
        p for p in directory.glob("*.yaml") if not p.name.startswith("_")
    )


def _load_user_rules(
    directory: Path, constants: dict[str, object], seen: dict[str, Provenance]
) -> tuple[list[object], list[UnsupportedCheck]]:
    """Compile an operator's catalog, reporting rather than raising on failure."""
    rules: list[object] = []
    problems: list[UnsupportedCheck] = []
    for path in _rule_files(directory):
        try:
            entries = [(path, entry) for entry in _read_file(path, constants)]
            rules.extend(compile_entries(entries, trusted=False, seen=seen))
        except CatalogError as exc:
            logger.warning("skipping user rule file %s: %s", path.name, exc)
            problems.append(UnsupportedCheck(
                rule_id=f"{USER_RULE_PREFIX}.{path.stem}",
                title=f"Custom rules in {path.name}",
                reason=f"This file could not be loaded, so its checks did not run: {exc}",
            ))
    return rules, problems


@dataclass(frozen=True)
class Catalog:
    rules: list[object]
    # Files that failed to load, surfaced to the operator as "not checkable".
    problems: list[UnsupportedCheck]


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    """Every rule, built-in first then user-supplied, in catalog order.

    Cached rather than built at import so a malformed catalog surfaces as a
    CatalogError from the first scan, not as an ImportError that breaks
    unrelated tests confusingly.
    """
    paths = _rule_files(CATALOG_DIR)
    if not paths:
        raise CatalogError(f"no catalog files found in {CATALOG_DIR}")
    constants = _load_constants()

    seen: dict[str, Provenance] = {}
    entries = [(path, entry) for path in paths for entry in _read_file(path, constants)]
    rules = compile_entries(entries, trusted=True, seen=seen)

    problems: list[UnsupportedCheck] = []
    rules_dir = get_settings().rules_dir
    if rules_dir:
        directory = Path(rules_dir).expanduser()
        if directory.is_dir():
            # Appended after the built-ins so their evaluation order is unchanged.
            user_rules, problems = _load_user_rules(directory, constants, seen)
            rules.extend(user_rules)
        else:
            logger.warning("RULES_DIR %s is not a directory; no user rules loaded", directory)

    return Catalog(rules=rules, problems=problems)
