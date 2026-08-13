import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, watchScan } from "../api/client";
import type { ScanProgressEvent } from "../api/types";

const PHASE_LABEL: Record<string, string> = {
  collect: "Collecting telemetry",
  analyze: "Analyzing",
  advise: "Generating advice",
  persist: "Saving",
  done: "Done",
  error: "Failed",
};

export default function ScanButton() {
  const queryClient = useQueryClient();
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<ScanProgressEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cleanup = useRef<(() => void) | null>(null);

  useEffect(() => () => cleanup.current?.(), []);

  const start = async () => {
    setError(null);
    setProgress(null);
    try {
      const { run_id } = await api.startScan();
      setRunning(true);
      cleanup.current = watchScan(run_id, setProgress, (ok) => {
        setRunning(false);
        setProgress(null);
        if (!ok) setError("Scan failed — see run history for details.");
        queryClient.invalidateQueries();
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not start scan");
    }
  };

  return (
    <div className="scanwrap">
      <button className="primary" onClick={start} disabled={running}>
        {running && <span className="spinner" />}
        {running ? "Scanning" : "Run scan"}
      </button>
      {running && progress && (
        <div className="scan-progress">
          <div className="progress-bar">
            <div style={{ width: `${progress.pct ?? 30}%` }} />
          </div>
          <div className="progress-detail">
            {PHASE_LABEL[progress.phase] ?? progress.phase}
            {progress.detail ? ` — ${progress.detail}` : ""}
          </div>
        </div>
      )}
      {error && !running && (
        <div className="scan-progress">
          <div className="progress-detail">{error}</div>
        </div>
      )}
    </div>
  );
}
