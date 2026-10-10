import type { Metadata } from "next";
import { headers } from "next/headers";

import { BriefPage } from "@/components/brief/brief-page";
import { requireOwner } from "@/lib/auth";
import { briefFor, briefHistory } from "@/lib/brief/data";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { localToday } from "@/lib/goals/period";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Morning brief" };

// Today's brief (the latest row for today), then the history.
export default async function Page() {
  await requireOwner();
  const supabase = await createClient();
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const today = localToday();
  const [brief, history] = await Promise.all([briefFor(supabase, today, { withTests }), briefHistory(supabase, { withTests })]);
  return <BriefPage brief={brief} history={history} day={today} isToday />;
}
