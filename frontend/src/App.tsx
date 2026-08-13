import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError } from "./api/client";
import ScanButton from "./components/ScanButton";
import Dashboard from "./pages/Dashboard";
import Findings from "./pages/Findings";
import History from "./pages/History";
import Settings from "./pages/Settings";
import Trends from "./pages/Trends";
import { ThemeContext, nextTheme, themeMeta, useThemeState } from "./theme";

export default function App() {
  const themeState = useThemeState();
  const current = themeMeta(themeState.theme);
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
          <button
            className="theme-toggle"
            onClick={() => themeState.setTheme(nextTheme(themeState.theme))}
            title={`Theme: ${current.label} — click for ${themeMeta(nextTheme(themeState.theme)).label}`}
            aria-label={`Color theme: ${current.label}. Activate for ${
              themeMeta(nextTheme(themeState.theme)).label
            }`}
          >
            {current.icon}
          </button>
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
