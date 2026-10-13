import { expect, test } from "@playwright/test";

import { builtGroups } from "../src/lib/nav";
import { HOME_TOP, NEWS_CHIPS, categoryFromParam, feedHref } from "../src/lib/feed/types";

// /news' pure helpers (Chat 3 Phase 3 scope addition): the category chips,
// their URL state, Home's short feed size, and the nav. No page, no server.

test.describe("category chips", () => {
  test("the owner's seven, in order, keyed by the db/042 categories", () => {
    expect(NEWS_CHIPS.map((c) => c.label)).toEqual([
      "All", "Deals & Capital", "Hospitality & Real Estate", "Sports Business", "Film/TV & Music",
      "Luxury & Marine", "Betting & Markets",
    ]);
    expect(NEWS_CHIPS.map((c) => c.category)).toEqual([
      null, "deals", "hospitality", "sports_business", "film_music", "luxury_marine", "betting",
    ]);
  });

  test("?category= is a chip; anything else, 'other' included, is All", () => {
    expect(categoryFromParam("deals")).toBe("deals");
    expect(categoryFromParam(["betting", "deals"])).toBe("betting");
    for (const raw of [undefined, "", "Deals", "other", "gossip", ["x"]]) expect(categoryFromParam(raw)).toBeNull();
  });
});

test.describe("news URLs", () => {
  test("the chip survives the tabs and pages", () => {
    expect(feedHref("/news", { tab: "for-you", category: "deals" })).toBe("/news?category=deals#stories");
    expect(feedHref("/news", { tab: "latest", category: "deals", page: 3 })).toBe("/news?category=deals&feed=latest&latest=3#stories");
    expect(feedHref("/news", { tab: "latest", anchor: false })).toBe("/news?feed=latest");
    expect(feedHref("/news", { tab: "for-you", category: null, anchor: false })).toBe("/news");
  });

  test("an old Home feed URL maps to the same view on /news", () => {
    // Home redirects ?feed=latest&latest=2 here (src/app/(app)/page.tsx).
    expect(feedHref("/news", { tab: "latest", page: 2, anchor: false })).toBe("/news?feed=latest&latest=2");
    expect(feedHref("/news", { tab: "for-you", page: 2, anchor: false })).toBe("/news?foryou=2");
  });
});

test.describe("Home and the nav", () => {
  test("Home shows For you's top 10", () => {
    expect(HOME_TOP).toBe(10);
  });

  test("News is built, inside Home", () => {
    const command = builtGroups().find((g) => g.key === "command")!;
    expect(command.pages.map((p) => p.href)).toContain("/news");
  });
});
