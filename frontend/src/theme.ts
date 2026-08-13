import { createContext, useContext, useEffect, useMemo, useState } from "react";

export type Theme = "dark" | "light" | "trail";

export const THEMES: { id: Theme; label: string }[] = [
  { id: "dark", label: "Frappé — dark" },
  { id: "light", label: "Latte — light" },
  { id: "trail", label: "Trail — dark" },
];

const isTheme = (value: string | null): value is Theme =>
  THEMES.some((t) => t.id === value);

export const ThemeContext = createContext<{
  theme: Theme;
  setTheme: (theme: Theme) => void;
}>({ theme: "dark", setTheme: () => {} });

export function useThemeState() {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem("hh-theme");
    if (isTheme(saved)) return saved;
    // Only the Catppuccin pair follows the OS; Trail is an explicit choice.
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("hh-theme", theme);
  }, [theme]);
  return { theme, setTheme };
}

export const useTheme = () => useContext(ThemeContext);

/** Chart colors resolved from the active theme's CSS variables. */
export function useChartColors() {
  const { theme } = useTheme();
  return useMemo(() => {
    const style = getComputedStyle(document.documentElement);
    const v = (name: string) => style.getPropertyValue(name).trim();
    return { line: v("--accent"), grid: v("--border"), tick: v("--ink-muted"),
             surface: v("--surface"), ink: v("--ink") };
  }, [theme]);
}
