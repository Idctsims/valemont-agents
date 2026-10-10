import type { Metadata } from "next";
import { headers } from "next/headers";

import { CapitalView } from "@/components/capital/capital-view";
import { requireOwner } from "@/lib/auth";
import { capitalData } from "@/lib/capital/data";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { localToday } from "@/lib/goals/period";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Capital" };

export default async function CapitalPage() {
  await requireOwner();
  // Test entries (db/027) are shown only to a request whose e2e marker this
  // server verifies; a marker that does not verify is refused, loudly.
  const marker = decideMarker((await headers()).get(MARKER_HEADER), process.env);
  if (marker.kind === "refused") throw new Error(`Test marker refused: ${marker.reason}.`);
  const supabase = await createClient();
  return (
    <CapitalView data={await capitalData(supabase, { withTests: marker.kind === "test" })} today={localToday()} />
  );
}
