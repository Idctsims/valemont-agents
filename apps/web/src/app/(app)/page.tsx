import type { Metadata } from "next";

import { headers } from "next/headers";

import { BriefCard } from "@/components/brief/brief-card";
import { Latest } from "@/components/feed/latest";
import { GoalLedger } from "@/components/goals/goal-ledger";
import { TodayModule } from "@/components/ventures/today";
import { requireOwner } from "@/lib/auth";
import { briefFor } from "@/lib/brief/data";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { latestStories } from "@/lib/feed/data";
import { pageFromParam } from "@/lib/feed/types";
import { ensureRollover, weekGoals } from "@/lib/goals/data";
import { localToday, longDate, weekRange, weekStamp, weekStart } from "@/lib/goals/period";
import { createClient } from "@/lib/supabase/server";
import { ventureLinks, ventureToday } from "@/lib/ventures/data";

export const metadata: Metadata = { title: "Home" };

export default async function Home({ searchParams }: { searchParams: Promise<{ latest?: string | string[] }> }) {
  await requireOwner();
  const supabase = await createClient();
  const today = localToday();
  const page = pageFromParam((await searchParams).latest);
  await ensureRollover(supabase);
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const [goals, todayRows, links, brief, feed] = await Promise.all([
    weekGoals(supabase, today),
    ventureToday(supabase),
    ventureLinks(supabase),
    briefFor(supabase, today, { withTests }),
    latestStories(supabase, { pillar: "home", page, withTests }),
  ]);
  // Display only ("3h"): rendered per request.
  // eslint-disable-next-line react-hooks/purity
  const now = Date.now();

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

      <Latest stories={feed.stories} page={page} hasMore={feed.hasMore} now={now} />
    </>
  );
}
