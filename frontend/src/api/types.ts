export type Severity = "critical" | "high" | "medium" | "low" | "info";

export interface RunSummary {
  id: string;
  started_at: string | null;
  finished_at: string | null;
  status: "running" | "completed" | "failed";
  site_name: string | null;
  health_score: number | null;
  device_count: number | null;
  client_count: number | null;
  advice_status: "ok" | "skipped" | "failed";
  advice_error: string | null;
  /** Open findings only — dismissed ones are excluded, matching the score. */
  severity_counts: Partial<Record<Severity, number>>;
  dismissed_count: number;
  error: string | null;
}

export interface Finding {
  id: number;
  dismissed: boolean;
  rule_id: string;
  severity: Severity;
  category: string;
  title: string;
  summary: string;
  evidence: Record<string, unknown>;
  recommendation: string;
  subject_type: string;
  subject_id: string | null;
  subject_name: string | null;
}

export interface Suggestion {
  priority: number;
  title: string;
  rationale: string;
  steps: string[];
  effort: "low" | "medium" | "high";
  related_rule_ids: string[];
}

export interface UnsupportedCheck {
  rule_id: string;
  title: string;
  reason: string;
}

export interface AdvicePlan {
  overall_assessment: string;
  items: Suggestion[];
  quick_wins: string[];
}

export interface DeviceRadio {
  frequency_ghz: number | null;
  channel: number | null;
  channel_width_mhz: number | null;
  wlan_standard: string | null;
  tx_retries_pct: number | null;
}

export interface DeviceHardware {
  id: string;
  name: string;
  model: string;
  mac: string;
  ip: string | null;
  kind: "gateway" | "access_point" | "switch" | "other";
  state: string;
  supported: boolean;
  firmware_version: string | null;
  firmware_updatable: boolean;
  cpu_pct: number | null;
  mem_pct: number | null;
  load_5m: number | null;
  load_15m: number | null;
  uptime_sec: number | null;
  last_heartbeat_at: string | null;
  ports_total: number;
  ports_up: number;
  poe_ports_up: number;
  uplink_tx_bps: number | null;
  uplink_rx_bps: number | null;
  radios: DeviceRadio[];
}

export interface RunDetail extends RunSummary {
  site_metrics: Record<string, number>;
  findings: Finding[];
  /** Empty for runs scanned before the hardware inventory was persisted. */
  devices?: DeviceHardware[];
  suggestions: Suggestion[];
  advice: AdvicePlan | null;
  unsupported_checks?: UnsupportedCheck[];
  previous_health_score?: number | null;
}

export interface TrendPoint {
  run_id: string;
  at: string;
  subject_id: string | null;
  subject_name: string | null;
  value: number;
}

export interface TrendResponse {
  metric: string;
  subjects: { subject_id: string | null; subject_name: string | null }[];
  points: TrendPoint[];
}

export interface CompareResponse {
  older: RunSummary;
  newer: RunSummary;
  new: Finding[];
  resolved: Finding[];
  persisting: Finding[];
}

export interface SettingsInfo {
  unifi_host: string;
  unifi_port: number;
  unifi_tls_verify: boolean;
  unifi_api_prefix: string;
  unifi_site: string;
  unifi_api_key_set: boolean;
  anthropic_api_key_set: boolean;
  anthropic_base_url: string;
  advisor_model: string;
  demo_mode: boolean;
  legacy_api_enabled: boolean;
  nextdns_enabled: boolean;
  wan_probe_enabled: boolean;
}

export interface TestConnectionResult {
  ok: boolean;
  application_version?: string;
  sites?: { id: string; name: string }[];
  error?: string;
}

export interface Dismissal {
  id: number;
  rule_id: string;
  subject_id: string | null;
  subject_name: string | null;
  title: string | null;
  reason: string | null;
  created_at: string | null;
}

export interface ScanProgressEvent {
  phase: "collect" | "analyze" | "advise" | "persist" | "done" | "error";
  detail: string;
  pct: number | null;
}

// --- rule catalog ---

export type RuleKind = "declarative" | "python" | "none";
export type RuleStatus = "active" | "disabled" | "not_checkable" | "unloadable";

/** A predicate node, in the same shape the YAML uses. */
export interface PredicateNode {
  all?: PredicateNode[];
  any?: PredicateNode[];
  not?: PredicateNode;
  binding?: string;
  op?: string;
  value?: unknown;
}

export interface SeveritySpec {
  base: Severity;
  escalate: { when: PredicateNode; to: Severity }[];
}

export interface RuleEmit {
  index: number;
  key: string | null;
  severity: SeveritySpec;
  title: string;
  summary: string;
  recommendation: string;
  evidence: Record<string, { raw?: string; op?: string }>;
  source?: string;
  subject?: string;
  where?: PredicateNode | null;
  compute?: Record<string, Record<string, unknown>>;
  aggregate?: Record<string, unknown> | null;
}

export interface RuleFile {
  name: string;
  /**
   * Relative to `base`, never absolute — the same string on every install.
   * Where the base actually lives is reported once, not per rule.
   */
  path: string;
  base: "app" | "rules_dir";
  /** True when the file lives in RULES_DIR and is the operator's to change. */
  editable: boolean;
}

export interface RuleSummary {
  id: string;
  kind: RuleKind;
  status: RuleStatus;
  validated: boolean;
  category: string | null;
  origin: "builtin" | "user";
  source_file: RuleFile;
  emits: RuleEmit[];
  /** not_checkable / unloadable only */
  title?: string;
  reason?: string;
  enrichment?: string | null;
  enrichment_configured?: boolean;
  /** python only */
  impl?: { ref: string; doc: string; path: string | null; line: number | null };
  provides?: string[];
}

export interface SourceInfo {
  name: string;
  doc: string;
  bindings: string[];
}

export interface RulesResponse {
  loaded_at: string;
  /** Rule ids switched off locally, via the overrides file. */
  overrides: string[];
  rules_dir: { configured: boolean; path: string | null; exists: boolean };
  counts: Partial<Record<RuleStatus, number>>;
  categories: string[];
  constants: Record<string, unknown>;
  sources: SourceInfo[];
  rules: RuleSummary[];
}

export interface RuleValidation {
  ok: boolean;
  errors: { stage: string; rule_id: string | null; message: string }[];
  warnings: { message: string }[];
  rules: RuleSummary[];
  preview: {
    basis: string;
    matched: number;
    findings: { severity: Severity; title: string; summary: string;
                subject_name: string | null }[];
  } | null;
}

export interface RuleFileContent {
  name: string;
  path: string;
  content: string;
}

export interface RuleFilesResponse {
  dir: string | null;
  files: RuleFileContent[];
}

export interface RuleSaveResult extends RuleValidation {
  saved: boolean;
  name: string;
  path: string;
  catalog?: RulesResponse;
}
