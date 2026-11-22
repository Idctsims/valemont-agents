import { expect, test } from "@playwright/test";

import {
  MAX_CARDS, anchorKey, finalize, matchPick, parseAnchor, vibePrompt, vibeSchema, type Anchor, type Candidate, type VibePick,
} from "../src/lib/screening/vibe";

// The Screening Room's "More like…" (owner, Nov 21; docs/screening.md §8):
// the anchor key, matching Claude's picks to TMDB, and the final list.

const gladiator: Anchor = { kind: "movie", id: 98, label: "Gladiator", year: 2000 };

function cand(over: Partial<Candidate>): Candidate {
  return { tmdbId: 1, kind: "movie", name: "Troy", year: 2004, popularity: 10, posterPath: "/troy.jpg", ...over };
}
function pick(over: Partial<VibePick>): VibePick {
  return { title: "Troy", year: 2004, kind: "movie", reason: "Ancient epic, tragic heroes", ...over };
}

test.describe("the anchor key", () => {
  test("a title or a person by its TMDB id; text lowercased and collapsed", () => {
    expect(anchorKey(gladiator)).toBe("movie:98");
    expect(anchorKey({ kind: "tv", id: 1399, label: "Game of Thrones" })).toBe("tv:1399");
    expect(anchorKey({ kind: "person", id: 578, label: "Ridley Scott" })).toBe("person:578");
    expect(anchorKey({ kind: "text", label: "  90s   London ROM-COMS " })).toBe("text:90s london rom-coms");
    expect(anchorKey({ kind: "text", label: "x".repeat(300) })).toBe(`text:${"x".repeat(120)}`);
  });
});

test.describe("matching a pick on TMDB", () => {
  test("the exact year beats a more popular other year", () => {
    const hits = [cand({ tmdbId: 2, year: 2024, popularity: 99 }), cand({ tmdbId: 1, year: 2004 })];
    expect(matchPick(pick({}), hits)?.tmdbId).toBe(1);
  });

  test("a year off by one when there is no exact; the more popular of those", () => {
    const hits = [cand({ tmdbId: 3, year: 2005, popularity: 5 }), cand({ tmdbId: 4, year: 2003, popularity: 8 })];
    expect(matchPick(pick({}), hits)?.tmdbId).toBe(4);
  });

  test("nothing within a year is no match; nor another kind; nor another name", () => {
    expect(matchPick(pick({}), [cand({ year: 2010 })])).toBeNull();
    expect(matchPick(pick({}), [cand({ kind: "tv" })])).toBeNull();
    expect(matchPick(pick({}), [cand({ name: "Troy: Fall of a City" })])).toBeNull();
  });

  test("names compare without case or punctuation; no year asked takes the most popular", () => {
    expect(matchPick(pick({ title: "Ben Hur", year: 1959 }), [cand({ name: "Ben-Hur", year: 1959 })])).not.toBeNull();
    expect(matchPick(pick({ title: "bridget jones's diary", year: 2001 }), [cand({ name: "Bridget Jones’s Diary", year: 2001 })])).not.toBeNull();
    const hits = [cand({ tmdbId: 5, popularity: 1 }), cand({ tmdbId: 6, popularity: 50 })];
    expect(matchPick(pick({ year: null }), hits)?.tmdbId).toBe(6);
  });
});

test.describe("the final list", () => {
  test("unverified dropped, the anchor dropped, duplicates once, Claude's order kept, capped", () => {
    const rows = [
      { pick: pick({ title: "Troy" }), hit: cand({ tmdbId: 652, name: "Troy" }) },
      { pick: pick({ title: "Gladiator" }), hit: cand({ tmdbId: 98, name: "Gladiator", year: 2000 }) },
      { pick: pick({ title: "Made Up Epic" }), hit: null },
      { pick: pick({ title: "Kingdom of Heaven", reason: "Scott again" }), hit: cand({ tmdbId: 1495, name: "Kingdom of Heaven", year: 2005 }) },
      { pick: pick({ title: "Troy" }), hit: cand({ tmdbId: 652, name: "Troy" }) },
      { pick: pick({ title: "Rome", kind: "tv" }), hit: cand({ tmdbId: 652, kind: "tv", name: "Rome", year: 2005 }) },
    ];
    const out = finalize(rows, gladiator);
    expect(out.map((c) => `${c.kind}:${c.tmdbId}`)).toEqual(["movie:652", "movie:1495", "tv:652"]);
    expect(out[1]).toEqual({ tmdbId: 1495, kind: "movie", name: "Kingdom of Heaven", year: 2005, posterPath: "/troy.jpg", reason: "Scott again" });

    const many = Array.from({ length: 30 }, (_, i) => ({ pick: pick({}), hit: cand({ tmdbId: 1000 + i }) }));
    expect(finalize(many, gladiator)).toHaveLength(MAX_CARDS);
  });
});

test.describe("the prompt", () => {
  test("a title carries its year and kind; a person their department; text as typed", () => {
    expect(vibePrompt(gladiator)).toContain("Gladiator (2000)");
    expect(vibePrompt(gladiator)).toContain("film");
    expect(vibePrompt({ kind: "tv", id: 1, label: "Succession", year: 2018 })).toContain("series");
    expect(vibePrompt({ kind: "person", id: 578, label: "Ridley Scott", dept: "Directing" })).toContain("Ridley Scott (Directing)");
    expect(vibePrompt({ kind: "text", label: "90s London rom-coms" })).toContain("90s London rom-coms");
  });

  test("the schema takes a list of picks and refuses a bad kind", () => {
    expect(vibeSchema.safeParse({ picks: [pick({})] }).success).toBe(true);
    expect(vibeSchema.safeParse({ picks: [{ ...pick({}), kind: "book" }] }).success).toBe(false);
  });
});

test.describe("a request's anchor", () => {
  test("titles and people need an id; text two letters to 120; anything else is a sentence", () => {
    expect(parseAnchor({ kind: "movie", id: 98, label: "Gladiator", year: 2000 })).toEqual(
      { kind: "movie", id: 98, label: "Gladiator", year: 2000, dept: null });
    expect(parseAnchor({ kind: "person", id: "578", label: "Ridley Scott", dept: "Directing" })).toMatchObject({ id: 578, dept: "Directing" });
    expect(parseAnchor({ kind: "text", label: "  slow   heists " })).toEqual({ kind: "text", label: "slow heists" });
    expect(typeof parseAnchor({ kind: "movie", label: "Gladiator" })).toBe("string");
    expect(typeof parseAnchor({ kind: "book", id: 1, label: "Dune" })).toBe("string");
    expect(typeof parseAnchor({ kind: "text", label: "x" })).toBe("string");
    expect(typeof parseAnchor({ kind: "text", label: "x".repeat(121) })).toBe("string");
    expect(typeof parseAnchor(null)).toBe("string");
  });
});
