import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { REPO_TOPICS_SHOWN, STARS_7D_LABELS, stars7dText, starCount, type Stars7dSource } from "../src/lib/feed/types";

// The repo card's pure helpers (Chat 4 Phase 2; db/049, workers/feed/repos.py).
// No page, no server.

const repoRoot = join(__dirname, "..", "..", "..");

test.describe("stars_7d labels", () => {
  test("are the worker's LABELS exactly", () => {
    const py = readFileSync(join(repoRoot, "workers", "feed", "repos.py"), "utf8");
    const m = /LABELS: dict\[str, str\] = \{([^}]*)\}/.exec(py);
    expect(m).not.toBeNull();
    const worker = Object.fromEntries([...m![1].matchAll(/"([a-z_]+)": "([^"]+)"/g)].map((x) => [x[1], x[2]]));
    expect(worker).toEqual(STARS_7D_LABELS);
  });

  test("are db/049's stars_7d_source values", () => {
    const sql = readFileSync(join(repoRoot, "db", "049_repos.sql"), "utf8");
    for (const source of Object.keys(STARS_7D_LABELS)) expect(sql).toContain(`'${source}'`);
  });

  test("only a measurement reads as one; every estimate says so", () => {
    expect(stars7dText({ stars7d: 340, stars7dSource: "measured" })).toBe("+340 7d");
    expect(stars7dText({ stars7d: 1204, stars7dSource: "trending_weekly" })).toBe("+1,204 trending est.");
    expect(stars7dText({ stars7d: 88, stars7dSource: "trending_daily" })).toBe("+88 trending est.");
    expect(stars7dText({ stars7d: 120, stars7dSource: "since_created" })).toBe("+120 since created");
    expect(stars7dText({ stars7d: 50, stars7dSource: "age_average" })).toBe("+50 avg est.");
    // The two guesses read differently (owner, 2026-10-14).
    expect(STARS_7D_LABELS.age_average).not.toBe(STARS_7D_LABELS.trending_weekly);
    const estimates: Stars7dSource[] = ["trending_weekly", "trending_daily", "since_created", "age_average"];
    for (const s of estimates) expect(STARS_7D_LABELS[s]).not.toBe("7d");
  });

  test("no stars_7d, no text", () => {
    expect(stars7dText({ stars7d: null, stars7dSource: null })).toBeNull();
    expect(stars7dText({ stars7d: 5, stars7dSource: null })).toBeNull();
  });

  test("counts and topics", () => {
    expect(starCount(238274)).toBe("238,274");
    expect(stars7dText({ stars7d: -3, stars7dSource: "measured" })).toBe("−3 7d");
    expect(REPO_TOPICS_SHOWN).toBe(3);
  });
});
