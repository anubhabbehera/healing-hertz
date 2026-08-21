import { createContext, useContext, useEffect, useMemo, useState } from "react";

export type Theme = "dark" | "light" | "trail" | "synth";

export const THEMES: { id: Theme; label: string; icon: string }[] = [
  { id: "dark", label: "Frappé — dark", icon: "☾" },
  { id: "light", label: "Latte — light", icon: "☀" },
  { id: "trail", label: "Trail — dark", icon: "◈" },
  { id: "synth", label: "Synth — neumorphic", icon: "◍" },
];

const isTheme = (value: string | null): value is Theme =>
  THEMES.some((t) => t.id === value);

/** The theme one click away, wrapping back to the first. */
export function nextTheme(theme: Theme): Theme {
  const index = THEMES.findIndex((t) => t.id === theme);
  return THEMES[(index + 1) % THEMES.length].id;
}

export const themeMeta = (theme: Theme) =>
  THEMES.find((t) => t.id === theme) ?? THEMES[0];

export const ThemeContext = createContext<{
  theme: Theme;
  setTheme: (theme: Theme) => void;
}>({ theme: "dark", setTheme: () => {} });

export function useThemeState() {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem("hh-theme");
    if (isTheme(saved)) return saved;
    // Only the Catppuccin pair follows the OS; Trail and Synth are explicit
    // choices — neither has a light counterpart to switch to.
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
             surface: v("--surface"), ink: v("--ink"),
             // Series identity, in this fixed order — see the --series-* block
             // in styles.css for how the four were chosen and validated.
             series: [v("--series-1"), v("--series-2"), v("--series-3"), v("--series-4")] };
  }, [theme]);
}
