import { expect, test } from "@playwright/test";

import { PLAYS_MAX, fieldX, parseSummary, periodLabels, periodName, shortName } from "../src/lib/gameday/summary";

// The game page's ESPN summary parser (owner, Nov 21: the Google-style game
// page). Fixtures are cut from real ESPN summaries read Nov 21 (VT at Miami,
// live, college football; MIL at PHI, final, NBA), trimmed to the fields read.

function team(id: string, abbr: string) {
  return { id, abbreviation: abbr, location: abbr, shortDisplayName: abbr };
}

function football(over: Record<string, unknown> = {}) {
  return {
    header: {
      week: 12,
      competitions: [{
        status: { type: { state: "in", detail: "8:52 - 2nd Quarter" } },
        competitors: [
          { id: "2390", homeAway: "home", score: "11", rank: 7, possession: false, team: team("2390", "MIA"),
            record: [{ type: "total", summary: "9-1" }], linescores: [{ displayValue: "3" }, { displayValue: "8" }] },
          { id: "259", homeAway: "away", score: "14", possession: true, team: team("259", "VT"),
            record: [{ type: "total", summary: "6-4" }], linescores: [{ displayValue: "14" }, { displayValue: "0" }] },
        ],
      }],
    },
    drives: {
      previous: [{
        team: { id: "259" },
        plays: [
          { id: "1", text: "(15:00) #22 J.Weinberg kickoff 65 yards", period: { number: 1 }, clock: { displayValue: "15:00" },
            awayScore: 0, homeScore: 0, type: { text: "Kickoff" } },
          { id: "2", text: "(06:43) E.Grunkemeyer pass to J.Overton Jr. for a TD", period: { number: 1 },
            clock: { displayValue: "6:43" }, awayScore: 7, homeScore: 0, scoringPlay: true, type: { text: "Passing Touchdown" } },
        ],
      }],
      current: {
        description: "2 plays, 16 yards, 0:50",
        team: { id: "259" },
        plays: [
          { id: "3", text: "(09:19) Shotgun #17 E.Grunkemeyer pass complete to #3 Q.Brown for 13 yards", period: { number: 2 },
            clock: { displayValue: "9:19" }, awayScore: 14, homeScore: 11, type: { text: "Pass Reception" },
            end: { distance: 10, yardsToEndzone: 59, downDistanceText: "1st & 10 at VT 41", team: { id: "259" } } },
        ],
      },
    },
    leaders: [
      { team: { id: "2390" }, leaders: [{ displayName: "Passing Yards", leaders: [{ displayValue: "7/11, 87 YDS, 1 INT",
        athlete: { displayName: "Darian Mensah", shortName: "D. Mensah" } }] }] },
    ],
    boxscore: {
      teams: [
        { team: { id: "259" }, statistics: [{ name: "firstDowns", label: "1st Downs", displayValue: "8" }] },
        { team: { id: "2390" }, statistics: [{ name: "firstDowns", label: "1st Downs", displayValue: "7" }] },
      ],
      players: [
        { team: { id: "259" }, statistics: [
          { name: "passing", labels: ["C/ATT", "YDS", "TD", "INT"],
            athletes: [{ athlete: { displayName: "Ethan Grunkemeyer", jersey: "17" }, stats: ["9/14", "112", "1", "0"] }] },
          { name: "kickReturns", labels: ["NO", "YDS"],
            athletes: [{ athlete: { displayName: "Dillon Hill Jr.", jersey: "6" }, stats: ["1", "22"] }] },
        ] },
      ],
    },
    scoringPlays: [{ text: "E. Grunkemeyer pass to J. Overton Jr. for 5 yds, for a TD", awayScore: 7, homeScore: 0,
                     period: { number: 1 }, clock: { displayValue: "6:43" }, team: { id: "259" }, type: { text: "Passing Touchdown" } }],
    gameInfo: { venue: { fullName: "Hard Rock Stadium", address: { city: "Miami Gardens", state: "FL" } },
                weather: { temperature: 79, conditionId: "Partly cloudy" } },
    ...over,
  };
}

test.describe("parseSummary: a live football game", () => {
  const s = parseSummary(football(), "cfb")!;

  test("sides by homeAway, with records, ranks, period lines and the score", () => {
    expect(s.away).toEqual({ abbr: "VT", record: "6-4", rank: undefined, lines: ["14", "0"], score: "14", possession: true });
    expect(s.home).toMatchObject({ abbr: "MIA", record: "9-1", rank: 7, lines: ["3", "8"], score: "11" });
    expect(s.periods).toEqual(["1", "2", "3", "4"]);
    expect(s.state).toBe("in");
    expect(s.detail).toBe("8:52 - 2nd Quarter");
    expect(s.note).toBe("Week 12");
  });

  test("down and distance from the current drive's last play, on the away-left field", () => {
    expect(s.situation).toEqual({ text: "1st & 10 at VT 41", offense: "away", x: 41, toGo: 51, drive: "2 plays, 16 yards, 0:50" });
  });

  test("plays from the drives, newest first, the leading clock stripped, sides from the drive", () => {
    expect(s.plays.map((p) => p.text)).toEqual([
      "Shotgun #17 E.Grunkemeyer pass complete to #3 Q.Brown for 13 yards",
      "E.Grunkemeyer pass to J.Overton Jr. for a TD",
      "#22 J.Weinberg kickoff 65 yards",
    ]);
    expect(s.plays[0]).toMatchObject({ period: "2nd Quarter", clock: "9:19", side: "away" });
    expect(s.lastPlay?.text).toBe(s.plays[0].text);
  });

  test("leaders, team stats, player groups with short names, scoring, venue and weather", () => {
    expect(s.leaders).toEqual([{ side: "home", category: "Passing Yards", name: "D. Mensah", line: "7/11, 87 YDS, 1 INT" }]);
    expect(s.teamStats).toEqual([{ label: "1st Downs", away: "8", home: "7" }]);
    expect(s.players.away.map((g) => g.name)).toEqual(["Passing", "Kick returns"]);
    // full: ESPN's whole name, which the Parlays page matches players by (Phase 4.5).
    expect(s.players.away[0].rows).toEqual([{ name: "E. Grunkemeyer", jersey: "17", stats: ["9/14", "112", "1", "0"],
                                               full: "Ethan Grunkemeyer" }]);
    expect(s.players.away[0].key).toBe("passing");
    expect(s.players.away[1].rows[0].name).toBe("D. Hill Jr.");
    expect(s.players.home).toEqual([]);
    expect(s.scoring).toHaveLength(1);
    expect(s.scoring[0]).toMatchObject({ period: "1st Quarter", side: "away", away: 7, home: 0, type: "Passing Touchdown" });
    expect(s.venue).toBe("Hard Rock Stadium, Miami Gardens, FL");
    expect(s.weather).toBe("79°, partly cloudy");
  });
});

test.describe("parseSummary: odd or partial shapes drop a section, never the page", () => {
  test("no header competitors is no summary at all", () => {
    expect(parseSummary({}, "nfl")).toBeNull();
    expect(parseSummary({ header: { competitions: [{ competitors: [] }] } }, "nfl")).toBeNull();
    expect(parseSummary("not json", "nfl")).toBeNull();
  });

  test("a final game: no situation, no last play; ranks past 25 are not ranks", () => {
    const f = football();
    f.header.competitions[0].status.type = { state: "post", detail: "Final" };
    (f.header.competitions[0].competitors[0] as { rank: number }).rank = 99;
    const s = parseSummary(f, "cfb")!;
    expect(s.situation).toBeUndefined();
    expect(s.lastPlay).toBeUndefined();
    expect(s.home.rank).toBeUndefined();
  });

  test("missing sections come back empty", () => {
    const s = parseSummary(football({ leaders: "x", boxscore: null, scoringPlays: undefined, drives: 3, gameInfo: [] }), "cfb")!;
    expect(s.leaders).toEqual([]);
    expect(s.teamStats).toEqual([]);
    expect(s.players).toEqual({ away: [], home: [] });
    expect(s.plays).toEqual([]);
    expect(s.situation).toBeUndefined();
    expect(s.venue).toBeUndefined();
  });

  test("top-level plays (basketball) win over drives, capped, newest first", () => {
    const plays = Array.from({ length: PLAYS_MAX + 20 }, (_, i) => ({ text: `play ${i}`, period: { number: 1, displayValue: "1st Quarter" } }));
    const s = parseSummary(football({ plays }), "nba")!;
    expect(s.plays).toHaveLength(PLAYS_MAX);
    expect(s.plays[0].text).toBe(`play ${PLAYS_MAX + 19}`);
    expect(s.note).toBeUndefined(); // a week is football's
  });
});

test.describe("the summary's helpers", () => {
  test("period columns: regulation, then OT", () => {
    expect(periodLabels("nba", 0)).toEqual(["1", "2", "3", "4"]);
    expect(periodLabels("nba", 6)).toEqual(["1", "2", "3", "4", "OT", "2OT"]);
    expect(periodLabels("nhl", 3)).toEqual(["1", "2", "3"]);
    expect(periodLabels("mcbb", 3)).toEqual(["1", "2", "OT"]);
    expect(periodLabels("mlb", 11)).toHaveLength(11);
    expect(periodLabels("mlb", 11).at(-1)).toBe("11");
  });

  test("period names", () => {
    expect(periodName("nfl", { number: 2 })).toBe("2nd Quarter");
    expect(periodName("nhl", { number: 3 })).toBe("3rd Period");
    expect(periodName("nfl", { number: 5 })).toBe("Overtime");
    expect(periodName("nba", { number: 6 })).toBe("2nd Overtime");
    expect(periodName("nba", { number: 1, displayValue: "1st Quarter" })).toBe("1st Quarter");
    expect(periodName("nba", {})).toBe("");
  });

  test("the ball's spot: away drives right, home drives left, clamped", () => {
    expect(fieldX("away", 59)).toBe(41);
    expect(fieldX("home", 65)).toBe(65);
    expect(fieldX("away", 120)).toBe(0);
  });

  test("short names", () => {
    expect(shortName("Darian Mensah", undefined)).toBe("D. Mensah");
    expect(shortName("Darian Mensah", "D. Mensah")).toBe("D. Mensah");
    expect(shortName("Pelé", undefined)).toBe("Pelé");
    expect(shortName(undefined, undefined)).toBe("");
  });
});
