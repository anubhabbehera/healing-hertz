import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { RulesResponse, Severity } from "../api/types";
import SeverityBadge from "../components/SeverityBadge";

/**
 * Build a rule, check it, copy it.
 *
 * There is deliberately no save button — see the note in the panel, and the
 * docstring on backend/app/api/routes_rules.py. The operator writes the file.
 */

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];
const OPS = ["eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "is_null", "is_not_null"];
const UNARY = new Set(["is_null", "is_not_null"]);

interface Condition {
  binding: string;
  op: string;
  value: string;
}

/** Quote only when YAML would otherwise read it as something else. */
function scalar(raw: string): string {
  if (raw === "") return '""';
  if (/^-?\d+(\.\d+)?$/.test(raw)) return raw;
  if (/^(true|false|null|yes|no|on|off)$/i.test(raw)) return `"${raw}"`;
  if (raw.startsWith("$")) return raw;
  return /[:#{}[\],&*?|<>=!%@`"']/.test(raw) ? JSON.stringify(raw) : raw;
}

function conditionYaml(c: Condition): string {
  if (UNARY.has(c.op)) return `[${c.binding}, ${c.op}]`;
  const value = c.value.includes(",")
    ? `[${c.value.split(",").map((v) => scalar(v.trim())).join(", ")}]`
    : scalar(c.value);
  return `[${c.binding}, ${c.op}, ${value}]`;
}

function block(text: string, indent: string): string {
  const clean = text.trim().replace(/\s+/g, " ");
  return `>-\n${indent}${clean}`;
}

export default function AddRule({ data }: { data: RulesResponse }) {
  const [name, setName] = useState("spare_port");
  const [category, setCategory] = useState(data.categories[0] ?? "wired");
  const [source, setSource] = useState(data.sources[0]?.name ?? "devices");
  const [combinator, setCombinator] = useState<"all" | "any">("all");
  const [conditions, setConditions] = useState<Condition[]>([
    { binding: "", op: "eq", value: "" },
  ]);
  const [severity, setSeverity] = useState<Severity>("info");
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [recommendation, setRecommendation] = useState("");
  const [pasted, setPasted] = useState<string | null>(null);

  const bindings = data.sources.find((s) => s.name === source)?.bindings ?? [];
  const sourceDoc = data.sources.find((s) => s.name === source)?.doc ?? "";

  const generated = useMemo(() => {
    const real = conditions.filter((c) => c.binding);
    const where =
      real.length === 0
        ? ""
        : real.length === 1
          ? `        where: ${conditionYaml(real[0])}\n`
          : `        where:\n          ${combinator}:\n` +
            real.map((c) => `            - ${conditionYaml(c)}`).join("\n") + "\n";

    return (
      `# Added by hand — see docs/rules.md for the full syntax.\n` +
      `rules:\n` +
      `  - id: custom.${name || "unnamed"}\n` +
      `    kind: declarative\n` +
      `    category: ${category}\n` +
      `    emits:\n` +
      `      - source: ${source}\n` +
      where +
      `        severity: ${severity}\n` +
      `        title: ${scalar(title || "A short headline")}\n` +
      `        summary: ${block(summary || "What this means, in a sentence or two.", "          ")}\n` +
      `        recommendation: ${block(recommendation || "What the operator should do.", "          ")}\n`
    );
  }, [name, category, source, combinator, conditions, severity, title, summary, recommendation]);

  const draft = pasted ?? generated;
  const check = useMutation({ mutationFn: () => api.validateRule(draft) });

  const insert = (binding: string) => setTitle((t) => `${t}{${binding}}`);

  return (
    <details className="card">
      <summary>
        <h2>Add a rule</h2>
      </summary>

      <p className="muted">
        This builds the YAML and checks it. It does not save anything — copy the result
        into a file in{" "}
        {data.rules_dir.configured ? <code>{data.rules_dir.path}</code> : <code>RULES_DIR</code>}
        {" "}yourself, then press Reload rules.
      </p>

      {pasted === null && (
        <>
          <div className="select-row">
            <label htmlFor="rid">Id</label>
            <span className="muted">custom.</span>
            <input id="rid" value={name} onChange={(e) => setName(e.target.value.replace(/[^a-z0-9_]/g, ""))} />
            <label htmlFor="rcat">Category</label>
            <select id="rcat" value={category} onChange={(e) => setCategory(e.target.value)}>
              {data.categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <label htmlFor="rsev">Severity</label>
            <select id="rsev" value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
              {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>

          <div className="select-row">
            <label htmlFor="rsrc">Source</label>
            <select id="rsrc" value={source} onChange={(e) => { setSource(e.target.value);
              setConditions([{ binding: "", op: "eq", value: "" }]); }}>
              {data.sources.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </div>
          <p className="muted">{sourceDoc}</p>

          <div className="select-row">
            <label htmlFor="rcomb">Match</label>
            <select id="rcomb" value={combinator} onChange={(e) => setCombinator(e.target.value as "all" | "any")}>
              <option value="all">all conditions</option>
              <option value="any">any condition</option>
            </select>
          </div>

          {conditions.map((c, i) => (
            <div className="select-row" key={i}>
              <select
                value={c.binding}
                onChange={(e) => setConditions((cs) =>
                  cs.map((x, j) => (j === i ? { ...x, binding: e.target.value } : x)))}
              >
                <option value="">— binding —</option>
                {bindings.map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
              <select
                value={c.op}
                onChange={(e) => setConditions((cs) =>
                  cs.map((x, j) => (j === i ? { ...x, op: e.target.value } : x)))}
              >
                {OPS.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
              <input
                value={c.value}
                disabled={UNARY.has(c.op)}
                placeholder={c.op === "in" || c.op === "not_in" ? "comma, separated" : "value"}
                onChange={(e) => setConditions((cs) =>
                  cs.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))}
              />
              <button className="secondary mini"
                onClick={() => setConditions((cs) => cs.filter((_, j) => j !== i))}>
                Remove
              </button>
            </div>
          ))}
          <button className="secondary mini"
            onClick={() => setConditions((cs) => [...cs, { binding: "", op: "eq", value: "" }])}>
            Add condition
          </button>

          <dl className="kv">
            <dt>Title</dt>
            <dd>
              <input value={title} onChange={(e) => setTitle(e.target.value)}
                     placeholder="{device_name} port {port_idx} is down" />
              <div className="mini-row">
                {bindings.slice(0, 8).map((b) => (
                  <button key={b} className="secondary mini" onClick={() => insert(b)}>
                    {`{${b}}`}
                  </button>
                ))}
              </div>
            </dd>
            <dt>Summary</dt>
            <dd><textarea value={summary} rows={2} onChange={(e) => setSummary(e.target.value)} /></dd>
            <dt>Recommendation</dt>
            <dd><textarea value={recommendation} rows={2}
                          onChange={(e) => setRecommendation(e.target.value)} /></dd>
          </dl>
        </>
      )}

      <div className="mini-row">
        <button className="secondary" onClick={() => setPasted(pasted === null ? generated : null)}>
          {pasted === null ? "Paste YAML instead" : "Back to the form"}
        </button>
      </div>

      {pasted !== null && (
        <textarea rows={16} value={pasted} onChange={(e) => setPasted(e.target.value)}
                  style={{ width: "100%", fontFamily: "monospace" }} />
      )}

      {pasted === null && <pre>{generated}</pre>}

      <div className="mini-row">
        <button className="secondary" onClick={() => check.mutate()} disabled={check.isPending}>
          {check.isPending ? "Checking…" : "Check this rule"}
        </button>
        <button className="secondary" onClick={() => navigator.clipboard?.writeText(draft)}>
          Copy YAML
        </button>
      </div>

      {check.data && (
        <>
          {check.data.errors.map((e, i) => (
            <div className="callout error" key={i}>
              <strong>{e.stage}</strong> — {e.message}
            </div>
          ))}
          {check.data.ok && (
            <div className="callout">
              Valid — {check.data.rules.length} rule(s).
              {check.data.preview && (
                <>
                  {" "}Against the bundled sample network (not yours) it produces{" "}
                  <strong>{check.data.preview.matched}</strong> finding(s).
                </>
              )}
            </div>
          )}
          {check.data.warnings.map((w, i) => (
            <div className="callout" key={i}>{w.message}</div>
          ))}
          {check.data.preview?.findings.map((f, i) => (
            <p key={i}>
              <SeverityBadge severity={f.severity} /> {f.title}
            </p>
          ))}
        </>
      )}
    </details>
  );
}
