import { expect, test } from "@playwright/test";

import {
  ageText, cents, costText, edgeText, parseSingles, rowWords, singlesHref, stale, toLeg, type SingleRow,
} from "../src/lib/betting/singles";

// The Props tab's pure logic (owner, Nov 25; docs/props_tab.md): the URL, a row
// in words, the price after fee, the edge worded for what it is, a row as a slip
// leg. No page, no server.

const prop = (o: Partial<SingleRow> = {}): SingleRow => ({
  m: "KXNBAPTS-26NOV25MINOKC-OKCSGA2-30", side: "yes", k: "prop", label: "Shai Gilgeous-Alexander: 30+",
  who: "Shai Gilgeous-Alexander", stat: "points", line: 29.5, team: "OKC", game: "MIN Timberwolves at OKC Thunder",
  start: 1795653000, ask: 0.62, fee: 0.016492, p: 0.61, edge: -0.026492, mult: 1.569, tier: "kalshi",
  ya: 0.62, na: 0.4, age: 300, ...o,
});

test.describe("the URL", () => {
  test("defaults: Props, Safer, best value, the first page; junk falls back", () => {
    expect(parseSingles({})).toEqual({ sport: undefined, kind: "prop", tier: "safer", q: undefined, sort: "edge", offset: 0 });
    expect(parseSingles({ kind: "line", tier: "lotto", sort: "mult", offset: "100", q: "  curry ", sport: "Basketball" }))
      .toEqual({ sport: "Basketball", kind: "line", tier: "lotto", q: "curry", sort: "mult", offset: 100 });
    expect(parseSingles({ kind: "x", tier: "huge", sort: "y", offset: "-3" })).toMatchObject({ kind: "prop", tier: "safer", sort: "edge", offset: 0 });
  });

  test("defaults are left out; a filter change goes back to the first page; paging keeps the rest", () => {
    const q = parseSingles({ sport: "Basketball", tier: "mid", offset: "50" });
    expect(singlesHref(parseSingles({}))).toBe("/betting/props");
    expect(singlesHref(q, { kind: "line" })).toBe("/betting/props?sport=Basketball&kind=line&tier=mid");
    expect(singlesHref(q, { offset: 100 })).toBe("/betting/props?sport=Basketball&tier=mid&offset=100");
    expect(singlesHref(q, { q: "edwards" })).toBe("/betting/props?sport=Basketball&tier=mid&q=edwards");
  });
});

test.describe("a row", () => {
  test("in words, through the Parlays page's phrasebook: never a ticker", () => {
    expect(rowWords(prop())).toEqual({ title: "Shai Gilgeous-Alexander", detail: "30+ pts", game: "MIN Timberwolves at OKC Thunder" });
    expect(rowWords(prop({ side: "no" })).detail).toBe("29 or fewer pts");
    const total = prop({ k: "game_line", who: undefined, stat: "total", line: 224.5, label: "Over 224.5 points scored", per: "1H" });
    expect(rowWords(total).title).toBe("1st half: Over 224.5");
  });

  test("the price after the fee, in either unit", () => {
    expect(costText(prop(), "x")).toBe("1.57×");
    expect(costText(prop(), "pct")).toBe("63.6%");
  });

  test("the edge against an outside price; on Kalshi's own price only the cost to trade", () => {
    expect(edgeText(prop({ tier: "books", edge: 0.0213 }))).toBe("+2.1¢ vs books");
    expect(edgeText(prop({ tier: "espn", edge: -0.004 }))).toBe("−0.4¢ vs ESPN");
    expect(edgeText(prop())).toBe("costs 2.6¢ to trade");
    expect(edgeText(prop({ edge: 0.001 }))).toBe("at Kalshi's own price");
    expect(cents(0)).toBe("0.0¢");
  });

  test("age and stale past 20 minutes", () => {
    expect(ageText(30)).toBe("just now");
    expect(ageText(300)).toBe("5 min old");
    expect(ageText(7300)).toBe("2 h old");
    expect(stale(prop({ age: 1200 }))).toBe(false);
    expect(stale(prop({ age: 1201 }))).toBe(true);
  });

  test("as a slip leg: its side at its ask, both sides for the flip, YES-only kept", () => {
    expect(toLeg(prop(), "Basketball")).toEqual({
      t: "KXNBAPTS-26NOV25MINOKC-OKCSGA2-30", side: "yes", label: "Shai Gilgeous-Alexander: 30+ pts",
      game: "MIN Timberwolves at OKC Thunder", a: 0.62, sport: "Basketball", tm: "OKC", py: 0.62, pn: 0.4,
    });
    const no = toLeg(prop({ side: "no", ask: 0.4, combo: "yes_only" }), "Basketball");
    expect(no).toMatchObject({ side: "no", a: 0.4, yo: true, label: "Shai Gilgeous-Alexander: 29 or fewer pts" });
    const line = toLeg(prop({ k: "game_line", who: "OKC", stat: "winner", label: "OKC", line: undefined }), "Basketball");
    expect(line).toMatchObject({ label: "OKC wins", game: "MIN Timberwolves at OKC Thunder" });
  });
});
