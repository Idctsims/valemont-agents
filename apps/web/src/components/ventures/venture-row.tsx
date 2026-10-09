import Link from "next/link";

import { STAGE_LABEL, dueLabel, isSetUp, nearestOpen, type Venture, type VentureDate } from "@/lib/ventures/types";

/**
 * One venture as a ledger line: the name in the display serif, the stage as
 * a small mono tag, the next action on the second line, a warning bar and tag
 * when something blocks it, and the nearest open date on the right. A venture
 * that has only a name reads "Not set up" and opens its detail in edit mode.
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

  return (
    <li data-testid="venture-row" data-slug={venture.slug} className="relative border-b border-border">
      {blocked && <span aria-hidden className="absolute inset-y-3 left-0 w-0.5 bg-danger" />}
      <Link
        href={setUp ? `/ventures/${venture.slug}` : `/ventures/${venture.slug}?edit=1`}
        className="flex min-h-16 items-start gap-4 py-3 pl-3 transition-colors hover:bg-surface"
      >
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="font-display text-2xl text-text">{venture.name}</span>
            {venture.stage && (
              <span className="font-mono text-xs text-text-muted">{STAGE_LABEL[venture.stage].toLowerCase()}</span>
            )}
            {blocked && <span className="font-mono text-xs text-danger">blocked</span>}
          </span>
          {setUp ? (
            venture.next_action && (
              <span className="mt-1 block text-sm text-text-muted text-pretty">{venture.next_action}</span>
            )
          ) : (
            <span className="mt-1 block text-sm text-text-muted">Not set up</span>
          )}
        </span>
        {next && (
          <span
            data-testid="venture-next-date"
            className={`shrink-0 pt-2 font-mono text-xs tabular-nums ${late ? "text-danger" : "text-text-muted"}`}
          >
            {dueLabel(next.due_on, today)}
          </span>
        )}
      </Link>
    </li>
  );
}
