// The Arsenal's shapes and labels (Chat 5 Phase 1; db/054). Shared by the
// server reads (data.ts) and the client cards.

export type Category =
  | "finance_business" | "history_military" | "film_tv" | "sports" | "music_culture" | "philosophy" | "literature_myth";
export type Room = "boardroom" | "pitch" | "negotiation" | "homies";
export type Verification = "verified" | "corrected" | "unverified" | "pending";
export type RefReaction = "love" | "not_interested" | "known";

export const CATEGORY_LABEL: Record<Category, string> = {
  finance_business: "Finance & business",
  history_military: "History & war",
  film_tv: "Film & TV",
  sports: "Sports",
  music_culture: "Music & culture",
  philosophy: "Philosophy",
  literature_myth: "Literature & myth",
};

export const ROOM_LABEL: Record<Room, string> = {
  boardroom: "Boardroom",
  pitch: "Pitch",
  negotiation: "Negotiation",
  homies: "Homies",
};

export type Ref = {
  id: number;
  category: Category;
  title: string;
  origin: string;
  meaning: string;
  deployLine: string;
  room: Room;
  sourceWork: string;
  quote: string | null;
  quoteBy: string | null;
  sourceUrl: string | null;
  verification: Verification;
  verificationNote: string | null;
  isTest: boolean;
  /** The latest reaction (null: none, or taken back). */
  reaction: RefReaction | null;
};

export type Word = {
  id: number;
  lemma: string;
  pronunciation: string | null;
  etymology: string | null;
  etymologyUrl: string | null;
  meaning: string;
  room: Room;
  sentence: string;
  verification: Verification;
  isTest: boolean;
  reaction: RefReaction | null;
};

export type Drop = {
  id: number;
  date: string;
  refs: Ref[];
  word: Word;
  isTest: boolean;
};

export type PastDrop = {
  id: number;
  date: string;
  refs: { id: number; title: string; category: Category; room: Room; sourceWork: string; deployLine: string }[];
  word: string;
  isTest: boolean;
};

export type UnverifiedRef = {
  id: number;
  title: string;
  category: Category;
  sourceWork: string;
  note: string | null;
  isTest: boolean;
};

const CREDIT = "as written in";
const TRAILING_PARENS = /\s*\([^()]*\)\s*$/;

/** The work a source_work names, without its trailing parentheticals:
 *  "The Social Network (2010)" -> "The Social Network". */
export function workTitle(sourceWork: string): string {
  let work = sourceWork.trim();
  for (let m = TRAILING_PARENS.exec(work); m && m.index > 0; m = TRAILING_PARENS.exec(work)) {
    work = work.slice(0, m.index).trim();
  }
  return work;
}

/** Who a quote is credited to on the card (owner, 2026-10-16). A film or TV
 *  quote credits the character AND the work, never the real person alone:
 *  "Sean Parker (as written in The Social Network)". Applied at render
 *  time, so cards written before the generator rule read the same way. The
 *  worker's twin is workers/arsenal/cards.py credit_film_quote. */
export function quoteCredit(category: Category, quoteBy: string | null, sourceWork: string): string | null {
  const speaker = (quoteBy ?? "").split(/\s+/).filter(Boolean).join(" ");
  if (!speaker) return null;
  if (category !== "film_tv") return speaker;
  const work = workTitle(sourceWork);
  const lower = speaker.toLowerCase();
  if (!work || lower.includes(CREDIT) || lower.includes(work.toLowerCase())) return speaker;
  return `${speaker} (${CREDIT} ${work})`;
}

/** "Thu, Oct 16" for a YYYY-MM-DD date (no timezone shift). */
export function dropDate(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(y, m - 1, d)));
}
