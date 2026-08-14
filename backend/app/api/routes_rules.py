"""Browse the rule catalog, and manage the operator's own checks.

Configuring checks here rather than in the repo is the point of the tool, so
these endpoints do write to disk. What they are allowed to touch is bounded on
every axis, and `loader.user_rule_path` is the single gate every read, write and
delete passes through:

  * only inside RULES_DIR -- a strict name pattern with no separators, no
    traversal and no reserved underscore prefix, then a resolved-parent check
    that also catches a symlink pointing out of the directory;
  * only files ending .yaml;
  * only content that validates as declarative rules, checked before anything is
    written, so an invalid file is never created;
  * never Python, because a user rule may not name code to import. Rule content
    is data.

No endpoint takes a path, and none reads or writes anywhere else. If you are
adding one that does, that is the boundary being crossed.

This does not touch the UniFi console, so SECURITY.md's "read-only against
UniFi" guarantee is unaffected -- that one is about never changing the
operator's network. The relevant caveat is different: there is no
authentication, so the loopback bind is what keeps these endpoints private.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.rules import describe
from app.rules.base import RunHistory
from app.rules.loader import (
    CatalogError,
    RuleFileError,
    _load_constants,
    _substitute,
    compile_entries,
    load_catalog,
    load_overrides,
    save_overrides,
    user_rule_path,
    user_rules_dir,
)
from app.rules.schema import CatalogFile
from app.rules.unsupported import check_enrichments

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rules", tags=["rules"])

# A draft arrives over HTTP rather than being a file the operator wrote, so it
# gets a size bound. This is necessary but nowhere near sufficient on its own --
# see NoAliasLoader.
MAX_DRAFT_CHARS = 64_000
MAX_PREVIEW_FINDINGS = 5


class NoAliasLoader(yaml.SafeLoader):
    """safe_load, minus anchors and aliases.

    An alias bomb (`&a [*b,*b,*b,…]`) is a few hundred bytes and expands inside
    the parser, so a length limit does not contain it. Rejecting aliases costs
    nothing here -- no rule uses them -- and this lives in the route rather than
    the loader so existing user rule files keep parsing exactly as before.
    """

    def compose_node(self, parent, index):
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.YAMLError("YAML anchors and aliases are not supported here")
        return super().compose_node(parent, index)


class RuleDraft(BaseModel):
    yaml: str = Field(min_length=1, max_length=MAX_DRAFT_CHARS)


def _enrichment_configured(attr: str | None) -> bool:
    if attr is None:
        return False
    s = get_settings()
    return {
        "rf": bool(s.unifi_username and s.unifi_password),
        "dns": bool(s.nextdns_api_key and s.nextdns_profile_id),
        "wan": s.wan_probe and not s.demo_mode,
    }.get(attr, False)


def _rules_dir_info() -> dict:
    directory = user_rules_dir()
    return {
        "configured": directory is not None,
        "path": str(directory) if directory else None,
        "exists": directory.is_dir() if directory else False,
    }


def _catalog_payload() -> dict:
    catalog = load_catalog()

    rules = [describe.describe_rule(r) for r in catalog.rules]
    rules += [describe.describe_disabled(d) for d in catalog.disabled]
    rules += [describe.describe_problem(p) for p in catalog.problems]
    rules += [
        describe.describe_unsupported(check, attr, _enrichment_configured(attr))
        for check, attr in check_enrichments()
    ]

    counts: dict[str, int] = {}
    for rule in rules:
        counts[rule["status"]] = counts.get(rule["status"], 0) + 1

    return {
        "loaded_at": catalog.loaded_at.isoformat(),
        "overrides": sorted(load_overrides()),
        "rules_dir": _rules_dir_info(),
        "counts": counts,
        "categories": describe.describe_categories(),
        "constants": _load_constants(),
        "sources": describe.describe_sources(),
        "rules": rules,
    }


@router.get("")
async def list_rules() -> dict:
    """Every check this build knows about, running or not."""
    return _catalog_payload()


@router.post("/reload")
async def reload_rules() -> dict:
    """Re-read the rule files.

    The catalog is cached, so a rule file saved after startup is invisible until
    something clears it -- which is exactly when an operator is authoring one.

    Reads only the files it was already configured to read, and writes nothing.
    """
    load_catalog.cache_clear()
    try:
        return _catalog_payload()
    except CatalogError as exc:
        # A broken built-in catalog. Report it rather than silently restoring the
        # previous one: running rules the operator cannot see is worse than a
        # visible outage, and the next scan would hit this anyway.
        logger.error("catalog failed to reload: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _demo_preview(rules: list) -> dict | None:
    """Run a draft against the bundled sample network.

    Against the operator's own network this would mean a fresh collection --
    dozens of console requests from an unauthenticated endpoint, and seconds of
    latency in a form. The sample fixtures are offline and instant, and catch the
    two things a compile check cannot: a template that renders to nonsense, and a
    predicate that matches everything.
    """
    if not rules:
        return None
    try:
        snapshot = await _demo_snapshot()
        findings = []
        for rule in rules:
            findings.extend(rule.evaluate(snapshot, RunHistory()))
    except Exception as exc:  # noqa: BLE001 - a preview must never fail the check
        logger.warning("draft preview failed: %s", exc)
        return None

    return {
        "basis": "demo_fixtures",
        "matched": len(findings),
        "findings": [
            {
                "severity": str(f.severity),
                "title": f.title,
                "summary": f.summary,
                "subject_name": f.subject_name,
            }
            for f in findings[:MAX_PREVIEW_FINDINGS]
        ],
    }


_DEMO_SNAPSHOT = None


async def _demo_snapshot():
    """The sample snapshot, built once per process.

    Imported lazily so the demo fixtures are only read if someone validates a
    draft; a listing request never touches them.
    """
    global _DEMO_SNAPSHOT
    if _DEMO_SNAPSHOT is None:
        from app.collectors.snapshot import collect_snapshot
        from app.demo import DemoUnifiClient

        _DEMO_SNAPSHOT = await collect_snapshot(DemoUnifiClient())
    return _DEMO_SNAPSHOT


async def _check_draft(text: str, *, replacing: set[str] | None = None) -> dict:
    """Validate rule YAML the way the loader would.

    Every step below is the loader's own, so a draft that passes here is a draft
    that will load -- the restrictions on user rules (the `custom.` namespace,
    and no naming Python to import) come from `compile_entries(trusted=False)`
    rather than a second implementation that could drift.

    ``replacing`` are ids already defined by the file being overwritten; they
    must not count as collisions with themselves.
    """
    warnings: list[dict] = []

    try:
        raw = yaml.load(text, Loader=NoAliasLoader)
    except yaml.YAMLError as exc:
        return {"ok": False, "errors": [{"stage": "yaml", "rule_id": None,
                                         "message": str(exc)}],
                "warnings": [], "rules": [], "preview": None}

    if raw is None:
        return {"ok": False, "errors": [{"stage": "yaml", "rule_id": None,
                                         "message": "The draft is empty."}],
                "warnings": [], "rules": [], "preview": None}

    try:
        # Constants must be expanded here too, or a draft using $DFS_CHANNELS
        # fails validation and then works once saved.
        raw = _substitute(raw, _load_constants(), "draft.yaml")
        parsed = CatalogFile.model_validate(raw)
    except CatalogError as exc:
        return {"ok": False, "errors": [{"stage": "schema", "rule_id": None,
                                         "message": str(exc)}],
                "warnings": [], "rules": [], "preview": None}
    except ValidationError as exc:
        # Per-field rather than one stringified blob, so the form can point at
        # the key that is wrong.
        errors = [
            {"stage": "schema", "rule_id": None,
             "message": f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"}
            for err in exc.errors()
        ]
        return {"ok": False, "errors": errors, "warnings": [], "rules": [],
                "preview": None}

    # Seed with the live catalog so a draft that collides with an existing rule
    # is caught here rather than after it is saved.
    catalog = load_catalog()
    seen = {r.id: r.provenance for r in catalog.rules}
    seen |= {d.entry.id: d.provenance for d in catalog.disabled}
    for rule_id in replacing or ():
        seen.pop(rule_id, None)

    disabled: list = []
    try:
        compiled = compile_entries(
            [(Path("draft.yaml"), entry) for entry in parsed.rules],
            trusted=False,
            seen=seen,
            disabled=disabled,
        )
    except CatalogError as exc:
        return {"ok": False, "errors": [{"stage": "compile", "rule_id": None,
                                         "message": str(exc)}],
                "warnings": [], "rules": [], "preview": None}
    except RecursionError:
        # Deeply nested predicates recurse through validation and compilation;
        # this is not caught by the handlers above and would otherwise 500.
        return {"ok": False, "errors": [{"stage": "compile", "rule_id": None,
                                         "message": "The rule is nested too deeply."}],
                "warnings": [], "rules": [], "preview": None}

    for entry in disabled:
        warnings.append({"message": f"{entry.entry.id} is disabled, so it will not run."})
    if not compiled and not disabled:
        warnings.append({"message": "The draft declares no rules."})

    directory = user_rules_dir()
    if directory is None:
        warnings.append({"message": "RULES_DIR is not set, so saved rules will not load."})
    elif not directory.is_dir():
        warnings.append({"message": f"RULES_DIR {directory} does not exist yet."})

    return {
        "ok": True,
        "errors": [],
        "warnings": warnings,
        "rules": [describe.describe_rule(r) for r in compiled],
        "preview": await _demo_preview(compiled),
    }


@router.post("/validate")
async def validate_draft(draft: RuleDraft) -> dict:
    """Check a draft rule without saving it.

    Deliberately 200 on both outcomes: the request is well-formed either way, and
    the content is what is being reported on. A 4xx would make the client throw
    where it wants to render.
    """
    return await _check_draft(draft.yaml)


# --- managing your own rule files ------------------------------------------
#
# These write to disk. That is the point of the tool -- checks are meant to be
# configured here, not in the repo -- but it is a real surface, so it is bounded
# on every axis that matters:
#
#   * only inside RULES_DIR, resolved by loader.user_rule_path, which refuses
#     separators, traversal, symlinks out of the directory and reserved names;
#   * only files ending .yaml;
#   * only content that validates as declarative rules, checked before anything
#     is written -- an invalid file is never created;
#   * never Python, because user rules cannot name code to import.
#
# It remains an unauthenticated endpoint, so the loopback bind is what keeps it
# private. SECURITY.md says so plainly.


class RuleFileWrite(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_DRAFT_CHARS)


class OverrideUpdate(BaseModel):
    rule_id: str = Field(min_length=1, max_length=200)
    disabled: bool


def _require_rules_dir() -> Path:
    directory = user_rules_dir()
    if directory is None:
        raise HTTPException(
            status_code=409,
            detail="RULES_DIR is not set, so there is nowhere to keep your rules.",
        )
    return directory


def _ids_in(path: Path) -> set[str]:
    """Rule ids a file currently defines, so overwriting it isn't self-collision."""
    if not path.is_file():
        return set()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        return {r["id"] for r in raw.get("rules", []) if isinstance(r, dict) and "id" in r}
    except (yaml.YAMLError, OSError, AttributeError):
        return set()


@router.get("/files")
async def list_rule_files() -> dict:
    """The operator's own rule files, with their contents, for editing."""
    directory = user_rules_dir()
    if directory is None or not directory.is_dir():
        return {"dir": str(directory) if directory else None, "files": []}
    files = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        try:
            files.append({"name": path.name, "path": str(path),
                          "content": path.read_text()})
        except OSError as exc:  # pragma: no cover - unreadable file
            logger.warning("cannot read %s: %s", path, exc)
    return {"dir": str(directory), "files": files}


@router.put("/files/{name}")
async def save_rule_file(name: str, body: RuleFileWrite) -> dict:
    """Create or replace one of the operator's rule files.

    The content is validated first and only written if it would load, so saving
    can never leave the catalog in a state the next scan trips over.
    """
    _require_rules_dir()
    try:
        path = user_rule_path(name)
    except RuleFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await _check_draft(body.content, replacing=_ids_in(path))
    if not result["ok"]:
        # Reported, not raised: the form renders these inline the same way it
        # renders a plain validation failure.
        return {**result, "saved": False, "name": name, "path": str(path)}

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content)
    load_catalog.cache_clear()
    return {**result, "saved": True, "name": name, "path": str(path),
            "catalog": _catalog_payload()}


@router.delete("/files/{name}")
async def delete_rule_file(name: str) -> dict:
    """Remove one of the operator's rule files."""
    _require_rules_dir()
    try:
        path = user_rule_path(name)
    except RuleFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{name} does not exist.")

    path.unlink()
    load_catalog.cache_clear()
    return {"deleted": name, "catalog": _catalog_payload()}


@router.post("/overrides")
async def set_override(update: OverrideUpdate) -> dict:
    """Switch a check on or off for this install.

    Written to an overrides file rather than editing the shipped catalog, so the
    choice survives an upgrade and works in a container where the built-in rule
    files live inside the image.
    """
    _require_rules_dir()
    known = {r.id for r in load_catalog().rules}
    known |= {d.entry.id for d in load_catalog().disabled}
    if update.rule_id not in known:
        raise HTTPException(status_code=404, detail=f"No rule called {update.rule_id}.")

    disabled = load_overrides()
    disabled.add(update.rule_id) if update.disabled else disabled.discard(update.rule_id)
    save_overrides(disabled)
    load_catalog.cache_clear()
    return {"rule_id": update.rule_id, "disabled": update.disabled,
            "catalog": _catalog_payload()}
