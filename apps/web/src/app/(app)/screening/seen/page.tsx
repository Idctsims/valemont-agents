import type { Metadata } from "next";

import { headers } from "next/headers";
import Link from "next/link";

import { SeenPassFlow } from "@/components/screening/seen-pass";
import { PageHeader } from "@/components/ui/page-header";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { seenPass } from "@/lib/screening/recs";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Seen it? · Screening Room" };

// The seen-it pass (Chat 4 Phase 4; db/053): resumes at the first title with
// no answer; `?again=1` (from Settings, or the end of the pass) starts over
// from the first title, answers kept and re-answerable. A test title shows
// only with the verified e2e marker.

type Params = { again?: string | string[] };

export default async function SeenPage({ searchParams }: { searchParams: Promise<Params> }) {
  await requireOwner();
  const supabase = await createClient();
  const params = await searchParams;
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const titles = await seenPass(supabase, { withTests });
  const again = (Array.isArray(params.again) ? params.again[0] : params.again) === "1";
  const firstOpen = titles.findIndex((t) => !t.answer);
  const start = again ? 0 : firstOpen === -1 ? titles.length : firstOpen;

  return (
    <>
      <PageHeader label="Screening" title="Seen it?"
                  summary="Titles near your favorites. Tell me which you've watched, so they never come up as picks." />
      {titles.length === 0 ? (
        <p data-testid="seen-none" className="text-base text-text-muted">
          The pass is made with the first recommendations run. <Link href="/screening" className="text-accent hover:text-text">Back</Link>
        </p>
      ) : (
        <SeenPassFlow key={again ? "again" : "resume"} titles={titles} start={start} />
      )}
    </>
  );
}
