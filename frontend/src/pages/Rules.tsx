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

/** A rule's state is a dot, not a word: running is the norm and needs no label. */
const STATUS_TONE: Record<RuleStatus, string> = {
  active: "good",
  disabled: "off",
  not_checkable: "medium",
  unloadable: "critical",
};

/** Categories are open-ended, so tints cycle by catalog order rather than being
    enumerated — the same category keeps the same color on every row. */
const CATEGORY_TINTS = 6;

function humanize(name: string): string {
  const words = name.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function RuleTags({ rule, categories }: { rule: RuleSummary; categories: string[] }) {
  const tint = rule.category ? categories.indexOf(rule.category) % CATEGORY_TINTS : 0;
  return (
    <span className="rule-tags">
      {rule.category && (
        <span className={`pill tint-${tint}`}>{humanize(rule.category)}</span>
      )}
      {/* Every checkable rule is declarative unless it is Python — the common
          case says nothing, so only the exception gets a tag. */}
      {rule.kind === "python" && <span className="pill plain">Python</span>}
      {rule.origin === "user" && <span className="pill accent">Custom</span>}
      {rule.status !== "active" && (
        <span className={`pill ${STATUS_TONE[rule.status]}`}>{STATUS_LABEL[rule.status]}</span>
      )}
      <span className="muted file">{rule.source_file.name}</span>
    </span>
  );
}

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

function RuleDetail({ rule, data, onEdit, onToggle }: {
  rule: RuleSummary;
  data: RulesResponse;
  onEdit?: (name: string) => void;
  onToggle?: (ruleId: string, disabled: boolean) => void;
}) {
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
        <code className="muted">
          {rule.source_file.base === "rules_dir" ? "RULES_DIR/" : ""}
          {rule.source_file.path}
        </code>
        <CopyPath label="Copy path" value={rule.source_file.path} />
        {rule.source_file.editable && onEdit && (
          <button className="secondary mini" onClick={() => onEdit(rule.source_file.name)}>
            Edit
          </button>
        )}
        {!rule.source_file.editable && rule.status !== "not_checkable" &&
          rule.status !== "unloadable" && onToggle && (
          <button
            className="secondary mini"
            onClick={() => onToggle(rule.id, rule.status !== "disabled")}
            title="Built-in rules are switched off in a local overrides file, not by editing them"
          >
            {rule.status === "disabled" ? "Enable" : "Disable"}
          </button>
        )}
      </div>

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
  const [editing, setEditing] = useState<string | null>(null);

  const override = useMutation({
    mutationFn: ({ id, disabled }: { id: string; disabled: boolean }) =>
      api.setRuleOverride(id, disabled),
    onSuccess: (res) => queryClient.setQueryData(["rules"], res.catalog),
  });
  const toggle = (ruleId: string, disabled: boolean) =>
    override.mutate({ id: ruleId, disabled });

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
          {" "}Under Docker it is read <strong>inside the container</strong> — mount a
          directory there in <code>docker-compose.yml</code> for it to work.
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

        <div className="rule-list">
          {visible.map((rule) => (
            <details className="rule-item" key={rule.id}>
              <summary>
                <span className="chev">▸</span>
                <span className="rule-line">
                  <span
                    className={`dot ${STATUS_TONE[rule.status]}`}
                    title={STATUS_LABEL[rule.status]}
                  />
                  <span className="title">{rule.id}</span>
                  <span className="subject">
                    {rule.title ?? rule.emits[0]?.title ?? ""}
                  </span>
                </span>
                <RuleTags rule={rule} categories={data.categories} />
              </summary>
              <div className="rule-body">
                <RuleDetail rule={rule} data={data} onEdit={setEditing} onToggle={toggle} />
              </div>
            </details>
          ))}
        </div>
        {visible.length === 0 && <p className="muted">No checks match those filters.</p>}
      </div>

      {override.isError && (
        <div className="callout error">
          Could not change that rule: {(override.error as Error).message}
        </div>
      )}

      <AddRule data={data} editing={editing} onDone={() => setEditing(null)} />
    </div>
  );
}
