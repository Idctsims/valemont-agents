// The Monday quiz's logic (Chat 5 Phase 2; docs/arsenal.md §9.6). No AI and
// no server imports: e2e/arsenal-unit.spec.ts runs it. The page builds a
// week's quiz once from the saved items (data.ts ensureWeekQuiz) and stores
// it (db/059 arsenal_quizzes); each answer is one review row with the FSRS
// card ts-fsrs computes here (the scheduler only, default parameters).

import { createEmptyCard, fsrs, Rating, type Card, type CardInput } from "ts-fsrs";

export const QUIZ_SIZE = 10;
export const QUIZ_MIN_SAVED = 4;
const OPTIONS = 4;

export type QuestionKind = "fits" | "means" | "sentence";

/** A saved item, with its review history's latest point. */
export type QuizItem =
  | { kind: "ref"; id: number; title: string; meaning: string; savedAt: string; due: string | null;
      lastReview: string | null; reviews: number }
  | { kind: "word"; id: number; lemma: string; sentence: string; savedAt: string; due: string | null;
      lastReview: string | null; reviews: number };

/** What a distractor can be drawn from: other saved items first, then
 *  dropped references and words (the same world, real or test). */
export type Distractors = {
  refs: { id: number; title: string; meaning: string }[];
  words: { id: number; lemma: string; sentence: string }[];
};

/** One stored question (db/059 arsenal_quizzes.questions). `answer` never
 *  reaches the browser before the answer (QuestionView). */
export type Question = {
  no: number;
  kind: QuestionKind;
  ref_id?: number;
  word_id?: number;
  prompt: string;
  options: string[];
  answer: number;
};
export type QuestionView = Omit<Question, "answer">;

export const KIND_ASK: Record<QuestionKind, string> = {
  fits: "Which reference fits this?",
  means: "What does it mean?",
  sentence: "Pick the sentence that uses it right.",
};

/** Due first (a never-reviewed item is due from its save), oldest due
 *  first; then the rest, least recently reviewed first. */
export function orderItems(items: QuizItem[], now: Date): QuizItem[] {
  const due = (i: QuizItem) => Date.parse(i.due ?? i.savedAt);
  const last = (i: QuizItem) => (i.lastReview ? Date.parse(i.lastReview) : -Infinity);
  const isDue = (i: QuizItem) => due(i) <= now.getTime();
  return [...items].sort((a, b) => {
    if (isDue(a) !== isDue(b)) return isDue(a) ? -1 : 1;
    if (isDue(a)) return due(a) - due(b) || a.id - b.id;
    return last(a) - last(b) || a.id - b.id;
  });
}

/** min(10, 2 x saved references + saved words): the size the Brief announces. */
export function quizSize(items: QuizItem[]): number {
  const refs = items.filter((i) => i.kind === "ref").length;
  return Math.min(QUIZ_SIZE, 2 * refs + (items.length - refs));
}

function shuffle<T>(xs: T[], rng: () => number): T[] {
  const a = [...xs];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/** Up to n distinct strings, the saved pool's first (shuffled), then the
 *  rest (shuffled), never `not` itself. */
function pick(first: string[], rest: string[], not: string, n: number, rng: () => number): string[] {
  const out: string[] = [];
  const seen = new Set([not.trim().toLowerCase()]);
  for (const s of [...shuffle(first, rng), ...shuffle(rest, rng)]) {
    const k = s.trim().toLowerCase();
    if (!k || seen.has(k)) continue;
    seen.add(k);
    out.push(s);
    if (out.length === n) break;
  }
  return out;
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export const MASK = "____";
/** Short or common words are left: masking "the" would give nothing away but read badly. */
const MASK_SKIP = new Set(["the", "and", "with", "from", "that", "this", "your", "into", "over", "what", "when",
  "they", "them", "have", "just", "then", "than", "were", "will", "make", "made", "take", "took", "like", "about",
  "only", "never", "every", "first", "last", "more", "most", "some"]);

/**
 * "Which reference fits this?" asks with the card's meaning, which often
 * names the reference itself ("A Pyrrhic victory costs more than it wins").
 * The title is masked out of it (owner, Oct 21): the whole title, then each
 * distinctive word of it (4+ letters, not a common word, a possessive 's
 * included), with runs of masks collapsed to one. A trailing parenthetical
 * on the title ("(mock)", "(2010)") is not part of the name.
 */
export function maskTitle(text: string, title: string): string {
  const name = title.replace(/\s*\([^()]*\)\s*$/, "").trim();
  if (!name) return text;
  let out = text.replace(new RegExp(escape(name), "gi"), MASK);
  for (const raw of name.split(/[^\p{L}\p{N}'’]+/u)) {
    const word = raw.replace(/['’]s$/i, "");
    const possessive = word !== raw;
    if (MASK_SKIP.has(word.toLowerCase())) continue;
    // A short name masks only as the possessive the title wrote ("See's"),
    // so a plain "see" in the meaning stays.
    if (word.length < 4 && !(possessive && word.length >= 2)) continue;
    const tail = word.length < 4 ? "['’]s" : "(?:['’]s)?";
    out = out.replace(new RegExp(`(?<![\\p{L}\\p{N}])${escape(word)}${tail}(?![\\p{L}\\p{N}])`, "giu"), MASK);
  }
  return out.replace(new RegExp(`${MASK}(?:\\s+${MASK})+`, "g"), MASK);
}

/** Another word's sentence with this word swapped in for its own lemma, or
 *  null when that lemma is not in the sentence as written. The misuse is
 *  the distractor: the sentence was built around a different word. */
export function swapWord(sentence: string, ownLemma: string, lemma: string): string | null {
  const re = new RegExp(`\\b${escape(ownLemma)}\\b`, "i");
  const m = re.exec(sentence);
  if (!m) return null;
  const swapped = m[0][0] === m[0][0].toUpperCase() ? lemma[0].toUpperCase() + lemma.slice(1) : lemma;
  return sentence.slice(0, m.index) + swapped + sentence.slice(m.index + m[0].length);
}

function question(item: QuizItem, kind: QuestionKind, saved: QuizItem[], pool: Distractors,
                  rng: () => number): Omit<Question, "no"> | null {
  let right: string;
  let wrong: string[];
  let prompt: string;
  if (item.kind === "ref") {
    const others = saved.filter((s): s is Extract<QuizItem, { kind: "ref" }> => s.kind === "ref" && s.id !== item.id);
    const dropped = pool.refs.filter((r) => r.id !== item.id && !others.some((o) => o.id === r.id));
    const field = kind === "fits" ? "title" : "meaning";
    right = item[field];
    prompt = kind === "fits" ? maskTitle(item.meaning, item.title) : item.title;
    wrong = pick(others.map((o) => o[field]), dropped.map((d) => d[field]), right, OPTIONS - 1, rng);
  } else {
    const others = saved.filter((s): s is Extract<QuizItem, { kind: "word" }> => s.kind === "word" && s.id !== item.id);
    const dropped = pool.words.filter((w) => w.id !== item.id && !others.some((o) => o.id === w.id));
    const swap = (w: { lemma: string; sentence: string }) => swapWord(w.sentence, w.lemma, item.lemma);
    right = item.sentence;
    prompt = item.lemma;
    wrong = pick(others.map(swap).filter((s): s is string => !!s), dropped.map(swap).filter((s): s is string => !!s),
                 right, OPTIONS - 1, rng);
  }
  if (wrong.length < OPTIONS - 1) return null;
  const options = shuffle([right, ...wrong], rng);
  return {
    kind,
    ...(item.kind === "ref" ? { ref_id: item.id } : { word_id: item.id }),
    prompt,
    options,
    answer: options.indexOf(right),
  };
}

/**
 * The week's questions: pass 1 asks each item once (a reference alternates
 * "fits" and "means" by its review count; a word is always "sentence");
 * with fewer than 10 items, pass 2 asks each reference its other kind. A
 * question with fewer than 3 distractors is skipped.
 */
export function buildQuestions(items: QuizItem[], pool: Distractors, now: Date,
                               rng: () => number = Math.random): Question[] {
  const ordered = orderItems(items, now);
  const out: Omit<Question, "no">[] = [];
  const firstKind = (i: QuizItem): QuestionKind => (i.kind === "word" ? "sentence" : i.reviews % 2 === 0 ? "fits" : "means");
  for (const item of ordered) {
    if (out.length === QUIZ_SIZE) break;
    const q = question(item, firstKind(item), items, pool, rng);
    if (q) out.push(q);
  }
  for (const item of ordered) {
    if (out.length === QUIZ_SIZE) break;
    if (item.kind !== "ref") continue;
    const q = question(item, firstKind(item) === "fits" ? "means" : "fits", items, pool, rng);
    if (q) out.push(q);
  }
  return out.map((q, i) => ({ no: i + 1, ...q }));
}

/** The Monday (YYYY-MM-DD) on or before a YYYY-MM-DD day. */
export function weekStart(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7));
  return t.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------- FSRS

// Made on first use, so a page that only shows questions never builds it.
let scheduler: ReturnType<typeof fsrs> | null = null;

/** ts-fsrs's Card as stored (dates as ISO strings). */
export type StoredCard = Omit<Card, "due" | "last_review"> & { due: string; last_review?: string | null };

export function toStored(card: Card): StoredCard {
  return { ...card, due: card.due.toISOString(), last_review: card.last_review ? card.last_review.toISOString() : null };
}

/** The card after one answer: right is Good, wrong is Again. */
export function nextCard(previous: StoredCard | null, correct: boolean, now: Date): StoredCard {
  const card: Card | CardInput = previous ? { ...previous, last_review: previous.last_review ?? undefined } : createEmptyCard(now);
  scheduler ??= fsrs();
  return toStored(scheduler.next(card, now, correct ? Rating.Good : Rating.Again).card);
}

export const ratingOf = (correct: boolean) => (correct ? "good" : "again") as "good" | "again";
