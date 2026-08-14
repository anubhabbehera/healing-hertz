"""Browse the rule catalog, and check a draft rule before saving it.

**Nothing here writes to the filesystem, and nothing here takes a path.**

That is deliberate and load-bearing. The app has no authentication and is
loopback-only by design (SECURITY.md); a write endpoint would therefore be an
arbitrary-file-write primitive for anything able to issue a request from the
host. CORS does not help -- it governs who may *read* a response, while a simple
POST still executes, and the side effect is the whole point of such an attack.

So authoring a rule is deliberately generate-and-copy: the operator gets
validated YAML and saves it themselves. If you are here to add a "save to
RULES_DIR" endpoint, that is the thing this design exists to avoid.

Validation is not a write path under SECURITY.md's read-only rule, which scopes
"only GET requests are issued" to outbound console traffic: it issues no UniFi
request, opens no database session, and reads nothing beyond the catalog files
the process already loads. POST /api/settings/test-connection is the existing
precedent for a POST that computes and mutates nothing.
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
    _load_constants,
    _substitute,
    compile_entries,
    load_catalog,
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
        "path_scope": describe.path_scope(),
        "repo_ref": describe.REPO_REF,
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


@router.post("/validate")
async def validate_draft(draft: RuleDraft) -> dict:
    """Check a draft rule without saving it.

    Deliberately 200 on both outcomes: the request is well-formed either way, and
    the content is what is being reported on. A 4xx would make the client throw
    where it wants to render.

    Every step below is the loader's own, so a draft that passes here is a draft
    that will load -- the restrictions on user rules (the `custom.` namespace,
    and no naming Python to import) come from `compile_entries(trusted=False)`
    rather than a second implementation that could drift.
    """
    errors: list[dict] = []
    warnings: list[dict] = []

    try:
        raw = yaml.load(draft.yaml, Loader=NoAliasLoader)
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
        for err in exc.errors():
            location = ".".join(str(p) for p in err["loc"])
            errors.append({"stage": "schema", "rule_id": None,
                           "message": f"{location}: {err['msg']}"})
        return {"ok": False, "errors": errors, "warnings": [], "rules": [],
                "preview": None}

    # Seed with the live catalog so a draft that collides with an existing rule
    # is caught here rather than after it is saved.
    catalog = load_catalog()
    seen = {r.id: r.provenance for r in catalog.rules}
    seen |= {d.entry.id: d.provenance for d in catalog.disabled}

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
