import { expect, test } from "@playwright/test";

import {
  MAX_LEGS, ageLabel, cleanReason, comboStatusLabel, pricesStale, costLabel, legTierLabel, loadLegs, marketTicker, pricingLabel, probLabel,
  quotedLegs, ratioLabel, requestLegs, saveLegs, seLabel, sideOf, toggleLeg, type ComboLeg,
} from "../src/lib/betting/combo";
import { pageSlice } from "../src/lib/wags/page-context";

// Combo mode's pure logic (Chat 8 Phase 3; db/076, db/077). No page, no server.

const leg = (t: string, side: "yes" | "no" = "yes"): ComboLeg => ({ t, side, label: `${t} label`, game: "A at B" });

test.describe("ticks", () => {
  test("a market's ticker from its event and db/077's suffix", () => {
    expect(marketTicker({ e: "KXNBAPTS-26NOV11BOSTOR" }, { x: "BOSDWHITE9-10" })).toBe("KXNBAPTS-26NOV11BOSTOR-BOSDWHITE9-10");
    expect(marketTicker({ e: "KXPGAPLAYOFF-AUCPBCS26" }, { x: "" })).toBe("KXPGAPLAYOFF-AUCPBCS26");
    expect(marketTicker({ e: "KXNBAGAME-X" }, {})).toBeNull();
  });

  test("tick, untick, switch sides, and the 25-leg cap", () => {
    let legs = toggleLeg([], leg("A"));
    expect(legs).toEqual([leg("A")]);
    legs = toggleLeg(legs, leg("A", "no"));
    expect(legs).toEqual([leg("A", "no")]);
    expect(sideOf(legs, "A")).toBe("no");
    expect(toggleLeg(legs, leg("A", "no"))).toEqual([]);
    const full = Array.from({ length: MAX_LEGS }, (_, i) => leg(`T${i}`));
    expect(toggleLeg(full, leg("EXTRA"))).toHaveLength(MAX_LEGS);
  });

  test("kept in storage; blocked or junk storage is empty, never a throw", () => {
    const box = new Map<string, string>();
    const s = { getItem: (k: string) => box.get(k) ?? null, setItem: (k: string, v: string) => void box.set(k, v),
                removeItem: (k: string) => void box.delete(k) };
    saveLegs(s, [leg("A"), leg("B", "no")]);
    expect(loadLegs(s)).toEqual([leg("A"), leg("B", "no")]);
    saveLegs(s, []);
    expect(box.size).toBe(0);
    expect(loadLegs({ getItem: () => "not json" })).toEqual([]);
    expect(loadLegs({ getItem: () => JSON.stringify([{ t: "lower-case", side: "yes", label: "", game: "" }]) })).toEqual([]);
    expect(loadLegs({ getItem: () => { throw new Error("blocked"); } })).toEqual([]);
    expect(() => saveLegs({ setItem: () => { throw new Error("full"); }, removeItem: () => undefined }, [leg("A")]))
      .not.toThrow();
    expect(loadLegs(null)).toEqual([]);
  });

  test("the request db/076 takes", () => {
    expect(requestLegs([{ t: "KXA-1", side: "yes" }, { t: "KXB-2", side: "no" }]))
      .toEqual([{ market_ticker: "KXA-1", side: "yes" }, { market_ticker: "KXB-2", side: "no" }]);
    expect(requestLegs([{ t: "KXA-1", side: "yes" }])).toBeNull();
    expect(requestLegs([{ t: "KXA-1", side: "YES" }, { t: "KXB-2", side: "no" }])).toBeNull();
    expect(requestLegs([{ t: "kxa", side: "yes" }, { t: "KXB-2", side: "no" }])).toBeNull();
    expect(requestLegs(Array.from({ length: 26 }, (_, i) => ({ t: `KX-${i}`, side: "yes" })))).toBeNull();
  });
});

test.describe("the sheet's words", () => {
  const quote = {
    naive_p: 0.341, fair_p: 0.3712, se: 0.0011, kalshi_naive_cost: 0.3634,
    combo_status: "build_manually" as const, combo_reason: "YES only on Kalshi: KXNBAGAME-26NOV11BOSTOR",
    blocks: [{ legs: [{ t: "A", side: "yes" as const, p: 0.55, tier: "books" }, { t: "B", side: "no" as const, p: 0.62, tier: "kalshi", stale: true }] }],
  };

  test("probabilities, the SE, the ratio, the cost", () => {
    expect(probLabel(0.3712)).toBe("37.1%");
    expect(probLabel(0.00042)).toBe("0.042%");
    expect(probLabel(null)).toBe("—");
    expect(seLabel(0.0011)).toBe("± 0.11%");
    expect(seLabel(null)).toBe("");
    expect(ratioLabel(quote)).toBe("×1.09");
    expect(ratioLabel({ fair_p: 0.1, naive_p: 0 })).toBe("—");
    expect(costLabel(0.3634)).toBe("$0.36");
    expect(pricingLabel(7.9)).toBe("Pricing… 7 s");
  });

  test("the Kalshi status and a refusal's words", () => {
    expect(comboStatusLabel({ combo_status: "fits", combo_reason: null })).toBe("Kalshi combo · fits");
    expect(comboStatusLabel(quote)).toBe("Build manually: YES only on Kalshi: KXNBAGAME-26NOV11BOSTOR");
    expect(cleanReason("combo_requests: at most 20 combo prices in 10 minutes; the next one frees up at 21:14:03 UTC"))
      .toBe("At most 20 combo prices in 10 minutes; the next one frees up at 21:14:03 UTC");
  });

  test("each leg's quoted p and tier", () => {
    const m = quotedLegs(quote);
    expect(m.get("B")?.p).toBe(0.62);
    expect(legTierLabel(m.get("A")!.tier)).toBe("Books");
    expect(legTierLabel("kalshi")).toBe("Kalshi only");
  });
});

test("each leg's price age, and the quote's stale flag (owner, Nov 11)", () => {
  expect(ageLabel(30)).toBe("price under 1 min old");
  expect(ageLabel(4 * 60 + 5)).toBe("price 4 min old");
  expect(ageLabel(72 * 60)).toBe("price 1 h 12 min old");
  expect(ageLabel(null)).toBe("price age unknown");
  const leg = { t: "A", side: "yes" as const, p: 0.5, tier: "kalshi" };
  expect(pricesStale({ blocks: [{ legs: [{ ...leg, stale_k: false }] }] })).toBe(false);
  expect(pricesStale({ blocks: [{ legs: [leg] }, { legs: [{ ...leg, t: "B", stale_k: true }] }] })).toBe(true);
});

test("Wags opened from a quote gets that quote (ctx_combos, db/077)", () => {
  const payload = { combos: { about: "fair p is not profit", recent: [{ id: 12, fair_p: 0.4 }, { id: 13, fair_p: 0.2 }] } };
  expect(pageSlice(payload, { route: "/betting", label: "Combo · #13", pillar: "combos", slug: "13" }))
    .toEqual({ about: "fair p is not profit", quote: { id: 13, fair_p: 0.2 } });
  expect(pageSlice(payload, { route: "/betting", label: "Combo · #99", pillar: "combos", slug: "99" }))
    .toMatchObject({ id: "99" });
});
