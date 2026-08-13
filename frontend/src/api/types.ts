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
  severity_counts: Partial<Record<Severity, number>>;
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

export interface RunDetail extends RunSummary {
  site_metrics: Record<string, number>;
  findings: Finding[];
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
