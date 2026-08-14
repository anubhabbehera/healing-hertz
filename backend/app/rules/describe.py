"""Turn the loaded catalog into JSON for the API.

Kept out of the route module because it is the bulk of the work and both the
listing and the draft-validation endpoint need the same shapes -- a draft has to
render exactly like a loaded rule, or the preview is a different thing from what
will actually run.

Two shapes are normalised here so the frontend has one renderer rather than
several:

* ``emits`` is always a list. A declarative rule holds a list; a Python rule
  holds a dict keyed by the ``Binding.key`` its impl returns. Both come out as
  a list of blocks carrying ``index`` and a nullable ``key``.
* ``severity`` is always ``{base, escalate}``. A bare severity becomes a base
  with no escalations.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from .base import Category, Severity, UnsupportedCheck
from .declarative import DeclarativeRule
from .loader import CATALOG_DIR, DisabledRule, Provenance, user_rules_dir
from .schema import DeclarativeEntry, PythonEntry, SeveritySpec
from .sources import REGISTRY as SOURCE_REGISTRY


def path_scope() -> str:
    """Whether the paths this module reports mean anything outside the process.

    A hint for presentation only. In a container the absolute path is real but
    has no host counterpart, so the UI leads with the repo-relative path instead.
    """
    return "container" if Path("/.dockerenv").exists() else "host"


def _is_user_rule(rule_id: str) -> bool:
    # Sound rather than heuristic: compile_entries(trusted=False) enforces the
    # prefix, so a non-custom id cannot have come from a user file.
    return rule_id.startswith("custom.")


def _catalog_file(provenance: Provenance, rule_id: str) -> dict:
    """Where a rule's YAML lives on this machine.

    ``editable`` is what the UI keys its controls off: a rule in RULES_DIR is
    the operator's to change, a built-in one ships with the app and is switched
    off through the overrides file instead.
    """
    if _is_user_rule(rule_id):
        directory = user_rules_dir()
        return {
            "name": provenance.file,
            "path": str(directory / provenance.file) if directory else None,
            "editable": True,
        }
    return {
        "name": provenance.file,
        "path": str(CATALOG_DIR / provenance.file),
        "editable": False,
    }


def _severity(spec: Severity | SeveritySpec) -> dict:
    if isinstance(spec, SeveritySpec):
        return {
            "base": str(spec.base),
            "escalate": [
                {"when": _predicate(step.when), "to": str(step.to)}
                for step in spec.escalate
            ],
        }
    return {"base": str(spec), "escalate": []}


def _predicate(node: Any) -> Any:
    """A predicate tree as plain JSON.

    ``by_alias`` is load-bearing: NotOf stores its child as ``negate`` but
    aliases it to ``not``, which is what the YAML says. Without the alias the
    API would report a field name that appears in no rule file, and nothing
    would error.
    """
    if node is None:
        return None
    return node.model_dump(mode="json", by_alias=True)


def _emit_block(block: Any, index: int, key: str | None) -> dict:
    """One emit block. Declarative and Python blocks share everything but the
    fields only a declarative block has."""
    out = {
        "index": index,
        "key": key,
        "severity": _severity(block.severity),
        "title": block.title,
        "summary": block.summary,
        "recommendation": block.recommendation,
        "evidence": {
            name: value.model_dump(mode="json", by_alias=True)
            for name, value in block.evidence.items()
        },
    }
    if hasattr(block, "source"):
        out |= {
            "source": block.source,
            "subject": block.subject,
            "where": _predicate(block.where),
            "compute": {
                name: spec.model_dump(mode="json") for name, spec in block.compute.items()
            },
            "aggregate": (
                block.aggregate.model_dump(mode="json") if block.aggregate else None
            ),
        }
    return out


def _impl_info(rule: Any) -> dict:
    """Where a Python rule's logic lives.

    The catalog's ``impl`` string isn't retained after the class is resolved, so
    it is reconstructed from the instance -- which reproduces the exact value.
    """
    cls = type(rule.impl)
    module = cls.__module__
    try:
        line = inspect.getsourcelines(cls)[1]
        path = inspect.getfile(cls)
    except (OSError, TypeError):  # pragma: no cover - only if source is stripped
        line, path = None, None
    doc = inspect.getdoc(cls) or ""
    return {
        "ref": f"{module}:{cls.__qualname__}",
        "module": module,
        "class": cls.__qualname__,
        "doc": doc.split("\n\n", 1)[0],
        "path": path,
        "line": line,
    }


def describe_rule(rule: Any) -> dict:
    """One compiled, running rule."""
    declarative = isinstance(rule, DeclarativeRule)
    out = {
        "id": rule.id,
        "kind": "declarative" if declarative else "python",
        "status": "active",
        "validated": True,
        "category": str(rule.category),
        "origin": "user" if _is_user_rule(rule.id) else "builtin",
        "source_file": _catalog_file(rule.provenance, rule.id),
    }
    if declarative:
        out["emits"] = [
            _emit_block(emit.block, index, None)
            for index, emit in enumerate(rule.emits)
        ]
    else:
        out["impl"] = _impl_info(rule)
        out["emits"] = [
            _emit_block(block, index, key)
            for index, (key, block) in enumerate(rule.emits.items())
        ]
    return out


def describe_disabled(disabled: DisabledRule) -> dict:
    """An entry that parsed but was switched off.

    It was never compiled, so nothing here is validated -- a template could name
    a binding its source doesn't provide. Reporting `validated: false` lets the
    UI show that honestly rather than implying it would work if re-enabled.
    """
    entry = disabled.entry
    out = {
        "id": entry.id,
        "kind": entry.kind,
        "status": "disabled",
        "validated": False,
        "category": str(entry.category),
        "origin": "user" if _is_user_rule(entry.id) else "builtin",
        "source_file": _catalog_file(disabled.provenance, entry.id),
        "emits": [],
    }
    if isinstance(entry, DeclarativeEntry | PythonEntry):
        out["emits"] = [
            _emit_block(block, index, getattr(block, "key", None))
            for index, block in enumerate(entry.emits)
        ]
    if isinstance(entry, PythonEntry):
        out["provides"] = list(entry.provides)
    return out


def describe_unsupported(check: UnsupportedCheck, enrichment: str | None,
                         configured: bool) -> dict:
    """A check that cannot run until an integration supplies its data."""
    return {
        "id": check.rule_id,
        "kind": "none",
        "status": "not_checkable",
        "validated": False,
        # Several of these aren't Categories at all (there is no `security`),
        # so report nothing rather than inventing one.
        "category": None,
        "origin": "builtin",
        "title": check.title,
        "reason": check.reason,
        "enrichment": enrichment,
        "enrichment_configured": configured,
        "source_file": {
            "name": "unsupported.py",
            "path": str(Path(__file__).parent / "unsupported.py"),
            "editable": False,
        },
        "emits": [],
    }


def describe_problem(problem: UnsupportedCheck) -> dict:
    """A user rule file that failed to load."""
    directory = user_rules_dir()
    name = f"{problem.rule_id.split('.', 1)[-1]}.yaml"
    return {
        "id": problem.rule_id,
        "kind": "none",
        "status": "unloadable",
        "validated": False,
        "category": None,
        "origin": "user",
        "title": problem.title,
        "reason": problem.reason,
        "source_file": {
            "name": name,
            "path": str(directory / name) if directory else None,
            "editable": True,
        },
        "emits": [],
    }


def describe_sources() -> list[dict]:
    """The source registry.

    Built field by field on purpose: Source.iterate is a callable and bindings
    is a frozenset, so asdict() would either fail or serialise a function
    address. Bindings are sorted so the wire order is stable.
    """
    return [
        {"name": name, "doc": source.doc, "bindings": sorted(source.bindings)}
        for name, source in sorted(SOURCE_REGISTRY.items())
    ]


def describe_categories() -> list[str]:
    return [str(c) for c in Category]
