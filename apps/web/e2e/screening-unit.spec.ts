import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { buildCsp } from "../src/lib/csp";
import { builtGroups } from "../src/lib/nav";
import { chicagoDay, screeningTabFromParam, shortDate } from "../src/lib/screening/types";
import { MY_SERVICES, splitProviders, tmdbImage } from "../src/lib/tmdb/services";

// The Screening Room's pure helpers (Chat 4 Phase 3; db/051). No page, no server.

const repoRoot = join(__dirname, "..", "..", "..");

test.describe("services", () => {
  test("are the worker's MY_SERVICES exactly", () => {
    const py = readFileSync(join(repoRoot, "workers", "screening", "services.py"), "utf8");
    const block = /MY_SERVICES: dict\[int, str\] = \{([\s\S]*?)\n\}/.exec(py)![1];
    const worker = Object.fromEntries([...block.matchAll(/(\d+): "([^"]+)"/g)].map((m) => [Number(m[1]), m[2]]));
    expect(worker).toEqual(MY_SERVICES);
  });

  test("Tonight is the owner's streaming offers, one per service; the rest below", () => {
    const s = splitProviders({
      flatrate: [{ id: 1825, name: "HBO Max Amazon Channel", logo: null }, { id: 1899, name: "HBO Max", logo: null }],
      ads: [{ id: 2616, name: "Paramount+ Essential", logo: null }, { id: 2303, name: "Paramount+ Premium", logo: null }],
      free: [{ id: 191, name: "Kanopy", logo: null }],
      rent: [{ id: 10, name: "Amazon Video", logo: null }],
      buy: [{ id: 2, name: "Apple TV", logo: null }],
    });
    expect(s.tonight.map((t) => [t.service, t.kind])).toEqual([["HBO Max", "flatrate"], ["Paramount+", "ads"]]);
    expect(s.otherStream.map((o) => o.id)).toEqual([1825, 191]);
    expect(s.rent.map((r) => r.id)).toEqual([10]);
    expect(s.buy.map((r) => r.id)).toEqual([2]);
    expect(splitProviders(null)).toEqual({ tonight: [], otherStream: [], rent: [], buy: [] });
  });

  test("TMDB images are built only from a clean path", () => {
    expect(tmdbImage("/abc_123.jpg", "w92")).toBe("https://image.tmdb.org/t/p/w92/abc_123.jpg");
    expect(tmdbImage("https://evil.invalid/x.jpg")).toBeNull();
    expect(tmdbImage(null)).toBeNull();
  });
});

test.describe("the page's rules", () => {
  test("the CSP adds TMDB images and the nocookie trailer frame, nothing broader", () => {
    const csp = buildCsp("n0nce", { dev: false, https: true });
    expect(csp).toContain("img-src 'self' data: blob: https://image.tmdb.org");
    expect(csp).toContain("frame-src https://www.youtube-nocookie.com");
    expect(csp).not.toMatch(/frame-src[^;]*youtube\.com(?!-)/);
    expect(csp).toContain("frame-ancestors 'none'");
  });

  test("Screening Room is built at /screening", () => {
    const pages = builtGroups().flatMap((g) => g.pages);
    expect(pages.find((p) => p.label === "Screening Room")?.href).toBe("/screening");
  });

  test("tabs and dates", () => {
    expect(screeningTabFromParam("trailers")).toBe("trailers");
    for (const raw of [undefined, "", "Trailers", ["x"]]) expect(screeningTabFromParam(raw)).toBe("shelves");
    expect(shortDate("2026-11-01")).toBe("Nov 1");
    expect(chicagoDay(0, new Date("2026-10-15T04:30:00Z"))).toBe("2026-10-14"); // 23:30 Chicago
    expect(chicagoDay(1, new Date("2026-10-15T04:30:00Z"))).toBe("2026-10-15");
  });
});
