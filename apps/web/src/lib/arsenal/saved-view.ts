// My Arsenal's list as the page shows it (Chat 5 Phase 2). Pure, so
// e2e/arsenal-unit.spec.ts runs it.
//
// Unsave refreshes the page, and the server's list no longer holds the
// card, so the page keeps every card it removed (`held`) until it is left:
// its "Removed · Undo" line must survive the refresh (the owner's run of
// arsenal-saved.spec, Oct 21: the card and its Undo vanished).

export type SavedKeyed = { kind: "ref" | "word"; id: number; savedAt: string };

export const savedKey = (i: { kind: string; id: number }) => `${i.kind}:${i.id}`;

/** The server's items plus the held ones it dropped, newest save first; one entry per target. */
export function listWithHeld<T extends SavedKeyed>(items: T[], held: Map<string, T>): T[] {
  const out = new Map<string, T>();
  for (const i of items) out.set(savedKey(i), i);
  for (const [k, i] of held) if (!out.has(k)) out.set(k, i);
  return [...out.values()].sort((a, b) => b.savedAt.localeCompare(a.savedAt));
}

/** How many are still saved: everything listed that is not removed. */
export function keptCount<T extends SavedKeyed>(all: T[], removed: Set<string>): number {
  return all.filter((i) => !removed.has(savedKey(i))).length;
}
