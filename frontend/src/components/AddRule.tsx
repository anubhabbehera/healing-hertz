import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { RulesResponse, Severity } from "../api/types";
import SeverityBadge from "../components/SeverityBadge";

/**
 * Build a rule, check it, save it.
 *
 * Saving writes a .yaml file into RULES_DIR through the API. The content is
 * validated server-side first, so an invalid rule is never written — see the
 * note on backend/app/api/routes_rules.py for what the write is bounded to.
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

export default function AddRule({ data, editing, onDone }: {
  data: RulesResponse;
  /** Filename to load into the editor, when the operator pressed Edit. */
  editing?: string | null;
  onDone?: () => void;
}) {
  const queryClient = useQueryClient();
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
  const [filename, setFilename] = useState("my-rules.yaml");

  const files = useQuery({ queryKey: ["ruleFiles"], queryFn: api.ruleFiles });

  // Pressing Edit on a rule loads its file into the editor. The file arrives
  // from a query, so seeding the editor from it is exactly the external-system
  // sync an effect is for; there is no render-time value to derive it from.
  useEffect(() => {
    if (!editing) return;
    const file = files.data?.files.find((f) => f.name === editing);
    if (file) {
      // oxlint-disable-next-line react/set-state-in-effect
      setFilename(file.name);
      setPasted(file.content);
    }
  }, [editing, files.data]);

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

  // Takes its arguments explicitly rather than reading the draft state: undo
  // sets that state and saves in the same handler, where a state read would
  // still see the previous value.
  const save = useMutation({
    mutationFn: (arg?: { name: string; content: string }) =>
      api.saveRuleFile(arg?.name ?? filename, arg?.content ?? draft),
    onSuccess: (res) => {
      if (!res.saved) return;
      if (res.catalog) queryClient.setQueryData(["rules"], res.catalog);
      queryClient.invalidateQueries({ queryKey: ["ruleFiles"] });
      onDone?.();
    },
  });

  // Deleting keeps the file, so the undo below is just a save.
  const [undo, setUndo] = useState<{ name: string; content: string; at: string } | null>(null);

  const remove = useMutation({
    mutationFn: () => api.deleteRuleFile(filename),
    onSuccess: (res) => {
      queryClient.setQueryData(["rules"], res.catalog);
      queryClient.invalidateQueries({ queryKey: ["ruleFiles"] });
      setUndo({ name: res.deleted, content: res.content, at: res.trashed_to });
      setPasted(null);
      onDone?.();
    },
  });

  const existing = files.data?.files.some((f) => f.name === filename) ?? false;
  const result = save.data ?? check.data;

  const insert = (binding: string) => setTitle((t) => `${t}{${binding}}`);

  const yamlMode = pasted !== null;

  return (
    <details className="card addrule">
      <summary>
        <h2>Add a rule</h2>
        <span className="muted">
          {data.rules_dir.configured
            ? `Saved into ${data.rules_dir.path}`
            : "RULES_DIR is not set — you can still build and check one"}
        </span>
      </summary>

      {/* One switch, at the top: build it in the form, or write the YAML by hand.
          The old mid-page toggle read as a step in the form rather than a mode. */}
      <div className="addrule-modes">
        <div className="segmented">
          <button
            className={yamlMode ? "" : "on"}
            onClick={() => setPasted(null)}
          >
            Form
          </button>
          <button
            className={yamlMode ? "on" : ""}
            onClick={() => setPasted(pasted ?? generated)}
          >
            YAML
          </button>
        </div>
      </div>

      {!data.rules_dir.configured && (
        <div className="callout">
          Set <code>RULES_DIR</code> to a writable directory to save rules from here.
        </div>
      )}

      {!yamlMode && (
        <>
          <section className="step">
            <div className="step-head"><span className="step-n">1</span> What it looks at</div>
            <div className="field">
              <label htmlFor="rsrc">Source</label>
              <select id="rsrc" value={source} onChange={(e) => { setSource(e.target.value);
                setConditions([{ binding: "", op: "eq", value: "" }]); }}>
                {data.sources.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
            </div>
            <p className="hint">{sourceDoc}</p>
          </section>

          <section className="step">
            <div className="step-head">
              <span className="step-n">2</span> When it fires
              {conditions.length > 1 && (
                <select
                  className="inline-select"
                  value={combinator}
                  onChange={(e) => setCombinator(e.target.value as "all" | "any")}
                  aria-label="Match"
                >
                  <option value="all">match all</option>
                  <option value="any">match any</option>
                </select>
              )}
            </div>

            {conditions.map((c, i) => (
              <div className="cond-row" key={i}>
                <select
                  value={c.binding}
                  onChange={(e) => setConditions((cs) =>
                    cs.map((x, j) => (j === i ? { ...x, binding: e.target.value } : x)))}
                >
                  <option value="">field…</option>
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
                <button
                  className="icon-btn"
                  title="Remove this condition"
                  aria-label="Remove this condition"
                  onClick={() => setConditions((cs) => cs.filter((_, j) => j !== i))}
                >
                  ×
                </button>
              </div>
            ))}
            <button className="secondary mini"
              onClick={() => setConditions((cs) => [...cs, { binding: "", op: "eq", value: "" }])}>
              + condition
            </button>
            <p className="hint">
              No conditions means every row from the source becomes a finding.
            </p>
          </section>

          <section className="step">
            <div className="step-head">
              <span className="step-n">3</span> What it reports
              <select
                className="inline-select"
                value={severity}
                onChange={(e) => setSeverity(e.target.value as Severity)}
                aria-label="Severity"
              >
                {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="rtitle">Title</label>
              <input id="rtitle" value={title} onChange={(e) => setTitle(e.target.value)}
                     placeholder="{device_name} port {port_idx} is down" />
            </div>
            <div className="chip-row">
              <span className="hint">insert</span>
              {bindings.slice(0, 8).map((b) => (
                <button key={b} className="chip" onClick={() => insert(b)}>
                  {`{${b}}`}
                </button>
              ))}
            </div>
            <div className="field">
              <label htmlFor="rsum">Summary</label>
              <textarea id="rsum" value={summary} rows={2}
                        placeholder="What this means, in a sentence or two."
                        onChange={(e) => setSummary(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="rrec">Recommendation</label>
              <textarea id="rrec" value={recommendation} rows={2}
                        placeholder="What the operator should do."
                        onChange={(e) => setRecommendation(e.target.value)} />
            </div>
          </section>

          <section className="step">
            <div className="step-head"><span className="step-n">4</span> Where it lives</div>
            <div className="field">
              <label htmlFor="rid">Id</label>
              <span className="prefix">custom.</span>
              <input id="rid" value={name}
                     onChange={(e) => setName(e.target.value.replace(/[^a-z0-9_]/g, ""))} />
            </div>
            <div className="field">
              <label htmlFor="rcat">Category</label>
              <select id="rcat" value={category} onChange={(e) => setCategory(e.target.value)}>
                {data.categories.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="fname">File</label>
              <input id="fname" value={filename} onChange={(e) => setFilename(e.target.value)}
                     placeholder="my-rules.yaml" />
              {existing && <span className="hint">replaces the existing file</span>}
            </div>
          </section>

          <details className="yaml-preview">
            <summary>Preview YAML</summary>
            <pre>{generated}</pre>
          </details>
        </>
      )}

      {yamlMode && (
        <>
          <textarea className="yaml-editor" rows={16} value={pasted ?? ""}
                    onChange={(e) => setPasted(e.target.value)} />
          <div className="field">
            <label htmlFor="fname-yaml">File</label>
            <input id="fname-yaml" value={filename} onChange={(e) => setFilename(e.target.value)}
                   placeholder="my-rules.yaml" />
            {existing && <span className="hint">replaces the existing file</span>}
          </div>
        </>
      )}

      <div className="addrule-actions">
        <button
          className="primary"
          onClick={() => save.mutate(undefined)}
          disabled={save.isPending || !data.rules_dir.configured}
          title={data.rules_dir.configured ? "" : "RULES_DIR is not set"}
        >
          {save.isPending ? "Saving…" : existing ? "Save changes" : "Save rule"}
        </button>
        <button className="secondary" onClick={() => check.mutate()} disabled={check.isPending}>
          {check.isPending ? "Checking…" : "Check only"}
        </button>
        <button className="secondary" onClick={() => navigator.clipboard?.writeText(draft)}>
          Copy YAML
        </button>
        {existing && (
          <button
            className="secondary danger"
            onClick={() => remove.mutate()}
            title="The file is kept in .trash, so this can be undone"
            disabled={remove.isPending}
          >
            {remove.isPending ? "Deleting…" : "Delete file"}
          </button>
        )}
      </div>

      {save.isError && (
        <div className="callout error">Could not save: {(save.error as Error).message}</div>
      )}
      {remove.isError && (
        <div className="callout error">Could not delete: {(remove.error as Error).message}</div>
      )}
      {save.data?.saved && (
        <div className="callout">Saved as <code>{save.data.name}</code> and now running.</div>
      )}
      {undo && (
        <div className="callout">
          <strong>{undo.name}</strong> stopped running. The file is kept at{" "}
          <code>{undo.at}</code>.{" "}
          <button
            className="secondary mini"
            onClick={() => {
              setFilename(undo.name);
              setPasted(undo.content);
              save.mutate({ name: undo.name, content: undo.content });
              setUndo(null);
            }}
          >
            Undo
          </button>
        </div>
      )}

      {result && (
        <>
          {result.errors.map((e, i) => (
            <div className="callout error" key={i}>
              <strong>{e.stage}</strong> — {e.message}
            </div>
          ))}
          {result.ok && (
            <div className="callout">
              Valid — {result.rules.length} rule(s).
              {result.preview && (
                <>
                  {" "}Against the bundled sample network (not yours) it produces{" "}
                  <strong>{result.preview.matched}</strong> finding(s).
                </>
              )}
            </div>
          )}
          {result.warnings.map((w, i) => (
            <div className="callout" key={i}>{w.message}</div>
          ))}
          {result.preview?.findings.map((f, i) => (
            <p key={i}>
              <SeverityBadge severity={f.severity} /> {f.title}
            </p>
          ))}
        </>
      )}
    </details>
  );
}
