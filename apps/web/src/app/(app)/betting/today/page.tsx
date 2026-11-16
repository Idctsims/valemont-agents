import type { Metadata } from "next";

import { requireOwner } from "@/lib/auth";

export const metadata: Metadata = { title: "Betting · Today" };

// Today: the generated tickets of the day. They do not exist until Phase 4
// (the Parlay Generator), and no number is shown here that Workstream A has
// not signed off: the mock's example tickets are left out on purpose.

export default async function TodayPage() {
  await requireOwner();
  return (
    <section aria-labelledby="today-h">
      <p className="label-mono text-text-muted">Today&apos;s tickets</p>
      <h1 id="today-h" className="mt-3 font-display text-4xl text-text lg:text-display">Today</h1>
      <p data-testid="today-empty" className="mt-6 max-w-measure border-t border-border pt-4 text-lg text-text">
        Parlays drop with Phase 4.
      </p>
      <p className="mt-2 max-w-measure text-base text-text-muted text-pretty">
        Until then, build your own on the Board: open a game, tap prices into the slip and price it.
      </p>
    </section>
  );
}
