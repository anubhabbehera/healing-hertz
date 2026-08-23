import type {
  RuleDeleteResult,
  RuleFilesResponse,
  RuleSaveResult,
  RulesResponse,
  RuleValidation,
  CompareResponse,
  Dismissal,
  Finding,
  RunDetail,
  RunSummary,
  ScanProgressEvent,
  SettingsInfo,
  TestConnectionResult,
  TrendResponse,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, init);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  latestRun: () => request<RunDetail>("/api/runs/latest"),
  runs: () => request<RunSummary[]>("/api/runs"),
  runDetail: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  compare: (a: string, b: string) =>
    request<CompareResponse>(`/api/runs/compare?a=${a}&b=${b}`),
  trends: (metric: string, subjectId?: string | null) => {
    const params = new URLSearchParams({ metric });
    if (subjectId) params.set("subject_id", subjectId);
    return request<TrendResponse>(`/api/trends?${params}`);
  },
  settings: () => request<SettingsInfo>("/api/settings"),
  testConnection: () =>
    request<TestConnectionResult>("/api/settings/test-connection", { method: "POST" }),
  startScan: () => request<{ run_id: string }>("/api/scans", { method: "POST" }),
  /** Fail runs stuck at "running" after a crash or restart. */
  clearStaleScans: () =>
    request<{ cleared: number }>("/api/scans/clear-stale", { method: "POST" }),

  dismissals: () => request<Dismissal[]>("/api/dismissals"),
  /** Acknowledge a finding as won't-fix; the backend re-scores stored runs. */
  dismiss: (finding: Finding, reason: string | null, siteWide = false) =>
    request<Dismissal>("/api/dismissals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rule_id: finding.rule_id,
        subject_id: siteWide ? null : finding.subject_id,
        subject_name: siteWide ? null : finding.subject_name,
        title: finding.title,
        reason: reason || null,
      }),
    }),
  rules: () => request<RulesResponse>("/api/rules"),

  ruleFiles: () => request<RuleFilesResponse>("/api/rules/files"),

  saveRuleFile: (name: string, content: string) =>
    request<RuleSaveResult>(`/api/rules/files/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),

  deleteRuleFile: (name: string) =>
    request<RuleDeleteResult>(`/api/rules/files/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  setRuleOverride: (rule_id: string, disabled: boolean) =>
    request<{ rule_id: string; disabled: boolean; catalog: RulesResponse }>(
      "/api/rules/overrides",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rule_id, disabled }),
      },
    ),

  reloadRules: () => request<RulesResponse>("/api/rules/reload", { method: "POST" }),

  validateRule: (yaml: string) =>
    request<RuleValidation>("/api/rules/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ yaml }),
    }),

  restore: async (id: number) => {
    const resp = await fetch(`/api/dismissals/${id}`, { method: "DELETE" });
    if (!resp.ok) throw new ApiError(resp.status, resp.statusText);
  },
};

/** Subscribe to scan progress via SSE; falls back to polling if SSE errors. */
export function watchScan(
  runId: string,
  onProgress: (e: ScanProgressEvent) => void,
  onFinish: (ok: boolean) => void,
): () => void {
  const source = new EventSource(`/api/scans/${runId}/events`);
  let pollTimer: number | undefined;

  const finish = (ok: boolean) => {
    source.close();
    if (pollTimer) window.clearInterval(pollTimer);
    onFinish(ok);
  };

  source.addEventListener("progress", (event) => {
    const data = JSON.parse((event as MessageEvent).data) as ScanProgressEvent;
    onProgress(data);
    if (data.phase === "done") finish(true);
    if (data.phase === "error") finish(false);
  });

  source.onerror = () => {
    source.close();
    pollTimer = window.setInterval(async () => {
      try {
        const status = await request<{ status: string; progress: ScanProgressEvent | null }>(
          `/api/scans/${runId}`,
        );
        if (status.progress) onProgress(status.progress);
        if (status.status === "completed") finish(true);
        if (status.status === "failed") finish(false);
      } catch {
        finish(false);
      }
    }, 2000);
  };

  return () => {
    source.close();
    if (pollTimer) window.clearInterval(pollTimer);
  };
}
