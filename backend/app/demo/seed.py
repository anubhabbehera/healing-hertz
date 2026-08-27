"""Fill a demo database with scan history, so the trend views have something to show.

A demo scan is deterministic: the fixtures never move, so twenty demo scans
produce twenty identical rows and every chart is a flat line. Nothing in the
trend rules can fire against that, and neither can a person judge whether they
work.

This writes real runs — real findings, real metrics, saved through the same
repo functions a live scan uses — over a snapshot whose telemetry is moved
between runs along shapes worth looking at: a metric that steps and stays, one
that climbs toward a ceiling, one that spikes only at the end, and a pool that
slowly fills.

    uv run python -m app.demo.seed --runs 24 --days 12

Demo only. It refuses to run unless DEMO_MODE is set, because inventing scan
history for a real site would be indistinguishable from data the console
actually reported.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.collectors.snapshot import Snapshot, collect_snapshot
from app.config import get_settings
from app.db import repo
from app.db.engine import dispose, get_session_factory, init_db
from app.db.models import MetricSnapshot, ScanRun
from app.rules import health_score, run_rules
from app.unifi.models import ClientAccess, ClientOverview

from . import DemoUnifiClient

# Fixed so two seeded databases with the same arguments are identical, which is
# what makes a screenshot or a bug report reproducible.
SEED = 20260823


def _repair(snapshot: Snapshot) -> None:
    """Put the demo site into the state it would be in if someone had fixed it.

    The shipped fixtures are a site with every fault at once, which is the right
    baseline for testing rules and the wrong one for a history: a run that is
    already at zero cannot get worse, so the score chart is a flat floor. Faults
    come back one at a time in `_DEGRADATIONS` below.
    """
    for device in snapshot.devices:
        device.state = "ONLINE"
        device.firmware_updatable = False
        device.firmware_version = "6.6.55" if device.id.startswith("ap") else device.firmware_version
    for detail in snapshot.device_details.values():
        detail.state = "ONLINE"
        detail.firmware_updatable = False
        for port in detail.interfaces.ports:
            if port.speed_mbps is not None and port.max_speed_mbps:
                port.speed_mbps = port.max_speed_mbps
            if port.poe is not None and port.poe.state == "LIMITED":
                port.poe.state = "UP"
    ap1 = snapshot.device_details.get("ap1")
    if ap1:
        for radio in ap1.interfaces.radios:
            if radio.frequency_ghz and radio.frequency_ghz < 3:
                radio.channel, radio.channel_width_mhz = 11, 20
            else:
                radio.channel = 36  # off DFS
    ap4 = snapshot.device_details.get("ap4")
    if ap4:
        for radio in ap4.interfaces.radios:
            if radio.frequency_ghz and radio.frequency_ghz < 3:
                radio.channel = 1  # no longer sharing channel 6 with ap2
    for client in snapshot.clients:
        if client.access and client.access.type == "GUEST":
            client.access.authorized = True


def _firmware_behind(snapshot: Snapshot) -> None:
    for device in snapshot.devices:
        if device.id in ("sw2", "ap2"):
            device.firmware_updatable = True
    for dev_id in ("sw2", "ap2"):
        detail = snapshot.device_details.get(dev_id)
        if detail:
            detail.firmware_updatable = True
            if dev_id == "ap2":
                detail.firmware_version = "6.6.50"
    ap2 = next((d for d in snapshot.devices if d.id == "ap2"), None)
    if ap2:
        ap2.firmware_version = "6.6.50"  # drift against ap1/ap4 on 6.6.55


def _bad_24_channel(snapshot: Snapshot) -> None:
    ap1 = snapshot.device_details.get("ap1")
    for radio in ap1.interfaces.radios if ap1 else []:
        if radio.frequency_ghz and radio.frequency_ghz < 3:
            radio.channel, radio.channel_width_mhz = 3, 40


def _channel_overlap(snapshot: Snapshot) -> None:
    ap4 = snapshot.device_details.get("ap4")
    for radio in ap4.interfaces.radios if ap4 else []:
        if radio.frequency_ghz and radio.frequency_ghz < 3:
            radio.channel = 6  # same as ap2


def _dfs_channel(snapshot: Snapshot) -> None:
    ap1 = snapshot.device_details.get("ap1")
    for radio in ap1.interfaces.radios if ap1 else []:
        if radio.frequency_ghz and radio.frequency_ghz >= 3:
            radio.channel = 100


def _cabling_faults(snapshot: Snapshot) -> None:
    """Unused by the default story -- see the note on _DEGRADATIONS."""
    ap1 = snapshot.device_details.get("ap1")
    for port in ap1.interfaces.ports if ap1 else []:
        port.speed_mbps = 100  # a dead pair forces 100 Mbps
    sw1 = snapshot.device_details.get("sw1")
    for port in sw1.interfaces.ports if sw1 else []:
        if port.poe is not None:
            port.poe.state = "LIMITED"


def _ap_offline(snapshot: Snapshot) -> None:
    """Unused by the default story -- see the note on _DEGRADATIONS."""
    for device in snapshot.devices:
        if device.id == "ap3":
            device.state = "OFFLINE"
    detail = snapshot.device_details.get("ap3")
    if detail:
        detail.state = "OFFLINE"
    snapshot.device_stats.pop("ap3", None)


# A site decaying on a timeline: each fault appears at its point in the run
# sequence and stays. That is what gives the health score somewhere to fall
# from, and every drop in the chart has a finding behind it.
#
# _cabling_faults and _ap_offline are deliberately not in the list. Their
# severities (high and critical) drive the score to the floor a third of the way
# through, and a chart pinned at zero says less than one that keeps moving. Add
# them back for a worst-case demo.
_DEGRADATIONS: list[tuple[float, str, object]] = [
    (0.30, "firmware falls behind on two devices", _firmware_behind),
    (0.50, "an AP is moved to 2.4 GHz channel 3 at 40 MHz", _bad_24_channel),
    (0.70, "a second AP lands on channel 6", _channel_overlap),
    (0.85, "the 5 GHz radio is auto-moved onto DFS", _dfs_channel),
]


def _shape(snapshot: Snapshot, progress: float, is_last: bool, rng: random.Random) -> None:
    """Move this run's telemetry to where the story says it should be.

    `progress` runs 0 (oldest) to 1 (newest). Each metric moved here is one a
    trend rule reads, and each moves in a different shape so the rules can be
    told apart by what they catch.
    """
    def jitter(spread: float) -> float:
        return rng.uniform(-spread, spread)

    _repair(snapshot)
    for at, _description, degrade in _DEGRADATIONS:
        if progress >= at:
            degrade(snapshot)

    # Gateway CPU: quiet throughout, then one spike in the newest scan only.
    # Nothing but an anomaly score can catch this -- the level never holds.
    gw = snapshot.device_stats.get("gw1")
    if gw is not None:
        gw.cpu_utilization_pct = 93.0 if is_last else round(19 + jitter(2.5), 1)

    # Switch memory: a steady climb toward the 90% ceiling, which is what turns
    # "memory is high" into a date.
    sw = snapshot.device_stats.get("sw1")
    if sw is not None:
        sw.memory_utilization_pct = round(62 + 26 * progress + jitter(0.8), 1)

    # 2.4 GHz retries: one step change partway through, then a new normal. A
    # spot check after the step looks unremarkable; the series does not.
    ap = snapshot.device_stats.get("ap1")
    if ap is not None:
        for radio in ap.interfaces.radios:
            if radio.frequency_ghz and radio.frequency_ghz < 3:
                base = 4.0 if progress < 0.45 else 14.0
                radio.tx_retries_pct = round(base + jitter(0.7), 1)

    # A network filling up: a /26 with clients arriving, so pool pressure has
    # somewhere to go. The fixtures are left alone -- this is per-run state.
    default_net = next((n for n in snapshot.config.networks if n.id == "net1"), None)
    if default_net and default_net.ipv4_configuration:
        default_net.ipv4_configuration.prefix_length = 26  # .1-.62 assignable
        arrivals = 12 + round(38 * progress)
        snapshot.clients.extend(
            ClientOverview(
                id=f"seed-{i}",
                name=f"Workstation {i}",
                type="WIRED",
                macAddress=f"aa:11:00:00:{i // 256:02x}:{i % 256:02x}",
                ipAddress=f"192.168.1.{i + 2}",
                access=ClientAccess(type="DEFAULT", authorized=True),
            )
            for i in range(arrivals)
        )


async def seed(runs: int, days: float) -> int:
    settings = get_settings()
    if not settings.demo_mode:
        print("refusing to seed: DEMO_MODE is not set", file=sys.stderr)
        return 1

    await init_db()
    session_factory = get_session_factory()
    client = DemoUnifiClient()
    now = datetime.now(UTC)
    interval = timedelta(days=days) / max(runs - 1, 1)
    rng = random.Random(SEED)

    for i in range(runs):
        progress = i / max(runs - 1, 1)
        started_at = now - interval * (runs - 1 - i)

        snapshot = await collect_snapshot(client)
        snapshot.collected_at = started_at
        _shape(snapshot, progress, is_last=(i == runs - 1), rng=rng)

        async with session_factory() as session:
            history = await repo.load_history(session)
            dismissals = await repo.list_dismissals(session)
            findings, unsupported = run_rules(snapshot, history)
            for finding in findings:
                finding.dismissed = repo.is_dismissed(
                    dismissals, finding.rule_id, finding.subject_id
                )
            score = health_score(findings)

            run_id = uuid.uuid4().hex[:12]
            await repo.create_run(session, run_id, trigger="seed")
            await repo.save_run_results(
                session, run_id, snapshot, findings, score, None, "skipped",
                unsupported=unsupported,
            )
            # Backdate everything this run wrote. The timestamps are the whole
            # point: every statistic downstream is a function of them.
            run = await session.get(ScanRun, run_id)
            run.started_at = started_at
            run.finished_at = started_at + timedelta(seconds=20)
            await session.commit()

        print(f"  run {i + 1}/{runs}  {started_at:%Y-%m-%d %H:%M}  score {score}")

    async with session_factory() as session:
        series = await repo.load_metric_series(session)
        readings = await session.scalar(select(func.count()).select_from(MetricSnapshot))
    print(f"seeded {runs} runs, {readings} metric readings across {len(series)} series")
    await dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=24, help="how many scans to write")
    parser.add_argument("--days", type=float, default=12.0, help="span they cover")
    args = parser.parse_args()
    return asyncio.run(seed(args.runs, args.days))


if __name__ == "__main__":
    sys.exit(main())
