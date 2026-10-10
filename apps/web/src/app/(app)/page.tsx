import type { Metadata } from "next";

import { headers } from "next/headers";

import { BriefCard } from "@/components/brief/brief-card";
import { GoalLedger } from "@/components/goals/goal-ledger";
import { TodayModule } from "@/components/ventures/today";
import { requireOwner } from "@/lib/auth";
import { briefFor } from "@/lib/brief/data";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { ensureRollover, weekGoals } from "@/lib/goals/data";
import { localToday, longDate, weekRange, weekStamp, weekStart } from "@/lib/goals/period";
import { createClient } from "@/lib/supabase/server";
import { ventureLinks, ventureToday } from "@/lib/ventures/data";

export const metadata: Metadata = { title: "Home" };

/** Personalized feed (Chat 3). Renders nothing until the feed exists. */
function FeedSlot() {
  return null;
}

export default async function Home() {
  await requireOwner();
  const supabase = await createClient();
  const today = localToday();
  await ensureRollover(supabase);
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const [goals, todayRows, links, brief] = await Promise.all([
    weekGoals(supabase, today),
    ventureToday(supabase),
    ventureLinks(supabase),
    briefFor(supabase, today, { withTests }),
  ]);

  return (
    <>
      <header className="mb-10 lg:mb-14">
        <h1 className="font-display text-4xl text-text lg:text-display">{longDate(today)}</h1>
        <p className="mt-2 font-mono text-sm text-text-muted">{weekStamp(today)}</p>
      </header>

      {/* The Morning Brief, above TODAY, once today's brief exists. */}
      {brief && <BriefCard brief={brief} />}

      <div className="lg:max-w-3xl">
        {/* Venture dates due today or earlier; renders nothing when none are. */}
        <TodayModule rows={todayRows} />
        <GoalLedger
          goals={goals}
          horizon="weekly"
          periodLabel={weekRange(weekStart(today))}
          ventures={links.options}
          ventureNames={links.names}
        />
      </div>

      <FeedSlot />
    </>
  );
}
