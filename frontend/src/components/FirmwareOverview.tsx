import type { DeviceHardware } from "../api/types";
import DeviceIcon from "./DeviceIcon";

const KIND_LABEL: Record<DeviceHardware["kind"], string> = {
  gateway: "Gateway",
  access_point: "Access point",
  switch: "Switch",
  other: "Device",
};

/** Pending updates first, then offline hardware, then by name. */
function sorted(devices: DeviceHardware[]): DeviceHardware[] {
  return [...devices].sort((a, b) => {
    if (a.firmware_updatable !== b.firmware_updatable) return a.firmware_updatable ? -1 : 1;
    const aOff = a.state !== "ONLINE";
    const bOff = b.state !== "ONLINE";
    if (aOff !== bOff) return aOff ? -1 : 1;
    return (a.name || a.model).localeCompare(b.name || b.model);
  });
}

export default function FirmwareOverview({ devices }: { devices: DeviceHardware[] }) {
  if (devices.length === 0) return null;

  const rows = sorted(devices);
  const pending = devices.filter((d) => d.firmware_updatable).length;

  return (
    <div className="card">
      <div className="card-head">
        <h2>Firmware overview</h2>
        <span className="meta">
          {pending === 0
            ? `all ${devices.length} up to date`
            : `${pending} of ${devices.length} need updating`}
        </span>
      </div>
      <div className="fw-grid">
        {rows.map((d) => {
          const offline = d.state !== "ONLINE";
          return (
            <div className={`fw-card${d.firmware_updatable ? " updatable" : ""}`} key={d.id}>
              <DeviceIcon model={d.model} kind={d.kind} size={38} />
              <div className="fw-id">
                <div className="name" title={d.name || d.model}>
                  {d.name || d.model}
                </div>
                <div className="model" title={`${KIND_LABEL[d.kind]} · ${d.model}`}>
                  {d.model}
                </div>
              </div>
              <div className="fw-version">
                <div className="num">{d.firmware_version ?? "—"}</div>
                {/* State, not just colour: the tag is readable with the hue
                    ignored, which is how a firmware column has to work. */}
                <div className={`fw-tag ${d.firmware_updatable ? "pending" : "current"}`}>
                  {d.firmware_updatable ? "↑ update available" : "✓ latest"}
                </div>
                {offline && <div className="fw-note">{d.state.toLowerCase().replace(/_/g, " ")}</div>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
