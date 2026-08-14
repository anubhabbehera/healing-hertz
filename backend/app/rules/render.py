"""Turn bindings into Findings.

Every Finding produced from the catalog is built here, which makes this the one
place severity and category are coerced onto their enums. That matters more
than it looks: severity strings are persisted verbatim and re-read by
score_from_severities on every dismissal change, so a value that never becomes
a Severity must not reach the database.

Templates are rendered with str.format_map, which gives byte-identical output
to the f-strings the rules use today -- f-strings and format share __format__,
so "{cpu:.0f}" formats the same either way.

The security note is that "{a.__class__.__init__.__globals__}".format(a=obj) is
a real sandbox escape and format_map does not close it. Two things close it
here, and both are required:

  1. validate_template rejects any replacement field that is not a bare
     identifier, so attribute and subscript access never reach the formatter.
  2. Bindings are primitives (enforced by the source contract), so even a
     validator bypass has nothing to traverse.
"""

from __future__ import annotations

import re
from string import Formatter
from typing import Any

from .base import Category, Finding, Severity

_FORMATTER = Formatter()

# Deliberately narrow: alignment, sign, width, precision and a type character.
# Bounded width because "{x:>999999999}" is a cheap way to exhaust memory.
_FORMAT_SPEC_RE = re.compile(r"^[<>^=]?[+\- ]?#?0?\d{0,3}(?:\.\d{1,3})?[bcdeEfFgGnosxX%]?$")

PRIMITIVES = (str, int, float, bool, type(None))


class TemplateError(ValueError):
    """A template is malformed or names something that does not exist."""


def template_fields(template: str) -> set[str]:
    return {f for _, f, _, _ in _FORMATTER.parse(template) if f is not None}


def validate_template(template: str, available: set[str], where: str) -> None:
    """Reject a template that could escape the binding namespace."""
    for _, field, spec, conversion in _FORMATTER.parse(template):
        if field is None:
            continue
        if not field.isidentifier():
            # Catches "{a.__class__}", "{a[0]}" and positional "{0}" in one test.
            raise TemplateError(
                f"{where}: {{{field}}} is not a plain name; templates may only "
                "reference bindings by name"
            )
        if field not in available:
            close = ", ".join(sorted(available)[:8])
            raise TemplateError(
                f"{where}: {{{field}}} is not available here. Known bindings include: {close}"
            )
        if conversion not in (None, "s", "r", "a"):
            raise TemplateError(f"{where}: unsupported conversion !{conversion}")
        if spec and not _FORMAT_SPEC_RE.match(spec):
            raise TemplateError(f"{where}: unsupported format spec {spec!r} on {{{field}}}")


def render(template: str, bindings: dict[str, Any], where: str) -> str:
    try:
        return template.format_map(bindings)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise TemplateError(f"{where}: cannot render {template!r}: {exc}") from exc


def make_finding(
    *,
    rule_id: str,
    category: Category,
    severity: str,
    title: str,
    summary: str,
    recommendation: str,
    evidence: dict,
    subject_type: str = "site",
    subject_id: str | None = None,
    subject_name: str | None = None,
) -> Finding:
    """The single point where a catalog rule becomes a Finding."""
    return Finding(
        rule_id=rule_id,
        severity=Severity(severity),
        category=Category(category),
        title=title,
        summary=summary,
        evidence=evidence,
        recommendation=recommendation,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_name=subject_name,
    )
