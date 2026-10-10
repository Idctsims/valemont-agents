"use client";

import { useEffect, useRef, useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { money, shortDate, type Mode, type Point } from "@/lib/capital/types";

// Recharts, loaded only on /capital (next/dynamic in capital-view.tsx). One
// mode's series per chart, never paper and live on one line. Colours and type
// come from tokens.css (the chart-capital utilities), so nothing here is a
// literal colour or size.

function Tip({ active, payload }: { active?: boolean; payload?: { payload: Point }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-inner border border-border bg-surface px-3 py-2 font-mono text-xs text-text tabular-nums">
      <span className="text-text-muted">{shortDate(p.date)}</span> {money(p.value)}
    </div>
  );
}

/** Evenly spaced dates, always the first and the last. */
export function dateTicks(points: Point[], count: number): string[] {
  if (points.length <= count) return points.map((p) => p.date);
  const last = points.length - 1;
  const picks = Array.from({ length: count }, (_, i) => Math.round((i * last) / (count - 1)));
  return [...new Set(picks)].map((i) => points[i].date);
}

/** Phones get three labels, wider charts five: "Oct 9" never crowds. */
export const tickCount = (width: number) => (width < 520 ? 3 : 5);

/**
 * The first label hangs right of its point and the last hangs left, so
 * neither is cut at the chart's edges; the rest are centred. The class keeps
 * tokens.css's chart-capital type and colour.
 */
function EdgeTick(props: {
  x?: number;
  y?: number;
  index?: number;
  visibleTicksCount?: number;
  payload?: { value: string };
}) {
  const { x = 0, y = 0, index = 0, visibleTicksCount = 1, payload } = props;
  const anchor = index === 0 ? "start" : index === visibleTicksCount - 1 ? "end" : "middle";
  return (
    <text x={x} y={y} dy={12} textAnchor={anchor} className="recharts-cartesian-axis-tick-value">
      {payload ? shortDate(payload.value) : ""}
    </text>
  );
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

export default function CapitalChart({ points, mode }: { points: Point[]; mode: Mode }) {
  // Pad the value axis a little so a flat line sits mid-chart, not on the edge.
  const values = points.map((p) => p.value);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = Math.max(Math.round((hi - lo) * 0.15), Math.round(Math.abs(hi) * 0.02), 100);
  const [box, width] = useWidth<HTMLDivElement>();

  return (
    <div
      ref={box}
      data-testid="capital-chart"
      className={`chart-capital ${mode === "live" ? "chart-live" : "chart-paper"} h-48 w-full lg:h-64`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 8, right: 4, bottom: 0, left: 4 }}>
          <XAxis
            dataKey="date"
            ticks={dateTicks(points, tickCount(width))}
            tick={<EdgeTick />}
            interval={0}
            axisLine={false}
            tickLine={false}
          />
          <YAxis hide domain={[lo - pad, hi + pad]} />
          <Tooltip content={<Tip />} cursor={{ strokeWidth: 1 }} isAnimationActive={false} />
          <Area
            type="monotone"
            dataKey="value"
            isAnimationActive={false}
            dot={points.length <= 14 ? { r: 2.5 } : false}
            activeDot={{ r: 4 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
