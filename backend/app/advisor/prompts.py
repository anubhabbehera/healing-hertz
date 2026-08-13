"""Advisor payload builder.

Produces a compact, sanitized plain-text digest instead of raw JSON:
- MAC addresses, IP addresses, and serials never leave the machine
- client hostnames are pseudonymized (client-1, client-2, ...); SSIDs dropped
- infrastructure device names (APs/switches/gateway) are kept — actionable
  advice has to name them
- terse line-based format keeps the prompt small, so calls are fast and cheap
"""

from __future__ import annotations

import re

from app.collectors.snapshot import Snapshot
from app.rules.base import Finding, RunHistory, Severity

# Rough character budget for the user payload (~12k tokens at ~4 chars/token).
PAYLOAD_CHAR_BUDGET = 48_000

MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}\b")
IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

# Evidence keys that never leave the machine.
_DROP_KEYS = {"mac", "macaddress", "ip", "ipaddress", "serial", "ssid", "essid"}
# Evidence keys holding client identity — pseudonymized, not dropped.
_CLIENT_NAME_KEYS = {"name", "hostname"}

SYSTEM_PROMPT = """\
You are a senior UniFi network engineer reviewing an automated diagnostic scan of a \
UniFi Network deployment. You receive a terse plain-text digest: rule-engine findings \
(one per line: SEVERITY rule_id subject | evidence as key=value) followed by telemetry \
sections (RADIOS, TOP CPU, WAN, DNS, CLIENT RF when available).

Client devices are pseudonymized as client-1, client-2, ... and network addresses are \
redacted for privacy — refer to clients by those labels; the operator can map them \
locally. Infrastructure names (APs, switches, gateway) are real.

Produce a prioritized remediation plan. Ground every item in the supplied evidence — \
never invent devices, metrics, or issues not in the data. Look for root causes that \
connect multiple findings (e.g. channel overlap driving high retries, or a bad cable \
causing both a slow uplink and client complaints) and prioritize fixes that resolve \
several findings at once. Steps must be concrete and actionable in the UniFi Network \
UI (name the settings pages) or standard tooling. Keep rationale specific to this \
network. If the data shows a healthy network, say so plainly and keep the plan short."""


class ClientAnonymizer:
    """Stable pseudonyms for client device names within one payload."""

    def __init__(self) -> None:
        self._labels: dict[str, str] = {}

    def label(self, name: str) -> str:
        if name not in self._labels:
            self._labels[name] = f"client-{len(self._labels) + 1}"
        return self._labels[name]


def _redact_str(value: str) -> str:
    value = MAC_RE.sub("[mac]", value)
    return IPV4_RE.sub("[ip]", value)


def _sanitize(value, anon: ClientAnonymizer, key: str | None = None):
    if isinstance(value, dict):
        if key == "roamslast24h":  # {client name: count}; keys arrive lowercased
            return {anon.label(str(k)): v for k, v in value.items()}
        return {
            k: _sanitize(v, anon, k.lower())
            for k, v in value.items()
            if k.lower() not in _DROP_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(v, anon, key) for v in value]
    if isinstance(value, str):
        if key in _CLIENT_NAME_KEYS:
            return anon.label(value)
        return _redact_str(value)
    return value


def _fmt(value) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}={_fmt(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ";".join(_fmt(v) for v in value) + "]"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _finding_line(f: Finding, anon: ClientAnonymizer, with_evidence: bool) -> str:
    sev = f.severity.value.upper()[:4]
    subject = f.subject_name or "site"
    if f.subject_type == "client":
        subject = anon.label(subject)
    line = f"{sev} {f.rule_id} {subject}"
    if with_evidence and f.evidence:
        cleaned = _sanitize(f.evidence, anon)
        pairs = ",".join(f"{k}={_fmt(v)}" for k, v in cleaned.items())
        line += f" | {pairs}"
    return line


def _telemetry_lines(snapshot: Snapshot, history: RunHistory,
                     anon: ClientAnonymizer) -> list[str]:
    lines: list[str] = []
    online = sum(1 for d in snapshot.devices if d.state == "ONLINE")
    wireless = sum(1 for c in snapshot.clients if c.type.upper() == "WIRELESS")
    fw = sum(1 for d in snapshot.devices if d.firmware_updatable)
    lines.append(
        f"site={snapshot.site.name or 'default'} | unifi={snapshot.application_version} | "
        f"devices_online={online}/{len(snapshot.devices)} | "
        f"clients={len(snapshot.clients)} (wireless={wireless}) | "
        f"fw_updates_pending={fw} | prior_scans={len(history.runs)}"
    )

    radio_lines = []
    for dev_id, detail in snapshot.device_details.items():
        if not detail.is_access_point:
            continue
        stats = snapshot.device_stats.get(dev_id)
        for radio in detail.interfaces.radios:
            if not radio.channel:  # channel 0/None = radio disabled
                continue
            retries = None
            if stats:
                retries = next(
                    (r.tx_retries_pct for r in stats.interfaces.radios
                     if r.frequency_ghz == radio.frequency_ghz),
                    None,
                )
            retries_str = f" retries={retries:.1f}%" if retries is not None else ""
            radio_lines.append(
                f"  {detail.name} {radio.frequency_ghz}GHz ch{radio.channel} "
                f"{radio.channel_width_mhz}MHz{retries_str}"
            )
    if radio_lines:
        lines.append("RADIOS (ap band channel width retries)")
        lines.extend(radio_lines)

    cpu = sorted(
        ((snapshot.device_details[i].name if i in snapshot.device_details else i, s)
         for i, s in snapshot.device_stats.items()),
        key=lambda x: x[1].cpu_utilization_pct or 0,
        reverse=True,
    )[:5]
    if cpu:
        lines.append("TOP CPU: " + "; ".join(
            f"{name} cpu={s.cpu_utilization_pct:.0f}% mem={s.memory_utilization_pct:.0f}%"
            for name, s in cpu
            if s.cpu_utilization_pct is not None and s.memory_utilization_pct is not None
        ))

    if snapshot.wan is not None:
        lines.append(
            f"WAN PROBE: latency={snapshot.wan.latency_ms:.0f}ms "
            f"jitter={snapshot.wan.jitter_ms:.0f}ms loss={snapshot.wan.loss_pct:.1f}%"
        )
    if snapshot.dns is not None:
        top = ",".join(
            f"{d['domain']}({d['queries']})" for d in snapshot.dns.top_blocked[:5]
        )
        lines.append(
            f"DNS 24h: queries={snapshot.dns.queries} "
            f"blocked={snapshot.dns.blocked_pct:.0f}% "
            f"security_blocks={snapshot.dns.security_block_count}"
            + (f" top_blocked={top}" if top else "")
        )
    if snapshot.rf is not None:
        signals = [c.signal_dbm for c in snapshot.rf.clients if c.signal_dbm is not None]
        weak = sum(1 for s in signals if s <= -75)
        worst = f" worst={min(signals)}dBm" if signals else ""
        lines.append(
            f"CLIENT RF: wireless_seen={len(snapshot.rf.clients)} "
            f"below_-75dBm={weak}{worst} roams_24h={sum(snapshot.rf.roam_counts.values())}"
        )
    return lines


def build_payload(findings: list[Finding], snapshot: Snapshot, history: RunHistory) -> str:
    """Compact sanitized text within PAYLOAD_CHAR_BUDGET.

    Truncation priority: keep CRITICAL/HIGH with full evidence, then trim
    MEDIUM evidence, then collapse LOW/INFO to counts.
    """
    anon = ClientAnonymizer()
    telemetry = _telemetry_lines(snapshot, history, anon)

    def render(medium_evidence: bool, low_as_counts: bool) -> str:
        finding_lines: list[str] = []
        low_counts: dict[str, int] = {}
        for f in findings:
            if f.severity in (Severity.CRITICAL, Severity.HIGH):
                finding_lines.append(_finding_line(f, anon, with_evidence=True))
            elif f.severity == Severity.MEDIUM:
                finding_lines.append(_finding_line(f, anon, with_evidence=medium_evidence))
            elif low_as_counts:
                low_counts[f.rule_id] = low_counts.get(f.rule_id, 0) + 1
            else:
                finding_lines.append(_finding_line(f, anon, with_evidence=False))
        sections = ["UNIFI DIAGNOSTIC SCAN", telemetry[0], "", "FINDINGS", *finding_lines]
        if low_counts:
            sections.append(
                "low/info counts: "
                + ",".join(f"{rule}={n}" for rule, n in low_counts.items())
            )
        sections.extend(["", *telemetry[1:]])
        return "\n".join(sections)

    for medium_evidence, low_as_counts in ((True, False), (True, True), (False, True)):
        payload = render(medium_evidence, low_as_counts)
        if len(payload) <= PAYLOAD_CHAR_BUDGET:
            return payload
    return payload[:PAYLOAD_CHAR_BUDGET]
