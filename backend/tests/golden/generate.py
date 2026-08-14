"""Regenerate tests/golden/findings.json.

Run from the backend directory:

    uv run python -m tests.golden.generate

Only run this when a rule's output is *intentionally* changing, and review the
resulting diff line by line — this file is the sole guard against a refactor
silently rewording a finding or dropping a rule.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

GOLDEN = Path(__file__).parent / "findings.json"


async def main() -> int:
    os.environ["DEMO_MODE"] = "true"
    for var in ("UNIFI_HOST", "UNIFI_API_KEY", "UNIFI_USERNAME", "UNIFI_PASSWORD",
                "NEXTDNS_API_KEY", "NEXTDNS_PROFILE_ID", "ANTHROPIC_API_KEY"):
        os.environ[var] = ""
    os.environ["WAN_PROBE"] = "false"

    from tests.rule_scenarios import collect_all

    data = await collect_all()
    GOLDEN.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")

    scenarios = len(data)
    findings = sum(len(v["findings"]) for v in data.values())
    covered = {f["rule_id"] for v in data.values() for f in v["findings"]}
    print(f"wrote {GOLDEN.relative_to(Path.cwd())}: "
          f"{scenarios} scenarios, {findings} findings, {len(covered)} distinct rules")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
