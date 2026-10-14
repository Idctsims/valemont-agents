import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { builtGroups } from "../src/lib/nav";
import { pageSlice, parsePageContext } from "../src/lib/wags/page-context";
import { TECH_CHIPS, feedHref, techCategoryFromParam } from "../src/lib/feed/types";

// /tech's pure helpers (Chat 4 Phase 1; docs/tech_hub.md): the category chips,
// their URL state, and the nav. No page, no server.

test.describe("tech chips", () => {
  test("the owner's seven, in order, keyed by the db/045 categories", () => {
    expect(TECH_CHIPS.map((c) => c.label)).toEqual([
      "All", "Models & features", "Pricing & credits", "Tooling", "Agents", "Stack", "Community",
    ]);
    expect(TECH_CHIPS.map((c) => c.category)).toEqual([
      null, "models_features", "pricing_credits", "tooling", "agents", "stack", "community",
    ]);
  });

  test("the chips are the worker's CHIP_CATEGORIES (workers/feed/tech.py)", () => {
    const py = readFileSync(join(__dirname, "..", "..", "..", "workers", "feed", "tech.py"), "utf8");
    const m = /CHIP_CATEGORIES: tuple\[str, \.\.\.\] = \(([^)]*)\)/.exec(py);
    expect(m).not.toBeNull();
    const worker = [...m![1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]);
    expect(worker).toEqual(TECH_CHIPS.filter((c) => c.category).map((c) => c.category));
  });

  test("?category= is a chip; anything else, Home's categories and 'repos' included, is All", () => {
    expect(techCategoryFromParam("pricing_credits")).toBe("pricing_credits");
    expect(techCategoryFromParam(["tooling", "agents"])).toBe("tooling");
    for (const raw of [undefined, "", "Tooling", "other", "deals", "repos", ["x"]]) {
      expect(techCategoryFromParam(raw)).toBeNull();
    }
  });
});

test.describe("tech URLs", () => {
  test("the chip survives the tabs and pages", () => {
    expect(feedHref("/tech", { tab: "for-you", category: "pricing_credits" })).toBe("/tech?category=pricing_credits#stories");
    expect(feedHref("/tech", { tab: "latest", category: "tooling", page: 2 })).toBe("/tech?category=tooling&feed=latest&latest=2#stories");
    expect(feedHref("/tech", { tab: "for-you", category: null, anchor: false })).toBe("/tech");
  });
});

test.describe("Wags on /tech", () => {
  test("the page context names the tech pillar, and its slice is ctx_tech", () => {
    const page = parsePageContext({ route: "/tech", label: "Tech Hub", pillar: "tech" });
    expect(page).toEqual({ route: "/tech", label: "Tech Hub", pillar: "tech" });
    const tech = { official_7d: [{ title: "Claude Haiku 5.5 launch" }] };
    expect(pageSlice({ tech, goals: {} }, page!)).toEqual(tech);
  });
});

test.describe("the nav", () => {
  test("Tech Hub is built, at /tech", () => {
    const tech = builtGroups().find((g) => g.key === "tech")!;
    expect(tech.pages).toEqual([{ label: "Tech Hub", href: "/tech" }]);
  });
});
