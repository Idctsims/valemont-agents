import { WEEKLY_CAP } from "@/lib/goals/period";
import type { Segment } from "@/lib/goals/types";

// The week as ten slots. Filled = done, outline = open (or, in history,
// carried on), dashed = dropped, faint = an unused slot. Past ten, the strip
// keeps growing in the warning colour rather than hiding the overflow.

const STYLE: Record<Segment, { base: string; over: string }> = {
  done: { base: "bg-accent", over: "bg-danger" },
  open: { base: "border border-accent", over: "border border-danger" },
  carried: { base: "border border-accent", over: "border border-danger" },
  dropped: { base: "border border-dashed border-dead", over: "border border-dashed border-danger" },
  empty: { base: "bg-surface-2", over: "bg-surface-2" },
};

const LABEL: Record<Segment, string> = {
  done: "done",
  open: "open",
  carried: "carried",
  dropped: "dropped",
  empty: "unused",
};

export function ProgressStrip({
  segments,
  size = "lg",
}: {
  segments: Segment[];
  size?: "lg" | "sm";
}) {
  const counts = segments.reduce<Partial<Record<Segment, number>>>(
    (acc, s) => ({ ...acc, [s]: (acc[s] ?? 0) + 1 }),
    {},
  );
  const summary = (["done", "open", "carried", "dropped"] as const)
    .filter((s) => counts[s])
    .map((s) => `${counts[s]} ${LABEL[s]}`)
    .join(", ");

  return (
    <div
      role="img"
      aria-label={summary ? `Week: ${summary}` : "Week: nothing set"}
      data-testid="progress-strip"
      className={`flex w-full ${size === "lg" ? "h-3 gap-1" : "h-2 gap-0.5"}`}
    >
      {segments.map((s, i) => (
        <span
          key={i}
          data-segment={s}
          data-over={i >= WEEKLY_CAP || undefined}
          className={`min-w-0 flex-1 transition-colors ${i >= WEEKLY_CAP ? STYLE[s].over : STYLE[s].base}`}
        />
      ))}
    </div>
  );
}
