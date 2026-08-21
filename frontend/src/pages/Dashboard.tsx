import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { RunDetail, Severity } from "../api/types";
import FirmwareOverview from "../components/FirmwareOverview";
import HardwareOverview from "../components/HardwareOverview";
import SeverityBadge from "../components/SeverityBadge";

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

function scoreClass(score: number | null): string {
  if (score === null) return "";
  if (score >= 80) return "good";
  if (score >= 50) return "warn";
  return "bad";
}

/** The score, its movement and the severity mix on one line.
 *
 *  The score is the page's headline number, so it gets the width rather than
 *  sharing a row of four equals. The severity counts ride alongside it because
 *  they explain the score — as a separate strip of five they read as their own
 *  metric, and the empty ones took up as much room as the ones that mattered. */
function HealthBar({ run }: { run: RunDetail }) {
  const delta =
    run.health_score !== null && run.previous_health_score != null
      ? run.health_score - run.previous_health_score
      : null;
  const present = SEVERITIES.filter((sev) => (run.severity_counts[sev] ?? 0) > 0);
  const total = SEVERITIES.reduce((sum, sev) => sum + (run.severity_counts[sev] ?? 0), 0);

  return (
    <div className="healthbar">
      <div className={`score ${scoreClass(run.health_score)}`}>{run.health_score ?? "—"}</div>
      <div className="healthbar-id">
        <div className="label">Health score</div>
        <div className="delta">
          {delta === null
            ? "first scan"
            : delta === 0
              ? "no change since last scan"
              : `${delta > 0 ? "+" : ""}${delta} since last scan`}
        </div>
      </div>
      <div className="sev-strip">
        {total === 0 ? (
          <span className="sev-clear">no open findings</span>
        ) : (
          present.map((sev) => (
            <Link to="/findings" className={`sev-chip ${sev}`} key={sev} title={`${sev} findings`}>
              <span className="n">{run.severity_counts[sev]}</span>
              {sev}
            </Link>
          ))
        )}
      </div>
    </div>
  );
}

function KpiTiles({ run }: { run: RunDetail }) {
  const m = run.site_metrics ?? {};
  const online = m["site.device_online_count"];
  const offline =
    online != null && run.device_count != null ? run.device_count - online : null;
  const latency = m["wan.latency_ms"];
  const loss = m["wan.loss_pct"];
  const dnsBlocked = m["dns.blocked_pct"];

  return (
    <div className="tile-row">
      <div className="tile">
        <div className={`value ${offline ? "warn" : "good"}`}>
          {online ?? run.device_count ?? "—"}
          {run.device_count != null && <span className="unit">/{run.device_count}</span>}
        </div>
        <div>
          <div className="label">Devices online</div>
          <div className="delta">{offline ? `${offline} offline` : "full fleet reporting"}</div>
        </div>
      </div>
      <div className="tile">
        <div className="value">{run.client_count}</div>
        <div>
          <div className="label">Active clients</div>
          <div className="delta">across all bands</div>
        </div>
      </div>
      {latency != null ? (
        <div className="tile">
          <div className={`value ${latency < 80 && (loss ?? 0) < 2 ? "good" : "warn"}`}>
            {Math.round(latency)}
            <span className="unit">ms</span>
          </div>
          <div>
            <div className="label">WAN latency</div>
            <div className="delta">{loss != null ? `${loss.toFixed(1)}% probe loss` : ""}</div>
          </div>
        </div>
      ) : dnsBlocked != null ? (
        <div className="tile">
          <div className="value">
            {dnsBlocked.toFixed(0)}
            <span className="unit">%</span>
          </div>
          <div>
            <div className="label">DNS blocked</div>
            <div className="delta">last 24h</div>
          </div>
        </div>
      ) : (
        <div className="tile">
          <div className={`value ${run.dismissed_count ? "" : "good"}`}>
            {run.dismissed_count}
          </div>
          <div>
            <div className="label">Dismissed</div>
            <div className="delta">excluded from the score</div>
          </div>
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
  // Dismissed findings are deliberately absent here: this card is "what still
  // needs attention". They remain listed on the Findings page.
  const openFindings = run.findings.filter((f) => !f.dismissed);
  const preview = openFindings.slice(0, 8);

  return (
    <div>
      <h1>Dashboard</h1>
      <p className="subtitle">
        Site “{run.site_name}” · UniFi Network scan from{" "}
        {new Date(run.started_at!).toLocaleString()}
      </p>

      <HealthBar run={run} />
      <KpiTiles run={run} />

      <div className="grid-2">
        <div>
          <div className="card">
            <div className="card-head">
              <h2>Open findings</h2>
              <Link to="/findings" className="meta">
                {run.dismissed_count > 0 && `${run.dismissed_count} dismissed · `}
                view all →
              </Link>
            </div>
            {preview.length === 0 ? (
              <p className="muted">
                {run.dismissed_count > 0
                  ? "Nothing outstanding — the remaining findings are all dismissed. 🎉"
                  : "No findings — the network looks healthy. 🎉"}
              </p>
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

          <FirmwareOverview devices={run.devices ?? []} />

          <HardwareOverview devices={run.devices ?? []} findings={run.findings} />
        </div>

        <div>
          {run.advice ? (
            <div className="card">
              <div className="card-head">
                <h2>AI assessment</h2>
                <span className="meta">{run.suggestions.length} suggestions</span>
              </div>
              <p style={{ marginTop: 0, fontSize: 13.5 }}>{run.advice.overall_assessment}</p>
              {run.advice.items.length > run.suggestions.length && (
                <p className="muted" style={{ fontSize: 12.5 }}>
                  {run.advice.items.length - run.suggestions.length} suggestion(s) hidden —
                  written before you dismissed those findings, so the summary above may
                  still mention them.
                </p>
              )}
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
