import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

function DismissedFindings() {
  const queryClient = useQueryClient();
  const { data: dismissals } = useQuery({
    queryKey: ["dismissals"],
    queryFn: api.dismissals,
  });
  const restore = useMutation({
    mutationFn: api.restore,
    onSuccess: () => queryClient.invalidateQueries(),
  });

  return (
    <div className="card">
      <div className="card-head">
        <h2>Dismissed findings</h2>
        <span className="meta">{dismissals?.length ?? 0} active</span>
      </div>
      {!dismissals?.length ? (
        <p className="muted" style={{ margin: 0 }}>
          None. Dismiss a finding you can't act on and it stops counting against your
          health score, on this and future scans.
        </p>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th>Finding</th>
              <th>Scope</th>
              <th>Reason</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {dismissals.map((d) => (
              <tr key={d.id}>
                <td>
                  {d.title ?? d.rule_id}
                  <div className="muted" style={{ fontSize: 11 }}>{d.rule_id}</div>
                </td>
                <td>{d.subject_id ? (d.subject_name ?? d.subject_id) : "all subjects"}</td>
                <td className="muted">{d.reason || "—"}</td>
                <td style={{ textAlign: "right" }}>
                  <button
                    className="secondary"
                    onClick={() => restore.mutate(d.id)}
                    disabled={restore.isPending}
                  >
                    Restore
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function Settings() {
  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const test = useMutation({ mutationFn: api.testConnection });

  if (!settings) return <div className="empty">Loading…</div>;

  return (
    <div>
      <h1>Settings</h1>
      <p className="subtitle">
        Configuration is environment-driven — edit the backend <code>.env</code> file and
        restart to change it.
      </p>

      {settings.demo_mode && (
        <div className="callout">
          Demo mode is on — scans use bundled sample telemetry instead of a real console.
        </div>
      )}

      <div className="card">
        <dl className="kv">
          <dt>UniFi console</dt>
          <dd>
            {settings.unifi_host
              ? `${settings.unifi_host}:${settings.unifi_port}${settings.unifi_api_prefix}`
              : "not configured"}
          </dd>
          <dt>API key</dt>
          <dd>{settings.unifi_api_key_set ? "configured" : "not set"}</dd>
          <dt>TLS verification</dt>
          <dd>{settings.unifi_tls_verify ? "on" : "off (self-signed accepted)"}</dd>
          <dt>Site</dt>
          <dd>{settings.unifi_site || "first site on console"}</dd>
          <dt>AI advisor</dt>
          <dd>
            {settings.anthropic_api_key_set
              ? `enabled (${settings.advisor_model})`
              : "disabled — set ANTHROPIC_API_KEY to enable"}
          </dd>
          <dt>Advisor endpoint</dt>
          <dd>{settings.anthropic_base_url || "official Anthropic API"}</dd>
          <dt>Client RF / roaming (legacy API)</dt>
          <dd>
            {settings.legacy_api_enabled
              ? "enabled"
              : "off — set UNIFI_USERNAME/UNIFI_PASSWORD (read-only admin)"}
          </dd>
          <dt>NextDNS analytics</dt>
          <dd>
            {settings.nextdns_enabled
              ? "enabled"
              : "off — set NEXTDNS_API_KEY and NEXTDNS_PROFILE_ID"}
          </dd>
          <dt>WAN probe</dt>
          <dd>{settings.wan_probe_enabled ? "enabled" : "off"}</dd>
        </dl>
      </div>

      <DismissedFindings />

      <div className="card">
        <button
          className="secondary"
          onClick={() => test.mutate()}
          disabled={test.isPending}
        >
          {test.isPending ? "Testing…" : "Test connection"}
        </button>
        {test.data && (
          <div style={{ marginTop: 12 }}>
            {test.data.ok ? (
              <div className="callout">
                Connected — UniFi Network {test.data.application_version}. Sites:{" "}
                {test.data.sites?.map((s) => s.name || s.id).join(", ")}
              </div>
            ) : (
              <div className="callout error">{test.data.error}</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
