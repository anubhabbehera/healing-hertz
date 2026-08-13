import type { CompareResponse, Finding } from "../api/types";
import SeverityBadge from "./SeverityBadge";

function FindingList({ title, findings }: { title: string; findings: Finding[] }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div className="card-head" style={{ marginBottom: 6 }}>
        <h2>
          {title} <span className="muted">({findings.length})</span>
        </h2>
      </div>
      {findings.length === 0 ? (
        <p className="muted" style={{ margin: "4px 0" }}>
          None
        </p>
      ) : (
        findings.map((f) => (
          <div className="finding-row" key={`${f.rule_id}-${f.subject_id}-${f.id}`}>
            <span className={`dot ${f.severity}`} />
            <span className="title">{f.title}</span>
            {f.subject_name && <span className="subject">{f.subject_name}</span>}
            <SeverityBadge severity={f.severity} />
          </div>
        ))
      )}
    </div>
  );
}

export default function RunDiff({ diff }: { diff: CompareResponse }) {
  return (
    <div className="card">
      <div className="card-head">
        <h2>Run comparison</h2>
        <span className="meta">
          {diff.older.started_at?.slice(0, 16).replace("T", " ")} →{" "}
          {diff.newer.started_at?.slice(0, 16).replace("T", " ")} · score{" "}
          {diff.older.health_score} → {diff.newer.health_score}
        </span>
      </div>
      <FindingList title="New" findings={diff.new} />
      <FindingList title="Resolved" findings={diff.resolved} />
      <FindingList title="Persisting" findings={diff.persisting} />
    </div>
  );
}
