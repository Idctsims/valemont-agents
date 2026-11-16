import type { Metadata } from "next";

import { requireOwner } from "@/lib/auth";

export const metadata: Metadata = { title: "Betting · Record" };

// Record: the track record of committed tickets. Nothing is committed yet
// (commitments start with Phase 4 and Chat 9), so it says so; no figure is
// shown that Workstream A has not signed off (the mock's 0-0 band included).

export default async function RecordPage() {
  await requireOwner();
  return (
    <section aria-labelledby="record-h">
      <p className="label-mono text-text-muted">Track record</p>
      <h1 id="record-h" className="mt-3 font-display text-4xl text-text lg:text-display">Record</h1>
      <p data-testid="record-empty" className="mt-6 max-w-measure border-t border-border pt-4 text-lg text-text">
        No committed tickets yet.
      </p>
      <p className="mt-2 max-w-measure text-base text-text-muted text-pretty">
        The record starts with the first committed ticket, written before its games start and never edited after.
      </p>
    </section>
  );
}
