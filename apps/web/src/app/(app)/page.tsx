import type { Metadata } from "next";

import { GoalLedger } from "@/components/goals/goal-ledger";
import { requireOwner } from "@/lib/auth";
import { ensureRollover, weekGoals } from "@/lib/goals/data";
import { isoWeekLabel, localToday, longDate, weekRange, weekStart } from "@/lib/goals/period";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Home" };

/** Morning Brief (Chat 2 Phase 5). Renders nothing until the brief exists. */
function MorningBriefSlot() {
  return null;
}

/** Personalized feed (Chat 3). Renders nothing until the feed exists. */
function FeedSlot() {
  return null;
}

export default async function Home() {
  await requireOwner();
  const supabase = await createClient();
  const today = localToday();
  await ensureRollover(supabase);
  const goals = await weekGoals(supabase, today);

  return (
    <>
      <header className="mb-10 lg:mb-14">
        <h1 className="font-display text-4xl text-text lg:text-display">{longDate(today)}</h1>
        <p className="mt-2 font-mono text-sm text-text-muted">{isoWeekLabel(today)}</p>
      </header>

      <MorningBriefSlot />

      <GoalLedger goals={goals} horizon="weekly" periodLabel={weekRange(weekStart(today))} />

      <FeedSlot />
    </>
  );
}
