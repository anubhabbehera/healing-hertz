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

/** Colours are assigned per subject in a fixed order and never cycled: past
 *  the palette a series is drawn as a context hairline instead, so two devices
 *  can never share a colour. */
export const MAX_COLORED_SERIES = 4;

export type Series = { key: string; label: string };

const SHORT_TIME = {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
} as const;

/** One row per run, with a column per series: recharts joins on the x value,
 *  so points from different subjects have to be merged before they are drawn. */
function toRows(points: TrendPoint[], seriesKey: (p: TrendPoint) => string) {
  const rows = new Map<string, Record<string, string | number>>();
  for (const p of points) {
    const row = rows.get(p.at) ?? {
      at: p.at,
      label: new Date(p.at).toLocaleString(undefined, SHORT_TIME),
    };
    row[seriesKey(p)] = p.value;
    rows.set(p.at, row);
  }
  return [...rows.values()].sort((a, b) => String(a.at).localeCompare(String(b.at)));
}

export default function TrendChart({
  points,
  series,
  unit,
  height = 220,
}: {
  points: TrendPoint[];
  /** One entry per line. A single unnamed series needs no legend — the widget
   *  title names it — so callers pass a one-entry list for site metrics. */
  series: Series[];
  unit?: string;
  height?: number;
}) {
  const colors = useChartColors();
  const single = series.length === 1;
  const data = toRows(points, (p) => (single ? series[0].key : (p.subject_id ?? "unknown")));
  const colorOf = (i: number) =>
    i < MAX_COLORED_SERIES ? colors.series[i] : colors.tick;

  return (
    <div>
      {!single && (
        <div className="chart-legend">
          {series.map((s, i) => (
            <span className="legend-item" key={s.key}>
              <span className="swatch" style={{ background: colorOf(i) }} />
              {s.label}
            </span>
          ))}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={colors.grid} vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: colors.tick, fontSize: 11 }}
            stroke={colors.grid}
            tickLine={false}
            minTickGap={24}
          />
          <YAxis
            tick={{ fill: colors.tick, fontSize: 11 }}
            stroke={colors.grid}
            tickLine={false}
            width={40}
          />
          <Tooltip
            // recharts 3 widened the formatter signature; let it infer the value
            // type rather than pinning it to number.
            formatter={(value, name) => [`${value}${unit ?? ""}`, single ? "" : String(name)]}
            separator={single ? "" : ": "}
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
          {series.map((s, i) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={colorOf(i)}
              strokeWidth={i < MAX_COLORED_SERIES ? 2 : 1}
              // A gap in one device's history must not break the line for the
              // others; recharts connects across the missing column.
              connectNulls
              dot={{ r: 2.5, fill: colorOf(i), strokeWidth: 0 }}
              activeDot={{ r: 5 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
