import type { Metadata } from "next";

import { headers } from "next/headers";
import { notFound } from "next/navigation";

import { BettingFrame } from "@/components/betting/frame";
import { GameView } from "@/components/betting/game-view";
import { requireOwner } from "@/lib/auth";
import { loadGameMeta, loadGameTab, loadGameTabs } from "@/lib/betting/data";
import { MARKER_HEADER, decideMarker } from "@/lib/capital/marker";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Betting · Game" };

// A game as its own page (Workstream B; db/079): the header, the three tabs'
// counts, and the Game lines tab rendered with the page; Players (one stat at
// a time) and Halves and quarters load when opened, each cached by the page.

const TICKER = /^[A-Za-z0-9][A-Za-z0-9._-]{1,100}$/;

export default async function GamePage({ params }: { params: Promise<{ game: string }> }) {
  await requireOwner();
  const game = decodeURIComponent((await params).game);
  if (!TICKER.test(game)) notFound();
  const supabase = await createClient();
  const withTests = decideMarker((await headers()).get(MARKER_HEADER), process.env).kind === "test";
  const [meta, tabs, lines] = await Promise.all([
    loadGameMeta(supabase, game, withTests),
    loadGameTabs(supabase, game, withTests),
    loadGameTab(supabase, game, "lines", null, withTests),
  ]);
  if (!meta) notFound();

  return (
    <BettingFrame>
      <GameView key={game} meta={meta} tabs={tabs} initial={lines} />
    </BettingFrame>
  );
}
