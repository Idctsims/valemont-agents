import type { Metadata } from "next";

import { headers } from "next/headers";

import { DropCards } from "@/components/arsenal/drop-cards";
import { Fold } from "@/components/ui/fold";
import { PageHeader } from "@/components/ui/page-header";
import { pastDrops, todayDrop, unverifiedRefs } from "@/lib/arsenal/data";
import { CATEGORY_LABEL, ROOM_LABEL, dropDate } from "@/lib/arsenal/types";
import { requireOwner } from "@/lib/auth";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "The Arsenal" };

// The Arsenal (Chat 5 Phase 1; docs/arsenal.md, db/054).
//   Today        the Power Word, then three references in three categories,
//                each with where it comes from, what it means, the line to
//                deploy and its room; Checked or Corrected against its source.
//   Past drops   the last two weeks, newest first.
//   Unverified   cards the verifier could not stand behind, with the reason.
//                They never drop and are never styled like a checked card.
// A test row shows only to a request with the verified e2e marker.

export default async function ArsenalPage() {
  await requireOwner();
  const supabase = await createClient();
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const [today, past, unverified] = await Promise.all([
    todayDrop(supabase, { withTests }), pastDrops(supabase, { withTests }), unverifiedRefs(supabase, { withTests }),
  ]);

  return (
    <>
      <PageHeader label="Culture" title="The Arsenal"
                  summary="Three references and a Power Word a day, each checked against its source, with the line to use and the room it plays in." />
      {today ? (
        <DropCards key={today.id} word={today.word} refs={today.refs} />
      ) : (
        <p data-testid="arsenal-empty" className="border-t border-border pt-4 text-base text-text-muted lg:max-w-3xl">
          Today&apos;s drop lands at 05:30.
        </p>
      )}

      <div className="mt-10 lg:max-w-3xl">
        <Fold label="Past drops" count={past.length} testId="arsenal-past" lockWhenEmpty>
          {past.map((d) => (
            <li key={d.id} data-testid="past-drop" className="border-b border-border py-3">
              <p className="label-mono text-text-muted">
                {dropDate(d.date)} · <span className="normal-case tracking-normal text-text">{d.word}</span>
                {d.isTest && <span className="ml-2 rounded-pill border border-text-muted px-2 normal-case tracking-normal text-text">test</span>}
              </p>
              <ul className="mt-1">
                {d.refs.map((r) => (
                  <li key={r.id} className="mt-1">
                    <p className="text-base text-text">{r.title}</p>
                    <p className="text-sm text-text-muted">
                      {CATEGORY_LABEL[r.category]} · {ROOM_LABEL[r.room]} · {r.sourceWork}
                    </p>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </Fold>
        <Fold label="Unverified" count={unverified.length} testId="arsenal-unverified" lockWhenEmpty>
          {unverified.map((u) => (
            <li key={u.id} data-testid="unverified-ref" className="border-b border-border py-3">
              <p className="text-base text-text-muted">
                {u.title}
                {u.isTest && <span className="ml-2 rounded-pill border border-text-muted px-2 font-mono text-xs text-text">test</span>}
              </p>
              <p className="text-sm text-text-muted">{CATEGORY_LABEL[u.category]} · {u.sourceWork}</p>
              <p data-testid="unverified-reason" className="mt-1 text-sm text-danger">
                Not checked: {u.note ?? "no reason recorded"}
              </p>
            </li>
          ))}
        </Fold>
      </div>
    </>
  );
}
