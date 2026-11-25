import { expect, test } from "@playwright/test";

import { chanceWords, expText, parseAmounts, ticketLegs, warns, type PayoutTicket } from "../src/lib/betting/payout";

// "Bet $X to win $Y" (E1; docs/payout_target.md): the amounts, the words, a
// ticket as slip legs. No page, no server.

test("the amounts: $1 to $10,000 staked, more back than staked, at most $1,000,000", () => {
  expect(parseAmounts("10", "100")).toEqual({ stake: 10, payout: 100 });
  expect(parseAmounts(10.005, 99.999)).toEqual({ stake: 10.01, payout: 100 });
  expect(parseAmounts("0.5", "100")).toEqual({ error: "Stake $1 to $10,000." });
  expect(parseAmounts("10", "10")).toEqual({ error: "Pays back has to be more than the stake." });
  expect(parseAmounts("10", "2000000")).toEqual({ error: "Pays back at most $1,000,000." });
  expect(parseAmounts("ten", "100")).toEqual({ error: "Stake $1 to $10,000." });
});

test("the warning past 100x; the chance and the return in words", () => {
  expect(warns(10, 1000)).toBe(false);
  expect(warns(10, 1001)).toBe(true);
  expect(chanceWords(0.0893)).toBe("about 1 in 11");
  expect(chanceWords(0.31)).toBe("about 1 in 3");
  expect(chanceWords(0.62)).toBe("about 62 in 100");
  expect(chanceWords(0)).toBe("no chance on these prices");
  expect(expText(-0.098)).toBe("−9.8%");
  expect(expText(0.012)).toBe("+1.2%");
});

test("a ticket as slip legs: worded, each at its ask, both sides kept, YES-only kept", () => {
  const t: PayoutTicket = {
    k: 2, price: 0.093, fee: 0.0059, contracts: 101, fair: 0.0893, naive: 0.09, exp: -0.098, combo: "fits",
    pairs: 0, measured: 0, prior: 0,
    legs: [
      { m: "KXNBAPTS-26NOV25MIAPHI-PHITMAXEY0-30", side: "yes", k: "prop", label: "Tyrese Maxey: 30+", who: "Tyrese Maxey",
        stat: "points", line: 29.5, team: "PHI", game: "MIA Heat at PHI 76ers", sport: "Basketball", start: 1, ask: 0.3,
        p: 0.29, tier: "kalshi", ya: 0.3, na: 0.72, age: 60 },
      { m: "KXEPLGAME-26NOV25ARSCHE-TIE", side: "no", k: "game_line", label: "Tie", who: "Tie", stat: "winner",
        game: "Arsenal vs Chelsea", sport: "Soccer", start: 1, ask: 0.31, p: 0.3, tier: "books", ya: 0.7, na: 0.31,
        yo: true, age: 60 },
    ],
  };
  const legs = ticketLegs(t);
  expect(legs[0]).toMatchObject({ t: "KXNBAPTS-26NOV25MIAPHI-PHITMAXEY0-30", side: "yes", a: 0.3, py: 0.3, pn: 0.72,
                                  label: "Tyrese Maxey: 30+ pts", game: "MIA Heat at PHI 76ers", sport: "Basketball" });
  expect(legs[1]).toMatchObject({ side: "no", a: 0.31, yo: true, label: "No draw", sport: "Soccer" });
});
