import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { RunDetail, Severity } from "../api/types";
import SeverityBadge from "../components/SeverityBadge";

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

function scoreClass(score: number | null): string {
  if (score === null) return "";
  if (score >= 80) return "good";
  if (score >= 50) return "warn";
  return "bad";
}

function HeroTiles({ run }: { run: RunDetail }) {
  const m = run.site_metrics ?? {};
  const online = m["site.device_online_count"];
  const latency = m["wan.latency_ms"];
  const loss = m["wan.loss_pct"];
  const delta =
    run.health_score !== null && run.previous_health_score != null
      ? run.health_score - run.previous_health_score
      : null;
  const findingCount = Object.values(run.severity_counts).reduce((a, b) => a + (b ?? 0), 0);

  return (
    <div className="tile-row">
      <div className="tile">
        <div className={`value ${scoreClass(run.health_score)}`}>{run.health_score ?? "—"}</div>
        <div>
          <div className="label">Health score</div>
          {delta !== null && (
            <div className="delta">
              {delta === 0 ? "no change" : `${delta > 0 ? "+" : ""}${delta} vs last scan`}
            </div>
          )}
        </div>
      </div>
      <div className="tile">
        <div className={`value ${online != null && online < (run.device_count ?? 0) ? "warn" : "good"}`}>
          {online ?? run.device_count ?? "—"}
        </div>
        <div>
          <div className="label">Devices online</div>
          {online != null && run.device_count != null && online < run.device_count && (
            <div className="delta">{run.device_count - online} offline</div>
          )}
        </div>
      </div>
      <div className="tile">
        <div className="value">{run.client_count}</div>
        <div className="label">Active clients</div>
      </div>
      {latency != null ? (
        <div className="tile">
          <div className={`value ${latency < 80 && (loss ?? 0) < 2 ? "good" : "warn"}`}>
            {Math.round(latency)}
            <span className="unit">ms</span>
          </div>
          <div>
            <div className="label">WAN latency</div>
            <div className="delta">{loss != null ? `${loss.toFixed(1)}% loss` : ""}</div>
          </div>
        </div>
      ) : (
        <div className="tile">
          <div className={`value ${findingCount ? "warn" : "good"}`}>{findingCount}</div>
          <div className="label">Open findings</div>
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const { data: run, error } = useQuery({
    queryKey: ["latest"],
    queryFn: api.latestRun,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
    refetchInterval: 30_000,
  });

  if (error instanceof ApiError && error.status === 404) {
    return (
      <div className="empty">
        <h1 style={{ marginBottom: 8 }}>Welcome to healing-hertz</h1>
        No scans yet — press <strong>Run scan</strong> in the top bar to analyze your
        UniFi network.
      </div>
    );
  }
  if (!run) return <div className="empty">Loading…</div>;

  const m = run.site_metrics ?? {};
  const dnsQueries = m["dns.queries_24h"];
  const dnsBlocked = m["dns.blocked_pct"];
  const preview = run.findings.slice(0, 8);

  return (
    <div>
      <h1>Dashboard</h1>
      <p className="subtitle">
        Site “{run.site_name}” · UniFi Network scan from{" "}
        {new Date(run.started_at!).toLocaleString()}
      </p>

      <HeroTiles run={run} />

      <div className="tile-row" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))" }}>
        {SEVERITIES.map((sev) => (
          <div className="mini" key={sev}>
            <div className="label">{sev}</div>
            <div className="value" style={{ color: run.severity_counts[sev] ? `var(--${sev})` : "var(--ink-muted)" }}>
              {run.severity_counts[sev] ?? 0}
            </div>
          </div>
        ))}
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-head">
            <h2>Findings</h2>
            <Link to="/findings" className="meta">
              view all →
            </Link>
          </div>
          {preview.length === 0 ? (
            <p className="muted">No findings — the network looks healthy. 🎉</p>
          ) : (
            preview.map((f) => (
              <div className="finding-row" key={f.id}>
                <span className={`dot ${f.severity}`} />
                <span className="title">{f.title}</span>
                {f.subject_name && <span className="subject">{f.subject_name}</span>}
                <SeverityBadge severity={f.severity} />
              </div>
            ))
          )}
        </div>

        <div>
          {run.advice ? (
            <div className="card">
              <div className="card-head">
                <h2>AI assessment</h2>
                <span className="meta">{run.suggestions.length} suggestions</span>
              </div>
              <p style={{ marginTop: 0, fontSize: 13.5 }}>{run.advice.overall_assessment}</p>
              {run.suggestions.slice(0, 3).map((s) => (
                <div className="suggestion" key={s.priority}>
                  <div className="head">
                    {s.priority}. {s.title}
                    <span className="effort">{s.effort}</span>
                  </div>
                </div>
              ))}
              <Link to="/findings" style={{ fontSize: 13 }}>
                Full remediation plan →
              </Link>
            </div>
          ) : (
            <div className="callout">
              {run.advice_status === "skipped"
                ? "AI advice is off — set ANTHROPIC_API_KEY to get a remediation plan with each scan."
                : `AI advice failed${run.advice_error ? `: ${run.advice_error}` : ""} — rule findings are still available.`}
            </div>
          )}

          {(m["wan.latency_ms"] != null || dnsQueries != null) && (
            <div className="card">
              <div className="card-head">
                <h2>Network health</h2>
                <span className="meta">last 24h</span>
              </div>
              <div className="mini-row">
                {m["wan.latency_ms"] != null && (
                  <>
                    <div className="mini">
                      <div className="label">WAN latency</div>
                      <div className="value">{Math.round(m["wan.latency_ms"])}<span className="unit" style={{ fontSize: 12 }}> ms</span></div>
                    </div>
                    <div className="mini">
                      <div className="label">Probe loss</div>
                      <div className="value">{(m["wan.loss_pct"] ?? 0).toFixed(1)}%</div>
                    </div>
                  </>
                )}
                {dnsQueries != null && (
                  <>
                    <div className="mini">
                      <div className="label">DNS queries</div>
                      <div className="value">{Intl.NumberFormat().format(dnsQueries)}</div>
                    </div>
                    <div className="mini">
                      <div className="label">DNS blocked</div>
                      <div className="value">{(dnsBlocked ?? 0).toFixed(0)}%</div>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {run.advice && run.advice.quick_wins.length > 0 && (
            <div className="card">
              <div className="card-head">
                <h2>Quick wins</h2>
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: "var(--ink-secondary)" }}>
                {run.advice.quick_wins.slice(0, 3).map((w, i) => (
                  <li key={i} style={{ marginBottom: 6 }}>
                    {w}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
