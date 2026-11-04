# Sports data layer (Chat 7 Phase 1)

The schedule, status, score and broadcasters for every sport ESPN and Kalshi
both cover, in one table, `sports_events` (db/065). It is the layer Game Day
(Phase 3), the Kalshi mapping (Phase 2) and the Live Bet Tracker (Chat 9)
read. **Phase 1 only:** no Kalshi mapping (the column exists, NULL), no Game
Day UI, no pushes.

Owner decisions (2026-11-04): store every league below; Game Day shows the
owner's five first (CFB, NFL, NBA, F1, golf; `BUILD_BRIEF.md` §10); CFB is all
FBS; F1 is qualifying, sprint and race; golf is the tournament's rounds plus
the followed players' positions while live; broadcasters as ESPN gives them;
live games refresh every 60 s, schedules daily.

## 1. Leagues

One row per league in `sports_leagues` (db/065), the registry the worker reads
each schedule run.

| code | sport | ESPN path | params | fetch | kind | owner five |
|---|---|---|---|---|---|---|
| `nfl` | football | `football/nfl` | | per day | game | yes |
| `cfb` | football | `football/college-football` | `groups=80`, `limit=500` | per day | game | yes |
| `nba` | basketball | `basketball/nba` | | per day | game | yes |
| `f1` | racing | `racing/f1` | | range | race_session | yes |
| `pga` | golf | `golf/pga` | | range | golf_round | yes |
| `mcbb` | basketball | `basketball/mens-college-basketball` | `groups=50`, `limit=500` | per day | game | |
| `nhl` | hockey | `hockey/nhl` | | per day | game | |
| `mlb` | baseball | `baseball/mlb` | | per day | game | |
| `epl` | soccer | `soccer/eng.1` | | per day | game | |
| `mls` | soccer | `soccer/usa.1` | | per day | game | |
| `ucl` | soccer | `soccer/uefa.champions` | | per day | game | |
| `atp` | tennis | `tennis/atp` | | range | match | |
| `wta` | tennis | `tennis/wta` | | range | match | |
| `ufc` | mma | `mma/ufc` | | range | bout | |

Endpoint: `https://site.api.espn.com/apis/site/v2/sports/<path>/scoreboard?dates=…`.
Unofficial, no key, can change without notice (MASTER_PLAN §7).

## 2. Step 0 probe (2026-11-04, read only)

One call per league for today (`dates=20261104`) and one for a range
(`dates=20261104-20261110`), plus single future days and completed days for
the final shapes. Raw responses stayed in the scratchpad; trimmed real
fixtures (event lists cut, nothing inside an event changed) are in
`workers/tests/fixtures/sports/espn_*.json`, 18 files, 549 KB.

**What works:**

- **A date range is refused for every team sport:** HTTP 400,
  `{"code":400,"message":"Failed to get events endpoint."}`, for 7-, 3- and
  2-day ranges alike, with or without `limit`. Single days (`dates=YYYYMMDD`)
  work. So the schedule pulls team leagues **one call per date**. Tennis,
  golf, F1 and UFC accept a range (one call each).
- `dates` is the **US Eastern** date: NBA `20261104` held the 7:00 PM ET tip
  stored as `2026-11-05T00:00Z`.
- **CFB:** with a `dates` value, `groups=80` and no `groups` both returned the
  same 51 games for Sat Nov 7. With no `dates` at all the default is the
  current week's ranked games only (16). We always pass `dates`, `groups=80`
  and `limit=500`. Men's college basketball: `groups=50` (Division I),
  `limit=500` (65 games on Thu Nov 5).
- No `dates` on NFL returns the current week (15 games).
- **A single date inside a multi-day event returns that event** (checked:
  golf `20261106` mid-tournament, F1 `20261107` and `20261108` mid-weekend,
  ATP `20261106`, WTA `20261110`, UFC on the card's day). So the live job asks
  only for the dates in play, for every league.
- Every scoreboard's top level carries `events`, `leagues` and `provider`
  (plus `week`, `groups`, `day`, `season` on some): `events` and `leagues`
  are the contract checked on every response, an empty day included.

**Sizes** (bytes as served before decompression are the `gzip` column: ESPN
answers `Content-Encoding: gzip`; parsed size is `bytes`):

| call | bytes | gzip | events |
|---|---:|---:|---:|
| `nfl` Sun Nov 8 | 234,193 | 18,611 | 13 |
| `nfl` Sun Nov 1 (finals) | 107,078 | 11,089 | 12 |
| `cfb` Sat Nov 7 (all FBS) | 958,542 | 66,744 | 51 |
| `cfb` Wed Nov 4 | 60,646 | 6,887 | 3 |
| `nba` Wed Nov 4 | 276,420 | 21,667 | 14 |
| `mcbb` Wed Nov 4 | 300,779 | 25,660 | 26 |
| `mcbb` Thu Nov 5 | 722,287 | not measured | 65 |
| `nhl` Wed Nov 4 | 85,089 | 8,050 | 4 |
| `ucl` Wed Nov 4 (2 live) | 123,180 | 9,153 | 9 |
| `epl` Sun Nov 8 | 65,189 | 5,709 | 5 |
| `mlb`, `mls`, `epl` empty day | 1,393–2,503 | ~600 | 0 |
| `atp` range (Paris Masters + 1) | 275,693 | 21,465 | 2 tournaments |
| `wta` range | 559,829 | 40,930 | 8 tournaments |
| `pga` range (scheduled) | 42,453 | 5,165 | 1 |
| `pga` last week (final, hole by hole) | 698,156 | 21,518 | 1 |
| `f1` range (São Paulo GP) | 5,784 | 1,246 | 1 (5 sessions) |
| `f1` Mexico GP (final) | 39,077 | 2,781 | 1 |
| `ufc` range (one card) | 25,810 | 2,747 | 1 (15 bouts) |

The biggest single parse is a CFB Saturday (~1 MB of JSON), then a live golf
tournament (~0.7 MB) and a full NCAAB day (~0.7 MB).

**Status values seen** (`competitions[].status.type`, name|state):
`STATUS_SCHEDULED|pre`, `STATUS_IN_PROGRESS|in`, `STATUS_SECOND_HALF|in`,
`STATUS_FINAL|post`, `STATUS_RETIRED|post`, `STATUS_WALKOVER|post`,
`STATUS_CANCELED|post`. ESPN's documented-by-use others (not seen today, mapped
by name in §4): `STATUS_HALFTIME`, `STATUS_END_PERIOD`, `STATUS_FINAL_OT`,
`STATUS_FULL_TIME`, `STATUS_POSTPONED`, `STATUS_DELAYED`, `STATUS_RAIN_DELAY`,
`STATUS_SUSPENDED`, `STATUS_ABANDONED`, `STATUS_FORFEIT`,
`STATUS_PLAY_COMPLETE` (golf, a round finished).

**Broadcast field:** `competitions[].broadcasts[]` = `{market, names[]}`;
`market` is `national`, `home` or `away`. A flat `broadcast` string repeats
the first name. 2 of 14 NBA and 3 of 26 NCAAB games had no broadcasts; every
NFL and CFB game had one, all national. F1: Apple TV. Golf: "Golf Chnl".
UFC: Paramount+. UCL: Paramount+.

## 3. Event model per kind

One `sports_events` row per thing that starts, has a status and ends.
`(league, espn_id)` is unique.

| kind | leagues | one row is | espn_id | parent_espn_id | event_name | session |
|---|---|---|---|---|---|---|
| `game` | team sports, soccer | a game | the event id | NULL | ESPN's name ("BYU Cougars at Utah Utes") | NULL |
| `race_session` | f1 | qualifying, sprint qualifying, sprint, race | the competition id | the Grand Prix id | the GP's name | `Qual`, `Race`, … |
| `golf_round` | pga | one round of a tournament | `<event id>-r<n>` | the tournament id | the tournament's name | `R1`…`R4` |
| `bout` | ufc | one fight on a card | the competition id | the card id | the card's name | the weight class |
| `match` | atp, wta | one singles match | the competition id | the tournament id | the tournament's name | the round ("Semifinal") |

- **Teams** (`game`): `home`, `away` (display names), and `participants` =
  `{home: {id, abbr, name, rank, conference_id}, away: {…}}`. `rank` is
  ESPN's `curatedRank.current` when under 99 (CFB/NCAAB AP rank), else absent.
  `conference_id` is ESPN's (`8` is the SEC), so Phase 3 can surface Alabama,
  ranked, SEC and national TV first. `score` = `{home, away}` once the game
  has started.
- **F1**: practice sessions (`FP1`–`FP3`) are skipped; every other session is
  stored, so a sprint weekend's sessions arrive under whatever ESPN names them
  (no sprint weekend was on the calendar to probe). `score.results` = the top
  10 by ESPN's `order` once the session has started; `period` is the lap.
- **Golf**: ESPN's scoreboard has **one competition per tournament**, no
  per-round rows and no tee times. Rounds are derived: one per day from the
  tournament's `date` to its `endDate` (4 for a regular event), each
  `time_tbd`, starting at ESPN's day start (05:00 UTC, midnight Eastern). A
  round's status comes from the tournament's status and `period` (the round
  in play): earlier rounds `final`, the current one `live` (`final` once ESPN
  says `STATUS_PLAY_COMPLETE`), later ones `scheduled`. The round in play
  carries `score` = `{round, leaders: top 5, followed: […]}`, each
  `{name, pos, to_par, today, thru}`; `pos` is ESPN's `order` with ties shown
  as `T3`, `today` and `thru` from that round's line score. Followed players:
  **Scottie Scheffler, Brooks Koepka** (`sports/espn.py` `FOLLOWED_GOLFERS`).
  A finished round keeps the standings it last had (a later run never
  clears a stored score).
- **UFC**: one row per bout; `participants` = the two fighters with `winner`.
- **Tennis**: singles only (`mens-singles`, `womens-singles` groupings).
  **Scope: "majors+"** is read as tournaments ESPN flags `major: true`, plus
  any whose name contains "Masters" or "Finals" (Paris Masters, ATP Finals,
  WTA Finals). WTA 1000 events carry neither marker and are not stored;
  **owner to confirm**. A match with a `TBD` player (ESPN id `-4`) is not
  stored until both players are known. `score.sets` = each player's set
  scores.

Common columns: `starts_at` (timestamptz, ESPN's `date`, UTC), `time_tbd`
(ESPN's `timeValid` false), `broadcast` (text[], every name in ESPN's order,
deduplicated), `national_tv` (any `market = national`), `venue`, `status`
(§4), `espn_status` (ESPN's name), `status_detail` (ESPN's `shortDetail`),
`period`, `clock` (`displayClock`), `kalshi_event_ticker` (NULL until Phase 2;
the worker's upsert never touches it), `content_hash`, `created_at`,
`updated_at`, `is_test`.

## 4. Status lifecycle

`scheduled → live → final`, with `delayed`, `suspended`, `postponed` and
`canceled` off the main line. Mapped from ESPN's `state` first, then name:

| ESPN state | name | ours |
|---|---|---|
| `pre` | `STATUS_POSTPONED` | postponed |
| `pre` | `STATUS_CANCELED` | canceled |
| `pre` | `STATUS_DELAYED`, `STATUS_RAIN_DELAY` | delayed |
| `pre` | anything else | scheduled |
| `in` | `STATUS_DELAYED`, `STATUS_RAIN_DELAY`, `STATUS_SUSPENDED` | delayed |
| `in` | anything else | live |
| `post` | `STATUS_POSTPONED` | postponed |
| `post` | `STATUS_CANCELED`, `STATUS_ABANDONED` | canceled |
| `post` | `STATUS_SUSPENDED` | suspended (to resume another day) |
| `post` | anything else (`FINAL`, `FINAL_OT`, `FULL_TIME`, `RETIRED`, `WALKOVER`, `FORFEIT`, …) | final |

A **state** other than `pre`, `in`, `post` is a shape failure (§7). An
unfamiliar **name** maps by its state and is kept verbatim in `espn_status`,
so nothing is lost and nothing is guessed. A postponed game keeps its row;
when ESPN reschedules it, the same `espn_id` gets the new `starts_at`.
Statuses are ESPN's word and are not guarded against moving backwards (a
stat correction can reopen a final); every write is ESPN's latest.

## 5. Jobs and cadence

**`sports_schedule`**: daily 04:00 America/Chicago, and at boot. Every active
league: team leagues one call per Eastern date from yesterday to today + 7
(9 calls each); range leagues one call for the same span. Yesterday is in the
window so a game that ended while the worker was down still gets its final.
It fills the in-memory cache (below). **It is also the daily contract test:**
every response is validated (§7), including an empty day's top-level shape,
and the job note lists each league's event count.

**`sports_live`**: every 60 s. It reads nothing from the database and calls
ESPN only for a league with something in play, decided from the cache:

- **hot (every 60 s):** an event `live` or `delayed`, until `starts_at` + its
  kind's ceiling (game 8 h, race session 5 h, bout 8 h, match 8 h, golf round
  16 h); or a `scheduled` event with a real time from 15 min before
  `starts_at` to 3 h after it.
- **warm (every 10 min):** a `time_tbd` event that is not finished, from its
  `starts_at` (ESPN's day start) for 24 h, so a golf round or a tennis match
  with no published time is noticed within 10 min of going live; and any
  hot event past its ceiling, up to 24 h after `starts_at`.
- otherwise **idle: zero calls.**

A polled league is fetched for the Eastern dates of its in-play events only
(team leagues one call per date, range leagues one call). An event the
response should contain but does not is logged and counted in the note; it is
never deleted.

**Cache:** the last schedule run's events for its 9-day window: key, kind,
`starts_at`, `time_tbd`, status, content hash. A few thousand small tuples.
It decides the live tiers and skips sending rows whose hash is unchanged.
Rebuilt from ESPN at every boot (the boot run), never read back from the
database.

## 6. Writes, egress, RAM, calls

- **Upsert by `(league, espn_id)`, one statement per fetch:** the rows go up
  as one JSON array (`jsonb_to_recordset`), `ON CONFLICT … DO UPDATE … WHERE
  content_hash IS DISTINCT FROM EXCLUDED.content_hash AND NOT is_test`, so an
  unchanged row is not written, and a test row is never touched. RETURNING
  one boolean per written row. No `SELECT *` anywhere; the worker's only read
  is the registry (14 short rows) per schedule run.
- **Supabase egress:** ~0 KB per live run (no reads), a few KB per schedule
  run. Every job's line carries RSS and ~KB read (`core/jobs.run_tracked`).
- **RAM:** one response at a time: fetched, validated, parsed to small rows,
  dropped; `memory.release()` after each job. Peak transient: the CFB
  Saturday parse (~1 MB JSON, several MB of Python objects).
- **ESPN calls:** schedule ~95 a day (10 team leagues × 9 + 5 range leagues),
  plus each boot. Live: 0 when nothing is in play; 60 an hour per hot league
  per date; a heavy Saturday (CFB, NCAAB, NBA, NHL, EPL, UFC hot together)
  ~360 an hour, ~18 MB/h over the wire, all inbound to Railway.

## 7. Failure behavior

- **Schema-validated, fail loudly:** every field the parser reads is checked
  for presence and type (`sports/espn.py`). A missing or renamed required
  field raises `EspnShapeError(league, path)`. Optional fields (broadcasts,
  venue, score before the start, rank, linescores) may be absent; a required
  one is never stored as NULL.
- One league's failure (HTTP error, timeout, bad JSON, shape) does not stop
  the others in the same run; the run then fails naming every failed league,
  into `job_health`, so the health monitor pushes on two failures in a row.
  On a failed league fetch nothing from that league is written.
- HTTP 429 or 5xx: one retry after 2 s, then the league fails for this run.
- **Fallback (API-Sports): an interface wired to nothing.**
  `sports/provider.py` defines the provider contract (fetch a league's
  events for dates) with `EspnProvider` the only real one. `SPORTS_PROVIDER`
  (`espn`, the default; `api_sports`) selects it. `api_sports` refuses
  loudly at the job's start (`ProviderUnavailable`: no key, not built). The
  switch: get the API-Sports free key, implement `ApiSportsProvider.fetch`
  against the same `SportsEvent` rows, set `API_SPORTS_KEY` and
  `SPORTS_PROVIDER=api_sports` on Railway. Not built in Phase 1 (owner: only
  if needed).

## 8. What ESPN lacks

- **Date ranges for team sports** (400): one call per day instead.
- **Golf:** no per-round competitions and no tee times in the scoreboard;
  rounds are derived (§3), and their start is a day, not a time.
- **Tennis:** no broadcasters on any match; unplayed rounds carry `TBD`
  players and day-only times; ESPN marked some unplayed finals
  `STATUS_CANCELED` (stored as ESPN says; the next run updates them); one WTA
  event came with no status and no matches (skipped: nothing to store). No
  marker for WTA 1000 / ATP 500 tiers.
- **F1:** no sprint weekend on the calendar to probe; sprint session names
  are unverified (stored under whatever ESPN names them, practice excluded).
- **UFC:** the method of victory is not in the scoreboard (only `winner`).
- **MLB:** off season (0 events on Nov 4); the shape is the team-game shape,
  fixture `espn_mlb_empty.json` is the empty day.
- Odds (`competitions[].odds`) are present on some games and **not stored**
  in Phase 1.

## 9. Out of scope (Phase 1)

Kalshi event mapping (Phase 2, `kalshi_event_ticker` stays NULL), Game Day
UI and the Brief's slate (Phase 3; `ctx_sports_today` is registered as a
provider but is **not** a brief section; Wags reads it from the paste on,
since every enabled provider is in `context_snapshot()`), pushes, odds, box scores and player
stats, leagues beyond §1 (other soccer leagues later), and the API-Sports
implementation.
