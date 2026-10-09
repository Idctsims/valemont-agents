import type { Metadata } from "next";
import Link from "next/link";

import { GoalLedger } from "@/components/goals/goal-ledger";
import { GoalRow } from "@/components/goals/goal-row";
import { ProgressStrip } from "@/components/goals/progress-strip";
import { requireOwner } from "@/lib/auth";
import { ensureRollover, longTermGoals, monthGoals, weekGoals, weekHistory, type PastWeek } from "@/lib/goals/data";
import { isoWeekLabel, localToday, monthName, monthStart, weekRange, weekStart } from "@/lib/goals/period";
import { ordered, segments } from "@/lib/goals/types";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Goals" };

const VIEWS = [
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "long-term", label: "Long term" },
  { key: "history", label: "History" },
] as const;
type View = (typeof VIEWS)[number]["key"];

function parseView(v: string | string[] | undefined): View {
  return VIEWS.some((x) => x.key === v) ? (v as View) : "week";
}

export default async function GoalsPage({ searchParams }: PageProps<"/goals">) {
  await requireOwner();
  const view = parseView((await searchParams).view);
  const supabase = await createClient();
  const today = localToday();
  await ensureRollover(supabase);

  return (
    <div className="pb-composer lg:max-w-3xl lg:pb-0">
      <header className="mb-6 lg:mb-10">
        <h1 className="font-display text-4xl text-text lg:text-display">Goals</h1>
      </header>

      <nav aria-label="Goal views" className="mb-8">
        <ul className="grid grid-cols-4 border-b border-border">
          {VIEWS.map((v) => {
            const current = v.key === view;
            return (
              <li key={v.key} className="flex">
                <Link
                  href={v.key === "week" ? "/goals" : `/goals?view=${v.key}`}
                  aria-current={current ? "page" : undefined}
                  className={`tap relative flex flex-1 items-center justify-center text-sm transition-colors ${
                    current ? "font-medium text-text" : "text-text-muted hover:text-text"
                  }`}
                >
                  {v.label}
                  <span
                    aria-hidden
                    className={`absolute inset-x-2 -bottom-px h-0.5 transition-colors ${current ? "bg-accent" : "bg-transparent"}`}
                  />
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {view === "week" && (
        <GoalLedger
          goals={await weekGoals(supabase, today)}
          horizon="weekly"
          periodLabel={weekRange(weekStart(today))}
          composer="pinned"
          titled={false}
        />
      )}
      {view === "month" && (
        <GoalLedger
          goals={await monthGoals(supabase, today)}
          horizon="monthly"
          periodLabel={monthName(monthStart(today))}
          composer="pinned"
          titled={false}
        />
      )}
      {view === "long-term" && (
        <GoalLedger goals={await longTermGoals(supabase)} horizon="long_term" composer="pinned"
          titled={false} />
      )}
      {view === "history" && <History weeks={await weekHistory(supabase, today)} />}
    </div>
  );
}

function History({ weeks }: { weeks: PastWeek[] }) {
  if (weeks.length === 0) {
    return (
      <p className="font-display text-2xl text-text-muted text-balance">
        No finished weeks yet. Your first one closes on Sunday night.
      </p>
    );
  }
  return (
    <section aria-label="Past weeks" data-testid="goals-history">
      <ul className="border-t border-border">
        {weeks.map(({ monday, goals }) => {
          const done = goals.filter((g) => g.status === "done" && !g.moved).length;
          return (
            <li key={monday} className="border-b border-border">
              <details className="group">
                <summary className="flex cursor-pointer summary-plain items-center gap-4 py-4">
                  <span className="w-28 shrink-0">
                    <span className="block text-base text-text">{weekRange(monday)}</span>
                    <span className="block font-mono text-xs text-text-muted">{isoWeekLabel(monday)}</span>
                  </span>
                  <span className="min-w-0 flex-1">
                    <ProgressStrip segments={segments(goals, "history")} size="sm" />
                  </span>
                  <span
                    data-testid="history-fraction"
                    className="w-12 shrink-0 text-right font-mono text-sm text-text tabular-nums"
                  >
                    {done}/{goals.length}
                  </span>
                </summary>
                <ul className="mb-4 border-t border-border pl-2">
                  {ordered(goals).map((g) => (
                    <GoalRow key={g.id} goal={g} />
                  ))}
                </ul>
              </details>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
