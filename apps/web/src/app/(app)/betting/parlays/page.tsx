import type { Metadata } from "next";
import { headers } from "next/headers";

import { ParlaysView } from "@/components/betting/parlays-view";
import { requireOwner } from "@/lib/auth";
import { DAY_RE, chicagoToday, e2eParlayDay, loadParlayDay, loadParlayDays } from "@/lib/betting/parlay-data";
import { asStatus, asTier } from "@/lib/betting/parlays";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Betting · Parlays" };

// The Parlays tab (Chat 8 Phase 4.5; docs/parlays_page.md): the Parlay
// Generator's paper tickets for one Chicago day (?day=YYYY-MM-DD, default
// today), read through db/091's parlay_day. A verified e2e request may ask
// for ?day=e2e, the fixtures' own day. ?tier= and ?status= keep the filters
// (owner, Nov 25), and the day menu reads db/092's parlay_days.

export default async function ParlaysPage({ searchParams }: { searchParams: Promise<{ day?: string; tier?: string; status?: string }> }) {
  await requireOwner();
  const supabase = await createClient();
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const today = chicagoToday();
  const params = await searchParams;
  const asked = params.day;
  let day = asked && DAY_RE.test(asked) ? asked : today;
  if (asked === "e2e" && withTests) day = (await e2eParlayDay(supabase)) ?? today;
  let data;
  const daysP = loadParlayDays(supabase, withTests);
  try {
    data = await loadParlayDay(supabase, day, withTests);
  } catch (e) {
    console.error(`parlays: ${(e as Error).message}`);
    return (
      <p role="alert" data-testid="parlays-error" className="max-w-measure border-t border-border pt-4 text-lg text-text">
        The parlays didn&apos;t load. Try again in a moment.
      </p>
    );
  }
  // Keyed by day: a new day starts with its own first ticket selected (the desktop panel).
  return <ParlaysView key={data.day} day={data} today={today} days={await daysP}
                      tier={asTier(params.tier)} status={asStatus(params.status)} />;
}
