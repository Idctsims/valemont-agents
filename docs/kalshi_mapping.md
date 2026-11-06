# Kalshi mapping (Chat 7 Phase 2)

Each `sports_events` row (ESPN) is linked to its Kalshi event, or told why
not. Migration `db/066`; job `kalshi_map` (`workers/sports/kalshi_map.py`);
shapes `workers/venues/kalshi/events.py`. The design was decided from the
probe (`docs/kalshi_mapping_probe.md`, owner, 2026-11-04).

**Status, 2026-11-05: built and tested on branch `chat7-phase2`. Not merged,
not deployed** (owner's gates: Phase 1 readings in flight on the worker;
Railway Hobby funded first). db/066 is waiting for the owner's review before
the paste.

## 1. What is stored

**`sports_leagues`** gains three columns:
- `kalshi_series`: the league's Kalshi series. The job checks once per
  Chicago day that each series exists.
- `kalshi_map`: the match method, `team_pair`, `player_pair`, `by_date` or
  `off`.
- `days_ahead`: the schedule pull's horizon in days.

| league | series | method | days_ahead |
|---|---|---|---:|
| `nfl` | `KXNFLGAME` | team_pair | 14 |
| `cfb` | `KXNCAAFGAME` | team_pair | 14 |
| `nba` | `KXNBAGAME` | team_pair | 7 |
| `mcbb` | `KXNCAAMBGAME` | team_pair | 7 |
| `nhl` | `KXNHLGAME` | team_pair | 7 |
| `epl`, `mls`, `ucl` | `KXEPLGAME`, `KXMLSGAME`, `KXUCLGAME` | team_pair | 7 |
| `atp`, `wta` | `KXATPMATCH`, `KXWTAMATCH` | player_pair | 7 |
| `ufc` | `KXUFCFIGHT` | player_pair | 7 |
| `f1` | `KXF1RACE` | by_date | 7 |
| `pga` | `KXPGATOUR` | by_date | 7 |
| `mlb` | `KXMLBGAME` | **off** (out of scope: off-season) | 7 |

**`sports_team_aliases`** maps an ESPN team code to its Kalshi code.
- It stores exceptions only: no row means ESPN's code is Kalshi's.
- Uniqueness: one row per (league, ESPN code) and per (league, Kalshi code).
  An identity row is refused.
- Access: the owner and `valemont_readonly` read it; only a migration writes
  it.
- Seed, 91 rows:
  - NFL 2, NBA 6, CFB 14, NCAAB 57: from the probe.
  - **12 more, marked FOR REVIEW in the file:** NCAAB 4, EPL 5, MLS 3, from
    the dry run (§5).

**`sports_events`** gains these columns:

| column | holds |
|---|---|
| `neutral_site` | ESPN's flag (below) |
| `link_state` | `linked`, `not_listed`, `not_linked`, `out_of_scope`; NULL until first evaluated |
| `link_reason` | why it isn't linked: required for every state but `linked` |
| `link_method` | how it was linked: present exactly when linked |
| `linked_at` | when the link was made; stamped by the database |
| `link_state_at` | when the state last changed; stamped by the database |
| `home_away_swapped` | Kalshi's home side differs from ESPN's; linked rows only |
| `link_conflict` | a later, different unique candidate; linked rows only, never equal to the ticker |
| `link_conflict_at` | when the conflict was flagged; stamped by the database |

- **Every CHECK is NULL-safe** (db/031's lesson).
- **`sports_events_kalshi_ticker`** is a partial unique index: one team game,
  tennis match or UFC bout per Kalshi event (`kind IN ('game', 'match',
  'bout')`; owner, Nov 4). Golf rounds share their tournament's event and F1
  sessions their weekend's. The worker's `UNIQUE_KINDS` matches the index,
  and a fast test fails if they drift apart.
- **`link_method`** also allows `manual`, set only by a hand repair (below).
- **Backward-compatible with the deployed Phase 1 worker** (owner, Nov 4):
  - every new column is nullable with no default value, so Phase 1's upsert,
    which names none of them, inserts as before;
  - `neutral_site` is required only by the Phase 2 parser, never by the
    database;
  - both triggers fire only when a link column is set or changes, which
    Phase 1 never does.

  The self-test runs Phase 1's exact statement shape: an insert, then a live
  score and clock update on a linked row. Before the paste, the deployed
  text from `origin/main` was also run verbatim against a local db/066 (PG
  17). It inserted the row and updated it; the link was unchanged and
  `neutral_site` stayed NULL.

**`neutral_site`** is ESPN's `competitions[].neutralSite`:
- **Required** for football, basketball and hockey games. It is present in
  every fixture of those sports, and a missing flag fails that league loudly.
- **Optional** for MLB, whose shape is unverified in the off-season.
- **NULL for soccer**, whose scoreboard carries no flag.

The flag is part of the content hash, so every game row is rewritten once
after the deploy, as its flag arrives.

**`sports_events_link_guard`** is a trigger, BEFORE INSERT and BEFORE UPDATE.
It fires only when a link column changes, so ESPN's upsert never reaches it.
It enforces the **no-relink rule**: once a row is linked, its ticker, method
and home/away flag never change, and a relink or an unlink raises, unless
the repair log holds a row for exactly that change (below). It also stamps
`linked_at`, `link_state_at` and `link_conflict_at` from `now()` and ignores
any value a client sends for them.

**The repair path: hand use only** (owner, Nov 4).
- `sports_event_relinks` is an append-only, TRUNCATE-guarded log. Each row
  holds the event, its old ticker, the new ticker (NULL means unlinked), the
  reason, and the role, transaction and time, all stamped by the database.
- `sports_event_relink(event, new_ticker | NULL, reason[, swapped])` is
  worker-only (no client role and not the read-only role can run it). It is
  run only from `scripts.sports_event_relink`, and a fast test fails if
  anything else in the worker calls it. It writes the log row first, then
  the link, in one transaction.
- The guard allows a linked row's ticker to change only when a log row
  matches this event, its current ticker and the new one, **written in the
  same transaction**. A log row for a different change does not count.
- **A relink** sets method `manual`, records the given home/away flag (NULL
  when not given) and clears any conflict.
- **An unlink** is for good. The row becomes `out_of_scope` with the reason
  "unlinked by hand: …", kalshi_map never reads it again, and the guard
  refuses any later link to it. To move a link, relink instead.

```
..\venv\Scripts\python.exe -m scripts.sports_event_relink --event 123 --ticker KX... --reason "..." [--swapped | --not-swapped] [--dry-run]
..\venv\Scripts\python.exe -m scripts.sports_event_relink --event 123 --unlink --reason "..." [--dry-run]
```

## 2. The rules

**Reading Kalshi.**
- The job walks series → events → markets only: never the keyword search,
  never the raw open-markets list.
- It asks for `status=open`, with nested markets and milestones, 100 events
  a page.
- Each event is reduced to its ticker, the ticker's date (US Eastern), its
  milestone's start, and its sides.
- A side is a market other than `TIE`: its code is the market ticker's last
  segment, and its name is the market's `yes_sub_title`.
- Kalshi's (away, home) order is read from the ticker's two codes run
  together.

**team_pair** (NFL, CFB, NBA, NCAAB, NHL, soccer):
- The match key is the same series and the **unordered** pair of Kalshi
  codes, with ESPN's codes passed through the aliases.
- The Kalshi event's ticker date must be within **±1 US-Eastern day** of
  ESPN's date.
- It never matches on the start time or on the ticker date alone. An
  early-dated ticker (`…NOV03BALLMASS` for a Nov 4 game) and ESPN's time-TBD
  placeholder (UMass–Toledo) both match.
- `home_away_swapped` is set when Kalshi's home side is ESPN's away team.
  It stays NULL for soccer, whose home side is out of scope.

**player_pair** (ATP, WTA, UFC):
- The two names must match. Accents, case and word order are folded, so
  ESPN's "Wang Xinyu" matches Kalshi's "Xinyu Wang".
- If the full names differ, the surnames are tried.
- The date rule is ±1 day. UFC tickers are dated the day before the fight.

**by_date** (best effort):
- **F1:** the `Race` session links to the `KXF1RACE` event whose milestone
  start is within ±1 day. Every other session is `out_of_scope`, because
  Kalshi lists only the race winner.
- **Golf:** a round on day D links to the `KXPGATOUR` event whose start falls
  between D−4 and D+1.

**Deciding a row:**
- **Linked:** exactly one candidate, which no other row claims and isn't
  already linked elsewhere. Golf rounds and F1 sessions may share their
  event.
- **not_linked, two or more candidates:** the reason lists them.
- **not_linked, one event claimed by several ESPN rows:** every one of those
  rows, with the reason.
- **not_listed:** no candidate, and the row starts after the series' latest
  listed event start. Futures more than 30 days out, such as the 2027
  Masters, don't count toward that latest start.
- **not_linked, no event:** no candidate, inside the window. The reason names
  the pair, or the names, and the ±1 day.
- **Started:** a row that has started and has no open candidate keeps its
  earlier state. Its event closed at the start, so there is no new evidence.
  A row never checked before it started is `not_linked`, and the reason says
  it had started.
- **out_of_scope:**
  - leagues whose `kalshi_map` is `off` (MLB);
  - F1 sessions other than the race;
  - FCS games, which never reach `sports_events`.

  An out-of-scope row is not read again.

**The no-relink rule.**
- A linked row is never decided again.
- If a later run finds a different unique candidate, the row keeps its link.
  The candidate goes into `link_conflict`, and a WARNING is logged once (the
  flag is written once).
- The job's write skips any linked row, and the database guard refuses a
  relink from any writer that has no log row (the repair path, §1).

## 3. The job

- **When:** `kalshi_map` is chained after every `sports_schedule` run
  (04:00 Chicago and at boot), and also runs at 10:00, 16:00 and 22:00
  Chicago. That is every 6 h; `expected_interval_s` is 6 h.
- **Reads:**
  - The registry and the aliases.
  - Every row of every league that starts from 6 h ago on, except
    out-of-scope rows; **a time-TBD row (a golf round, most tennis) one day
    longer**, since its `starts_at` is ESPN's day start, not a time (owner,
    Nov 5: WWT R1, row 206, was first seen 15 h past its placeholder and
    was never read, so it stayed "Not checked yet"; `KALSHI_MAP_TBD_GRACE`).
    Columns: id, league, kind, session, `starts_at`, the
    two ESPN codes or the two names, `link_state`, the ticker,
    `link_conflict`.
  - Linked rows are read as well, which the brief didn't ask for. The
    no-relink check needs them, and so does refusing an event that another
    row already holds.
- **Contract test:**
  - Once per Chicago day, `GET /series/{ticker}` for every registered series.
  - Every `/events` page is shape-checked, so every run tests the contract.
  - A changed shape raises `KalshiShapeError` naming the series and the path.
- **Writes:** two statements, each changing a row only where a link column
  would change.
  - The decided states go in one statement, which never touches a linked row.
  - The conflict flags go in the other.
  - The worker also remembers each row's last confirmed decision and sends
    only changes. After a restart it sends every row once, and the database
    writes none of them.
- **Failure:** one league failing (HTTP error, a 429 after the client's
  backoff, a shape change) doesn't stop the others. Their rows are written,
  then the run fails, naming each failed league.
- **Job note:**
  - per-league state counts;
  - sent, written and conflicts;
  - Kalshi calls, 429s and seconds;
  - Kalshi events no row claims;
  - the contract result.

  `run_tracked` adds RSS and the KB read.

**Cost, measured locally on Nov 5:**

| measure | value |
|---|---|
| Kalshi calls per run | 29, including the 14-series daily check; 15–16 without it |
| 429s | 0 |
| Kalshi time | ~4.2–4.8 s |
| Largest page parse | ~2.6 MB of Python objects (`tracemalloc`); ~0.3 MB held per series after |
| Database read | ~66.5 KB for the rows (68,096 bytes metered, 730 rows), plus the registry (473 B) and the aliases (91 short rows) |

Five runs a day read ~0.35 MB. Worker RSS on Railway is unmeasured: there is
no deploy yet.

## 4. Tests

**`tests/test_kalshi_map.py`, 45 tests.** The fixtures are trimmed real
responses in `tests/fixtures/kalshi/`, recorded Nov 5. Chosen events are kept
whole with their milestones; golf and F1 market lists are cut to 4.
- **Shapes:** every fixture parses; every series check passes; every changed
  shape is loud.
- **Matching:** NFL with the aliases, NBA, the swapped NCAAB game, the ±1 day
  rule (an early ticker, a TBD start, two days off), a rematch two days
  apart, two candidates, two ESPN rows for one event, no-relink and the
  conflict flag, the horizon, started rows, soccer, tennis names, UFC,
  F1 race and qualifying, golf rounds and futures, MLB off.
- **The job:** stubbed ledger and a fake transport. Covered: write-once,
  restart, one contract check a day, linked rows never sent, one league
  failing, a shape change, a missing series, a 429.
- **Neutral site** and the 14-day horizon.
- **db/066:** every VALUES tuple in the file is checked against its column
  count.
- **Mutation-checked**, Nov 5. Each mutation below was applied in memory and
  turned the suite red; the unmutated module is 45/45.
  - match the ordered pair: 5 tests fail;
  - ±0 days: 5;
  - the ticker date only: 3;
  - take the first of two candidates: 1;
  - relink a linked row: 2;
  - no `not_listed` horizon: 3.
- **Added Nov 4 (now 50 tests):**
  - `UNIQUE_KINDS` equals the index's kinds;
  - the self-test's Phase 1 statements name exactly the deployed column list;
  - the only callers of the repair are the ledger and the script;
  - the script relinks or unlinks once and writes nothing on a refusal.

**`tests_live/test_kalshi_map_sql.py`, 10 tests,** runs the job's own SQL,
the guard and the repair:
- the narrow read; write-once; no-relink, both skipped by the write and
  refused by the database;
- the conflict flag; one game per event, with golf rounds and F1 sessions
  sharing and tennis matches refused;
- ESPN's upsert leaving the link columns alone while writing `neutral_site`;
- the repair:
  - refused without its log row, or with a log row for another change;
  - a relink with its log row, and the log row's stamps;
  - after a relink, a second move needs a second log row;
  - the unlink: out of scope for good, and the job cannot link it again;
  - the function's refusals;
  - the log is append-only, and TRUNCATE is refused.

**db/066's self-test was run before handover** on a throwaway local PG 17
cluster, with stubs for the db/001–db/064 objects it needs and db/065's two
tables. The file ran as one transaction (`psql -1`), the way the SQL Editor
runs a paste, and passed. Eight mutations of the file each failed it:
- uniqueness for golf only;
- uniqueness for games only;
- no log row needed;
- a log row for any new ticker;
- unlinks not kept;
- the function skipping the log;
- `neutral_site NOT NULL`;
- the log mutable.

The 10 live tests also passed against that database. The cross-transaction
case can't be tested inside one DO block or a rolled-back live test, so it
was checked once by hand on the scratch database: a log row committed in an
earlier transaction did not open the guard in a later one ("never relinked
or unlinked without its sports_event_relinks row"; the ticker was
unchanged).

`test_sports_events_sql.py` skips until db/066 is pasted, because the upsert
now writes `neutral_site`.

## 5. Dry run, 2026-11-05 02:11 UTC (before the paste)

`scripts.kalshi_map_dry_run` runs the job's own matching on production rows,
read as `valemont_readonly`, against Kalshi's live listing. It writes
nothing. The registry and aliases come from db/066's marked blocks, all 91
rows. Before the paste every row reads as never evaluated, so rows that had
already started show up as "had started" (§2).

| league | rows | linked | not_listed | not_linked | out_of_scope | Kalshi events unclaimed |
|---|---:|---:|---:|---:|---:|---:|
| NFL | 15 | **15** | 0 | 0 | 0 | 0 |
| NBA | 63 | **39** | 24 | 0 | 0 | 0 |
| CFB | 67 | 67 | 0 | 0 | 0 | 61 (FCS) |
| NCAAB | 445 | 182 | 190 | 73 | 0 | 0 |
| NHL | 52 | 46 | 6 | 0 | 0 | 0 |
| EPL | 10 | 10 | 0 | 0 | 0 | 0 |
| MLS | 15 | 15 | 0 | 0 | 0 | 0 |
| ATP | 9 | 8 | 0 | 1 | 0 | 0 |
| WTA | 34 | 9 | 21 | 4 | 0 | 0 |
| UFC | 14 | 14 | 0 | 0 | 0 | 0 |
| F1 | 2 | 1 (Race) | 0 | 0 | 1 (Qual) | 0 |
| PGA | 4 | 4 (WWTC, 4 rounds) | 0 | 0 | 0 | 0 |
| MLB, UCL | 0 | | | | | |

**NFL:** every game inside Kalshi's window is linked: 15 of 15. ESPN's stored
range currently ends Nov 10; Kalshi lists through Nov 17.

**NBA:** every game inside Kalshi's window is linked: 39 of 39. The last
linked tip is Nov 9 02:00 UTC, the latest Kalshi listing. The first
`not_listed` game is Nov 10 00:00 UTC.

**Every `not_linked`, with its reason:**
- **NCAAB, 73:**
  - **54** have no Kalshi event because the opponent isn't Division I, judged
    from the names (Virginia Lynchburg, King's, Bethesda, Harvard at the
    Chicago Maroons, and so on). Kalshi doesn't list those games. The full
    list with reasons is in the dry run's JSON.
  - **1** is D-I against D-I: Incarnate Word at UTEP, Nov 8 23:00 UTC. It
    starts at exactly Kalshi's latest listed tip, so it counts as inside the
    window.
  - **18** had started before this first check. The job runs every 6 h, so
    once deployed it sees each game before it starts.
- **ATP, 1:** Machac v Fils, which had started.
- **WTA, 4:**
  - 1 had started: Andreescu v Ortenzi, Cali Open.
  - 3 have no Kalshi event: the Angola Ladies Open, which Kalshi doesn't
    list.

**The 12 aliases FOR REVIEW.** Each came from an ESPN game in the window
beside an unclaimed Kalshi event on the same date, with the other team's
code equal:

| league | ESPN → Kalshi | evidence |
|---|---|---|
| NCAAB | UALB → ALBY | `ALBYMAN`, `MRMKALBY` |
| NCAAB | UPST → SCUS | `SCSTSCUS` |
| NCAAB | MIZ → MIZZ | `MIZZSLU` (CFB has the same alias) |
| NCAAB | LR → UALR | `SELAUALR` |
| EPL | MNC → MCI, BHA → BRI, LIV → LFC, CHE → CFC, MAN → MUN | `NFOMCI`, `BRIBRE`, `CRYLFC`, `SUNCFC`, `MUNAVL` |
| MLS | DC → DCU, RBNY → NYRB, LA → LAG | `DCUNYC`, `NYRBATL`, `RSLLAG` |

Without them, the first dry run left 5 NCAAB, 5 EPL and 3 MLS games
`not_linked` and the same Kalshi events unclaimed. With them, every Kalshi
event inside ESPN's dates is claimed, apart from CFB's FCS games. All 79
probe aliases were used by at least one row in the run.

**Home/away swaps** (linked, flag set): 5, all NCAAB, the probe's five:
Michigan v UConn, Portland v UT Arlington, Colorado v San Diego State,
Nebraska v Providence, and Saint Louis v Missouri. The last links through
the new MIZ alias.

**Calls and data:**
- Kalshi: 29 calls, 0 429s, 4.2 s; the contract check passed for 14/14
  series.
- Database: 68,096 bytes for the job's read, plus 8,638 bytes of ESPN names
  read only for this report.

## 6. Pending (not verified)

- **The paste of db/066 and its self-test**, then a full `tests_live -v`,
  metered, as the merge gate.
- **Everything after merge and deploy**, which is gated:
  - every NFL and NBA game inside Kalshi's window linked on the worker;
  - a link rate with reasons for every other league;
  - 0 relinks and 0 rows written without a change;
  - worker RSS at rest in the ~130–160 MB band;
  - NFL and CFB rows out to 14 days.
- **Golf beyond one tournament.** Only the World Wide Technology
  Championship was listed. Two tournaments in one week (an opposite-field
  event) would make every round ambiguous and leave it not linked.
- **F1 sprint weekends.** A `Sprint` session is out of scope, but its shape
  is unverified (`docs/sports_data.md` §8).
- **Soccer:** the home side, and UCL, which had 0 open events on Nov 5.
- **MLB:** the doubleheader suffix rule (probe §5) is not built; MLB is off.
- **Neutral sites:** the flag is stored but doesn't feed the swap logic. The
  5 swapped NCAAB games are plausibly neutral-site; that isn't checked.
