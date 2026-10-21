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
  from_arsenal: z.boolean(),
});
export type ArmCardIn = z.infer<typeof armCardSchema>;

const clean = (v: unknown) => String(v ?? "").split(/\s+/).filter(Boolean).join(" ");

/** cards.py normalize_key. */
export function normalizeKey(raw: unknown): string {
  return String(raw ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 80).replace(/_+$/, "");
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
      source_page: clean(item.source_page).slice(0, 200),
    },
    why: null,
  };
}

/** "The Godfather (1972) — the Arsenal key never_tell_anybody…" lines. */
export type ArsenalEntry = { key: string; title: string; kind: "seed" | "dropped" | "saved" };

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
    "Tsims describes a room he is about to walk into. Give him exactly THREE references that fit it, each a full card, best first.",
    "",
    "THE CATEGORIES",
    ...CATEGORIES.map((c) => `- ${c}: ${CATEGORY_NOTES[c]}`),
    "",
    `HIS TASTE (weights from his reactions and saves; 1.00 is neutral, higher is more of what he likes): ${weights}`,
    "",
    "HIS ARSENAL (references he already has: seeds, saved, dropped)",
    arsenal,
    "",
    "THE RULES",
    "- Well documented only: an English Wikipedia or Wikiquote page supports the origin. source_page names that page exactly.",
    "- Check the ending: if how it really ended undercuts the deploy line, reframe the line to fit what happened, or pick another reference.",
    "- A film or TV quote credits the character AND the work in quote_by. A quote is at most 15 words, exactly as said; when unsure, quote null.",
    "- source_work is always set.",
    "- canonical_key: snake_case naming the reference itself (crossing_the_rubicon).",
    "- At most ONE of the three may come from HIS ARSENAL: then use its key exactly and set from_arsenal true. Prefer fresh ones; from_arsenal is false for every other card.",
    "- The three should not repeat a category unless the room demands it.",
  ].join("\n");
}

/** The user turn: the situation, and the room when he picked one. */
export function armPrompt(situation: string, room: Room | null): string {
  return [
    `The room: ${situation.trim()}`,
    room ? `All three play in the ${room} room.` : "Pick the room each one plays best in.",
  ].join("\n");
}

export { ARSENAL_VOICE_VERSION };
