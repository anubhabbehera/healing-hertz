import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import TrendChart from "../components/TrendChart";

const METRICS = [
  { id: "site.health_score", label: "Health score", perSubject: false, unit: "" },
  { id: "site.client_count", label: "Client count", perSubject: false, unit: "" },
  { id: "site.device_online_count", label: "Devices online", perSubject: false, unit: "" },
  { id: "device.cpu_pct", label: "Device CPU %", perSubject: true, unit: "%" },
  { id: "device.mem_pct", label: "Device memory %", perSubject: true, unit: "%" },
  { id: "radio.tx_retries_pct", label: "Radio TX retries %", perSubject: true, unit: "%" },
];

export default function Trends() {
  const [metricId, setMetricId] = useState(METRICS[0].id);
  const [subjectId, setSubjectId] = useState<string | null>(null);
  const metric = METRICS.find((m) => m.id === metricId)!;

  const { data } = useQuery({
    queryKey: ["trends", metricId, subjectId],
    queryFn: () => api.trends(metricId, metric.perSubject ? subjectId : null),
  });

  // Pick the first subject automatically for per-device metrics.
  useEffect(() => {
    setSubjectId(null);
  }, [metricId]);
  useEffect(() => {
    if (metric.perSubject && !subjectId && data?.subjects.length) {
      setSubjectId(data.subjects[0].subject_id);
    }
  }, [data, metric.perSubject, subjectId]);

  const points = metric.perSubject
    ? (data?.points ?? []).filter((p) => p.subject_id === subjectId)
    : (data?.points ?? []);

  return (
    <div>
      <h1>Trends</h1>
      <p className="subtitle">Metrics across scan runs — history builds with each scan.</p>

      <div className="select-row">
        <label htmlFor="metric">Metric</label>
        <select id="metric" value={metricId} onChange={(e) => setMetricId(e.target.value)}>
          {METRICS.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
        {metric.perSubject && data && (
          <>
            <label htmlFor="subject">Subject</label>
            <select
              id="subject"
              value={subjectId ?? ""}
              onChange={(e) => setSubjectId(e.target.value)}
            >
              {data.subjects.map((s) => (
                <option key={s.subject_id ?? ""} value={s.subject_id ?? ""}>
                  {s.subject_name ?? s.subject_id}
                </option>
              ))}
            </select>
          </>
        )}
      </div>

      <div className="card">
        {points.length === 0 ? (
          <div className="empty">
            {points.length === 0 && (data?.points.length ?? 0) === 0
              ? "No data yet — run at least one scan."
              : "No data for this subject."}
          </div>
        ) : (
          <TrendChart points={points} unit={metric.unit} />
        )}
        {points.length === 1 && (
          <p className="muted">One data point so far — run more scans to see a trend line.</p>
        )}
      </div>
    </div>
  );
}
