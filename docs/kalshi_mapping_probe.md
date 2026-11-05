# Kalshi ↔ ESPN mapping probe (Chat 7 Phase 2, Step 0)

Read only. No migration and no worker change; nothing touched Railway.

- **Script:** `workers/scripts/kalshi_mapping_probe.py`, from `workers/` as
  `..\venv\Scripts\python.exe -m scripts.kalshi_mapping_probe --out <file.json>`.
- **Run:** 2026-11-05 00:13:35 UTC (Wed Nov 4, 19:13 ET), second of two
  runs. The first, earlier the same evening, gave the same series and the same
  per-league event and match counts.
- **Sources:** the numbers below come from the run's JSON (scratchpad, not
  committed), plus one narrow follow-up `db_inspect` query (§5).

**What it reads:**
- **Kalshi:** the existing public client. It walks series → events (with
  nested markets and milestones) and never uses the keyword search or the
  raw open-markets list.
- **Supabase:** `sports_events`, only through `db_inspect`.

## 0. How a game series is found (no hard-coded tickers)

1. **List:** `GET /series?category=Sports` returns 4,087 series, in one call.
2. **Candidates:** 298 series whose ticker ends `GAME`, `MATCH` or `FIGHT`
   and whose tags name one of our sports (Football, Basketball, Hockey,
   Baseball, Soccer, Tennis, MMA).
3. **One page each:** every candidate gets one page of `status=open` events
   with milestones. A candidate with no open event gets its newest settled
   event instead. 134 of the 298 had open events.
4. **League label:** the event's milestone carries Kalshi's own label,
   `details.league` (`NFL`, `NBA`, `NCAAFB`, `NCAAMB`, `NHL`, `MLB`, `EPL`,
   `MLS`, `UFC`, `Champions League`) or `details.tour` for tennis (`ATP`,
   `WTA`). The milestone also names the moneyline event:
   `details.main_game_event_ticker`. The game series is that event's series.
   The only table in the script maps labels to our league codes, never
   tickers.
5. **Golf and F1:** no game series exists. Their winner-level analogue is the
   series a golf or racing milestone names as its main event, found the same
   way across 196 Motorsport and Golf series.

**Useful for Phase 2:** a milestone also carries:
- `start_date`, the scheduled start in UTC;
- `details.home_team_id` and `details.away_team_id`.

Each moneyline market carries its team's id in `custom_strike`. Joining the
two says which market is home, with no name parsing.

## 1. Series per league

| Ours | Kalshi game series (moneyline-level) | Found from | Markets per event |
|---|---|---|---|
| `nfl` | `KXNFLGAME` "NFL Game" | 29 open events | 2 (one per team) |
| `cfb` | `KXNCAAFGAME` "College Football Game" | 256 open (FBS **and FCS**) | 2 |
| `nba` | `KXNBAGAME` "NBA Game" | 35 open | 2 |
| `mcbb` | `KXNCAAMBGAME` "Men's College Basketball Game" | 184 open | 2 |
| `nhl` | `KXNHLGAME` "NHL Game" | 46 open | 2 |
| `mlb` | `KXMLBGAME` "Professional Baseball Game" | settled only (off-season) | 2 |
| `epl` | `KXEPLGAME` "English Premier League Game" | 10 open | 3 (home, away, `TIE`) |
| `mls` | `KXMLSGAME` "Major League Soccer Game" | 15 open | 3 (with `TIE`) |
| `ucl` | `KXUCLGAME` "UEFA Champions League Game" | settled only (no open matchday) | 3 (with `TIE`) |
| `atp` | `KXATPMATCH` "ATP Tennis Match" | 16 open | 2 |
| `wta` | `KXWTAMATCH` "WTA Tennis Match" | 9 open | 2 |
| `ufc` | `KXUFCFIGHT` "UFC Fight" | 26 open | 2 |
| `f1` | **none.** Winner analogue: `KXF1RACE` (10 milestones; also `KXF1POLE` 1) | racing milestones | n/a |
| `pga` | **none.** Winner analogue: `KXPGATOUR` (16; also `KXDPWORLDTOUR` 9, `KXLPGATOUR` 4) | golf milestones | n/a |

**Alternates seen in discovery, not chosen:**
- **MLB:** `KXMLBSTGAME` (spring training) tied with `KXMLBGAME`, one
  settled milestone each. The tie is broken by the newer start (October
  against spring).
- **College hockey:** `KXNCAAHGAME` and `KXNCAAHOCKEYGAME` both exist. That
  isn't one of our leagues.
- **Tennis lookalikes:** `KXATPEXACTMATCH`, `KXATPCHALLENGERMATCH` and
  `KXATPGAME` exist too. `main_game_event_ticker` is what picks
  `KXATPMATCH`.

## 2. Listing window, NFL and NBA (and the rest of the measured five)

**ESPN's side is short.** `sports_events` holds games only through
**2026-11-12 04:00 UTC** for every measured league. The schedule job stores
about 8 days ahead; NFL ends at week 9's Monday game (Nov 10 01:15 UTC). So
"now to +21 days" was measured inside about **+7.4 days**, and beyond that
only Kalshi's own listing is known.

`status=unopened` returned 0 events for every series. Kalshi shows nothing
before a market opens, so the window is Kalshi's open time.

| League | Furthest ESPN game with a Kalshi event | Nearest upcoming ESPN game without one | Kalshi's furthest listed start | Lead (start − market open), hours |
|---|---|---|---|---|
| NFL | Nov 10 01:15 UTC (ESPN's last stored game; **all 15 matched**) | none inside `sports_events` | Nov 17 01:15 UTC (289 h ahead; through week 10) | 236 / 302 / 333 (min/median/max) |
| NBA | Nov 9 00:00 UTC (95.8 h ahead) | Nov 9 00:30 UTC, BKN at DAL (96.3 h ahead) | Nov 9 00:00 UTC | **95.9 / 95.9 / 95.9** |
| NHL | Nov 11 03:00 UTC (146.8 h) | Nov 12 00:00 UTC, TB at BUF | Nov 11 03:00 UTC | 165 / 167 / 168 |
| NCAAB | Nov 8 23:00 UTC (94.8 h) | inside the window: non-Division I games (below) | Nov 8 23:00 UTC | 93 / 95 / 96 |
| CFB | Nov 12 00:30 UTC (ESPN's last stored game) | Nov 11 05:00 UTC, UMass at Toledo: **a time-TBD hazard, not a gap** (§5) | Nov 18 20:00 UTC | 145 / 334 / 359 |

**Read plainly:**
- **NBA** markets open exactly ~96 h (4 days) before tip, every one. So a
  100% gate for NBA can only be measured on games starting within ~95 h of
  the check.
- **NCAAB** is the same, at ~4 days.
- **NHL** is ~7 days.
- **NFL** opens a week's slate 10 to 14 days ahead. At this run Kalshi
  listed weeks 9 and 10; ESPN's stored horizon ended inside week 9.
- **The 28 NBA and 6 NHL games ESPN has without a Kalshi event all start
  beyond Kalshi's listed range.** Inside the range there are **0 misses for
  NFL, NBA and NHL.**

**Missing inside Kalshi's range:**
- **CFB:** 1, the time-TBD case.
- **NCAAB:** 70.
  - 5 are matcher misses: Kalshi has the event, but the names, codes and
    home/away differ (§5).
  - 64 of the other 65 have a non-Division I opponent, judged by reading
    the names (Champion Christian, Bob Jones, Webster, Centre, Bethesda, …).
    That is not checked against a D-I list.
  - 1 is D-I against D-I: Incarnate Word at UTEP, Nov 8 23:00 UTC. It sits
    exactly at Kalshi's furthest listed start (94.8 h); edge of the window,
    not confirmed as a gap.

## 3. Ticker anatomy

US team leagues share one format:

```
<SERIES>-<YY><MON><DD><AWAY><HOME>
```

- **The date:** the scheduled start's US Eastern date.
- **No time part.**
- **Codes:** concatenated with no separator and 2–5 letters each, so the
  event ticker alone can't be split. Each market ticker's last segment is
  one team's code (`…-JACBAL-BAL`), and that is how the codes are read.

| Series | Events checked | Date = ET start date | Team order | Examples (ticker, milestone title, start UTC) |
|---|---|---|---|---|
| `KXNFLGAME` | 29 | 29 | away, home: 29 | `KXNFLGAME-26NOV05JACBAL` "JAC Jaguars at BAL Ravens" 11-06 01:15; `KXNFLGAME-26NOV08CINATL` "CIN Bengals at ATL Falcons" 11-08 14:30; `KXNFLGAME-26NOV08CLENO` "CLE Browns at NOLA Saints" 11-08 18:00 |
| `KXNBAGAME` | 35 | 35 | away, home: 35 | `KXNBAGAME-26NOV04ATLWAS` "ATL Hawks at WAS Wizards" 11-05 00:00; `KXNBAGAME-26NOV04PHIDET`; `KXNBAGAME-26NOV04CHABKN` |
| `KXNHLGAME` | 46 | 46 | away, home: 46 | `KXNHLGAME-26NOV04CHIFLA` "Chicago at Florida" 11-05 00:30; `KXNHLGAME-26NOV04OTTBUF`; `KXNHLGAME-26NOV04CGYSEA` |
| `KXNCAAMBGAME` | 184 | 184 | away, home: 184 | `KXNCAAMBGAME-26NOV04FDUBUT` "FDU at Butler"; `KXNCAAMBGAME-26NOV04LONGJOES`; `KXNCAAMBGAME-26NOV04UMESHP` |
| `KXNCAAFGAME` | 256 | **253** (3 dated a day early, below) | away, home: 256 | `KXNCAAFGAME-26NOV03BALLMASS` "Ball St. at UMass" 11-05 00:00; `KXNCAAFGAME-26NOV03CMUEMU`; `KXNCAAFGAME-26NOV03TOLSAC` |

The other series, 3 real events each (open where any, else settled):

| Series | Format | Examples |
|---|---|---|
| `KXMLBGAME` | date + **`HHMM`** (ET clock: `2000` for 00:00 UTC) + away + home, **`G1`/`G2`** on doubleheaders | `KXMLBGAME-26OCT282000MILTB` "Milwaukee vs Tampa Bay (Game 5)"; `-26OCT272000MILTB`; `-26OCT262000MILTB` |
| `KXEPLGAME` | date + two club codes; markets `<A>`, `<B>`, `TIE`; milestone titles use "vs" | `KXEPLGAME-26NOV06EVECOV` "Everton vs Coventry"; `-26NOV07LEETOT`; `-26NOV07ARSHUL` |
| `KXMLSGAME` | as EPL | `KXMLSGAME-26NOV07CHICLB` "Chicago Fire vs Columbus"; `-26NOV07CINNSH`; `-26NOV07DCUNYC` |
| `KXUCLGAME` | as EPL | `KXUCLGAME-26NOV04SLAARS` "Slavia Prague vs Arsenal"; `-26NOV04VIKSBH`; `-26NOV04RCLCOM` |
| `KXATPMATCH` | date + two 3-letter player codes | `KXATPMATCH-26NOV05SHERIN` "Shelton vs Rinderknech" 11-05 11:10; `-26NOV05BORBUS`; `-26NOV05HIJETC` |
| `KXWTAMATCH` | as ATP; **the date can be a day before the start** | `KXWTAMATCH-26NOV04UCHLIN` "Uchijima vs Linette", start 11-05 09:30 UTC; `-26NOV04ZAKADK` (11-05 11:50); `-26NOV05CIRSTO` |
| `KXUFCFIGHT` | date + two 3-letter fighter codes; **the date was the day before every fight shown** | `KXUFCFIGHT-26NOV06BASBRE` "Austin Bashi vs Lucas Brennan", start 11-07 22:40 UTC; `-26NOV06CACMIL` (11-07 22:20); `-26NOV06DELMAG` (11-07 23:20) |

**Not established:**
- **Home/away order in soccer:** the order of the two codes is not measured.
  The soccer milestones' "vs" titles don't say who is home; the milestone's
  team ids would.
- **When the date part was set:** for tennis and UFC, the date differs from
  the start's ET date in the examples shown. Whether it is the card or
  tournament day, or the day the market was made, isn't established.

**Two code sets inside Kalshi:**
- The milestone title "CLE Browns at **NOLA** Saints" uses `NOLA`, while the
  ticker and the market use `NO`.
- So the codes come from the market tickers, never from the title.

## 4. Team code differences (ESPN abbreviation ≠ Kalshi code)

The comparison is per matched game, both sides.

**NFL:** 30 of 32 teams compared. PIT and TEN have no game in
`sports_events` this week; Kalshi's codes for them are `PIT` and `TEN`.
2 differ:

| Team | ESPN | Kalshi |
|---|---|---|
| Jacksonville Jaguars | JAX | JAC |
| Washington Commanders | WSH | WAS |

**NBA:** all 30 teams compared. 6 differ:

| Team | ESPN | Kalshi |
|---|---|---|
| Washington Wizards | WSH | WAS |
| New York Knicks | NY | NYK |
| New Orleans Pelicans | NO | NOP |
| Utah Jazz | UTAH | UTA |
| San Antonio Spurs | SA | SAS |
| Golden State Warriors | GS | GSW |

**NHL:** all 32 compared, **0 differ.**

**CFB:** 129 teams compared, 14 differ (11%). 136 Kalshi codes weren't
compared, mostly FCS teams, which `sports_events` doesn't hold (FBS only).
Examples:

| Team | ESPN | Kalshi |
|---|---|---|
| Missouri | MIZ | MIZZ |
| NC State | NCSU | NCST |
| Texas A&M | TA&M | TXAM |
| South Carolina | SC | SCAR |
| Boise State | BOIS | BSU |
| Miami (OH) | M-OH | MOH |
| Louisiana | UL | ULL |
| Northwestern | NU | NW |
| Oklahoma | OU | OKLA |

**NCAAB:** 312 team-sides compared, 65 reported as differing. 8 of those
are not code differences but home/away swaps (§5), so **57 differ (18%).**
Examples:

| Team | ESPN | Kalshi |
|---|---|---|
| Indiana | IU | IND |
| Buffalo | BUF | BUFF |
| Butler | BTLR | BUT |
| UMass Lowell | UML | MASSL |
| Omaha | OMA | NEOM |
| Kansas City | KC | UMKC |
| Air Force | AF | AFA |
| William & Mary | W&M | WM |
| Detroit Mercy | DETM | DET |
| Jacksonville | JAX | JAC |

7 more codes went uncompared because the matcher missed the game: ALBY,
FDU, MIZZ, SCST, SCUS, SLU, UALR.

**Football against basketball, same school:** 13 schools differ in both
sports. Each side is consistent across the two:
- ESPN: MIZ, NCSU, TA&M, SC, BOIS, M-OH, UL, NU, OU, JXST, MTSU, CLT, CCU.
- Kalshi: MIZZ, NCST, TXAM, SCAR, BSU, MOH, ULL, NW, OKLA, JVST, MTU, CHAR,
  CCAR.

So for these, one college table would serve football and basketball. That
isn't checked beyond these 13, or for NHL or other sports.

## 5. Hazards

- **Same two teams twice on one date:**
  - **None** in the window: ESPN 0 and Kalshi 0 across the measured five
    (NFL, NBA, NHL, CFB, NCAAB).
  - **MLB doubleheaders (from Kalshi's 4,684 settled `KXMLBGAME` events):**
    10 same-date, same-teams groups. The pattern is a `G1`/`G2` suffix after
    the time, e.g. `KXMLBGAME-26SEP221305TBNYYG1` and `…26SEP221905TBNYYG2`.
  - **The suffix isn't consistent:** `KXMLBGAME-26SEP251910CHCBOS` (no
    suffix) is paired with `…26SEP251305CHCBOSG1`. 2,481 of the 4,684 carry
    the `HHMM` time part; when the format changed wasn't measured.
  - **So MLB needs the time and the suffix**, and the date and teams alone
    are not a key.
- **Neutral sites and home/away disagreement:**
  - Every Kalshi milestone title in the measured five says "at" (0 of 550 say
    "vs"). Kalshi doesn't flag a neutral site in the title.
  - `sports_events` doesn't store ESPN's neutral-site flag, so neutral sites
    **could not be counted**.
  - What was seen: **5 NCAAB games where ESPN and Kalshi disagree on which
    team is home.** They are Michigan/UConn, Portland/UT Arlington,
    Colorado/San Diego State and Nebraska/Providence (codes swapped), plus
    Missouri vs Saint Louis (ESPN home Missouri; Kalshi `MIZZSLU`, Saint
    Louis home).
  - Plausibly neutral or early-season events, not verified. **A mapping
    keyed on (home, away) misses them; key on the unordered pair.**
- **ESPN games Kalshi lacks inside Kalshi's window:**
  - **NFL, NBA, NHL:** 0.
  - **CFB:** 1, **UMass at Toledo**. ESPN has it at Nov 11 05:00 UTC with
    `time_tbd = true` (a midnight-ET placeholder). Kalshi has
    `KXNCAAFGAME-26NOV11MASSTOL` starting Nov 12 00:30 UTC (Nov 11, 7:30 PM
    ET). Same ET date, 19.5 h apart, so a start-time window fails.
    **A time-TBD game must match on ET date and teams.**
  - **NCAAB:** 70. 5 are matcher misses, where Kalshi has the game:
    - UAlbany at Manhattan;
    - SC State at USC Upstate;
    - Saint Louis/Missouri;
    - SE Louisiana at Little Rock;
    - Merrimack at UAlbany.

    The names differ ("University at Albany" against "UAlbany Great Danes",
    "USC Upstate" against "South Carolina Upstate Spartans") and so do the
    codes (ALBY/UALB, SCUS/UPST, UALR/LR).
    1 is D-I against D-I at the window's edge (§2). The other 64 have a
    non-D-I opponent, read from the names; full list in the run's JSON.
- **Kalshi events with no ESPN match** (ESPN's stored range only):
  - **NFL, NBA, NHL:** 0.
  - **CFB:** 62. 61 are FCS games, which ESPN's FBS-only CFB pull doesn't
    hold (`KXNCAAFGAME` mixes FBS and FCS). The 62nd is the UMass–Toledo
    time-TBD case.
  - **NCAAB:** 6: the 5 matcher misses above plus FDU at Butler, which
    ESPN has (`401902276`, live at the run) with ESPN codes BTLR and FDU
    against Kalshi's BUT and FDU, and "Fairleigh Dickinson" against "FDU".
    So 0 are real.
- **CFB tickers dated a day early (3 of 256):**
  - All three are dated **Nov 3** for games starting Nov 4 ET:
    `KXNCAAFGAME-26NOV03BALLMASS`, `-26NOV03CMUEMU`, `-26NOV03TOLSAC`.
  - The market rules read "originally scheduled for …", which fits a game
    moved after listing; not verified. **The ticker date is not a reliable
    join key.**
- **Home and away resolved from milestone team ids:** all 550 events, 0
  unresolved, 0 without a milestone.

**ESPN data read:**
- **The probe's query:** one query (642 rows; columns league, espn_id,
  starts_at, home, away, status, and the two ESPN abbreviations from
  `participants`), **64,640 bytes as printed, ~63 KB.** It covers the
  measured five leagues only, from 12 h before the run to +21 days.
- **Besides it:** 5 small inspection queries during the probe's
  development, not metered:
  - the column list;
  - per-league counts and date ranges;
  - one NFL row;
  - two lookups of specific games, 1 and 13 rows.

## 6. Calls, time, 429s

- **Each full run:** 703 Kalshi calls, all HTTP 200, **0 rate-limited
  (429)**, 180.3 s (second run) and 182.8 s (first). Paced at 0.25 s.
- **Of which:**
  - 1 series list;
  - 298 candidate pages (game series), plus 164 settled pages for the
    candidates with no open event;
  - 196 winner-candidate pages (golf and motorsport);
  - 11 pages for the 5 measured leagues, open and unopened;
  - 24 for the MLB settled season;
  - 9 for the other leagues' examples.
- **Exploration before the script** (series list, shape peeks, one
  `/milestones` look, a 298-series sweep): about 305 calls, no backoff
  warnings.

## 7. What this means for Phase 2 (findings, not design)

- **Discovery:** discover through milestones (`details.league` or `tour`,
  then `main_game_event_ticker`), not ticker spelling. Several lookalike
  series exist per sport.
- **Join key:** the unordered team pair, with the ET date and a start
  window. Start time alone fails on ESPN's time-TBD games. Home/away alone
  fails on the 5 swapped NCAAB games. The ticker date fails on moved games.
  Code maps are needed:
  - NFL 2 entries, NBA 6, NHL 0;
  - CFB ~14 of 129 teams seen;
  - NCAAB ~57 of 312.

  The college maps are partial: they cover only teams with a game in the
  window. The 13 schools seen in both sports had the same code pair in
  each.
- **The 100% gate's window is Kalshi's listing lead:**
  - NBA and NCAAB: ~95 h;
  - NHL: ~165 h;
  - NFL: 10–14 days (week slates);
  - CFB: 6–15 days.

  A gate measured on ESPN's whole ~8-day horizon would count NBA games
  Kalshi hasn't opened yet.
- **Scope gaps:**
  - CFB on Kalshi includes FCS, which `sports_events` doesn't.
  - NCAAB on ESPN includes games against non-D-I teams, which Kalshi
    doesn't list.

## Pending (not measured)

- ESPN's neutral-site flag; not stored in `sports_events`.
- NFL and NBA beyond ESPN's stored ~8 days: the brief's full 21-day window.
- Soccer home/away order in tickers.
- The tennis and UFC date rule.
- When MLB tickers gained the time part.
- Whether the 3 early-dated CFB tickers are moved games.
- Whether the 5 swapped NCAAB games are neutral-site.
