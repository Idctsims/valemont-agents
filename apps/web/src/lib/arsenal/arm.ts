// Arm Me's pure pieces (Chat 5 Phase 2; docs/arsenal.md §9.3): the card's
// shape and its validation (the web twin of workers/arsenal/cards.py
// validate_ref: the same key normalization, bounds and quote rules, so a
// card means the same thing whoever wrote it), the prompt, and the stream's
// line format. No server imports: e2e/arsenal-unit.spec.ts runs these.

import { z } from "zod";

import { ARSENAL_VOICE, ARSENAL_VOICE_VERSION } from "@/generated/arsenal-voice";

import { CATEGORIES, ROOMS, quoteCredit, type Category, type Room } from "./types";

export { CATEGORIES, ROOMS };
export { MAX_SITUATION, parseArmLine, type ArmCard, type ArmLine } from "./arm-stream";
import type { ArmCard } from "./arm-stream";
export const SOURCE_KINDS = ["wikipedia", "wikiquote", "other"] as const;

export const MAX_QUOTE_WORDS = 15;
const KEY = /^[a-z0-9][a-z0-9_]{1,79}$/;
/** db/054's CHECKs (cards.py BOUNDS). */
const BOUNDS = {
  title: [2, 120], origin: [10, 280], meaning: [5, 300], deploy_line: [5, 220], source_work: [2, 160],
} as const;

/** What each category means to the model (cards.py CATEGORY_NOTES). */
const CATEGORY_NOTES: Record<Category, string> = {
  finance_business: "deals, investors, operators, companies, markets",
  history_military: "battles, generals, empires, turning points",
  film_tv: "films and series the room has seen (power, money, crime, deals)",
  sports: "coaches, athletes, dynasties, comebacks",
  music_culture: "artists, labels, moguls, moments in culture",
  philosophy: "Stoics and strategists, applied only, never a seminar",
  literature_myth: "only when instantly recognizable",
};

/** One card as the model writes it (the structured output's element). */
export const armCardSchema = z.object({
  canonical_key: z.string(),
  category: z.enum(CATEGORIES),
  title: z.string(),
  origin: z.string(),
  meaning: z.string(),
  deploy_line: z.string(),
  room: z.enum(ROOMS),
  source_work: z.string(),
  quote: z.string().nullable(),
  quote_by: z.string().nullable(),
  source_kind: z.enum(SOURCE_KINDS),
  source_page: z.string(),
  /** The https URL of source_page, or null (owner, Oct 21: the card's Source link). */
  source_url: z.string().nullable(),
  from_arsenal: z.boolean(),
});
export type ArmCardIn = z.infer<typeof armCardSchema>;

/** The owner's speed bar (2026-11-03, replacing the dropped 10 s target):
 *  "well under a minute". A request slower than this says so in its row. */
export const SLOW_REQUEST_MS = 60_000;

/** Cards shown, always (owner, 2026-11-03: never 2). */
export const SHOW_CARDS = 3;
/** Cards asked for: one spare fills a suppressed slot. */
export const ASK_CARDS = 4;
/** The fill-up call asks for this many more than are missing. */
export const FILL_SPARE = 1;
/** HIS ARSENAL in the prompt: the most relevant entries only (owner,
 *  2026-11-03: ~30 titles and keys; it was all ~90, ~1,750 tokens). */
export const ARSENAL_CAP = 30;

/**
 * Arm Me's thinking, at its lowest, set explicitly (owner, Oct 21; Wags'
 * settings, src/lib/ai/claude.ts chatProviderOptions): Sonnet 5.5 cannot turn
 * thinking off, so `between_tools` (no upfront thinking) at effort `low`;
 * Haiku 5.5, the 80% downgrade, has thinking `disabled`.
 */
export function armProviderOptions(modelId: string) {
  if (modelId === "claude-haiku-5-5") return { anthropic: { thinking: { type: "disabled" as const } } };
  return { anthropic: { thinking: { type: "between_tools" as const }, effort: "low" as const } };
}

const clean =(v: unknown) => String(v ?? "").split(/\s+/).filter(Boolean).join(" ");

/** cards.py normalize_key. */
export function normalizeKey(raw: unknown): string {
  return String(raw ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 80).replace(/_+$/, "");
}

/** The model's source link, kept only as a plain https URL (db/054's
 *  source_url CHECK: https, at most 500 characters); else null. */
export function cleanSourceUrl(raw: unknown): string | null {
  const url = String(raw ?? "").trim();
  return /^https:\/\/[^\s"'<>]+$/.test(url) && url.length <= 500 ? url : null;
}

/** cards.py clean_quote: no wrapping quotation marks; null when absent or too long. */
export function cleanQuote(raw: unknown): string | null {
  const q = clean(raw).replace(/^["“”'‘’ ]+|["“”'‘’ ]+$/g, "");
  if (!q || q.split(" ").length > MAX_QUOTE_WORDS || q.length > 160) return null;
  return q;
}

/** The card to store, or why it is left out (cards.py validate_ref). A
 *  film or TV quote credits the character and the work, or goes. */
export function validateArmCard(item: ArmCardIn): { card: ArmCardIn; why: null } | { card: null; why: string } {
  const key = normalizeKey(item.canonical_key) || normalizeKey(item.title);
  if (!KEY.test(key)) return { card: null, why: `bad canonical_key ${JSON.stringify(item.canonical_key)}` };
  const fields = Object.fromEntries(
    (Object.keys(BOUNDS) as (keyof typeof BOUNDS)[]).map((f) => {
      const text = clean(item[f]);
      const [lo, hi] = BOUNDS[f];
      return [f, text.length >= lo && text.length <= hi ? text : null];
    }),
  ) as Record<keyof typeof BOUNDS, string | null>;
  const bad = Object.entries(fields).filter(([, v]) => v === null).map(([f]) => f);
  if (bad.length) return { card: null, why: `${bad.join(", ")} missing or out of bounds` };
  let quote = cleanQuote(item.quote);
  let quoteBy = quote ? clean(item.quote_by).slice(0, 80) || null : null;
  if (quote && item.category === "film_tv") {
    const credited = quoteCredit("film_tv", item.quote_by, fields.source_work!);
    quoteBy = credited && credited.length <= 80 ? credited : null;
    if (!quoteBy) quote = null;
  }
  return {
    card: {
      ...item, canonical_key: key, title: fields.title!, origin: fields.origin!, meaning: fields.meaning!,
      deploy_line: fields.deploy_line!, source_work: fields.source_work!, quote, quote_by: quoteBy,
      source_page: clean(item.source_page).slice(0, 200), source_url: cleanSourceUrl(item.source_url),
    },
    why: null,
  };
}

/** "The Godfather (1972) — the Arsenal key never_tell_anybody…" lines. */
export type ArsenalEntry = {
  key: string;
  title: string;
  kind: "seed" | "dropped" | "saved";
  category?: Category;
  room?: Room;
  /** arsenal_refs.id: newer breaks ties. */
  id?: number;
};

const STOP = new Set(["the", "and", "for", "with", "that", "this", "from", "into", "about", "your", "have", "will",
  "what", "when", "they", "them", "their", "then", "than", "been", "were", "just", "call", "meeting"]);

function words(text: string): Set<string> {
  return new Set(text.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length >= 4 && !STOP.has(w)));
}

/**
 * The ARSENAL_CAP entries most relevant to this request, best first (owner,
 * 2026-11-03). Score: 3 per word shared with the situation (title and key,
 * words of 4+ letters), +2 saved (he chose it), +1 in the room he picked,
 * plus the category's weight (his taste). Ties: newer first. A key left out
 * is not lost: if the model writes it anyway, the database returns it as
 * his own ("From your Arsenal") and ArmSlate counts it as one.
 */
export function rankArsenal(entries: ArsenalEntry[], situation: string, weights: Record<string, number>,
  room: Room | null, cap: number = ARSENAL_CAP): ArsenalEntry[] {
  const want = words(situation);
  const score = (e: ArsenalEntry) => {
    let s = 0;
    for (const w of words(`${e.title} ${e.key.replace(/_/g, " ")}`)) if (want.has(w)) s += 3;
    if (e.kind === "saved") s += 2;
    if (room && e.room === room) s += 1;
    return s + (e.category ? weights[`category:${e.category}`] ?? 1 : 1);
  };
  return entries
    .map((e) => ({ e, s: score(e) }))
    .sort((a, b) => b.s - a.s || (b.e.id ?? 0) - (a.e.id ?? 0))
    .slice(0, cap)
    .map((x) => x.e);
}

/**
 * Which cards are shown (owner, 2026-11-03: always SHOW_CARDS, never 2). Pure,
 * so e2e/arsenal-unit.spec.ts replays requests 28 and 29. Every card left out
 * says why in the request's note: a spare not needed, a duplicate, or a
 * second "From your Arsenal" card (at most one is shown).
 */
export class ArmSlate {
  readonly shown: ArmCard[] = [];
  readonly notes: string[] = [];

  constructor(readonly size: number = SHOW_CARDS) {}

  get full(): boolean {
    return this.shown.length >= this.size;
  }

  get missing(): number {
    return Math.max(this.size - this.shown.length, 0);
  }

  /** The keys a fill-up call must not write again. */
  keys(): string[] {
    return this.shown.map((c) => c.canonical_key);
  }

  /** A stored card: true when it is shown. */
  offer(card: ArmCard): boolean {
    if (this.full) {
      this.notes.push(`a spare card (${card.canonical_key}) was not needed`);
      return false;
    }
    if (this.shown.some((c) => c.id === card.id)) {
      this.notes.push(`a duplicate (${card.canonical_key}) was left out`);
      return false;
    }
    if (card.in_arsenal && this.shown.some((c) => c.in_arsenal)) {
      this.notes.push(`a second From-your-Arsenal card (${card.canonical_key}) was not shown`);
      return false;
    }
    this.shown.push(card);
    return true;
  }

  /** A card that never reached the database (invalid, not stored). */
  skip(why: string): void {
    this.notes.push(why);
  }
}

/**
 * The system prompt: the voice (the pool's own, generated from
 * prompts/arsenal_voice.md), the categories, the weights, the owner's
 * Arsenal, and Arm Me's rules. The voice already holds the four rooms with
 * their real contexts and the card fields.
 */
export function armSystem(opts: { weights: Record<string, number>; arsenal: ArsenalEntry[] }): string {
  const weights = Object.entries(opts.weights)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, w]) => `${k} ${w.toFixed(2)}`)
    .join(", ");
  const arsenal = opts.arsenal.length
    ? opts.arsenal.map((e) => `- ${e.key}: ${e.title} (${e.kind})`).join("\n")
    : "- (empty)";
  return [
    ARSENAL_VOICE,
    "",
    "ARM ME",
    `Tsims describes a room he is about to walk into. Give him exactly ${ASK_CARDS} references that fit it, each a full card, best first. He sees the first three that pass; the fourth is the spare.`,
    "",
    "THE CATEGORIES",
    ...CATEGORIES.map((c) => `- ${c}: ${CATEGORY_NOTES[c]}`),
    "",
    `HIS TASTE (weights from his reactions and saves; 1.00 is neutral, higher is more of what he likes): ${weights}`,
    "",
    "HIS ARSENAL (the ones he already has that fit best: seeds, saved, dropped; he has more)",
    arsenal,
    "",
    "THE RULES",
    "- Well documented only: an English Wikipedia or Wikiquote page supports the origin. source_page names that page exactly; source_url is its https URL (https://en.wikipedia.org/wiki/... or https://en.wikiquote.org/wiki/...), or null when you are not sure of it.",
    "- Check the ending: if how it really ended undercuts the deploy line, reframe the line to fit what happened, or pick another reference.",
    "- A film or TV quote credits the character AND the work in quote_by. A quote is at most 15 words, exactly as said; when unsure, quote null.",
    "- source_work is always set.",
    "- canonical_key: snake_case naming the reference itself (crossing_the_rubicon).",
    "- At most ONE card may come from HIS ARSENAL: then use its key exactly and set from_arsenal true. Prefer fresh ones; from_arsenal is false for every other card. A well-known reference he likely already has (a famous film scene, a classic deal) counts as his: at most one of those too.",
    "- The cards should not repeat a category unless the room demands it.",
  ].join("\n");
}

/** The fill-up turn: `count` fresh cards, none of `avoid` (owner, 2026-11-03:
 *  a suppressed slot is filled, never left empty). */
export function fillPrompt(situation: string, room: Room | null, count: number, avoid: string[]): string {
  return [
    armPrompt(situation, room),
    `Give exactly ${count} more, each fresh: from_arsenal false, nothing he already has, and none of these keys: ${avoid.join(", ") || "(none)"}.`,
  ].join("\n");
}

/** The user turn: the situation, and the room when he picked one. */
export function armPrompt(situation: string, room: Room | null): string {
  return [
    `The room: ${situation.trim()}`,
    room ? `All of them play in the ${room} room.` : "Pick the room each one plays best in.",
  ].join("\n");
}

export { ARSENAL_VOICE_VERSION };
