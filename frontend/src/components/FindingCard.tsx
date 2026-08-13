import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Finding, Suggestion } from "../api/types";
import SeverityBadge from "./SeverityBadge";

export default function FindingCard({
  finding,
  advice,
}: {
  finding: Finding;
  advice?: Suggestion[];
}) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const [siteWide, setSiteWide] = useState(false);

  const dismiss = useMutation({
    mutationFn: () => api.dismiss(finding, reason, siteWide),
    onSuccess: () => {
      setAsking(false);
      setReason("");
      queryClient.invalidateQueries();
    },
  });

  const related = (advice ?? []).filter((s) =>
    s.related_rule_ids.includes(finding.rule_id),
  );

  return (
    <details className={`finding ${finding.severity}${finding.dismissed ? " dismissed" : ""}`}>
      <summary>
        <SeverityBadge severity={finding.severity} />
        <span className="title">{finding.title}</span>
        {finding.dismissed && <span className="badge muted-badge">dismissed</span>}
        {finding.subject_name && <span className="subject">{finding.subject_name}</span>}
      </summary>
      <div className="finding-body">
        <div>{finding.summary}</div>
        {Object.keys(finding.evidence).length > 0 && (
          <table className="evidence-table">
            <tbody>
              {Object.entries(finding.evidence).map(([key, value]) => (
                <tr key={key}>
                  <td>{key}</td>
                  <td>
                    {typeof value === "object" ? JSON.stringify(value) : String(value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="rec">
          <strong>Recommendation:</strong> {finding.recommendation}
        </div>
        {related.map((s) => (
          <div className="suggestion" key={s.title}>
            <div className="head">
              AI advice: {s.title}
              <span className="effort">{s.effort}</span>
            </div>
            <div>{s.rationale}</div>
            <ol>
              {s.steps.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          </div>
        ))}

        {finding.dismissed ? (
          <p className="muted" style={{ marginBottom: 0 }}>
            Dismissed — still listed, but not counted against the health score.
            Restore it from <strong>Settings → Dismissed findings</strong>.
          </p>
        ) : asking ? (
          <div className="dismiss-form">
            <input
              type="text"
              placeholder="Why is this won't-fix? (optional)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              autoFocus
            />
            <label className="checkbox">
              <input
                type="checkbox"
                checked={siteWide}
                onChange={(e) => setSiteWide(e.target.checked)}
              />
              apply to every subject of this rule
            </label>
            <div className="dismiss-actions">
              <button
                className="primary"
                onClick={() => dismiss.mutate()}
                disabled={dismiss.isPending}
              >
                {dismiss.isPending ? "Dismissing…" : "Dismiss"}
              </button>
              <button className="secondary" onClick={() => setAsking(false)}>
                Cancel
              </button>
            </div>
            {dismiss.isError && (
              <div className="progress-detail">Could not dismiss — try again.</div>
            )}
          </div>
        ) : (
          <button className="secondary" onClick={() => setAsking(true)}>
            Dismiss finding
          </button>
        )}
      </div>
    </details>
  );
}
