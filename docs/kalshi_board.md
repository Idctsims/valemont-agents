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
  The escalator series stay on the board as parse misses (kind prop, nulls).
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
  otherwise, so it is never stale). Re-prices the events of the last full
  pull that start within 6 h. It stands down while a full pull runs.
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

## 8. Budgets (owner, Nov 6, at the merge of PR #97)

- **The board: ≤ 25 MB in Postgres at steady state** (tables plus indexes,
  under the start + 6 h prune). **The pull's peak: ≤ +35 MB over its start
  on the worker** (the Linux RSS reading, not the Windows working set).
- The Step 0 targets (< 10 MB, +20 MB) are retired. The first real run
  measured ~15.8 MB for one window and +26.7 MB on the owner's machine.
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
