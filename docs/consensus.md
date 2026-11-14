# The consensus engine (Chat 8 Phase 2)

Every binary market on the Kalshi board (`payout = 'binary'`, db/072) gets a
consensus probability, its source tier, the Kalshi ask, the taker fee and the
net edge per side, so Phase 4 can pick legs. Read-only to every venue:
nothing here bets (CLAUDE.md §1). Migration db/073; code `workers/consensus/`,
`workers/venues/odds/`, `workers/core/odds_credits.py`; page `/betting`.

**Edge is not profit** (CLAUDE.md §8). `edge_claimed` (> 0) is display only:
the edge-leg margin (2¢ net of fee) is set in Phase 4's pre-registration, not
here (owner, Nov 9). **Phase 4 note (owner, Nov 9): the generator re-quotes
every candidate leg and recomputes its edge on the fresh price BEFORE
selecting, not only at commit.**

## 1. The method (owner, Nov 9; frozen in Phase 4's pre-registration)

| | |
|---|---|
| De-vig | POWER: `p_i = q_i^k`, `k` solving `Σ q_i^k = 1`, `q_i = 1 / decimal odds`. Multiplicative (`q_i / Σq`) kept as a comparison only (`p_books_mult`). Checked against penaltyblog 1.13.1 to 1e-9 (a dev-only oracle: `requirements-dev.txt`, `tests/fixtures/devig_oracle.json`). |
| `books` tier | The Odds API, an EXACT line at ≥ 2 books. `p_books` = the median of each book's power-de-vigged p. |
| `espn` tier | Else ESPN's one book (DraftKings, the `close` line), same de-vig, flagged 1 book. |
| `kalshi` tier | No outside line. `p` = Kalshi's own mid. |
| `p_kalshi` | `(yes_bid + yes_ask) / 2`, normalized across the event's markets when Kalshi flags the event `mutually_exclusive` AND every market is quoted AND the mids sum within **0.95–1.05** (owner, Nov 9); else the plain mid. Ladder rungs are never normalized across rungs. The band guards an exclusive-but-not-exhaustive event (a cricket match whose draw has no market: mids sum ~0.79). |
| Blend | `p = 0.7 × p_outside + 0.3 × p_kalshi` (books or espn); `p = p_kalshi` (kalshi). |
| Edge | `edge(side) = p(side) − (ask(side) + taker_fee(ask(side)))`. NO is bought at `1 − yes_bid` with `p(NO) = 1 − p`. Never against the mid, never before fees (`venues/kalshi/edge.py`'s gate). |
| Fee | `ceil_6dp(0.07 × multiplier × P × (1 − P))` (`fees.py`; SQL `kalshi_taker_fee`, equal on every cent, `tests_live/test_consensus_sql.py`). |
| `edge_claimed` | **books tier only, and only past the four-method test (db/078; owner, 2026-11-13).** The side's edge is > 0 under the shipped method AND under the least favourable of power, multiplicative, Shin and additive: YES is re-priced at `p_books_lo`, NO at `p_books_hi` (the lowest and highest of the four methods' medians across the books, written by the worker; NULL = not tested = never claimed). **The espn tier never claims**: its edge is displayed only. Why: on Nov 12, of 140 espn legs none held 2c under all four methods and both claimed edges changed sign under three (`docs/betting_science_audit.md` D5). Before db/078 the rule was: books or espn tier AND the better side's edge > 0 AND **both prices fresh** (db/074): the Kalshi price read within 20 min (`kalshi_board_events.fetched_at`, the event's last read: the full pull and the refresh both touch it) and the outside price within 12 h (`leg_consensus.source_at`, advanced by every confirming pull). **A kalshi-tier leg never claims edge**: a price cannot have edge against itself; a positive kalshi-tier edge is a normalization artifact, reported (the run note's "kalshi-tier positive") and never claimed. **A stale leg shows its edge, tagged stale, and claims nothing.** |

The blend, the fee and the edges live in ONE place, SQL
`leg_consensus_for(games[])` (calling `consensus_leg`), which the view
`v_leg_consensus`, the page (`kalshi_board_game`'s `q`) and Wags
(`ctx_consensus`) all read. The kalshi tier is never stored: it is derived at
read time from `kalshi_board`'s live prices, so it follows every board
refresh with no write (the "Kalshi-only recompute" needs no job).

## 2. Matching: exact lines only

| Kalshi | Book |
|---|---|
| `KX*GAME …-HOU` | h2h, Houston (soccer: three ways with the draw, `-TIE`) |
| `KX*SPREAD …-HOU5` ("wins by over 4.5") | spread, Houston at −4.5 |
| `KX*TOTAL` line L | total, Over L |
| `KXNFLREC` / `KXNFLRECYDS` / `KXNFLRSHYDS` "X+" (line X − 0.5) | the player's Over at X − 0.5 |

**The page's `q`** (db/074; owner-approved): one packed string a binary
market, `<tier><letter><p><edge>`, e.g. `b9c515-93`: tier `b<n>` | `e` | `k`;
letter `a` + flags (8 the better side is NO, 1 claimed, 2 Kalshi stale, 4
outside stale); p in thousandths (3 digits); the side's edge in thousandths
of a dollar. No JSON array met the ≤ 115 KB target for an expanded NBA game
(DEN at HOU, 960 markets: arrays 52.5 / 35.5 / 30.6 KB of q; the string
16.1 KB; the game 110,561 bytes after the paste). ONE decoder,
`parseConsensus` (`apps/web/src/lib/betting/board.ts`), round-trip tested
against the SQL encoder's own output (`e2e/fixtures/consensus-packed.json`,
44 cases: production read-only plus the scratch copy). The YES fee is the
market's own `f`; the NO fee is derived (p(NO) − ask − edge) to 0.1¢.

The side of a team market is its ticker's last segment (digits dropped)
against the game's two ESPN codes through `sports_team_aliases`. Odds API
events map to `sports_events` by team names (`consensus/match.py`
`ODDS_NAMES` aliases the Odds API's spellings, e.g. "Los Angeles Clippers" →
"LA Clippers"), start within 6 h. **A Kalshi rung with no exact book line
stays kalshi tier**: no rung interpolation without a pre-registration
(owner, Nov 9). The rung model is Phase 4's, on `book_lines`.

Anytime TD: books price **Yes only** (Step 0), so there is nothing to
de-vig; it stays kalshi tier in v1 (a one-sided method goes into Phase 4's
pre-registration or nowhere). Not pulled under Option B.

## 3. Step 0 (Nov 9, read-only; 11 credits)

- **Cost = markets × regions per call**, whatever the number of events
  (`x-requests-last`): bulk NFL/NBA mainlines 3 each; event odds 2 (two
  markets) and 3 (three). `/sports` and `/events` cost 0.
- **Books (us):** draftkings, fanduel, betmgm, betrivers, betonlineag,
  lowvig, bovada, mybookieag, betus. NFL 6–9 books an event; NBA 1–9.
  The Odds API lists NBA about one day ahead (and Christmas games).
- **ESPN odds** on the scoreboard sports/espn.py already reads: one book,
  DraftKings, ML / spread / total both sides (soccer adds the draw), for
  cfb, nfl, nba, mcbb, nhl, epl, ucl; none for f1, pga, atp, wta, ufc, mls
  (null entries); mlb off-season.
- **Event matching, Nov 9 night:** NFL 1/1, NBA 8/8 to `sports_events` and
  the board; no alias needed. Off-window NFL events were real games outside
  the board's 36 h.
- **Exact matching:** moneylines 18/18 at ≥ 2 books; spread/total rungs NFL
  2/44, NBA 14/496 (Kalshi lists ~30 rungs a game, books one main line).
  MNF props: pass yds 3/19 rungs (Bovada alt only, 1 book each); anytime TD
  21/23 "1+" rungs (Yes only); receptions 14/86 rungs (16%; **14/14 book main
  lines land on a rung**), reception yds 5/135 (5/20 main lines), rush yds
  1/53 (1/10). Owner: the measure that matters is main lines captured.

## 4. Schedule and credits (Option B, owner, Nov 9)

`consensus` at **10:15 and 16:15 Chicago, never at boot** (a redeploy must
not spend credits).

| | Credits / month (est.) |
|---|---|
| NBA mainlines, every run with an NBA game on the board (morning) | ~90 |
| NFL mainlines on NFL game days (games whose Chicago date is today) | ~40 |
| NFL props per game on game days: receptions, rec yds, rush yds (3) | ~195 |
| 16:15: the free `/events` first; paid only when it lists a board game not priced today | ~0–15 |
| **Total** | **~340** |

**The guard** (`core/odds_credits.py`, core's): refuse a paid call when the
last reported remaining < 50 or this month's spend plus the call would pass
450, the spend being **the account's own count, the latest x-requests-used**
(`odds_api_usage().used`, db/074), not the call log's sum (which misses Step
0's 11); push once a month at 400 (`odds_credits_400`); the month's first refusal
pushes (`odds_credits_refused`) and fails its run, later refusals are noted.
The month is `odds_api_usage()`'s calendar month (UTC); the account's reset
date may differ. `ODDS_API_KEY` is in the root `.env` and Railway only, never
Vercel, never printed; the key rides in the query string, so no URL is ever
logged or recorded (`odds_api_calls.endpoint` is the path template).

**The board refresh's priority** (owner, Nov 9): inside its 300-call cap
and 3 h window, events with a books or espn row first, then the soonest
start (`consensus/priority.py`, process memory the consensus job fills; a
restart orders by start alone until the next consensus run).

**Coverage memory is process memory**: a restart between 10:15 and 16:15
can repay one bulk call (≤ 6 credits) plus props. A books row of a game the
process has no Odds API prices for today is kept by the sweep, not dropped.

## 5. Tables (db/073)

| | |
|---|---|
| `leg_consensus` | Outside-line rows only (books, espn), upserted changed-only; deleted with its board market (FK cascade: the board's sweep and its start + 6 h prune); swept per game by the job. |
| `book_lines` | An APP table with a prune, not append-only: one row per game, market, subject and book (line, both American prices, power p), appended when it changes; `book_lines_prune()` collapses each key to its last pre-start row once start + 6 h has passed. Measured ~120 B a row with its index; ~114,000 collapsed rows a year ≈ **~14 MB/year** (owner's target ≤ 15). |
| `odds_api_calls` | Append-only, TRUNCATE-guarded: every call, free ones too, with `x-requests-last/-used/-remaining`. |
| `kalshi_board_events.mutually_exclusive` | Kalshi's flag, from `/events?series_ticker=&status=open` once per Chicago day with `/series` (the full pull's note: "flags N events (M series read)"). |

## 6. First real run (Nov 9, 23:02 UTC, the owner's machine, branch `chat8-phase2`)

9 credits (NBA 3, NFL 3, MNF props 3); 480 remaining on the account; Odds API
events matched NBA 10/10, NFL 1/1, 0 misses; ESPN 81/113 games with odds (8
calls); 113 games, 2,419 markets; legs **books 58, espn 181, kalshi 13,235**
(Basketball 36/131/8,921, Football 22/10/1,557, Hockey 0/40/1,045); 239
written; `book_lines` +636; **claimed 23, kalshi-tier positive 0**; 9 s; RSS
70 MB (peak 72); ~301 KB read.

**The claimed edges were mostly a stale Kalshi price, not a finding.** MEM at
TOR: books (9) and ESPN both had MEM ~0.52–0.55, the board had MEM 0.44–0.45
from 21:49 UTC; Kalshi's live price was 0.54–0.55. The 22:58 board refresh
hit its 300-call cap and skipped every event from 00:18 UTC. NYK/MIL: the
board's price dated 15:30 (outside the refresh's 3 h). Phase 4 re-quotes
every leg at commit (owner, Nov 7).

## 7. Open

- `odds_api_calls` starts at the first real run: Step 0's 11 credits are not
  in it; the guard reads the account's own count (db/074), so it does not
  matter to the guard.
- At 10:15 Chicago the full pull is 45 min old: only games inside the
  refresh's 3 h window can claim edge then (the 20-min rule, by design).
- ESPN is fetched by the consensus job itself (8 scoreboard calls a run), not
  read from the sports jobs' responses.
