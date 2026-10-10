import type { Metadata } from "next";
import { headers } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { BriefPage } from "@/components/brief/brief-page";
import { requireOwner } from "@/lib/auth";
import { briefFor, briefHistory } from "@/lib/brief/data";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { localToday } from "@/lib/goals/period";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Morning brief" };

// One past brief by date (YYYY-MM-DD), with the history beneath. Today's
// date goes to /brief.
export default async function Page({ params }: { params: Promise<{ date: string }> }) {
  await requireOwner();
  const { date } = await params;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(`${date}T00:00:00Z`))) notFound();
  const today = localToday();
  if (date === today) redirect("/brief");
  const supabase = await createClient();
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const [brief, history] = await Promise.all([briefFor(supabase, date, { withTests }), briefHistory(supabase, { withTests })]);
  return <BriefPage brief={brief} history={history} day={date} isToday={false} />;
}
