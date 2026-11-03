// The Screening Room hook's shapes (Chat 5 Phase 3; db/062). Pure, shared by
// the server read (hook.ts) and the title page's client section, so
// e2e/arsenal-unit.spec.ts runs hookBusy.

import type { Ref, RefStatus, Room, Verification } from "./types";

export type HookCard = Ref & { status: RefStatus; held: boolean; heldNote: string | null };
export type HookWord = {
  id: number; lemma: string; pronunciation: string | null; meaning: string; room: Room; sentence: string;
  verification: Verification; verificationNote: string | null; saved: boolean; isTest: boolean;
};
export type TitleHook = {
  status: "queued" | "drafted" | "failed";
  note: string | null;
  cards: HookCard[];
  word: HookWord | null;
};

/** Still being written or checked: the page refreshes itself until it settles. */
export function hookBusy(h: TitleHook | null): boolean {
  if (!h) return false;
  if (h.status === "queued") return true;
  return h.status === "drafted"
    && (h.cards.some((c) => c.verification === "pending") || h.word?.verification === "pending");
}
