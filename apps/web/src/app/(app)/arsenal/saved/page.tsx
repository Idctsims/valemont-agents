import type { Metadata } from "next";

import { headers } from "next/headers";

import { ArsenalNav } from "@/components/arsenal/arsenal-nav";
import { SavedList } from "@/components/arsenal/saved-list";
import { PageHeader } from "@/components/ui/page-header";
import { mySaved } from "@/lib/arsenal/saved";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "My Arsenal" };

// My Arsenal (Chat 5 Phase 2; docs/arsenal.md §9.4, db/059): what the
// owner saved, from the drops, the Power Words and Arm Me, with each
// card's verification. Test saves show only with the verified e2e marker.

export default async function MyArsenalPage() {
  await requireOwner();
  const supabase = await createClient();
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const items = await mySaved(supabase, { withTests });
  return (
    <>
      <PageHeader label="Culture · The Arsenal" title="My Arsenal"
                  summary="Every reference and Power Word you saved, ready for the room and for Monday's quiz.">
        <ArsenalNav current="saved" />
      </PageHeader>
      <SavedList items={items} />
    </>
  );
}
