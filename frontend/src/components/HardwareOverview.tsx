import type { DeviceHardware, DeviceRadio, Finding, Severity } from "../api/types";

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

const KIND_LABEL: Record<DeviceHardware["kind"], string> = {
  gateway: "Gateway",
  access_point: "AP",
  switch: "Switch",
  other: "Device",
};

const HOUR_SEC = 3600;
const DAY_SEC = 86400;
/** Past this, a device has missed every firmware window since it last booted. */
const STALE_UPTIME_SEC = 180 * DAY_SEC;
/** The controller polls devices continuously; a quiet device is a suspect one. */
const STALE_HEARTBEAT_MS = 15 * 60 * 1000;

const CPU_WARN = 75;
const MEM_WARN = 80;
const BAD_PCT = 90;

type Flag = { label: string; tone: Severity };

function formatUptime(sec: number | null): string {
  if (sec === null) return "—";
  if (sec < HOUR_SEC) return `${Math.max(1, Math.round(sec / 60))}m`;
  if (sec < 2 * DAY_SEC) return `${Math.round(sec / HOUR_SEC)}h`;
  return `${Math.round(sec / DAY_SEC)}d`;
}

function meterTone(value: number, warn: number): string {
  if (value >= BAD_PCT) return "bad";
  if (value >= warn) return "warn";
  return "good";
}

function deviceFlags(d: DeviceHardware): Flag[] {
  const flags: Flag[] = [];
  if (d.state === "OFFLINE") flags.push({ label: "offline", tone: "critical" });
  else if (d.state !== "ONLINE") {
    flags.push({ label: d.state.toLowerCase().replace(/_/g, " "), tone: "high" });
  }
  if (!d.supported) flags.push({ label: "unsupported", tone: "high" });
  // Firmware has its own column, marked there — a chip as well is the same fact twice.
  if (d.uptime_sec !== null && d.uptime_sec < HOUR_SEC) {
    flags.push({ label: "just rebooted", tone: "info" });
  }
  if (d.uptime_sec !== null && d.uptime_sec > STALE_UPTIME_SEC) {
    flags.push({ label: "no reboot in 180d", tone: "low" });
  }
  if (
    d.state === "ONLINE" &&
    d.last_heartbeat_at &&
    Date.now() - new Date(d.last_heartbeat_at).getTime() > STALE_HEARTBEAT_MS
  ) {
    flags.push({ label: "stale heartbeat", tone: "medium" });
  }
  return flags;
}

/** Worst first: offline, then by the sharpest open finding, then by load. */
function sortKey(d: DeviceHardware, worst: Severity | undefined): number[] {
  return [
    d.state === "ONLINE" ? 1 : 0,
    worst ? SEVERITY_RANK[worst] : 9,
    -(d.cpu_pct ?? 0),
    -(d.mem_pct ?? 0),
  ];
}

function compare(a: number[], b: number[]): number {
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

/** "5.0" reads as a version string next to a firmware column; bands are 2.4/5/6. */
function bandLabel(ghz: number): string {
  return Number.isInteger(ghz) ? String(ghz) : ghz.toFixed(1);
}

function radioTitle(r: DeviceRadio): string {
  const parts = [r.frequency_ghz ? `${bandLabel(r.frequency_ghz)} GHz` : "unknown band"];
  if (r.channel !== null) parts.push(`ch ${r.channel}`);
  if (r.channel_width_mhz !== null) parts.push(`${r.channel_width_mhz} MHz`);
  if (r.wlan_standard) parts.push(r.wlan_standard);
  if (r.tx_retries_pct !== null) parts.push(`${Math.round(r.tx_retries_pct)}% retries`);
  return parts.join(" · ");
}

/** Bands as chips on one line — three of them stacked and wrapped read as noise. */
function Radios({ radios }: { radios: DeviceRadio[] }) {
  if (radios.length === 0) return <div className="value">—</div>;
  return (
    <div className="hw-bands">
      {radios.map((r, i) => (
        <span className="hw-band" key={i} title={radioTitle(r)}>
          {r.frequency_ghz ? bandLabel(r.frequency_ghz) : "?"}
        </span>
      ))}
    </div>
  );
}

function Meter({ label, value, warn }: { label: string; value: number | null; warn: number }) {
  if (value === null) {
    return (
      <div className="hw-meter">
        <div className="label">{label}</div>
        <div className="muted" style={{ fontSize: 12 }}>
          —
        </div>
      </div>
    );
  }
  const tone = meterTone(value, warn);
  return (
    <div className="hw-meter">
      <div className="label">
        {label} <span className={`num ${tone}`}>{Math.round(value)}%</span>
      </div>
      <div className="bar">
        <div className={`fill ${tone}`} style={{ width: `${Math.min(100, value)}%` }} />
      </div>
    </div>
  );
}

export default function HardwareOverview({
  devices,
  findings,
}: {
  devices: DeviceHardware[];
  findings: Finding[];
}) {
  // The inventory is written at scan time, so runs from before it existed have
  // none. Say so rather than hiding the card — a silently missing panel reads
  // as a broken build.
  if (devices.length === 0) {
    return (
      <div className="card">
        <div className="card-head">
          <h2>Hardware overview</h2>
        </div>
        <p className="muted">
          This scan recorded no hardware inventory — run a new scan to populate it.
        </p>
      </div>
    );
  }

  // Open findings already carry the device id as their subject, so the issue
  // count per device is a join rather than anything the backend needs to send.
  const bySubject = new Map<string, Finding[]>();
  for (const f of findings) {
    if (f.dismissed || f.subject_type !== "device" || !f.subject_id) continue;
    const list = bySubject.get(f.subject_id);
    if (list) list.push(f);
    else bySubject.set(f.subject_id, [f]);
  }

  const worstOf = (id: string): Severity | undefined =>
    bySubject
      .get(id)
      ?.map((f) => f.severity)
      .sort((a, b) => SEVERITY_RANK[a] - SEVERITY_RANK[b])[0];

  const rows = [...devices].sort((a, b) =>
    compare(sortKey(a, worstOf(a.id)), sortKey(b, worstOf(b.id))),
  );
  const online = devices.filter((d) => d.state === "ONLINE").length;
  const updatable = devices.filter((d) => d.firmware_updatable).length;

  return (
    <div className="card">
      <div className="card-head">
        <h2>Hardware overview</h2>
        <span className="meta">
          {online}/{devices.length} online
          {updatable > 0 && ` · ${updatable} firmware update(s)`}
        </span>
      </div>
      <div className="hw-list">
        {rows.map((d) => {
          const issues = bySubject.get(d.id) ?? [];
          const worst = worstOf(d.id);
          return (
            <div className="hw-row" key={d.id}>
              <span className={`dot ${d.state === "ONLINE" ? worst ?? "good" : "critical"}`} />
              <div className="hw-id">
                <div className="name">{d.name || d.model || d.mac}</div>
                {/* Flags ride the type line rather than a line of their own: two
                    lines of identity leave the row's full width to the stats. */}
                <div className="sub">
                  <span className="kind">
                    {KIND_LABEL[d.kind]} · {d.model}
                  </span>
                  {deviceFlags(d).map((flag) => (
                    <span className={`hw-chip ${flag.tone}`} key={flag.label}>
                      {flag.label}
                    </span>
                  ))}
                </div>
              </div>
              <div className="hw-stats">
                <Meter label="CPU" value={d.cpu_pct} warn={CPU_WARN} />
                <Meter label="MEM" value={d.mem_pct} warn={MEM_WARN} />
                <div className="hw-meta">
                  <div className="label">Uptime</div>
                  <div className="value">{formatUptime(d.uptime_sec)}</div>
                </div>
                <div className="hw-meta firmware">
                  <div className="label">Firmware</div>
                  <div
                    className={`value${d.firmware_updatable ? " updatable" : ""}`}
                    title={
                      d.firmware_updatable
                        ? `${d.firmware_version ?? "unknown"} — update available`
                        : d.firmware_version ?? "unknown"
                    }
                  >
                    {d.firmware_version ?? "—"}
                    {d.firmware_updatable && <span className="upd">↑</span>}
                  </div>
                </div>
                <div className="hw-meta bands">
                  <div className="label">
                    {d.kind === "access_point" ? "Radios · GHz" : "Ports"}
                  </div>
                  {d.kind === "access_point" ? (
                    <Radios radios={d.radios} />
                  ) : (
                    <div className="value">
                      {d.ports_total > 0 ? `${d.ports_up}/${d.ports_total}` : "—"}
                    </div>
                  )}
                </div>
                <span className={`badge ${worst ?? "muted-badge"}`}>
                  {issues.length > 0
                    ? `${issues.length} issue${issues.length > 1 ? "s" : ""}`
                    : "OK"}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
