export type LegStatus = "hit" | "on-pace" | "danger" | "dead" | "live";

const STATUS: Record<LegStatus, { label: string; className: string }> = {
  hit: { label: "Hit", className: "text-hit border-hit/40 bg-hit/10" },
  "on-pace": { label: "On pace", className: "text-on-pace border-on-pace/40 bg-on-pace/10" },
  danger: { label: "In danger", className: "text-danger border-danger/40 bg-danger/10" },
  dead: { label: "Dead", className: "text-dead border-dead/40 bg-dead/10" },
  live: { label: "Live", className: "text-live border-live/40 bg-live/10" },
};

export function StatusChip({ status, label }: { status: LegStatus; label?: string }) {
  const s = STATUS[status];
  return (
    <span
      className={`label-mono inline-flex h-7 shrink-0 items-center whitespace-nowrap gap-1.5 rounded-pill border px-2.5 ${s.className}`}
    >
      <span
        aria-hidden
        className={`size-1.5 rounded-pill bg-current ${status === "live" ? "animate-pulse" : ""}`}
      />
      {label ?? s.label}
    </span>
  );
}

/** Marks paper (simulated) money. Every money surface shows it until live. */
export function PaperBadge() {
  return (
    <span className="label-mono inline-flex h-6 shrink-0 items-center whitespace-nowrap rounded-pill bg-paper-bg px-2.5 text-paper-fg">
      Paper
    </span>
  );
}
