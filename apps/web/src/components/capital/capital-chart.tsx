"use client";

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

export default function CapitalChart({ points, mode }: { points: Point[]; mode: Mode }) {
  // Pad the value axis a little so a flat line sits mid-chart, not on the edge.
  const values = points.map((p) => p.value);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = Math.max(Math.round((hi - lo) * 0.15), Math.round(Math.abs(hi) * 0.02), 100);

  return (
    <div className={`chart-capital ${mode === "live" ? "chart-live" : "chart-paper"} h-48 w-full lg:h-64`}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 8, right: 4, bottom: 0, left: 4 }}>
          <XAxis
            dataKey="date"
            tickFormatter={shortDate}
            axisLine={false}
            tickLine={false}
            minTickGap={48}
            interval="preserveStartEnd"
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
