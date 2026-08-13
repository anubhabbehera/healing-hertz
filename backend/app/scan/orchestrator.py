from __future__ import annotations

import asyncio
import logging

from app.advisor.llm import generate_advice
from app.collectors.enrich import enrich_snapshot
from app.collectors.snapshot import collect_snapshot
from app.config import Settings
from app.db import repo
from app.db.engine import get_session_factory
from app.rules import health_score, run_rules
from app.unifi.client import UnifiClientProtocol

from .progress import ScanProgress

logger = logging.getLogger(__name__)

# One scan at a time; POST /api/scans returns 409 while held.
scan_lock = asyncio.Lock()


async def run_scan(
    run_id: str,
    client: UnifiClientProtocol,
    settings: Settings,
    progress: ScanProgress,
) -> None:
    session_factory = get_session_factory()
    try:
        await progress.emit("collect", "Collecting telemetry", 5)

        async def on_collect(detail: str) -> None:
            await progress.emit("collect", detail)

        snapshot = await collect_snapshot(client, settings.unifi_site, on_collect)
        await enrich_snapshot(snapshot, settings, on_collect)

        await progress.emit("analyze", "Running diagnostic rules", 55)
        async with session_factory() as session:
            history = await repo.load_history(session)
            dismissals = await repo.list_dismissals(session)
        findings, unsupported = run_rules(snapshot, history)
        # Findings the operator has acknowledged stay visible but cost no score.
        for finding in findings:
            finding.dismissed = repo.is_dismissed(dismissals, finding.rule_id, finding.subject_id)
        score = health_score(findings)
        await progress.emit("analyze", f"{len(findings)} findings, health score {score}", 65)

        await progress.emit("advise", "Generating remediation plan", 70)
        # Only unresolved problems go to the advisor: writing a remediation plan
        # for something the operator has explicitly dismissed is noise, and it
        # wastes payload budget on findings that will never be acted on.
        advice, advice_status, advice_error = await generate_advice(
            [f for f in findings if not f.dismissed], snapshot, history, settings
        )
        await progress.emit("advise", f"Advice: {advice_status}", 90)

        await progress.emit("persist", "Saving results", 95)
        async with session_factory() as session:
            await repo.save_run_results(
                session, run_id, snapshot, findings, score, advice, advice_status,
                unsupported=unsupported, advice_error=advice_error,
            )
        await progress.emit("done", "Scan complete", 100)
    except Exception as exc:
        logger.exception("Scan %s failed", run_id)
        async with session_factory() as session:
            await repo.mark_failed(session, run_id, str(exc))
        await progress.emit("error", str(exc))
    finally:
        await client.aclose()
