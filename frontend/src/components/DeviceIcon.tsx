import { useState } from "react";
import type { DeviceHardware } from "../api/types";

type Kind = DeviceHardware["kind"];

/** Drawn stand-ins, used when the console has no product art for a model.
 *  Deliberately plain: they read as a placeholder, not as a rival icon set. */
function Glyph({ kind, size }: { kind: Kind; size: number }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  if (kind === "access_point") {
    return (
      <svg {...common} className="device-glyph">
        <circle cx="12" cy="15.5" r="2" />
        <path d="M8.4 12a5 5 0 0 1 7.2 0" />
        <path d="M5.6 9a9 9 0 0 1 12.8 0" />
      </svg>
    );
  }
  if (kind === "switch") {
    return (
      <svg {...common} className="device-glyph">
        <rect x="3" y="8" width="18" height="8" rx="1.5" />
        <path d="M6.5 12h1M10 12h1M13.5 12h1M17 12h1" />
      </svg>
    );
  }
  if (kind === "gateway") {
    return (
      <svg {...common} className="device-glyph">
        <rect x="3" y="9" width="18" height="7" rx="1.5" />
        <path d="M7 12.5h4" />
        <circle cx="16.5" cy="12.5" r="1" />
      </svg>
    );
  }
  return (
    <svg {...common} className="device-glyph">
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <path d="M9 12h6" />
    </svg>
  );
}

/** Official product art for a model, falling back to a drawn glyph.
 *
 *  The backend serves the image, so this works on a machine with no internet
 *  access once the icon has been cached — and a 404 (unknown model, icons
 *  switched off) is an expected answer, not an error. */
export default function DeviceIcon({
  model,
  kind,
  size = 30,
}: {
  model: string;
  kind: Kind;
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  if (!model || failed) return <Glyph kind={kind} size={size} />;
  return (
    <img
      className="device-icon"
      src={`/api/device-icons/${encodeURIComponent(model)}`}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}
