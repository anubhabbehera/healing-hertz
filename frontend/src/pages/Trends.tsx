import { useQueries } from "@tanstack/react-query";
import { api } from "../api/client";
import type { TrendResponse } from "../api/types";
import TrendChart, { MAX_COLORED_SERIES, type Series } from "../components/TrendChart";

type Metric = {
  id: string;
  label: string;
  /** Per-subject metrics draw one line per device; site metrics draw one. */
  perSubject: boolean;
  unit: string;
  hint: string;
};

const METRICS: Metric[] = [
  { id: "site.health_score", label: "Health score", perSubject: false, unit: "",
    hint: "100 minus the weight of every open finding" },
  { id: "site.client_count", label: "Client count", perSubject: false, unit: "",
    hint: "devices associated at scan time" },
  { id: "site.device_online_count", label: "Devices online", perSubject: false, unit: "",
    hint: "a step down is hardware that stopped reporting" },
  { id: "device.cpu_pct", label: "Device CPU", perSubject: true, unit: "%",
    hint: "sustained load, not the momentary spike a scan can catch" },
  { id: "device.mem_pct", label: "Device memory", perSubject: true, unit: "%",
    hint: "a climb that never falls back is the interesting shape" },
  { id: "radio.tx_retries_pct", label: "Radio TX retries", perSubject: true, unit: "%",
    hint: "per radio; rising retries mean a degrading RF environment" },
  { id: "network.pool_pressure_pct", label: "Address pool use", perSubject: true, unit: "%",
    hint: "clients addressed inside each network, against what its prefix allows" },
];

/** Which lines a widget draws.
 *
 *  Two devices can carry the same name — a pair of identical switches often
 *  does — and two identical legend entries name neither of them, so repeats
 *  are numbered in the order the backend returned them. */
function seriesFor(metric: Metric, data: TrendResponse | undefined): Series[] {
  if (!metric.perSubject) return [{ key: "value", label: metric.label }];
  const subjects = data?.subjects ?? [];
  const seen = new Map<string, number>();
  const repeated = new Set(
    subjects
      .map((s) => s.subject_name ?? s.subject_id ?? "unknown")
      .filter((name, i, all) => all.indexOf(name) !== i),
  );
  return subjects.map((s) => {
    const name = s.subject_name ?? s.subject_id ?? "unknown";
    const n = (seen.get(name) ?? 0) + 1;
    seen.set(name, n);
    return {
      key: s.subject_id ?? "unknown",
      label: repeated.has(name) ? `${name} (${n})` : name,
    };
  });
}

function Widget({ metric, query }: { metric: Metric; query: { data?: TrendResponse; isPending: boolean } }) {
  const data = query.data;
  const points = data?.points ?? [];
  const series = seriesFor(metric, data);
  const runs = new Set(points.map((p) => p.at)).size;
  const overflow = Math.max(0, series.length - MAX_COLORED_SERIES);

  return (
    <div className="card widget">
      <div className="card-head">
        <h2>
          {metric.label}
          {metric.unit && <span className="unit"> {metric.unit}</span>}
        </h2>
        <span className="meta">
          {runs > 0 && `${runs} scan${runs > 1 ? "s" : ""}`}
          {metric.perSubject && series.length > 0 && ` · ${series.length} subject${series.length > 1 ? "s" : ""}`}
        </span>
      </div>
      <p className="widget-hint">{metric.hint}</p>
      {query.isPending ? (
        <div className="widget-empty">Loading…</div>
      ) : points.length === 0 ? (
        <div className="widget-empty">No data yet — run at least one scan.</div>
      ) : (
        <>
          <TrendChart points={points} series={series} unit={metric.unit} />
          {runs === 1 && (
            <p className="muted">One scan so far — the line appears once there are two.</p>
          )}
          {overflow > 0 && (
            <p className="muted">
              {overflow} further subject{overflow > 1 ? "s are" : " is"} drawn as a thin grey
              line: past {MAX_COLORED_SERIES} series the colours would stop being
              distinguishable. Hover a line to name it.
            </p>
          )}
        </>
      )}
    </div>
  );
}

export default function Trends() {
  // One request per metric, in parallel: the endpoint answers per metric, and
  // a widget that resolves early should render early.
  const queries = useQueries({
    queries: METRICS.map((m) => ({
      queryKey: ["trends", m.id],
      queryFn: () => api.trends(m.id, null),
    })),
  });

  return (
    <div>
      <h1>Trends</h1>
      <p className="subtitle">
        Every metric across scan runs — history builds with each scan. Per-device metrics
        draw one line per device, so an outlier shows up without picking it first.
      </p>

      <div className="widget-grid">
        {METRICS.map((metric, i) => (
          <Widget key={metric.id} metric={metric} query={queries[i]} />
        ))}
      </div>
    </div>
  );
}
