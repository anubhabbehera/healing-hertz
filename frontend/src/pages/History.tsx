import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import RunDiff from "../components/RunDiff";

export default function History() {
  const queryClient = useQueryClient();
  const { data: runs } = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const [selected, setSelected] = useState<string[]>([]);

  // A scan lives only in the server process that started it, so a row still
  // marked running after a crash or restart will never move on its own.
  const clearStale = useMutation({
    mutationFn: api.clearStaleScans,
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const { data: diff } = useQuery({
    queryKey: ["compare", ...selected],
    queryFn: () => api.compare(selected[0], selected[1]),
    enabled: selected.length === 2,
  });

  const toggle = (id: string) => {
    setSelected((current) =>
      current.includes(id)
        ? current.filter((x) => x !== id)
        : [...current.slice(-1), id],
    );
  };

  if (!runs) return <div className="empty">Loading…</div>;
  if (runs.length === 0)
    return <div className="empty">No scans yet — run one from the dashboard.</div>;

  return (
    <div>
      <h1>Run history</h1>
      <p className="subtitle">Select two runs to compare findings.</p>

      {runs.some((run) => run.status === "running") && (
        <div className="callout">
          Some runs are still marked <strong>running</strong>. If no scan is in
          progress, they are leftovers from a restart and can be cleared.
          <button
            className="secondary"
            onClick={() => clearStale.mutate()}
            disabled={clearStale.isPending}
          >
            {clearStale.isPending ? "Clearing…" : "Clear stuck runs"}
          </button>
        </div>
      )}

      <div className="card">
        <table className="data">
          <thead>
            <tr>
              <th></th>
              <th>Started</th>
              <th>Status</th>
              <th>Score</th>
              <th>Devices</th>
              <th>Clients</th>
              <th>Findings</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const findingCount = Object.values(run.severity_counts).reduce(
                (a, b) => a + (b ?? 0),
                0,
              );
              return (
                <tr key={run.id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.includes(run.id)}
                      onChange={() => toggle(run.id)}
                      disabled={run.status !== "completed"}
                      aria-label={`select run ${run.id}`}
                    />
                  </td>
                  <td>{run.started_at ? new Date(run.started_at).toLocaleString() : "—"}</td>
                  <td>
                    {run.status}
                    {run.error ? ` — ${run.error}` : ""}
                  </td>
                  <td>{run.health_score ?? "—"}</td>
                  <td>{run.device_count ?? "—"}</td>
                  <td>{run.client_count ?? "—"}</td>
                  <td>{findingCount}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {selected.length === 2 && diff && <RunDiff diff={diff} />}
    </div>
  );
}
