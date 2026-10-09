import Link from "next/link";

import { STAGE_LABEL, dueLabel, isSetUp, nearestOpen, type Venture, type VentureDate } from "@/lib/ventures/types";

/**
 * One venture as a ledger line.
 *
 * Set up: two lines. The name in the display serif with the stage as a small
 * mono tag (and "blocked" plus a warning bar when something blocks it), the
 * next action beneath in primary text, the nearest open date on the right.
 *
 * Name only: one line. The name left, a muted mono "not set up" right. It
 * opens the detail in edit mode. (Owner review, Chat 2 Phase 2: all seven
 * ventures fit one 390x844 screen.)
 */
export function VentureRow({
  venture,
  openDates,
  today,
}: {
  venture: Venture;
  openDates: VentureDate[];
  today: string;
}) {
  const setUp = isSetUp(venture);
  const next = nearestOpen(openDates);
  const late = next ? next.due_on < today : false;
  const blocked = !!venture.blockers;

  if (!setUp) {
    return (
      <li data-testid="venture-row" data-slug={venture.slug} data-setup="false" className="border-b border-border">
        <Link
          href={`/ventures/${venture.slug}?edit=1`}
          className="tap flex items-center justify-between gap-4 py-2 pl-3 transition-colors hover:bg-surface"
        >
          <span className="min-w-0 truncate font-display text-xl text-text">{venture.name}</span>
          <span className="shrink-0 font-mono text-xs text-text-muted">not set up</span>
        </Link>
      </li>
    );
  }

  return (
    <li data-testid="venture-row" data-slug={venture.slug} data-setup="true" className="relative border-b border-border">
      {blocked && <span aria-hidden className="absolute inset-y-3 left-0 w-0.5 bg-danger" />}
      <Link
        href={`/ventures/${venture.slug}`}
        className="flex items-start gap-4 py-3 pl-3 transition-colors hover:bg-surface"
      >
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="font-display text-xl text-text">{venture.name}</span>
            {venture.stage && (
              <span className="font-mono text-xs text-text-muted">{STAGE_LABEL[venture.stage].toLowerCase()}</span>
            )}
            {blocked && <span className="font-mono text-xs text-danger">blocked</span>}
          </span>
          {venture.next_action && (
            <span className="mt-0.5 block text-sm text-text text-pretty">{venture.next_action}</span>
          )}
        </span>
        {next && (
          <span
            data-testid="venture-next-date"
            className={`shrink-0 pt-1.5 font-mono text-xs tabular-nums ${late ? "text-danger" : "text-text-muted"}`}
          >
            {dueLabel(next.due_on, today)}
          </span>
        )}
      </Link>
    </li>
  );
}
