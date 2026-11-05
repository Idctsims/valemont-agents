import { expect, test } from "@playwright/test";

import { BROADCAST_SERVICES, annotateSportsContext, servicesFor } from "../src/lib/gameday/services";
import {
  addDays, applyPatches, compareInDay, dayLabel, gameDay, kalshiUrl, matchup, pollIds, sectionize, startLabel, tvLabel,
  type SlateRow,
} from "../src/lib/gameday/slate";
import { tvTier } from "../src/lib/gameday/tiers";

// Game Day's pure logic (Chat 7 Phase 3; docs/gameday.md). No page, no server.
// The database twins (db/068 gameday_day, sports_tv_tier) are held to the same
// answers by db/068's self-test and tests_live/test_gameday_sql.py.

let next = 1;
function row(over: Partial<SlateRow>): SlateRow {
  return { id: next++, league: "nfl", day: "2026-11-07", at: "2026-11-07T18:00:00Z", tier: 2, status: "scheduled", ...over };
}

test.describe("the game day: 05:00 to 05:00 Chicago", () => {
  test("04:59 is still the night before; 05:00 is the new day (CST)", () => {
    expect(gameDay("2026-11-08T10:59:00Z")).toBe("2026-11-07"); // 04:59 CST
    expect(gameDay("2026-11-08T11:00:00Z")).toBe("2026-11-08"); // 05:00 CST
    expect(gameDay("2026-11-08T05:59:00Z")).toBe("2026-11-07"); // 23:59 CST the 7th
  });

  test("summer time (CDT) and the fall-back morning go by the wall clock, as the database does", () => {
    expect(gameDay("2026-07-04T09:59:00Z")).toBe("2026-07-03"); // 04:59 CDT
    expect(gameDay("2026-07-04T10:00:00Z")).toBe("2026-07-04"); // 05:00 CDT
    // Sun Nov 1 2026, clocks fall back at 02:00: 04:59 CST is 10:59Z.
    expect(gameDay("2026-11-01T10:59:00Z")).toBe("2026-10-31");
    expect(gameDay("2026-11-01T11:00:00Z")).toBe("2026-11-01");
  });

  test("a TBD event goes by ESPN's Eastern date, never a day early", () => {
    // ESPN files a TBD game at midnight Eastern: 23:00 Chicago the evening before.
    expect(gameDay("2026-11-14T05:00:00Z", true)).toBe("2026-11-14");
    expect(gameDay("2026-11-14T05:00:00Z", false)).toBe("2026-11-13");
  });

  test("addDays and the day labels", () => {
    expect(addDays("2026-11-30", 1)).toBe("2026-12-01");
    expect(dayLabel("2026-11-05", "2026-11-05")).toBe("Tonight");
    expect(dayLabel("2026-11-06", "2026-11-05")).toBe("Tomorrow");
    expect(dayLabel("2026-11-07", "2026-11-05")).toBe("Sat, Nov 7");
  });
});

test.describe("start times", () => {
  test("TBD renders TBD, never the stored placeholder", () => {
    expect(startLabel({ at: "2026-11-14T05:00:00Z", tbd: true })).toBe("TBD");
    expect(startLabel({ at: "2026-11-14T05:00:00Z" })).toBe("11:00 PM"); // what the placeholder would read
    expect(startLabel({ at: "2026-11-08T01:15:00Z" })).toBe("7:15 PM");
  });

  test("a blank broadcast is Not listed", () => {
    expect(tvLabel([])).toBe("Not listed");
    expect(tvLabel(undefined)).toBe("Not listed");
    expect(tvLabel(["ESPN", "ESPN+"])).toBe("ESPN · ESPN+");
  });
});

test.describe("sections", () => {
  test("Live now (any day) · Tonight · This week (the next 7, by day)", () => {
    const today = "2026-11-07";
    const liveYesterday = row({ day: "2026-11-06", status: "live" });
    const tonight = row({ day: today });
    const final = row({ day: today, status: "final" });
    const sun = row({ day: "2026-11-08" });
    const fri = row({ day: "2026-11-14" });
    const tooFar = row({ day: "2026-11-15" });
    const s = sectionize([fri, tonight, liveYesterday, sun, tooFar, final], today);
    expect(s.live.map((r) => r.id)).toEqual([liveYesterday.id]);
    expect(s.tonight.map((r) => r.id).sort()).toEqual([tonight.id, final.id].sort());
    expect(s.week.map((d) => d.day)).toEqual(["2026-11-08", "2026-11-14"]);
    expect(s.week.flatMap((d) => d.rows).some((r) => r.id === tooFar.id)).toBe(false);
  });
});

test.describe("order inside a day (owner, 2026-11-04)", () => {
  test("live first; Alabama; then CFB, NFL, NBA, F1, golf; then the other leagues", () => {
    const at = "2026-11-07T18:00:00Z";
    const rows = [
      row({ league: "nhl", at }), row({ league: "pga", at }), row({ league: "f1", at }), row({ league: "nba", at }),
      row({ league: "nfl", at }), row({ league: "cfb", at, tier: 3 }), row({ league: "cfb", at: "2026-11-07T23:00:00Z", bama: true, tier: 1 }),
      row({ league: "mcbb", at, status: "live" }),
    ];
    const order = [...rows].sort(compareInDay).map((r) => (r.bama ? "bama" : r.status === "live" ? "live" : r.league));
    expect(order).toEqual(["live", "bama", "cfb", "nfl", "nba", "f1", "pga", "nhl"]);
  });

  test("inside CFB: ranked, then SEC, then national TV, then the rest; then by start", () => {
    const rest = row({ league: "cfb", tier: 3, at: "2026-11-07T15:00:00Z" });
    const national = row({ league: "cfb", tier: 1, at: "2026-11-07T15:00:00Z" });
    const sec = row({ league: "cfb", tier: 3, sec: true, at: "2026-11-07T16:00:00Z" });
    const rankedLate = row({ league: "cfb", tier: 3, hr: 20, at: "2026-11-08T01:00:00Z" });
    const rankedEarly = row({ league: "cfb", tier: 2, ar: 3, at: "2026-11-07T17:00:00Z" });
    const sorted = [rest, national, sec, rankedLate, rankedEarly].sort(compareInDay).map((r) => r.id);
    expect(sorted).toEqual([rankedEarly.id, rankedLate.id, sec.id, national.id, rest.id]);
  });

  test("ties go by start, then the bigger network", () => {
    const a = row({ league: "nfl", tier: 2, at: "2026-11-08T18:00:00Z" });
    const b = row({ league: "nfl", tier: 1, at: "2026-11-08T18:00:00Z" });
    const c = row({ league: "nfl", tier: 1, at: "2026-11-08T21:25:00Z" });
    expect([c, a, b].sort(compareInDay).map((r) => r.id)).toEqual([b.id, a.id, c.id]);
  });
});

test.describe("matchups", () => {
  test("ranks, at, vs at a neutral site, and an event's title", () => {
    expect(matchup({ away: "Alabama Crimson Tide", home: "LSU Tigers", ar: 5, hr: 14 }))
      .toBe("No. 5 Alabama Crimson Tide at No. 14 LSU Tigers");
    expect(matchup({ away: "A", home: "B", neutral: true })).toBe("A vs B");
    expect(matchup({ title: "São Paulo Grand Prix", session: "Qual" })).toBe("São Paulo Grand Prix · Qual");
  });
});

test.describe("the services map (owner, 2026-11-05)", () => {
  test("exact matches", () => {
    expect(servicesFor(["Paramount+"])).toEqual(["Paramount+"]);
    expect(servicesFor(["Prime Video"])).toEqual(["Prime Video"]);
    expect(servicesFor(["Amazon Prime Video"])).toEqual(["Prime Video"]);
    expect(servicesFor(["Prime"])).toEqual(["Prime Video"]);
    expect(servicesFor(["Netflix"])).toEqual(["Netflix"]);
    expect(servicesFor(["HBO Max"])).toEqual(["HBO Max"]);
    expect(servicesFor(["Max"])).toEqual(["HBO Max"]);
    expect(servicesFor(["Showtime", "Starz"])).toEqual(["Showtime", "Starz"]);
  });

  test("the judgment calls: CBS and TNT/truTV/TBS yes; CBSSN and Prime Video (Local) no", () => {
    expect(servicesFor(["CBS"])).toEqual(["Paramount+"]);
    expect(servicesFor(["TNT"])).toEqual(["HBO Max"]);
    expect(servicesFor(["truTV"])).toEqual(["HBO Max"]);
    expect(servicesFor(["TBS"])).toEqual(["HBO Max"]);
    expect(servicesFor(["CBSSN"])).toEqual([]);
    expect(servicesFor(["Prime Video (Local)"])).toEqual([]);
  });

  test("everything else is not the owner's; case and spaces do not matter; one mark per service", () => {
    for (const name of ["ESPN", "ESPN+", "Peacock", "Apple TV", "Hulu", "Disney+", "Fox", "ABC", "NBC", "MNMT", "Golf Chnl"]) {
      expect(servicesFor([name]), name).toEqual([]);
    }
    expect(servicesFor([" paramount+ ", "CBS", "TNT", "truTV"])).toEqual(["Paramount+", "HBO Max"]);
    expect(servicesFor(null)).toEqual([]);
  });

  test("the map holds exactly the approved keys", () => {
    expect(Object.keys(BROADCAST_SERVICES).sort()).toEqual([
      "AMAZON PRIME VIDEO", "CBS", "HBO MAX", "MAX", "NETFLIX", "PARAMOUNT PLUS", "PARAMOUNT+", "PRIME", "PRIME VIDEO",
      "SHOWTIME", "STARZ", "TBS", "TNT", "TRUTV",
    ]);
  });

  test("Wags' context gains streams_on where a game streams on the owner's services", () => {
    const ctx = annotateSportsContext({
      goals: { x: 1 },
      sports_today: {
        about: "Sports.",
        owner_five: [{ what: "A at B", tv: ["CBS"] }, { what: "C at D", tv: ["ESPN"] }],
        alabama_next: [{ what: "Alabama at LSU", tv: ["ABC"] }],
        other_tonight: [{ what: "Hockey", tv: ["TNT"] }],
      },
    });
    const s = ctx.sports_today as Record<string, { streams_on?: string[] }[]>;
    expect(s.owner_five[0].streams_on).toEqual(["Paramount+"]);
    expect(s.owner_five[1].streams_on).toBeUndefined();
    expect(s.alabama_next[0].streams_on).toBeUndefined();
    expect(s.other_tonight[0].streams_on).toEqual(["HBO Max"]);
    expect(ctx.goals).toEqual({ x: 1 });
    expect(annotateSportsContext({ goals: 1 })).toEqual({ goals: 1 });
  });
});

test.describe("tiers (the twin of db/068 sports_tv_tier)", () => {
  test("broadcast > national cable > anything else > none", () => {
    expect(tvTier(["ABC"])).toBe(1);
    expect(tvTier(["Fox"])).toBe(1);
    expect(tvTier(["ESPN+", "CBS"])).toBe(1);
    expect(tvTier(["SEC Network"])).toBe(2);
    expect(tvTier(["truTV"])).toBe(2);
    expect(tvTier(["Golf Chnl"])).toBe(2);
    expect(tvTier(["ESPN+"])).toBe(3);
    expect(tvTier(["Prime Video"])).toBe(3);
    expect(tvTier(["MNMT"])).toBe(3);
    expect(tvTier([])).toBe(4);
  });
});

test.describe("the live poll", () => {
  const now = Date.parse("2026-11-07T20:00:00Z");

  test("asks only for rows in play, or a timed game due to start (5 min before to 3 h after)", () => {
    const live = row({ status: "live" });
    const delayed = row({ status: "delayed" });
    const due = row({ at: "2026-11-07T20:04:00Z" });
    const started = row({ at: "2026-11-07T18:00:00Z" });
    const later = row({ at: "2026-11-07T20:06:00Z" });
    const stale = row({ at: "2026-11-07T16:59:00Z" });
    const tbd = row({ at: "2026-11-07T19:00:00Z", tbd: true });
    const final = row({ status: "final", at: "2026-11-07T19:00:00Z" });
    expect(pollIds([live, delayed, due, started, later, stale, tbd, final], now))
      .toEqual([live.id, delayed.id, due.id, started.id]);
  });

  test("nothing in play: nothing is polled; and never more than 60 ids", () => {
    expect(pollIds([row({ at: "2026-11-08T18:00:00Z" }), row({ status: "final" })], now)).toEqual([]);
    expect(pollIds(Array.from({ length: 80 }, () => row({ status: "live" })), now)).toHaveLength(60);
  });

  test("a patch replaces what moves; a field the poll leaves out is cleared", () => {
    const r = row({ status: "live", score: { home: 7, away: 3 }, clock: "4:12", detail: "Q2 4:12" });
    const other = row({});
    const [a, b] = applyPatches([r, other], [{ id: r.id, status: "final", score: { home: 24, away: 17 }, detail: "Final" }, { id: 999999, status: "live" }]);
    expect(a).toMatchObject({ status: "final", score: { home: 24, away: 17 }, detail: "Final" });
    expect(a.clock).toBeUndefined();
    expect(b).toBe(other);
  });
});

test.describe("Kalshi", () => {
  test("the event page URL is built from the series and the ticker", () => {
    expect(kalshiUrl("KXNFLGAME", "KXNFLGAME-26NOV05JACBAL"))
      .toBe("https://kalshi.com/markets/kxnflgame/kxnflgame/kxnflgame-26nov05jacbal");
  });
});
