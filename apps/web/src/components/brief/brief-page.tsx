import Link from "next/link";

import { Fold } from "@/components/ui/fold";
import { briefDay, type Brief } from "@/lib/brief/types";

import { AskWagsAboutThis, BriefLabel, OneMove, Sections } from "./brief-parts";

// /brief and /brief/[date]: one brief, editorial, then the history. The
// headline is the page's one hero; section titles in mono; lines as
// hairline rows; the one move last and emphasized.

export function BriefPage({ brief, history, day, isToday }: { brief: Brief | null; history: Brief[]; day: string; isToday: boolean }) {
  const past = history.filter((b) => b.date !== day);
  return (
    <div data-testid="brief-page" className="lg:max-w-3xl">
      {brief ? (
        <article aria-label={`Morning brief, ${briefDay(brief.date)}`}>
          <BriefLabel brief={brief} withDate />
          <h1 data-testid="brief-headline" className="mt-3 font-display text-4xl text-text text-balance lg:text-display">
            {brief.body.headline}
          </h1>
          {brief.body.sections.length > 0 && (
            <div className="mt-12">
              <Sections sections={brief.body.sections} />
            </div>
          )}
          <div className="mt-12">
            <OneMove text={brief.body.one_move} large />
          </div>
          <div className="mt-6 -ml-3">
            <AskWagsAboutThis />
          </div>
        </article>
      ) : (
        <div data-testid="brief-empty">
          <p className="label-mono text-text-muted">Morning brief · {briefDay(day)}</p>
          <h1 className="mt-3 font-display text-4xl text-text text-balance">
            {isToday ? "No brief yet today." : "No brief for this day."}
          </h1>
          <p className="mt-3 text-base text-text-muted">
            {isToday
              ? "It is written at 6:15 AM from your goals, ventures and capital, and arrives as a push at 9:45."
              : "Briefs began on Oct 11, 2026; a day the worker was down has none."}
          </p>
        </div>
      )}

      <div className="mt-16">
        {past.length > 0 ? (
          <Fold label="Past briefs" count={past.length} testId="brief-history">
            {past.map((b) => (
              <li key={b.id} className="border-b border-border">
                <Link
                  href={`/brief/${b.date}`}
                  className="tap flex items-baseline justify-between gap-4 py-2 transition-colors hover:text-accent"
                >
                  <span className="min-w-0 truncate text-sm text-text">{b.body.headline}</span>
                  <span className="shrink-0 font-mono text-xs text-text-muted">{briefDay(b.date)}</span>
                </Link>
              </li>
            ))}
          </Fold>
        ) : (
          <p className="label-mono text-text-muted">No past briefs yet</p>
        )}
        {!isToday && (
          <Link href="/brief" className="tap mt-2 inline-flex items-center font-mono text-xs text-text-muted hover:text-text">
            ← Today&apos;s brief
          </Link>
        )}
      </div>
    </div>
  );
}
