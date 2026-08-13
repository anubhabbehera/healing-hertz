import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { Severity } from "../api/types";
import FindingCard from "../components/FindingCard";

const SEVERITIES: (Severity | "all")[] = ["all", "critical", "high", "medium", "low", "info"];

export default function Findings() {
  const [filter, setFilter] = useState<Severity | "all">("all");
  const { data: run, error } = useQuery({
    queryKey: ["latest"],
    queryFn: api.latestRun,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  });

  if (error instanceof ApiError && error.status === 404) {
    return <div className="empty">No scans yet — run one from the dashboard.</div>;
  }
  if (!run) return <div className="empty">Loading…</div>;

  const visible = run.findings.filter((f) => filter === "all" || f.severity === filter);
  const findings = visible.filter((f) => !f.dismissed);
  const dismissed = visible.filter((f) => f.dismissed);

  return (
    <div>
      <h1>Findings</h1>
      <p className="subtitle">
        {run.findings.filter((f) => !f.dismissed).length} open finding(s) from the latest
        scan of “{run.site_name}”
        {run.findings.some((f) => f.dismissed) &&
          `, plus ${run.findings.filter((f) => f.dismissed).length} dismissed`}
        .
      </p>

      <div className="select-row">
        <label htmlFor="sev">Severity</label>
        <select id="sev" value={filter} onChange={(e) => setFilter(e.target.value as Severity | "all")}>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <div className="card">
        {findings.length === 0 ? (
          <p className="muted">No open findings at this severity. 🎉</p>
        ) : (
          findings.map((f) => (
            <FindingCard key={f.id} finding={f} advice={run.suggestions} />
          ))
        )}
      </div>

      {dismissed.length > 0 && (
        <details className="card">
          <summary className="muted" style={{ cursor: "pointer" }}>
            Dismissed findings ({dismissed.length}) — reported, but not counted against
            the health score
          </summary>
          <div style={{ marginTop: 10 }}>
            {dismissed.map((f) => (
              <FindingCard key={f.id} finding={f} advice={run.suggestions} />
            ))}
          </div>
        </details>
      )}

      {run.advice && run.advice.quick_wins.length > 0 && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Quick wins</h2>
          <ul>
            {run.advice.quick_wins.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {run.unsupported_checks && run.unsupported_checks.length > 0 && (
        <details className="card">
          <summary className="muted" style={{ cursor: "pointer" }}>
            Checks not possible via the Integration API ({run.unsupported_checks.length})
          </summary>
          <div style={{ marginTop: 10 }}>
            {run.unsupported_checks.map((u) => (
              <p key={u.rule_id}>
                <strong>{u.title}</strong>
                <br />
                <span className="muted">{u.reason}</span>
              </p>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
