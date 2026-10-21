import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { ARSENAL_VOICE, ARSENAL_VOICE_VERSION } from "../src/generated/arsenal-voice";
import {
  armPrompt, armProviderOptions, armSystem, cleanSourceUrl, FIRST_CARD_BAR_MS, normalizeKey, validateArmCard,
  type ArmCardIn,
} from "../src/lib/arsenal/arm";
import { parseArmLine } from "../src/lib/arsenal/arm-stream";
import {
  buildQuestions, MASK, maskTitle, nextCard, orderItems, quizSize, swapWord, weekStart, type QuizItem,
} from "../src/lib/arsenal/quiz";
import { keptCount, listWithHeld, savedKey } from "../src/lib/arsenal/saved-view";
import { checkTagKind, quoteCredit, saveLabel, workTitle } from "../src/lib/arsenal/types";

// The Arsenal's render-time quote credit (owner, 2026-10-16): a film or TV
// quote credits the character AND the work, on cards written before the
// generator rule too. The same cases as workers/tests/test_arsenal.py
// FilmQuoteCredit (cards.credit_film_quote). No page, no server.

test("a bare film or TV speaker gains the work", () => {
  expect(quoteCredit("film_tv", "Sean Parker", "The Social Network (2010)"))
    .toBe("Sean Parker (as written in The Social Network)");
  expect(quoteCredit("film_tv", "Logan Roy", "Succession (2018–2023)")).toBe("Logan Roy (as written in Succession)");
});

test("an already credited speaker is kept as it is", () => {
  expect(quoteCredit("film_tv", "Sean Parker (as written in The Social Network)", "The Social Network (2010)"))
    .toBe("Sean Parker (as written in The Social Network)");
  expect(quoteCredit("film_tv", "Gordon Gekko in Wall Street", "Wall Street (1987)")).toBe("Gordon Gekko in Wall Street");
});

test("outside film and TV, and with no speaker, nothing is added", () => {
  expect(quoteCredit("history_military", "Julius Caesar", "Julius Caesar, 49 BC")).toBe("Julius Caesar");
  expect(quoteCredit("film_tv", null, "Heat (1995)")).toBeNull();
  expect(quoteCredit("film_tv", "  ", "Heat (1995)")).toBeNull();
});

test("the work drops only trailing parentheticals", () => {
  expect(workTitle("Diamonds from Sierra Leone (Remix) (2005)")).toBe("Diamonds from Sierra Leone");
  expect(workTitle("(500) Days of Summer (2009)")).toBe("(500) Days of Summer");
  expect(workTitle("Heat")).toBe("Heat");
});

// ---------------------------------------------------------------- Phase 2 (docs/arsenal.md §9)

test("Arm Me speaks the pool's voice: the prompt carries prompts/arsenal_voice.md, version and all", () => {
  // Run from apps/web (pnpm test:unit).
  const file = readFileSync(join(process.cwd(), "..", "..", "prompts", "arsenal_voice.md"), "utf8").replace(/\r\n/g, "\n");
  const body = file.replace(/^<!--[\s\S]*?-->\s*/, "");
  expect(body.startsWith(`VOICE_VERSION: ${ARSENAL_VOICE_VERSION}\n`)).toBe(true);
  expect(body).toContain(ARSENAL_VOICE);
  const system = armSystem({ weights: { "category:film_tv": 1.3 }, arsenal: [{ key: "crossing_the_rubicon", title: "Crossing the Rubicon", kind: "seed" }] });
  expect(system).toContain(ARSENAL_VOICE);
  expect(system).toContain("category:film_tv 1.30");
  expect(system).toContain("- crossing_the_rubicon: Crossing the Rubicon (seed)");
  expect(system).toContain("At most ONE of the three may come from HIS ARSENAL");
  expect(armPrompt("  Board call on the raise.  ", "boardroom")).toBe("The room: Board call on the raise.\nAll three play in the boardroom room.");
});

test("Arm Me's thinking is set explicitly at its lowest: Wags' settings", () => {
  // src/lib/ai/claude.ts chatProviderOptions (server-only, so its values are pinned here).
  expect(armProviderOptions("claude-sonnet-5-5")).toEqual({ anthropic: { thinking: { type: "between_tools" }, effort: "low" } });
  expect(armProviderOptions("claude-haiku-5-5")).toEqual({ anthropic: { thinking: { type: "disabled" } } });
  expect(FIRST_CARD_BAR_MS).toBe(10_000);
});

const card = (over: Partial<ArmCardIn> = {}): ArmCardIn => ({
  canonical_key: "Crossing the Rubicon!", category: "history_military", title: "Crossing the Rubicon",
  origin: "Caesar crossed the Rubicon with one legion in 49 BC.", meaning: "No way back.",
  deploy_line: "Once we sign, we have crossed the Rubicon.", room: "boardroom", source_work: "Julius Caesar, 49 BC",
  quote: null, quote_by: null, source_kind: "wikipedia", source_page: "Crossing the Rubicon", source_url: null,
  from_arsenal: false, ...over,
});

test("a card is held to the pool's rules (cards.py validate_ref)", () => {
  expect(normalizeKey("Crossing the Rubicon!")).toBe("crossing_the_rubicon");
  expect(validateArmCard(card()).card?.canonical_key).toBe("crossing_the_rubicon");
  expect(validateArmCard(card({ origin: "Too short" })).why).toBe("origin missing or out of bounds");
  // A quote over 15 words goes; the card stays.
  const long = validateArmCard(card({ quote: "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen", quote_by: "X" })).card!;
  expect([long.quote, long.quote_by]).toEqual([null, null]);
  // A film or TV quote credits the character and the work.
  const film = validateArmCard(card({ category: "film_tv", source_work: "The Godfather (1972)",
    quote: "“Never tell anybody outside the family what you're thinking again.”", quote_by: "Vito Corleone" })).card!;
  expect([film.quote, film.quote_by]).toEqual(["Never tell anybody outside the family what you're thinking again.",
    "Vito Corleone (as written in The Godfather)"]);
});

test("the stream's lines parse one JSON object each, and junk is skipped", () => {
  expect(parseArmLine('{"type":"done","requestId":4,"note":null}')).toEqual({ type: "done", requestId: 4, note: null });
  expect(parseArmLine("")).toBeNull();
  expect(parseArmLine("{not json")).toBeNull();
});

test("tags: unchecked until saved, then checking; a held conflict is unverified; a seed is your canon", () => {
  expect(checkTagKind({ verification: "pending", status: "arm", saved: false })).toBe("unchecked");
  expect(checkTagKind({ verification: "pending", status: "arm", saved: true })).toBe("checking");
  expect(checkTagKind({ verification: "verified", status: "arm", saved: true, held: true })).toBe("unverified");
  expect(checkTagKind({ verification: "corrected" })).toBe("corrected");
  expect(checkTagKind({ verification: "pending", status: "seed" })).toBe("seed");
  expect(checkTagKind({ verification: "pending", status: "pool" })).toBeNull();
});

const NOW = new Date("2026-10-26T15:00:00Z");
const ref = (id: number, over: Partial<Extract<QuizItem, { kind: "ref" }>> = {}): QuizItem => ({
  kind: "ref", id, title: `Ref ${id}`, meaning: `Meaning ${id}.`, savedAt: "2026-10-20T00:00:00Z", due: null,
  lastReview: null, reviews: 0, ...over,
});
const SENTENCES = ["We hold the {} line here.", "The {} question decides it.", "Call it {} and move on.",
                   "Nobody doubts the {} part."];
const word = (id: number, lemma: string): QuizItem => ({
  kind: "word", id, lemma, sentence: SENTENCES[id % SENTENCES.length].replace("{}", lemma),
  savedAt: "2026-10-20T00:00:00Z", due: null, lastReview: null, reviews: 0,
});
const seeded = (seed = 7) => () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;

test("order: due first (oldest due), never-reviewed due from the save; then least recently reviewed", () => {
  const items = [
    ref(1, { due: "2026-11-30T00:00:00Z", lastReview: "2026-10-01T00:00:00Z", reviews: 2 }),
    ref(2, { due: "2026-10-25T00:00:00Z", lastReview: "2026-10-20T00:00:00Z", reviews: 1 }),
    ref(3),
    ref(4, { due: "2026-12-01T00:00:00Z", lastReview: "2026-09-01T00:00:00Z", reviews: 3 }),
  ];
  expect(orderItems(items, NOW).map((i) => i.id)).toEqual([3, 2, 4, 1]);
});

test("ten questions from five references: each asked both ways, four distinct options, the answer among them", () => {
  const items = [1, 2, 3, 4, 5].map((i) => ref(i));
  const qs = buildQuestions(items, { refs: [], words: [] }, NOW, seeded());
  expect(qs).toHaveLength(10);
  expect(quizSize(items)).toBe(10);
  expect(qs.map((q) => q.no)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  expect(qs.slice(0, 5).every((q) => q.kind === "fits") && qs.slice(5).every((q) => q.kind === "means")).toBe(true);
  for (const q of qs) {
    expect(new Set(q.options).size).toBe(4);
    const r = items.find((i) => i.id === q.ref_id)!;
    expect(q.options[q.answer]).toBe(q.kind === "fits" ? (r as { title: string }).title : (r as { meaning: string }).meaning);
  }
});

test("distractors fall back to dropped references, and a question without three is skipped", () => {
  const two = [ref(1), ref(2)];
  expect(buildQuestions(two, { refs: [], words: [] }, NOW, seeded())).toHaveLength(0);
  const pool = { refs: [10, 11, 12].map((i) => ({ id: i, title: `Dropped ${i}`, meaning: `Dropped meaning ${i}.` })), words: [] };
  expect(buildQuestions(two, pool, NOW, seeded())).toHaveLength(4);
});

test("a Power Word's wrong sentences are other words' sentences with it swapped in", () => {
  expect(swapWord("Incumbent advantage is real.", "incumbent", "asymmetric")).toBe("Asymmetric advantage is real.");
  expect(swapWord("No such word here.", "incumbent", "asymmetric")).toBeNull();
  const words = [word(1, "incumbent"), word(2, "provenance"), word(3, "fiduciary"), word(4, "optionality")];
  const q = buildQuestions(words, { refs: [], words: [] }, NOW, seeded())[0];
  expect(q.kind).toBe("sentence");
  const own = words.find((w) => w.id === q.word_id) as { sentence: string };
  expect(q.options[q.answer]).toBe(own.sentence);
  expect(q.options.filter((o) => o.includes(q.prompt))).toHaveLength(4);
  expect(new Set(q.options).size).toBe(4);
});

test("weeks start on Monday; FSRS: right is Good (due later), wrong is Again (due sooner)", () => {
  expect(weekStart("2026-10-26")).toBe("2026-10-26");
  expect(weekStart("2026-11-01")).toBe("2026-10-26");
  expect(weekStart("2026-10-21")).toBe("2026-10-19");
  const good = nextCard(null, true, NOW);
  const again = nextCard(null, false, NOW);
  expect(good.reps).toBe(1);
  expect(Date.parse(good.due)).toBeGreaterThan(Date.parse(again.due));
  const twice = nextCard(good, true, new Date(good.due));
  expect(twice.reps).toBe(2);
  expect(Date.parse(twice.due)).toBeGreaterThan(Date.parse(good.due));
});

// ---------------------------------------------------------------- the owner's pre-merge fixes (Oct 21)

test("My Arsenal: a card unsaved and then dropped by the refresh keeps its line (the missing Undo)", () => {
  type I = { kind: "ref" | "word"; id: number; savedAt: string; title: string };
  const a: I = { kind: "ref", id: 1, savedAt: "2026-10-21T10:00:00Z", title: "Rubicon" };
  const b: I = { kind: "ref", id: 2, savedAt: "2026-10-21T11:00:00Z", title: "Medici" };
  const w: I = { kind: "word", id: 1, savedAt: "2026-10-21T09:00:00Z", title: "optionality" };
  const held = new Map([[savedKey(a), a]]);
  // Before the refresh: the server still lists it. After: it does not; the held copy keeps it.
  expect(listWithHeld([b, a, w], held).map(savedKey)).toEqual(["ref:2", "ref:1", "word:1"]);
  expect(listWithHeld([b, w], held).map(savedKey)).toEqual(["ref:2", "ref:1", "word:1"]);
  expect(keptCount(listWithHeld([b, w], held), new Set(["ref:1"]))).toBe(2);
  // Undo: the server lists it again (a new save time); still one entry, now first.
  const again = { ...a, savedAt: "2026-10-21T12:00:00Z" };
  expect(listWithHeld([again, b, w], held).map((i) => `${savedKey(i)}@${i.savedAt.slice(11, 13)}`))
    .toEqual(["ref:1@12", "ref:2@11", "word:1@09"]);
  // A ref and a word with the same id are different targets.
  expect(listWithHeld([w], new Map([[savedKey(a), a]]))).toHaveLength(2);
});

test("the Save toggle reads Saved when saved", () => {
  expect(saveLabel(false)).toBe("Save");
  expect(saveLabel(true)).toBe("Saved");
});

test("Arm Me's Source link: the model's https URL is kept, anything else is dropped", () => {
  expect(cleanSourceUrl("https://en.wikipedia.org/wiki/Crossing_the_Rubicon")).toBe("https://en.wikipedia.org/wiki/Crossing_the_Rubicon");
  for (const bad of [null, "", "http://en.wikipedia.org/wiki/X", "javascript:alert(1)", "https://x y", `https://e.com/${"a".repeat(500)}`]) {
    expect(cleanSourceUrl(bad)).toBeNull();
  }
  expect(validateArmCard(card({ source_url: "https://en.wikiquote.org/wiki/The_Godfather" })).card?.source_url)
    .toBe("https://en.wikiquote.org/wiki/The_Godfather");
  expect(validateArmCard(card({ source_url: null })).card?.source_url).toBeNull();
  expect(armSystem({ weights: {}, arsenal: [] })).toContain("source_url is its https URL");
});

test("the quiz masks the title out of 'Which reference fits this?'", () => {
  expect(maskTitle("A Pyrrhic victory costs more than it wins.", "Pyrrhic victory")).toBe("A ____ costs more than it wins.");
  expect(maskTitle("Once over the Rubicon, there is no way back.", "Crossing the Rubicon")).toBe("Once over the ____, there is no way back.");
  expect(maskTitle("See's pricing power: raise every year.", "See's Candies (mock)")).toBe("____ pricing power: raise every year.");
  expect(maskTitle("You see the price rise.", "See's Candies")).toBe("You see the price rise.");
  expect(maskTitle("Keep your enemies closer than your friends.", "Keep your enemies closer")).toBe("____ than your friends.");
  // Common short words stay; an unrelated meaning is untouched.
  expect(maskTitle("No way back.", "The Rubicon")).toBe("No way back.");
  const items = [1, 2, 3, 4].map((i) => ref(i, { title: `Gambit ${i}`, meaning: `Play the gambit ${i} early.` }));
  for (const q of buildQuestions(items, { refs: [], words: [] }, NOW, seeded()).filter((x) => x.kind === "fits")) {
    expect(q.prompt).not.toMatch(/gambit/i);
    expect(q.prompt).toContain(MASK);
  }
});
