import { createContext, useContext, useEffect, useMemo, useState } from "react";

export type Theme = "dark" | "light";

export const ThemeContext = createContext<{ theme: Theme; toggle: () => void }>({
  theme: "dark",
  toggle: () => {},
});

export function useThemeState() {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem("hh-theme");
    if (saved === "dark" || saved === "light") return saved;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("hh-theme", theme);
  }, [theme]);
  return { theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) };
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
