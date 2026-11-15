import { BettingNav } from "@/components/betting/betting-nav";
import { BettingStore } from "@/components/betting/betting-store";
import { requireOwner } from "@/lib/auth";

// /betting (Chat 8; Workstream B's page): the sections and the x / % switch
// on every view, and the slip's legs and the unit kept in this browser across
// them. Read-only: nothing here bets, and nothing ever bets automatically
// (CLAUDE.md §1).

export default async function BettingLayout({ children }: { children: React.ReactNode }) {
  await requireOwner();
  return (
    <BettingStore>
      <BettingNav />
      {children}
    </BettingStore>
  );
}
