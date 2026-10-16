import { expect, test } from "@playwright/test";

import { quoteCredit, workTitle } from "../src/lib/arsenal/types";

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
