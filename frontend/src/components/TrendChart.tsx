import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendPoint } from "../api/types";
import { useChartColors } from "../theme";

// Single-series line: no legend (the page's metric picker names the series),
// recessive grid/axes, ink-colored text, hover tooltip. Colors track the theme.
export default function TrendChart({ points, unit }: { points: TrendPoint[]; unit?: string }) {
  const colors = useChartColors();
  const data = points.map((p) => ({
    ...p,
    label: new Date(p.at).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }),
  }));
  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
        <CartesianGrid stroke={colors.grid} vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: colors.tick, fontSize: 11 }}
          stroke={colors.grid}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: colors.tick, fontSize: 11 }}
          stroke={colors.grid}
          tickLine={false}
          width={44}
        />
        <Tooltip
          formatter={(value: number) => [`${value}${unit ?? ""}`, ""]}
          separator=""
          cursor={{ stroke: colors.tick, strokeDasharray: "3 3" }}
          contentStyle={{
            borderRadius: 10,
            border: `1px solid ${colors.grid}`,
            background: colors.surface,
            color: colors.ink,
            fontSize: 12.5,
            boxShadow: "0 6px 18px rgba(0,0,0,0.25)",
          }}
          labelStyle={{ color: colors.tick }}
          itemStyle={{ color: colors.ink }}
        />
        <Line
          type="monotone"
          dataKey="value"
          stroke={colors.line}
          strokeWidth={2}
          dot={{ r: 3, fill: colors.line, strokeWidth: 0 }}
          activeDot={{ r: 5 }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
