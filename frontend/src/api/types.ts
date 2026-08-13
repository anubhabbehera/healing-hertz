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
