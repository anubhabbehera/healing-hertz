import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { RuleStatus, RuleSummary, RulesResponse } from "../api/types";
import Predicate from "../components/Predicate";
import SeverityBadge from "../components/SeverityBadge";
import AddRule from "../components/AddRule";

const STATUS_LABEL: Record<RuleStatus, string> = {
  active: "Running",
  disabled: "Disabled",
  not_checkable: "Not checkable",
  unloadable: "Failed to load",
};

function CopyPath({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="secondary mini"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          // Clipboard needs a secure context; behind a plain-HTTP proxy it
          // throws. The path is on screen either way.
          setCopied(false);
        }
      }}
      title={value}
    >
      {copied ? "Copied" : label}
    </button>
  );
}

function RuleDetail({ rule, data }: { rule: RuleSummary; data: RulesResponse }) {
  const source = rule.emits[0]?.source
    ? data.sources.find((s) => s.name === rule.emits[0].source)
    : undefined;

  return (
    <div>
      {rule.reason && <p className="muted">{rule.reason}</p>}

      {rule.impl && (
        <p className="muted">
          {rule.impl.doc || "Implemented in Python."}{" "}
          <code>{rule.impl.ref}</code>
        </p>
      )}

      {source && (
        <p className="muted">
          Reads <code>{source.name}</code> — {source.doc}
        </p>
      )}

      {rule.emits.map((emit) => (
        <div key={emit.index} style={{ marginTop: 12 }}>
          {emit.key && <p className="muted">when: {emit.key}</p>}
          {emit.source !== undefined && (
            <>
              <p className="muted">Fires when</p>
              <Predicate node={emit.where} constants={data.constants} />
            </>
          )}
          <p>
            <SeverityBadge severity={emit.severity.base} />
            {emit.severity.escalate.map((step, i) => (
              <span key={i} className="muted">
                {" "}→ <SeverityBadge severity={step.to} /> when{" "}
                <Predicate node={step.when} constants={data.constants} />
              </span>
            ))}
          </p>
          {/* Templates shown verbatim: the placeholders are the point. */}
          <dl className="kv">
            <dt>Title</dt>
            <dd><code>{emit.title}</code></dd>
            <dt>Summary</dt>
            <dd className="muted">{emit.summary}</dd>
            <dt>Recommendation</dt>
            <dd className="muted">{emit.recommendation}</dd>
            {Object.keys(emit.evidence).length > 0 && (
              <>
                <dt>Evidence</dt>
                <dd><code>{Object.keys(emit.evidence).join(", ")}</code></dd>
              </>
            )}
          </dl>
        </div>
      ))}

      <div className="mini-row">
        {rule.source_file.path && (
          <>
            <code className="muted">
              {data.path_scope === "container" && rule.source_file.repo_path
                ? rule.source_file.repo_path
                : rule.source_file.path}
            </code>
            <CopyPath
              label="Copy path"
              value={
                data.path_scope === "container" && rule.source_file.repo_path
                  ? rule.source_file.repo_path
                  : rule.source_file.path
              }
            />
          </>
        )}
        {rule.source_file.github_url && (
          <a href={rule.source_file.github_url} target="_blank" rel="noreferrer">
            View on GitHub ({data.repo_ref}) ↗
          </a>
        )}
      </div>
      {data.path_scope === "container" && rule.source_file.path && (
        <p className="muted">
          Inside the container this is <code>{rule.source_file.path}</code>.
        </p>
      )}
    </div>
  );
}

export default function Rules() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["rules"], queryFn: api.rules });
  const reload = useMutation({
    mutationFn: api.reloadRules,
    onSuccess: (fresh) => queryClient.setQueryData(["rules"], fresh),
  });

  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<RuleStatus | "all">("all");
  const [category, setCategory] = useState("all");

  const visible = useMemo(() => {
    if (!data) return [];
    const needle = search.trim().toLowerCase();
    return data.rules.filter(
      (r) =>
        (status === "all" || r.status === status) &&
        (category === "all" || r.category === category) &&
        (!needle ||
          r.id.toLowerCase().includes(needle) ||
          (r.title ?? "").toLowerCase().includes(needle) ||
          r.emits.some((e) => e.title.toLowerCase().includes(needle))),
    );
  }, [data, search, status, category]);

  if (!data) return <div className="empty">Loading…</div>;

  const dir = data.rules_dir;

  return (
    <div>
      <h1>Rules</h1>
      <p className="subtitle">
        Every check this build knows about. Rules are files — this page tells you which
        one to edit.
      </p>

      {!dir.configured && (
        <div className="callout">
          Set <code>RULES_DIR</code> to a directory of YAML files to add your own checks.
        </div>
      )}
      {dir.configured && !dir.exists && (
        <div className="callout error">
          <code>RULES_DIR</code> is set to <code>{dir.path}</code>, which does not exist,
          so no custom rules are loaded.
          {data.path_scope === "container" && (
            <>
              {" "}That path is read <strong>inside the container</strong> — mount a
              directory there in <code>docker-compose.yml</code> for it to work.
            </>
          )}
        </div>
      )}

      <div className="select-row">
        <label htmlFor="q">Search</label>
        <input
          id="q"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="rule id or title"
        />
        <label htmlFor="status">Status</label>
        <select id="status" value={status} onChange={(e) => setStatus(e.target.value as RuleStatus | "all")}>
          <option value="all">All ({data.rules.length})</option>
          {(Object.keys(STATUS_LABEL) as RuleStatus[])
            .filter((s) => data.counts[s])
            .map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]} ({data.counts[s]})
              </option>
            ))}
        </select>
        <label htmlFor="cat">Category</label>
        <select id="cat" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="all">All</option>
          {data.categories.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>{visible.length} of {data.rules.length} checks</h2>
          <button
            className="secondary"
            onClick={() => reload.mutate()}
            disabled={reload.isPending}
            title="Re-read the rule files from disk"
          >
            {reload.isPending ? "Reloading…" : "Reload rules"}
          </button>
        </div>
        <p className="muted">
          Catalog loaded {new Date(data.loaded_at).toLocaleString()}. Reload re-reads rule
          files; changing <code>RULES_DIR</code> still needs a restart.
        </p>
        {reload.isError && (
          <div className="callout error">
            The catalog could not be reloaded: {(reload.error as Error).message}
          </div>
        )}

        <table className="data">
          <thead>
            <tr>
              <th>Rule</th>
              <th>Category</th>
              <th>Status</th>
              <th>File</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((rule) => (
              <tr key={rule.id}>
                <td colSpan={4}>
                  <details>
                    <summary>
                      <span className="title">{rule.id}</span>{" "}
                      <span className="subject">
                        {rule.title ?? rule.emits[0]?.title ?? ""}
                      </span>
                      <span className="mini-row">
                        <span className="badge muted-badge">{rule.category ?? "—"}</span>
                        <span className="badge muted-badge">{STATUS_LABEL[rule.status]}</span>
                        <span className="badge muted-badge">{rule.kind}</span>
                        {rule.origin === "user" && <span className="badge muted-badge">custom</span>}
                        <span className="muted">{rule.source_file.name}</span>
                      </span>
                    </summary>
                    <RuleDetail rule={rule} data={data} />
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {visible.length === 0 && <p className="muted">No checks match those filters.</p>}
      </div>

      <AddRule data={data} />
    </div>
  );
}
