"""Check that the rule catalog loads, without running a scan.

    uv run python -m app.rules.validate

Exits non-zero if the built-in catalog is broken or any user rule file failed
to load. Built-in failures are a shipped bug, so CI should run this; operators
can run it after editing RULES_DIR to see what a scan would have skipped.
"""

from __future__ import annotations

import sys

from .declarative import DeclarativeRule
from .loader import CatalogError, load_catalog


def main() -> int:
    try:
        catalog = load_catalog()
    except CatalogError as exc:
        print(f"catalog is broken: {exc}", file=sys.stderr)
        return 1

    declarative = sum(1 for r in catalog.rules if isinstance(r, DeclarativeRule))
    custom = sum(1 for r in catalog.rules if r.id.startswith("custom."))
    print(
        f"{len(catalog.rules)} rules loaded "
        f"({declarative} declarative, {len(catalog.rules) - declarative} python"
        + (f", {custom} user-supplied" if custom else "")
        + ")"
    )

    for problem in catalog.problems:
        print(f"  SKIPPED {problem.rule_id}: {problem.reason}", file=sys.stderr)
    return 1 if catalog.problems else 0


if __name__ == "__main__":
    sys.exit(main())
