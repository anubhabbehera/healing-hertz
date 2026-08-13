import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError } from "./api/client";
import ScanButton from "./components/ScanButton";
import Dashboard from "./pages/Dashboard";
import Findings from "./pages/Findings";
import History from "./pages/History";
import Settings from "./pages/Settings";
import Trends from "./pages/Trends";
import type { Theme } from "./theme";
import { THEMES, ThemeContext, useThemeState } from "./theme";

export default function App() {
  const themeState = useThemeState();
  const { data: latest } = useQuery({
    queryKey: ["latest"],
    queryFn: api.latestRun,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
    refetchInterval: 30_000,
  });

  return (
    <ThemeContext.Provider value={themeState}>
      <header className="topbar">
        <div className="brand">
          <span className="logo">◉</span>
          healing<span>hertz</span>
        </div>
        <nav>
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/findings">Findings</NavLink>
          <NavLink to="/trends">Trends</NavLink>
          <NavLink to="/history">History</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <div className="topbar-right">
          {latest?.started_at && (
            <span className="timestamp">
              scanned {new Date(latest.started_at).toLocaleString()}
            </span>
          )}
          <ScanButton />
          <select
            className="theme-select"
            value={themeState.theme}
            onChange={(e) => themeState.setTheme(e.target.value as Theme)}
            aria-label="Color theme"
            title="Color theme"
          >
            {THEMES.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/findings" element={<Findings />} />
          <Route path="/trends" element={<Trends />} />
          <Route path="/history" element={<History />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </ThemeContext.Provider>
  );
}
