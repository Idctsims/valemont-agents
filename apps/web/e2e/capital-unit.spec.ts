// Capital logic, no browser and no server: cents arithmetic, the sign rules
// (must agree with db/026's CHECK), today's change, the optimistic update
// (one mode only; paper and live never mix), the correction amount, the
// chart series and ranges. Run: pnpm test:unit

import { expect, test } from "@playwright/test";

import {
  applyEntry,
  centsToDecimal,
  changeLabel,
  changeOf,
  correctionFor,
  emptyMode,
  inRange,
  money,
  parseAmount,
  series,
  share,
  signedCents,
  signedMoney,
  sourceLabel,
  toCents,
  type ModeToday,
} from "../src/lib/capital/types";
import { decideMarker } from "../src/lib/capital/marker";
import { NAV, builtGroups } from "../src/lib/nav";

const paper = (value: number, prior: number | null = null): ModeToday => ({
  mode: "paper",
  total: { source: "total", value, prior },
  sources: [{ source: "bankroll", value, prior }],
});

test("cents round-trip a numeric(14,2) without float drift", () => {
  expect(toCents("1000.00")).toBe(100000);
  expect(toCents(0.1 + 0.2)).toBe(30);
  expect(toCents("-30.50")).toBe(-3050);
  expect(centsToDecimal(100000)).toBe("1000.00");
  expect(centsToDecimal(-3050)).toBe("-30.50");
  expect(centsToDecimal(5)).toBe("0.05");
});

test("money is formatted with a true minus and a sign when asked", () => {
  expect(money(100000)).toBe("$1,000.00");
  expect(money(-4000)).toBe("−$40.00");
  expect(signedMoney(25000)).toBe("+$250.00");
  expect(signedMoney(-4000)).toBe("−$40.00");
  expect(signedMoney(0)).toBe("$0.00");
});

test("a withdrawal subtracts; deposits and adjustments carry their own sign", () => {
  expect(signedCents({ kind: "deposit", amount: 500 })).toBe(500);
  expect(signedCents({ kind: "withdrawal", amount: 500 })).toBe(-500);
  expect(signedCents({ kind: "adjustment", amount: -500 })).toBe(-500);
});

test("today's change reads against the prior snapshot, and '— today' without one", () => {
  expect(changeLabel(changeOf({ source: "total", value: 125000, prior: 100000 }))).toBe("+$250.00 · +25.0% today");
  expect(changeLabel(changeOf({ source: "total", value: 96000, prior: 100000 }))).toBe("−$40.00 · −4.0% today");
  expect(changeLabel(changeOf({ source: "total", value: 100000, prior: 100000 }))).toBe("$0.00 · 0.0% today");
  expect(changeLabel(changeOf({ source: "total", value: 100000, prior: null }))).toBe("— today");
  expect(changeOf({ source: "total", value: 5000, prior: 0 })?.pct).toBeNull();
  expect(changeOf({ source: "total", value: 125000, prior: 100000 })?.tone).toBe("up");
});

test("an optimistic entry moves its own mode's bankroll and total only", () => {
  const after = applyEntry(paper(100000, 100000), { mode: "paper", kind: "deposit", amount: 5000 });
  expect(after.total.value).toBe(105000);
  expect(after.sources[0].value).toBe(105000);
  expect(after.total.prior).toBe(100000); // the snapshot does not move
  // A live entry never touches paper.
  expect(applyEntry(paper(100000), { mode: "live", kind: "deposit", amount: 5000 })).toEqual(paper(100000));
  // The first live entry starts live from nothing, separately.
  const live = applyEntry(emptyMode("live"), { mode: "live", kind: "deposit", amount: 5000 });
  expect(live).toEqual({
    mode: "live",
    total: { source: "total", value: 5000, prior: null },
    sources: [{ source: "bankroll", value: 5000, prior: null }],
  });
});

test("a correction is the exact negation, so it restores the prior total", () => {
  for (const e of [
    { kind: "deposit" as const, amount: 5000 },
    { kind: "withdrawal" as const, amount: 5000 },
    { kind: "adjustment" as const, amount: -1234 },
  ]) {
    const fix = correctionFor(e);
    expect(fix).not.toBe(0);
    const once = applyEntry(paper(100000), { mode: "paper", ...e });
    const back = applyEntry(once, { mode: "paper", kind: "adjustment", amount: fix });
    expect(back.total.value).toBe(100000);
  }
});

test("the amount field accepts dollars and cents, and nothing that is not money", () => {
  expect(parseAmount("50")).toBe(5000);
  expect(parseAmount("50.5")).toBe(5050);
  expect(parseAmount("$1,250.25")).toBe(125025);
  expect(parseAmount(".75")).toBe(75);
  for (const bad of ["", "0", "0.00", "-5", "5.123", "abc", "1e3"]) expect(parseAmount(bad)).toBeNull();
});

test("the series is one point per snapshot date plus today's live value", () => {
  const snaps = [
    { snap_date: "2026-10-07", value: 100000 },
    { snap_date: "2026-10-08", value: 90000 },
    { snap_date: "2026-10-08", value: 20000 }, // a second source on the same date
    { snap_date: "2026-10-09", value: 1 }, // today's date is never a snapshot; ignored
  ];
  expect(series(snaps, "2026-10-09", 115000)).toEqual([
    { date: "2026-10-07", value: 100000 },
    { date: "2026-10-08", value: 110000 },
    { date: "2026-10-09", value: 115000 },
  ]);
  expect(series([], "2026-10-09", 100000)).toHaveLength(1); // "History starts tonight"
});

test("ranges keep the last 7, 30 and 91 days; All keeps everything", () => {
  const pts = ["2026-06-01", "2026-07-15", "2026-09-15", "2026-10-03", "2026-10-09"].map((date) => ({ date, value: 1 }));
  expect(inRange(pts, "1W", "2026-10-09").map((p) => p.date)).toEqual(["2026-10-03", "2026-10-09"]);
  expect(inRange(pts, "1M", "2026-10-09").map((p) => p.date)).toEqual(["2026-09-15", "2026-10-03", "2026-10-09"]);
  expect(inRange(pts, "3M", "2026-10-09").map((p) => p.date)).toEqual(["2026-07-15", "2026-09-15", "2026-10-03", "2026-10-09"]);
  expect(inRange(pts, "All", "2026-10-09")).toHaveLength(5);
});

test("share bars stay within 0..1 of their own mode's total", () => {
  expect(share(50, 100)).toBe(0.5);
  expect(share(150, 100)).toBe(1);
  expect(share(-10, 100)).toBe(0);
  expect(share(10, 0)).toBe(0);
  expect(sourceLabel("bankroll")).toBe("Bankroll");
  expect(sourceLabel("bot_equity")).toBe("Bot equity");
});

test("Capital Tracker is built and reachable from the nav", () => {
  const pillar = NAV.flatMap((g) => g.pillars).find((p) => p.href === "/capital");
  expect(pillar?.built).toBe(true);
  expect(builtGroups().flatMap((g) => g.pages).map((p) => p.href)).toContain("/capital");
});

test("the e2e marker: honoured only on a local build, and only when it matches", () => {
  const env = { E2E_TEST_MARKER: "s3cret-marker" };
  expect(decideMarker(null, env)).toEqual({ kind: "none" });
  expect(decideMarker("", env)).toEqual({ kind: "none" });
  expect(decideMarker("s3cret-marker", env)).toEqual({ kind: "test", marker: "s3cret-marker" });
  // Present but not verified: refused, never treated as a real entry.
  expect(decideMarker("wrong", env).kind).toBe("refused");
  expect(decideMarker("s3cret-marke", env).kind).toBe("refused");
  expect(decideMarker("s3cret-marker", {}).kind).toBe("refused"); // the server has no marker
  // Any Vercel deployment refuses, production and preview alike (both read
  // the real database), even if the variable were ever set there.
  for (const VERCEL_ENV of ["production", "preview", "development", undefined, ""]) {
    expect(decideMarker("s3cret-marker", { ...env, VERCEL: "1", VERCEL_ENV })).toEqual({
      kind: "refused",
      reason: "a Vercel deployment never accepts the test marker",
    });
  }
  // Without VERCEL, VERCEL_ENV alone does not matter: the build is local.
  expect(decideMarker("s3cret-marker", { ...env, VERCEL_ENV: "production" }).kind).toBe("test");
});