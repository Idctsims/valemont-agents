import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import {
  REPO_TOPICS_SHOWN, STARS_7D_LABELS, SURGE_AGE_DAYS, SURGE_LABEL, SURGE_SHARE, starSurge, stars7dText, starCount,
  type Stars7dSource,
} from "../src/lib/feed/types";

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

  test("star surge: rea's real numbers say 'star surge — unverified'", () => {
    // Production, 2026-10-14: 183,758 trending weekly on 239,045, created 2026-04-14.
    const rea = { stars: 239045, stars7d: 183758, stars7dSource: "trending_weekly" as const,
                  createdAt: "2026-04-14T00:51:32Z", surfacedAt: "2026-10-14T21:29:10Z" };
    expect(starSurge(rea)).toBe(true);
    expect(SURGE_LABEL).toBe("star surge — unverified");
    // Half or less, or a repo 30 days old or younger, or a measured/other source: no label.
    expect(starSurge({ ...rea, stars7d: 119522 })).toBe(false);
    expect(starSurge({ ...rea, createdAt: "2026-09-15T00:00:00Z" })).toBe(false);
    expect(starSurge({ ...rea, stars7dSource: "measured" })).toBe(false);
    expect(starSurge({ ...rea, stars7dSource: "age_average" })).toBe(false);
    expect(starSurge({ ...rea, createdAt: null })).toBe(false);
    expect(starSurge({ ...rea, stars7dSource: "trending_daily", stars7d: 130000 })).toBe(true);
  });

  test("the surge rule is the worker's trending_suspect", () => {
    const py = readFileSync(join(repoRoot, "workers", "feed", "repos.py"), "utf8");
    expect(Number(/^SUSPECT_SHARE = ([\d.]+)$/m.exec(py)?.[1])).toBe(SURGE_SHARE);
    expect(Number(/^SUSPECT_AGE_DAYS = (\d+)$/m.exec(py)?.[1])).toBe(SURGE_AGE_DAYS);
  });

  test("counts and topics", () => {
    expect(starCount(238274)).toBe("238,274");
    expect(stars7dText({ stars7d: -3, stars7dSource: "measured" })).toBe("−3 7d");
    expect(REPO_TOPICS_SHOWN).toBe(3);
  });
});
