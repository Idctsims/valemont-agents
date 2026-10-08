export type LegStatus = "hit" | "on-pace" | "danger" | "dead" | "live";

// Opaque *-fill tokens, not opacity modifiers, so check:contrast measures the
// exact background the label sits on. The full-strength border carries the
// at-a-glance signal.
const STATUS: Record<LegStatus, { label: string; className: string }> = {
  hit: { label: "Hit", className: "text-hit border-hit bg-hit-fill" },
  "on-pace": { label: "On pace", className: "text-on-pace border-on-pace bg-on-pace-fill" },
  danger: { label: "In danger", className: "text-danger border-danger bg-danger-fill" },
  dead: { label: "Dead", className: "text-dead border-dead bg-dead-fill" },
  live: { label: "Live", className: "text-live border-live bg-live-fill" },
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
