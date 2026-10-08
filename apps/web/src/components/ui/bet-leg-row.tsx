import { StatusChip, type LegStatus } from "./badges";

export type Leg = {
  player: string;
  team: string;
  market: string;
  line: string;
  current: number;
  target: number;
  clock: string;
  status: LegStatus;
  /** Live probability the leg cashes, 0–1. */
  prob: number;
};

const BAR: Record<LegStatus, string> = {
  hit: "bg-hit",
  "on-pace": "bg-on-pace",
  danger: "bg-danger",
  dead: "bg-dead",
  live: "bg-live",
};

/**
 * One leg of a slip: stat against line, status, live probability. Lays out by
 * the width of its container (wrap the list in `@container`), not the
 * viewport, so it works in a narrow column as well as full width.
 */
export function BetLegRow({ leg }: { leg: Leg }) {
  const pct = Math.min(100, Math.round((leg.current / leg.target) * 100));

  return (
    <div className="grid grid-cols-1 gap-3 py-4 @xl:grid-cols-12 @xl:items-center @xl:gap-4">
      <div className="@xl:col-span-5">
        <p className="text-base font-medium text-text">
          {leg.player} <span className="label-mono text-text-muted">{leg.team}</span>
        </p>
        <p className="text-sm text-text-muted">
          {leg.market} <span className="font-mono text-text">{leg.line}</span>
        </p>
      </div>

      <div className="@xl:col-span-4">
        <div className="flex items-baseline justify-between font-mono text-sm tabular-nums">
          <span className="text-text">
            {leg.current}
            <span className="text-text-muted">/{leg.target}</span>
          </span>
          <span className="label-mono text-text-muted">{leg.clock}</span>
        </div>
        <div
          className="mt-2 h-1.5 overflow-hidden rounded-pill bg-surface-3"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={leg.target}
          aria-valuenow={leg.current}
          aria-label={`${leg.market}: ${leg.current} of ${leg.target}`}
        >
          <div className={`h-full rounded-pill ${BAR[leg.status]}`} style={{ width: `${pct}%` }} />
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 @xl:col-span-3 @xl:justify-end">
        <StatusChip status={leg.status} />
        <span className="font-mono text-lg tabular-nums text-text">
          {Math.round(leg.prob * 100)}
          <span className="text-sm text-text-muted">%</span>
        </span>
      </div>
    </div>
  );
}
