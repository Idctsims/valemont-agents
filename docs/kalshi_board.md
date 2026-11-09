# The Kalshi board (Chat 8 Phase 1)

Every open Kalshi sports market for the next ~36 hours: game lines and every
prop, every sport, each with its taker fee at the ask and a combo-eligible
flag, linked to `sports_events` where a link exists. It is the **pre-game leg
universe** for Chat 8 Phases 2–4 (owner, Nov 6: Lotto uses props from every
sport; nothing is excluded at this layer).

Read-only to Kalshi: no key, no order, nothing automated (CLAUDE.md §1). The
board is a set of current APP tables (db/071), upserted in place and pruned. It
is not the immutable `kalshi_markets` archive, and it is never written there.
Parlay resolution and Chat 9's live tracker read the ledger and Kalshi
directly, never this board.

Code: `workers/venues/kalshi/board.py` (fetch and parse, no SQL),
`workers/sports/kalshi_board.py` (the jobs), SQL in `core/ledger.py`;
`apps/web/src/app/(app)/betting` (the Board tab).

## 1. Step 0 (read-only probe, Nov 6)

`scripts/kalshi_board_probe.py`, run ~12:30 UTC Nov 6 (window to ~00:30 UTC
Nov 8): 4,156 Sports series in one 6.5 MB `/series?category=Sports` call; 605
with an open market in the window; 35,479 markets in 4,874 events; 0 combo
markets in the events tree; 3 open collections, answering without auth;
FeeRegime refused 0 of the 4,156 series' fee types. Details are in the
owner's Step 0 decisions (Nov 6).

## 2. The fetch path (as built)

1. `GET /milestones?category=Sports&minimum_start_date=<now>`, every page:
   the window is the milestones starting in [now, now + 36 h), and their
   primary and related event tickers. ~11 calls, ~2.4 MB.
2. `GET /series/{t}` for each series that owns a window event, **once per
   Chicago day** (the first pull of the day; later pulls that day read only
   series new to the window). Title, tags (Kalshi's sport), `fee_type` and
   `fee_multiplier`. ~609 calls, ~2 KB each.
3. `GET /multivariate_event_collections?status=open`: one page, ~1.2 MB.
4. `GET /markets?…&status=open`, 100 a page: `event_ticker=E` for a series
   with at most 2 window events, else `series_ticker=S` with its
   out-of-window events dropped as parsed. One page at a time; the raw JSON is
   dropped before the next call.

**The empty answer is a contract failure** (owner, Nov 6). A window series
whose events return no open market is recorded; the other series are still
written, linked, swept and pruned, and then the run fails, naming each empty
series with its event count. A pull with no market at all fails the same way.

**Not yet open is not empty (owner, Nov 7).** Before an empty window series
is counted, each of its events is asked once for
`/markets?event_ticker=E&status=unopened&limit=1`. An event Kalshi lists as
not yet open counts as `not_yet_open=<n>` in the note and never fails the
run (it joins the window at the next pull). An event with neither open nor
unopened markets still counts as empty and fails the run. Why: the 21:30
UTC Nov 7 pull read KXATPMATCH (7 events) and KXATPDOUBLES (3) minutes
before their markets' `open_time` (21:35–21:36 UTC); milestones list a match
before its markets open.

**Not open is not empty, in any status (owner, Nov 9; supersedes the
`status=unopened` ask above).** Each event of an empty window series is asked
once with NO status filter (`/markets?event_ticker=E&limit=200`). An event
whose markets Kalshi lists in any non-open status (initialized, closed,
paused, settled, whatever it returns) counts as `not_open=<n>` by status in
the note, with the series named (`not_open=3 (initialized 3; KXFOO)`; a mixed
event reads `closed+settled`), and never fails the run.

An event with **zero markets of any status** is Kalshi listing the event
before its markets exist, not a broken feed: it counts as `no_markets=<n>`
with the series named (`no_markets=3 (KXCOPADELREYADVANCE 3)`) and never fails
the run on its own. The rule exists to catch a broken fetch path (Nov 6's
comma batch, 200 and empty), which empties MANY events at once. So the run
fails (after the rest is written) only when **(a)** zero-market events exceed
**2%** of the window's events (`NO_MARKETS_MAX_SHARE`), or **(b)** a **game
series** of the owner's five leagues has **every** one of its window events at
zero markets. A game series is the `sports_leagues` registry's
`kalshi_series` for cfb, nfl, nba, f1 and pga (`KXNCAAFGAME`, `KXNFLGAME`,
`KXNBAGAME`, `KXF1RACE`, `KXPGATOUR`), read through `ledger.sports_leagues()`
only when a real run has zero-market events. Props and other series of those
leagues fall under the 2% share only (owner, Nov 9, PR #103).

Why: the 15:30 UTC Nov 9 pull failed on four Copa del Rey series (3 events
each) whose BTTS, SPREAD and TOTAL markets opened at 19:06 and 20:06 UTC;
`status=unopened` returned nothing for them, then and after. Read at 20:17
UTC: those 9 events `active`; the 3 `KXCOPADELREYADVANCE` events had zero
markets of any status.

**Excluded series, by name (owner, Nov 7):** `KXNFLPASSYDSPRECISE` ("Pass
Yards Precise") is never read. It is a per-yard duplicate of `KXNFLPASSYDS`:
1,204 open markets a game, one at every yard for each QB (16,856 in 14
events, ~44 MB from Kalshi, read Nov 7). Its series query overflowed the
60-page cap and failed the 20:08 UTC Nov 7 boot pull. The list is exact
names in `sports/kalshi_board.EXCLUDED_SERIES`, never a pattern; the note
reads `excluded=<n> (<series> <events> events)`.

**A page-cap overflow is a skipped series, not a dead pull (owner, Nov 7).**
A series whose `/markets` query still pages at the cap is recorded as
overflowed; nothing of it is written, swept or kept in the window; the rest
of the pull is written, linked, swept and pruned; then the run fails naming
it (`overflowed=<n>` in the note), so it alerts like an empty series. The
refresh keys on "the board was written", not "the last pull was ok": its
window is set before that failure is raised.

### Departures from the approved path (approved by the owner, Nov 6)

- **`/markets` takes ONE `event_ticker`.** A comma-separated list answers
  HTTP 200 with an empty list (verified Nov 6, five tickers: 0 markets; one
  ticker: 48). The approved "≤10 events per call" cannot work, and it fails
  silently. A pull whose window has events but returns no market now fails
  loud. Per-event calls for every event would be ~4,800 calls a pull, so a
  series with more than 2 window events is read by series instead. It is
  still filtered, so no combo market can arrive.
- **Not the bulk `/series?category=Sports` list.** Its one 6.5 MB response
  took the process to +41 MB while it was held (measured Nov 6, against the
  +20 MB budget then in force; §8). Each window series is read alone instead, once a Chicago
  day. That is still the current fee type from `/series`, read daily.

## 3. Kinds, subject, stat, line (owner-approved, Nov 6)

- `prop`: the market names a player (`primary_participant_key`, or a
  `custom_strike` key ending `_player`).
- `game_line`: otherwise a series ending GAME, MATCH, FIGHT, SPREAD or TOTAL
  (team totals included), or a period (1Q–4Q, 1H, 2H, 1P–9P), with or without
  SPREAD/TOTAL. A half or period winner is a game line. Pinned by tests: CFB
  team totals and the 1H winner (the probe's two misreads).
- `other`: correct score, overtime, win margin, points leader, race-to,
  double result, outrights.
- `line` is `floor_strike`. A game line parses Kalshi's `yes_sub_title`
  first ("X wins by over 7.5 points", "Over 66.5 points", "X over 6.5
  points"), because titles vary by period. A prop parses its title
  ("Naz Reid: 10+ rebounds" → subject, stat). A parse miss never drops the
  market: it stays with nulls and counts per series in the job note.
- `variant`: `protected` for the `…PROT` series. **What protected means**
  (KXNBAREBPROT's rules, Nov 6): if the player plays in the first half but
  not after it, the contract resolves to the fair price before tip-off,
  unless the stat line was already reached. The ordinary series settles on
  the stats once the player enters the game. Both settle to a fair price if
  the player never enters. No logic depends on it yet. **Phase 4 (owner,
  Nov 6):** the generator spec says which variant each leg uses and why.
- **`payout`: `binary` or `scalar` (owner, Nov 7; db/072).** A binary
  market pays $1 or $0; a scalar one pays a fraction of $1 that grows with
  a stat. **Scalar markets are never parlay legs** (no hit or miss), and
  their YES price is not a probability. **Kalshi has no field that says
  which:** `market_type` reads `binary` on every ladder and escalator (with
  `settlement_bounds_type` `default` and `notional_value_dollars` 1.0000;
  read Nov 7 on 4 scalar and 3 binary markets, the only differences being
  `custom_strike`'s scalar keys, `strike_type` `custom`,
  `price_level_structure` and `price_ranges`). So the worker sets
  `payout = 'scalar'` when `custom_strike.scalar_cap` is present (the
  owner's fallback rule), never from the series name. A CHECK ties it to
  the variant: scalar exactly when the variant is `ladder` or `escalator`,
  and a scalar row has no `line`.
- **Phase 4's leg universe is `payout = 'binary'` only.** The board shows
  scalar markets with a "Scales" chip and never a combo chip; the combo
  filter and every combo count take binary markets only; the readers and
  `ctx_kalshi_board` count scalar apart; parse misses count binary rows
  only.
- `variant` `ladder` and `escalator` (owner, Nov 7; db/072 widens the
  CHECK): Kalshi's **scalar** player markets, every sport, read from
  `custom_strike` (present `scalar_cap`; `exponent` present = escalator).
  Read from the rules text on Nov 7:
  - **ladder** (KXNFLLADDERREC, KXNFLLADDERRECYDS, KXNFLLADDERRSHYDS,
    KXNFLFFPTSLADDER): YES pays a fixed amount per unit of the stat ($0.05 a
    reception; $0.01 a PPR fantasy point, Sleeper's figure), capped at $1.00
    at `scalar_cap`; NO pays $1.00 minus YES.
  - **escalator** (KXNFLESCALATORREC, …RECYDS, …RSHYDS, KXNBAESCALATORPTS,
    KXNBAESCALATOR3PT): YES pays `floor(10000 × (min(s, cap) / cap)^3) / 10000`
    in `scalar_step` steps (yards in steps of 10, NBA points in 2s), $1.00 at
    `scalar_cap`; NO pays $1.00 minus YES.

  subject = `yes_sub_title` (the player), stat = `custom_strike.statistic`,
  **line NULL** (no threshold to hit). The cap, the stat value at which YES
  pays $1.00, rides in the label ("Emeka Egbuka: receptions, $1 at 20"); no
  new column. A scalar row is never a parse miss. These were 241 of the 467
  misses at the 15:30 UTC Nov 7 pull.
- Two more Nov 7 fixes: the period prefix is word-bounded ("Seth McGowan:
  10+ rushing yards" had lost its subject to "Set"), and a game line reads
  "Ben Shelton -3.5 games" (KXATPGSPREAD's sub-title) as a spread.
- `custom_strike` holds Kalshi's own player and team ids. It is read for the
  kind only and never stored.

## 4. Fees and combos

- `taker_fee_at_ask` = `FeeRegime(series fee_type, multiplier).taker_fee(yes_ask)`,
  NULL when the ask is not strictly between 0 and 1. A fee type or
  multiplier FeeRegime does not model fails the run, naming the series.
- **Combo eligibility is per event.** An event is eligible when an open
  collection lists it in `associated_event_tickers`; `combo_yes_only` comes
  from `associated_events[].is_yes_only`. The 3 open collections
  (`KXMVESPORTSMULTIGAMEEXTENDED-R`, `KXMVECROSSCATEGORY-R`,
  `KXMVECROSSCATEGORY-SHARD1-R`) each list the same 3,413 events.
  `is_single_market_per_event`, `is_all_yes` and `is_ordered` are all false;
  `size_min` is 2 and `size_max` is 0. Per event: `size_max` 1 on 2,887
  events and absent on 519; `is_yes_only` on 449 (the game moneylines among
  them); `active_quoters` empty on all 3,406 read.
- **The per-event leg limit decides a same-game combo** (owner, Nov 6). The
  collections allow several markets from one event, but an event's own
  `size_max` caps it: `kalshi_board_events.combo_size_max` (smallint). NULL
  means absent on the event, i.e. no stated per-event cap; it is never
  coerced to 0 or 1, by the worker or the database. Two collections stating
  different caps: the tighter one is kept. A same-game parlay whose legs
  exceed an event's cap cannot be one Kalshi combo: Phase 3 says "build
  manually". `combo_quoters` keeps `active_quoters` as a count only, so a
  future non-empty value shows without storing the list. Both are NULL on an
  ineligible event (CHECK). The page shows the cap on the combo chip
  ("Combo · YES only · max 1").

## 5. NO prices

The board stores YES bid and ask only; `v_kalshi_board` gives
`no_bid = 1 − yes_ask` and `no_ask = 1 − yes_bid`. Checked on live data
before the paste: **0 mismatches over 35,521 markets** (every market with
all four prices, Nov 6), and again on every run (the job note).

## 6. Schedule, writes, prune

- `kalshi_board`: 09:30 and 15:30 Chicago and 150 s after boot. Expected
  interval 18 h.
- `kalshi_board_refresh`: every 15 min, working 08:00–23:00 Chicago (idle
  otherwise, so it is never stale). **Rebuilt (owner, Nov 7)** after its
  first Saturday: 453–631 s a run, 626–864 calls, ~10% 429s, 44–61 MB from
  Kalshi, while re-pricing 6 h by series. Now:
  - **Per event only** (`event_ticker=E`), never by series; scope: the last
    full pull's events starting within **3 h** and not yet started,
    **soonest first**.
  - **Hard caps a run: ≤ 300 Kalshi calls (429s count) and ≤ 300 s.** No
    call starts after 280 s, and each times out at 15 s, so a call in flight
    still ends inside the cap. At a cap it stops; the note says how many
    events it skipped, which cap, and the first skipped start; only events
    read whole are swept and touched.
  - **No overlap:** `max_instances=1` and `coalesce` (the scheduler's
    defaults, pinned by a test for both jobs); it idles while a full pull
    holds the lock.
  - **Adaptive pacing** (`venues/kalshi/board.Paced`, AIMD): it starts at
    0.5 s between calls; a 429 doubles the gap (≤ 4 s), and every 25 answers
    without one ease it 10% (≥ 0.3 s). The pace carries over to the next run
    in the process. The note carries the 429 count, its rate and the pace.
    Target: under 2% of calls.
  - Targets on a Saturday (owner): median run ≤ 120 s, ≤ 10 MB from Kalshi.
  - **Stays as built (owner, Nov 7).** Its first Saturday run (22:31:55
    UTC) hit the call cap: 300 of 1,056 events within 3 h, 756 skipped (from
    00:00 UTC), 0 429s, pace 0.30 s, ~3.4 MB, 106 s. **The skip count is
    expected on a busy slate:** the page shows each event's source time
    ("as of"), and Phase 4 re-quotes every leg at commit, so a stale board
    price never reaches a ledger row. No change unless the median run over
    Sunday's slate exceeds 300 s or 429s exceed 2% of calls.
- The full pull's note leads with `empty_series=<n>` (inside `job_health`'s
  500 characters, so 0 is read, not inferred) and lists the top 10 parse
  misses by series.
- **Changed-only.** The process keeps a compact map of 64-bit hashes
  (rebuilt by each full pull) and sends only rows whose content changed. The
  database writes only rows whose stored columns differ. Nothing is read back,
  so the job's Supabase egress is statement replies only.
- After the writes: `kalshi_board_touch` (fetched_at / checked_at),
  `kalshi_board_sweep` (markets of the asked-for events that Kalshi no longer
  lists open), `kalshi_board_link` (`sports_event_id`, and `starts_at` from
  the linked `sports_events` row; else the milestone's start),
  `kalshi_board_prune`: an event and its markets go once
  `coalesce(starts_at, close_time) + 6 h` has passed (owner, Nov 6); a series
  goes once it has no event left and has not been checked for 7 days.
- Every log line carries RSS and KB (`core/jobs.run_tracked`).

## 7. Measured (dry runs on the owner's machine, Nov 6, nothing written)

| | Value |
|---|---|
| Markets / events / series | 35,521 / 4,811 / 609 |
| Kinds | Football lines 12,910, other 496; Basketball lines 6,343, props 4,560, other 192; Soccer lines 5,043, props 151, other 2,576; Hockey lines 857, props 528, other 30; the rest other or lines |
| Combo-eligible | 19,521 markets in 1,955 events |
| Parse misses | 92 (KXNBAESCALATORPTS 48, KXNBAESCALATOR3PT 41, KXATPGSPREAD 3) |
| NO complement | 0 mismatches of 35,521 |
| Kalshi | 1,422 calls, 0 429s, ~88.7 MB, 361 s (200 a page) |
| Peak RSS over the pull's start | +21.2 MB at 200 a page; **+18.9 MB at 100 a page** |

**The final dry run (the build, 100 a page):** 35,680 markets / 4,821 events
/ 615 series / 1,593 Kalshi calls / 0 429s / ~89.0 MB from Kalshi / 400 s /
+18.9 MB peak over the pull's start (Windows working set; the worker's Linux
RSS comes with its first real run). The rows above at 200 a page are the run
before it (35,521 / 4,811 / 609 / 1,422 calls / ~88.7 MB / 361 s); commit
`53d19e1`'s evidence line paired that run's counts with this run's calls,
time and RSS by mistake, and this table is the record.

## 8. Budgets (owner, Nov 6, at the merge of PR #97; the board revised Nov 8)

- **The board: ≤ 35 MB in Postgres at steady state** (tables plus indexes,
  under the start + 6 h prune; owner, Nov 8). **The pull's peak: ≤ +35 MB
  over its start on the worker** (the Linux RSS reading, not the Windows
  working set).
- The Step 0 targets (< 10 MB, +20 MB) are retired, and so is the Nov 6
  board figure of 25 MB. The first real run measured ~15.8 MB for one
  window and +26.7 MB on the owner's machine.
- **The steady state, read 06:01 UTC Nov 8** (db_inspect; MB = 2^20):
  `kalshi_board` 22.5 MB (heap 15.8, indexes 6.7; 54,608 rows),
  `kalshi_board_events` 5.0 MB (6,416 rows), `kalshi_series_board` 0.6 MB
  (745 rows): **28.1 MB** (29,458,432 bytes), over the old 25 MB, under the
  new 35 MB. The steady state holds more rows than one pull fetches
  (39–49k) because games started in the last 6 h stay until the prune. Not
  bloat: 4,227 dead rows on `kalshi_board` (~8%) and 997 on events, both
  auto-vacuumed since 22:00 UTC Nov 7. The database: 284,953,747 bytes.
- The ways to shrink the rows (store the market ticker's suffix only,
  `fillfactor` 90, a short kind code) are parked. Revisit them only if the
  database passes ~350 MB: the same trigger as the parked feed retention
  follow-ups.

## 9. The page

`/betting`, the Board tab: one sport at a time (chips, busiest first), the
games collapsed (start, counts by kind, combo count, source time). A game's
markets load on expand through `expandGame` (db/071 `kalshi_board_game`):
game lines first, then props, then other, each market with its label, YES
bid / ask, the fee per contract at the ask and the variant, and each event
with its combo chip (YES only when it is) and source time. Filters
(`?sport=`, `?kind=lines|props|other`, `?combo=1`) live in the URL.
