import type { PredicateNode } from "../api/types";

/**
 * Renders a rule's condition.
 *
 * Read-only by design — there is no write path anywhere in this feature, so
 * anything that looked editable would be a lie.
 */

const OPS: Record<string, string> = {
  eq: "=",
  ne: "≠",
  lt: "<",
  lte: "≤",
  gt: ">",
  gte: "≥",
  in: "is one of",
  not_in: "is not one of",
  contains: "contains",
  is_null: "is not set",
  is_not_null: "is set",
};

const UNARY = new Set(["is_null", "is_not_null"]);

/**
 * Recover the name of a named constant from its value.
 *
 * Constants are substituted before the catalog is validated, so a compiled rule
 * holds the literal — the DFS check carries 58 raw channel numbers where the
 * file says `$DFS_CHANNELS`. Matching the value back to its name is what keeps
 * the most interesting rule in the catalog readable.
 */
function constantName(value: unknown, constants: Record<string, unknown>): string | null {
  if (!Array.isArray(value) || value.length < 2) return null;
  const encoded = JSON.stringify(value);
  const hit = Object.keys(constants)
    .filter((name) => JSON.stringify(constants[name]) === encoded)
    .sort();
  return hit.length ? `$${hit[0]}` : null;
}

function Value({ value, constants }: { value: unknown; constants: Record<string, unknown> }) {
  const named = constantName(value, constants);
  if (named) {
    return (
      <details>
        <summary>
          <code>{named}</code> <span className="muted">({(value as unknown[]).length} values)</span>
        </summary>
        <code className="muted">{(value as unknown[]).join(", ")}</code>
      </details>
    );
  }
  if (Array.isArray(value)) {
    return <code>{value.length > 6
      ? `${value.slice(0, 6).join(", ")}, …+${value.length - 6}`
      : value.join(", ")}</code>;
  }
  if (value === null || value === undefined) return <code>null</code>;
  return <code>{String(value)}</code>;
}

export default function Predicate({
  node,
  constants,
  depth = 0,
}: {
  node: PredicateNode | null | undefined;
  constants: Record<string, unknown>;
  depth?: number;
}) {
  if (!node) return <span className="muted">every row</span>;

  const indent = { paddingLeft: depth ? 16 : 0 };

  if (node.all || node.any) {
    const children = node.all ?? node.any ?? [];
    return (
      <div style={indent}>
        <span className="muted">{node.all ? "all of" : "any of"}</span>
        {children.map((child, i) => (
          <Predicate key={i} node={child} constants={constants} depth={depth + 1} />
        ))}
      </div>
    );
  }

  if (node.not) {
    return (
      <div style={indent}>
        <span className="muted">not</span>
        <Predicate node={node.not} constants={constants} depth={depth + 1} />
      </div>
    );
  }

  const op = OPS[node.op ?? ""] ?? node.op;
  return (
    <div style={indent}>
      <code>{node.binding}</code> <span className="muted">{op}</span>{" "}
      {UNARY.has(node.op ?? "") ? null : <Value value={node.value} constants={constants} />}
    </div>
  );
}
