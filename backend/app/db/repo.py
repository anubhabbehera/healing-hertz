from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.advisor.schema import AdvicePlan
from app.analytics import subnets
from app.analytics.timeseries import Point, Series
from app.collectors.snapshot import Snapshot
from app.rules import score_from_severities
from app.rules.base import Finding, HistoricalRun, RunHistory, UnsupportedCheck
from app.unifi.models import DeviceDetail, DeviceOverview

from .models import Dismissal, FindingRow, MetricSnapshot, ScanRun, SuggestionRow


def _utc(dt: datetime | None) -> datetime | None:
    """SQLite loses tzinfo; normalize reads back to UTC-aware."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def create_run(session: AsyncSession, run_id: str, trigger: str = "manual") -> ScanRun:
    run = ScanRun(id=run_id, started_at=datetime.now(UTC), status="running", trigger=trigger)
    session.add(run)
    await session.commit()
    return run


STALE_RUN_ERROR = "Interrupted — the server stopped before this scan finished"


async def abandon_stale_runs(
    session: AsyncSession, error: str = STALE_RUN_ERROR, exclude: set[str] | None = None
) -> int:
    """Fail every run still marked running.

    A scan only lives in the process that started it, so any 'running' row left
    behind by a crash or restart can never finish. Called at startup and from
    the manual clear endpoint; returns how many rows were reset.
    """
    result = await session.execute(select(ScanRun).where(ScanRun.status == "running"))
    stale = [r for r in result.scalars() if not exclude or r.id not in exclude]
    for run in stale:
        run.status = "failed"
        run.error = error
        run.finished_at = datetime.now(UTC)
    if stale:
        await session.commit()
    return len(stale)


async def mark_failed(session: AsyncSession, run_id: str, error: str) -> None:
    run = await session.get(ScanRun, run_id)
    if run:
        run.status = "failed"
        run.error = error
        run.finished_at = datetime.now(UTC)
        await session.commit()


def _metric_rows(run_id: str, snapshot: Snapshot, score: int) -> list[MetricSnapshot]:
    rows = [
        MetricSnapshot(run_id=run_id, subject_type="site", subject_id=None,
                       subject_name=snapshot.site.name, metric="site.health_score", value=score),
        MetricSnapshot(run_id=run_id, subject_type="site", subject_id=None,
                       subject_name=snapshot.site.name, metric="site.client_count",
                       value=len(snapshot.clients)),
        MetricSnapshot(run_id=run_id, subject_type="site", subject_id=None,
                       subject_name=snapshot.site.name, metric="site.device_online_count",
                       value=sum(1 for d in snapshot.devices if d.state == "ONLINE")),
    ]
    for dev_id, stats in snapshot.device_stats.items():
        detail = snapshot.device_details.get(dev_id)
        name = detail.name if detail else dev_id
        for metric, value in [
            ("device.cpu_pct", stats.cpu_utilization_pct),
            ("device.mem_pct", stats.memory_utilization_pct),
            ("device.uptime_sec", stats.uptime_sec),
        ]:
            if value is not None:
                rows.append(MetricSnapshot(run_id=run_id, subject_type="device",
                                           subject_id=dev_id, subject_name=name,
                                           metric=metric, value=float(value)))
        for radio in stats.interfaces.radios:
            if radio.tx_retries_pct is not None:
                rows.append(MetricSnapshot(
                    run_id=run_id, subject_type="radio",
                    subject_id=f"{dev_id}:{radio.frequency_ghz}",
                    subject_name=f"{name} {radio.frequency_ghz} GHz",
                    metric="radio.tx_retries_pct", value=radio.tx_retries_pct))
    for dev_id, detail in snapshot.device_details.items():
        for port in detail.interfaces.ports:
            if port.state == "UP" and port.speed_mbps is not None:
                rows.append(MetricSnapshot(
                    run_id=run_id, subject_type="port",
                    subject_id=f"{dev_id}:{port.idx}",
                    subject_name=f"{detail.name} port {port.idx}",
                    metric="port.speed_mbps", value=float(port.speed_mbps)))
    # Address-pool occupancy is stored as a metric so it can be trended: a pool
    # at 60% is fine, a pool that has climbed 3 points a week is a date.
    if snapshot.config is not None:
        client_ips = [c.ip_address for c in snapshot.clients]
        for net in snapshot.config.networks:
            ipv4 = net.ipv4_configuration
            cidr = subnets.network_of(
                ipv4.host_ip_address if ipv4 else None,
                ipv4.prefix_length if ipv4 else None,
            )
            pressure = subnets.pool_pressure(
                subnets.hosts_in(cidr, client_ips), subnets.usable_hosts(cidr)
            )
            if pressure is not None:
                rows.append(MetricSnapshot(
                    run_id=run_id, subject_type="network", subject_id=net.id,
                    subject_name=net.name, metric="network.pool_pressure_pct",
                    value=round(pressure * 100, 2)))
    if snapshot.wan is not None:
        rows.append(MetricSnapshot(run_id=run_id, subject_type="site", subject_id=None,
                                   subject_name="WAN", metric="wan.latency_ms",
                                   value=snapshot.wan.latency_ms))
        rows.append(MetricSnapshot(run_id=run_id, subject_type="site", subject_id=None,
                                   subject_name="WAN", metric="wan.loss_pct",
                                   value=snapshot.wan.loss_pct))
    if snapshot.dns is not None:
        rows.append(MetricSnapshot(run_id=run_id, subject_type="site", subject_id=None,
                                   subject_name="DNS", metric="dns.blocked_pct",
                                   value=round(snapshot.dns.blocked_pct, 2)))
        rows.append(MetricSnapshot(run_id=run_id, subject_type="site", subject_id=None,
                                   subject_name="DNS", metric="dns.queries_24h",
                                   value=float(snapshot.dns.queries)))
    return rows


def _device_kind(overview: DeviceOverview, detail: DeviceDetail | None) -> str:
    """Coarse hardware class, from whichever feature shape the API returned."""
    if detail is not None and detail.features is not None:
        if detail.features.gateway is not None:
            return "gateway"
        if detail.features.access_point is not None:
            return "access_point"
        if detail.features.switching is not None:
            return "switch"
    features = set(overview.features)
    if "gateway" in features:
        return "gateway"
    if "accessPoint" in features:
        return "access_point"
    if "switching" in features:
        return "switch"
    return "other"


def _device_rows(snapshot: Snapshot) -> list[dict]:
    """Flatten the snapshot into one hardware record per adopted device."""
    rows = []
    for dev in snapshot.devices:
        detail = snapshot.device_details.get(dev.id)
        stats = snapshot.device_stats.get(dev.id)
        ports = detail.interfaces.ports if detail else []
        radios = detail.interfaces.radios if detail else []
        # Stats carry per-radio retries keyed only by band, so join on frequency.
        retries = {
            r.frequency_ghz: r.tx_retries_pct
            for r in (stats.interfaces.radios if stats else [])
            if r.frequency_ghz is not None
        }
        rows.append({
            "id": dev.id,
            "name": dev.name,
            "model": dev.model,
            "mac": dev.mac_address,
            "ip": dev.ip_address,
            "kind": _device_kind(dev, detail),
            "state": dev.state,
            "supported": dev.supported,
            "firmware_version": dev.firmware_version,
            "firmware_updatable": dev.firmware_updatable,
            "cpu_pct": stats.cpu_utilization_pct if stats else None,
            "mem_pct": stats.memory_utilization_pct if stats else None,
            "load_5m": stats.load_average_5_min if stats else None,
            "load_15m": stats.load_average_15_min if stats else None,
            "uptime_sec": stats.uptime_sec if stats else None,
            "last_heartbeat_at": (
                _utc(stats.last_heartbeat_at).isoformat()
                if stats and stats.last_heartbeat_at
                else None
            ),
            "ports_total": len(ports),
            "ports_up": sum(1 for p in ports if p.state == "UP"),
            "poe_ports_up": sum(
                1 for p in ports if p.poe is not None and p.poe.state == "UP"
            ),
            "uplink_tx_bps": stats.uplink.tx_rate_bps if stats and stats.uplink else None,
            "uplink_rx_bps": stats.uplink.rx_rate_bps if stats and stats.uplink else None,
            "radios": [
                {
                    "frequency_ghz": r.frequency_ghz,
                    "channel": r.channel,
                    "channel_width_mhz": r.channel_width_mhz,
                    "wlan_standard": r.wlan_standard,
                    "tx_retries_pct": retries.get(r.frequency_ghz),
                }
                for r in radios
            ],
        })
    return rows


async def save_run_results(
    session: AsyncSession,
    run_id: str,
    snapshot: Snapshot,
    findings: list[Finding],
    score: int,
    advice: AdvicePlan | None,
    advice_status: str,
    unsupported: list[UnsupportedCheck] | None = None,
    advice_error: str | None = None,
) -> None:
    run = await session.get(ScanRun, run_id)
    assert run is not None
    run.unsupported_json = json.dumps(
        [{"rule_id": u.rule_id, "title": u.title, "reason": u.reason}
         for u in (unsupported or [])]
    )
    run.devices_json = json.dumps(_device_rows(snapshot))
    run.status = "completed"
    run.finished_at = datetime.now(UTC)
    run.site_id = snapshot.site.id
    run.site_name = snapshot.site.name
    run.application_version = snapshot.application_version
    run.health_score = score
    run.device_count = len(snapshot.devices)
    run.client_count = len(snapshot.clients)
    run.advice_status = advice_status
    run.advice_json = advice.model_dump_json() if advice else None
    run.advice_error = advice_error

    for f in findings:
        session.add(FindingRow(
            run_id=run_id, rule_id=f.rule_id, severity=f.severity.value,
            category=f.category.value, title=f.title, summary=f.summary,
            evidence_json=json.dumps(f.evidence), recommendation=f.recommendation,
            subject_type=f.subject_type, subject_id=f.subject_id, subject_name=f.subject_name,
            dismissed=f.dismissed,
        ))
    if advice:
        for item in advice.items:
            session.add(SuggestionRow(
                run_id=run_id, priority=item.priority, title=item.title,
                rationale=item.rationale, steps_json=json.dumps(item.steps),
                effort=item.effort, related_rule_ids_json=json.dumps(item.related_rule_ids),
            ))
    session.add_all(_metric_rows(run_id, snapshot, score))
    await session.commit()


async def get_run(session: AsyncSession, run_id: str) -> ScanRun | None:
    return await session.get(ScanRun, run_id)


async def list_runs(session: AsyncSession, limit: int = 50, offset: int = 0) -> list[ScanRun]:
    result = await session.execute(
        select(ScanRun).order_by(ScanRun.started_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars())


async def latest_completed_run(session: AsyncSession) -> ScanRun | None:
    result = await session.execute(
        select(ScanRun).where(ScanRun.status == "completed")
        .order_by(ScanRun.started_at.desc()).limit(1)
    )
    return result.scalars().first()


async def previous_completed_run(session: AsyncSession, before: datetime) -> ScanRun | None:
    result = await session.execute(
        select(ScanRun).where(ScanRun.status == "completed", ScanRun.started_at < before)
        .order_by(ScanRun.started_at.desc()).limit(1)
    )
    return result.scalars().first()


async def get_trends(
    session: AsyncSession, metric: str, subject_id: str | None = None
) -> list[dict]:
    query = (
        select(MetricSnapshot, ScanRun.started_at)
        .join(ScanRun, MetricSnapshot.run_id == ScanRun.id)
        .where(MetricSnapshot.metric == metric, ScanRun.status == "completed")
        .order_by(ScanRun.started_at.asc())
    )
    if subject_id is not None:
        query = query.where(MetricSnapshot.subject_id == subject_id)
    result = await session.execute(query)
    return [
        {
            "run_id": snap.run_id,
            "at": _utc(started_at).isoformat(),
            "subject_id": snap.subject_id,
            "subject_name": snap.subject_name,
            "value": snap.value,
        }
        for snap, started_at in result.all()
    ]


async def list_metric_subjects(session: AsyncSession, metric: str) -> list[dict]:
    result = await session.execute(
        select(MetricSnapshot.subject_id, MetricSnapshot.subject_name)
        .where(MetricSnapshot.metric == metric).distinct()
    )
    return [{"subject_id": sid, "subject_name": name} for sid, name in result.all()]


async def compare_runs(session: AsyncSession, run_a: str, run_b: str) -> dict:
    """Diff findings between two runs, keyed by (rule_id, subject_id)."""
    a = await session.get(ScanRun, run_a)
    b = await session.get(ScanRun, run_b)
    if a is None or b is None:
        raise KeyError("run not found")
    # Ensure a = older, b = newer
    if a.started_at > b.started_at:
        a, b = b, a

    def keyed(run: ScanRun) -> dict[tuple[str, str | None], FindingRow]:
        return {(f.rule_id, f.subject_id): f for f in run.findings}

    old, new = keyed(a), keyed(b)
    return {
        "older": _run_summary(a),
        "newer": _run_summary(b),
        "new": [_finding_dict(new[k]) for k in new.keys() - old.keys()],
        "resolved": [_finding_dict(old[k]) for k in old.keys() - new.keys()],
        "persisting": [_finding_dict(new[k]) for k in new.keys() & old.keys()],
    }


# How far back the trend read-model reaches. Series analysis wants many more
# points than the run-to-run comparisons do -- a median and a slope over five
# readings say very little -- but it still has to stay a bounded read.
SERIES_RUNS = 200


async def load_metric_series(session: AsyncSession, runs: int = SERIES_RUNS) -> list[Series]:
    """Every stored metric over the last `runs` completed scans, oldest first.

    One series per (metric, subject), which is the shape the trend statistics
    take: they compare a subject against its own past, never against the site
    average.
    """
    recent = (
        select(ScanRun.id, ScanRun.started_at)
        .where(ScanRun.status == "completed")
        .order_by(ScanRun.started_at.desc())
        .limit(runs)
        .subquery()
    )
    result = await session.execute(
        select(MetricSnapshot, recent.c.started_at)
        .join(recent, MetricSnapshot.run_id == recent.c.id)
        .order_by(recent.c.started_at.asc())
    )

    grouped: dict[tuple[str, str | None], Series] = {}
    for snap, started_at in result.all():
        key = (snap.metric, snap.subject_id)
        series = grouped.get(key)
        if series is None:
            series = Series(
                metric=snap.metric,
                subject_id=snap.subject_id,
                subject_name=snap.subject_name,
                points=[],
            )
            grouped[key] = series
        series.points.append(Point(at=_utc(started_at), value=snap.value))
    return list(grouped.values())


async def load_history(session: AsyncSession, n: int = 5) -> RunHistory:
    """Read-model of the last n completed runs for cross-run rules (newest first)."""
    runs = await session.execute(
        select(ScanRun).where(ScanRun.status == "completed")
        .order_by(ScanRun.started_at.desc()).limit(n)
    )
    history = []
    for run in runs.scalars():
        uptimes: dict[str, float] = {}
        retries: dict[str, float] = {}
        site_metrics: dict[str, float] = {}
        for m in run.metrics:
            if m.metric == "device.uptime_sec" and m.subject_id:
                uptimes[m.subject_id] = m.value
            elif m.metric == "radio.tx_retries_pct" and m.subject_id:
                retries[m.subject_id] = m.value
            elif m.subject_type == "site":
                site_metrics[m.metric] = m.value
        history.append(HistoricalRun(
            run_id=run.id,
            started_at=_utc(run.started_at),
            device_uptimes=uptimes,
            radio_retries=retries,
            site_metrics=site_metrics,
            finding_keys={(f.rule_id, f.subject_id) for f in run.findings},
        ))
    return RunHistory(runs=history, series=await load_metric_series(session))


def _run_summary(run: ScanRun) -> dict:
    # Counts cover open findings only. Dismissed ones don't affect the health
    # score, so counting them here would contradict the score right next to it.
    severities: dict[str, int] = {}
    dismissed = 0
    for f in run.findings:
        if f.dismissed:
            dismissed += 1
            continue
        severities[f.severity] = severities.get(f.severity, 0) + 1
    return {
        "dismissed_count": dismissed,
        "id": run.id,
        "started_at": _utc(run.started_at).isoformat() if run.started_at else None,
        "finished_at": _utc(run.finished_at).isoformat() if run.finished_at else None,
        "status": run.status,
        "site_name": run.site_name,
        "health_score": run.health_score,
        "device_count": run.device_count,
        "client_count": run.client_count,
        "advice_status": run.advice_status,
        "advice_error": run.advice_error,
        "severity_counts": severities,
        "error": run.error,
    }


def _finding_dict(f: FindingRow) -> dict:
    return {
        "id": f.id,
        "dismissed": bool(f.dismissed),
        "rule_id": f.rule_id,
        "severity": f.severity,
        "category": f.category,
        "title": f.title,
        "summary": f.summary,
        "evidence": json.loads(f.evidence_json),
        "recommendation": f.recommendation,
        "subject_type": f.subject_type,
        "subject_id": f.subject_id,
        "subject_name": f.subject_name,
    }


def run_detail_dict(run: ScanRun) -> dict:
    from app.rules.unsupported import unsupported_checks

    detail = _run_summary(run)
    detail["site_metrics"] = {
        m.metric: m.value for m in run.metrics if m.subject_type == "site"
    }
    detail["findings"] = [_finding_dict(f) for f in run.findings]
    # Empty for runs recorded before the inventory was persisted; the dashboard
    # hides the hardware card rather than showing a half-empty table.
    detail["devices"] = json.loads(run.devices_json) if run.devices_json else []

    # A scan's advice is written before the operator may have dismissed
    # anything. Drop suggestions whose every referenced rule is now fully
    # dismissed, so the plan doesn't keep recommending work that was waived.
    # Suggestions with no rule reference are general advice and always kept.
    dismissed_rules = {f.rule_id for f in run.findings if f.dismissed}
    open_rules = {f.rule_id for f in run.findings if not f.dismissed}
    waived = dismissed_rules - open_rules

    suggestions = []
    for s in sorted(run.suggestions, key=lambda s: s.priority):
        related = json.loads(s.related_rule_ids_json)
        if related and set(related) <= waived:
            continue
        suggestions.append({
            "priority": s.priority,
            "title": s.title,
            "rationale": s.rationale,
            "steps": json.loads(s.steps_json),
            "effort": s.effort,
            "related_rule_ids": related,
        })
    detail["suggestions"] = suggestions
    detail["advice"] = json.loads(run.advice_json) if run.advice_json else None
    if run.unsupported_json is not None:
        detail["unsupported_checks"] = json.loads(run.unsupported_json)
    else:  # runs recorded before per-run persistence existed
        detail["unsupported_checks"] = [
            {"rule_id": u.rule_id, "title": u.title, "reason": u.reason}
            for u in unsupported_checks(None)
        ]
    return detail


# --- dismissals -------------------------------------------------------------


def is_dismissed(dismissals: list[Dismissal], rule_id: str, subject_id: str | None) -> bool:
    """A dismissal matches its exact subject, or every subject when subject_id is NULL."""
    return any(
        d.rule_id == rule_id and (d.subject_id is None or d.subject_id == subject_id)
        for d in dismissals
    )


async def list_dismissals(session: AsyncSession) -> list[Dismissal]:
    result = await session.execute(select(Dismissal).order_by(Dismissal.created_at.desc()))
    return list(result.scalars())


async def apply_dismissals(session: AsyncSession) -> None:
    """Re-apply every dismissal across all stored runs and re-score them.

    Historical runs are rescored too, so the trend line reflects the operator's
    current judgement rather than a mix of old and new policy.
    """
    dismissals = await list_dismissals(session)
    runs = (await session.execute(select(ScanRun))).scalars().all()
    for run in runs:
        for finding in run.findings:
            finding.dismissed = is_dismissed(dismissals, finding.rule_id, finding.subject_id)
        if run.status != "completed":
            continue
        score = score_from_severities(f.severity for f in run.findings if not f.dismissed)
        run.health_score = score
        for metric in run.metrics:
            if metric.metric == "site.health_score":
                metric.value = score
    await session.commit()


async def add_dismissal(
    session: AsyncSession,
    rule_id: str,
    subject_id: str | None,
    subject_name: str | None = None,
    title: str | None = None,
    reason: str | None = None,
) -> Dismissal:
    existing = (
        await session.execute(
            select(Dismissal).where(
                Dismissal.rule_id == rule_id, Dismissal.subject_id == subject_id
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing
    dismissal = Dismissal(
        rule_id=rule_id,
        subject_id=subject_id,
        subject_name=subject_name,
        title=title,
        reason=reason,
        created_at=datetime.now(UTC),
    )
    session.add(dismissal)
    await session.commit()
    await apply_dismissals(session)
    return dismissal


async def remove_dismissal(session: AsyncSession, dismissal_id: int) -> bool:
    dismissal = await session.get(Dismissal, dismissal_id)
    if dismissal is None:
        return False
    await session.delete(dismissal)
    await session.commit()
    await apply_dismissals(session)
    return True


def dismissal_dict(d: Dismissal) -> dict:
    return {
        "id": d.id,
        "rule_id": d.rule_id,
        "subject_id": d.subject_id,
        "subject_name": d.subject_name,
        "title": d.title,
        "reason": d.reason,
        "created_at": _utc(d.created_at).isoformat() if d.created_at else None,
    }
