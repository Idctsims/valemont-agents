import { expect, test } from "@playwright/test";

import { SEARCH_MAX_TEAMS, nameMatches, searchFilter, searchPattern, teamHits, type SearchRow } from "../src/lib/gameday/search";

// Game Day's team search (owner, Nov 21): the pattern sent to PostgREST, the
// name match, and one hit per team opening its live, next or last game.

const NOW = Date.parse("2026-11-21T20:00:00Z");
let id = 1;
function game(over: Partial<SearchRow>): SearchRow {
  return {
    id: id++, league: "cfb", home: "LSU Tigers", away: "Alabama Crimson Tide", starts_at: "2026-11-21T23:00:00Z",
    status: "scheduled", aloc: "Alabama", an: "Crimson Tide", al: "ncaa/500/333.png", ac: "9e1b32",
    hloc: "LSU", hn: "Tigers", hl: "ncaa/500/99.png", hc: "461d7c", ...over,
  };
}

test.describe("the search pattern", () => {
  test("too short is no search", () => {
    expect(searchPattern("")).toBeNull();
    expect(searchPattern(" a ")).toBeNull();
    expect(searchPattern("'.")).toBeNull();
  });

  test("letters and digits kept; spaces and punctuation one wildcard; nothing that breaks the filter", () => {
    expect(searchPattern("Bama")).toBe("Bama");
    expect(searchPattern("ala  crim")).toBe("ala*crim");
    expect(searchPattern("St. John's")).toBe("St*John*s");
    expect(searchPattern("a,b(c)\"d")).toBe("a*b*c*d");
    expect(searchPattern("x".repeat(80))!.length).toBe(40);
  });
});

test.describe("the filter", () => {
  test("short text starts a word; four letters or more match anywhere", () => {
    expect(searchFilter("st")).toBe("home.ilike.st*,home.ilike.\"* st*\",away.ilike.st*,away.ilike.\"* st*\"");
    expect(searchFilter("bama")).toBe("home.ilike.*bama*,away.ilike.*bama*");
    expect(searchFilter("ala*cr")).toBe("home.ilike.*ala*cr*,away.ilike.*ala*cr*");
  });
});

test.describe("name match", () => {
  test("inside the name, or every word starting a word", () => {
    expect(nameMatches("Alabama Crimson Tide", "bama")).toBe(true);
    expect(nameMatches("Alabama Crimson Tide", "ala crim")).toBe(true);
    expect(nameMatches("Alabama Crimson Tide", "crimson")).toBe(true);
    expect(nameMatches("Alabama Crimson Tide", "tide ala")).toBe(true);
    expect(nameMatches("Alabama Crimson Tide", "lsu")).toBe(false);
    expect(nameMatches("St. John's Red Storm", "st johns")).toBe(true);
    expect(nameMatches("St. John's Red Storm", "st. john")).toBe(true);
    expect(nameMatches("Atlético Madrid", "atletico")).toBe(true);
  });
});

test.describe("one hit per team", () => {
  test("live beats next beats last; the side and opponent come from that game", () => {
    const rows = [
      game({ starts_at: "2026-11-14T23:00:00Z", status: "final", home: "Alabama Crimson Tide", away: "Auburn Tigers",
             hloc: "Alabama", hn: "Crimson Tide", hl: "ncaa/500/333.png", aloc: "Auburn", an: "Tigers", al: "ncaa/500/2.png" }),
      game({}),
    ];
    const [bama] = teamHits(rows, "bama", NOW);
    expect(bama).toMatchObject({ name: "Alabama Crimson Tide", gameId: rows[1].id, home: false, opponent: "LSU Tigers" });
    expect(bama.face).toEqual({ loc: "Alabama", nick: "Crimson Tide", logo: "ncaa/333", color: "9e1b32" });

    const live = [...rows, game({ starts_at: "2026-11-21T19:00:00Z", status: "live", away: "Alabama Crimson Tide" })];
    expect(teamHits(live.sort((a, b) => a.starts_at.localeCompare(b.starts_at)), "bama", NOW)[0].gameId).toBe(live.find((r) => r.status === "live")!.id);

    const past = [rows[0]];
    expect(teamHits(past, "bama", NOW)[0]).toMatchObject({ gameId: rows[0].id, home: true, opponent: "Auburn Tigers", status: "final" });
  });

  test("both sides can answer; the same name in two leagues is two teams", () => {
    const rows = [
      game({ home: "Tigers United", away: "LSU Tigers" }),
      game({ league: "mcbb", home: "LSU Tigers", away: "Kentucky Wildcats" }),
    ];
    const hits = teamHits(rows, "tigers", NOW);
    expect(hits.map((h) => `${h.league} ${h.name}`)).toEqual(["cfb Tigers United", "cfb LSU Tigers", "mcbb LSU Tigers"]);
  });

  test("a name that starts with the text first, a live game first among equals, then the owner's order", () => {
    const rows = [
      game({ league: "nba", home: "Miami Heat", away: "Boston Celtics" }),
      game({ league: "cfb", home: "Miami Hurricanes", away: "Virginia Tech Hokies", status: "live", starts_at: "2026-11-21T19:30:00Z" }),
      game({ league: "cfb", home: "Miami (OH) RedHawks", away: "Ohio Bobcats" }),
    ].sort((a, b) => a.starts_at.localeCompare(b.starts_at));
    expect(teamHits(rows, "miami", NOW).map((h) => h.name)).toEqual(["Miami Hurricanes", "Miami (OH) RedHawks", "Miami Heat"]);
  });

  test("capped", () => {
    const rows = Array.from({ length: 30 }, (_, i) => game({ home: `Team ${i} Tigers`, away: `Other ${i}` }));
    expect(teamHits(rows, "tigers", NOW)).toHaveLength(SEARCH_MAX_TEAMS);
  });
});
