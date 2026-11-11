# Same-game correlation (Chat 8 Phase 3)

Price any set of Kalshi board legs as one parlay: the naive product, the
correlated fair probability, and whether Kalshi takes it as one combo. Read-only
to every venue: nothing here bets (CLAUDE.md §1). Migrations db/076 and db/077;
code `workers/consensus/correlation.py`, `combo.py`, `combo_job.py`; the table
`docs/correlation_rho.json` (byte-identical copy in `workers/consensus/`); the
page `/betting` (Combo mode and its sheet).

**A fair price is not profit** (CLAUDE.md §8). It is the joint probability the
consensus marginals and a frozen correlation table imply; whether combos priced
this way beat Kalshi is a question for Phase 4's pre-registration, not this page.

## 1. The method (owner decisions, Nov 11)

| | |
|---|---|
| Games | Independent. The joint is the product over games of each game's within-game joint. |
| Marginals | Each leg's consensus p (db/073/074 `leg_consensus_for`: books, espn or kalshi tier); a NO leg is 1 − p. |
| Within a game | Gaussian copula. Leg i is the event that its latent standard normal clears z_i = Φ⁻¹(1 − p_i), oriented so higher is the YES of an over or a win; a NO leg is the other side of the same threshold, which flips the sign of its correlations. |
| k = 1 | p, exactly. |
| k = 2 | The bivariate normal CDF (Genz 2004, Drezner–Wesolowsky with Gauss–Legendre points), by inclusion–exclusion on intervals. Tested against 588 reference points (scipy quadrature, cross-checked with Owen's T): max error 3.3e-16. |
| k ≥ 3 | Monte Carlo through a Cholesky factor: 200,000 draws from a seed derived from the sorted legs (the same legs give the same number), the standard error returned; 1,000,000 draws when the SE is over 5% of the estimate. Pure stdlib (no numpy: worker RSS). |
| Σ | From the frozen ρ table by relationship (§3). A Σ that is not positive definite is shrunk toward the identity by the smallest factor that makes it one (bisection on the Cholesky pivots), and the factor is logged and stored in the quote's block. |
| Naive | The product of the legs' p. |
| Kalshi naive cost | The product of each leg's (ask + taker fee) on its side. |
| Combo status | "fits" when every leg's event is combo-eligible, at most its event's `combo_size_max` legs come from it (NULL: no stated cap), and a YES-only event's legs are YES; else "build manually" with the first reason. |

### Legs on one latent (decision 4)

Merged before the copula into one interval on that latent, priced from the
legs' own p (so ρ = ±1 never enters a matrix):

- one player's rungs of one stat (the protected twin included);
- one series' spread rungs, both clubs (the canonical club's margin: "B wins by
  over L" is "A's margin is under −L");
- one series' total rungs; one club's team-total rungs.

Same direction: the harder rung (ρ = 1). Opposite directions: exactly
p_low − p_high. A pair that cannot both hit is refused.

### Refused, never priced

A market twice (both sides, or the same leg twice); two outcomes of one result
(a mutually exclusive event, db/073's flag, or two clubs' winners of one series,
flagged or not: a soccer draw is its own market); rungs that cannot both hit;
one player's stat beside a sum that holds it (pts and PRA; "not jointly
gaussian; pick one"; every sport); a scalar market; a leg off the board or with
no price; a game already started.

## 2. Player → team (Step 0, Nov 11)

A prop's `custom_strike` carries `<sport>_team`, a Kalshi id equal to the id on
that team's GAME market, whose ticker ends in the team's code. The worker
learns id → code from GAME markets (fetched first in every pull) and writes
`kalshi_board.team` (db/076; the ids are never stored). Measured Nov 11: NBA
4,527/4,527 prop markets (122/122 players), NFL Sunday 3,752/3,764 (212/212
players; the 12 misses are "No Touchdown"), NHL 1,062/1,068 (the 6 "no goal").
The ticker prefix is NOT a fallback for props: `…CARNO-NONE` reads as "NO".

## 3. The ρ table (`docs/correlation_rho.json`, version 2026-11-11.1)

Frozen by its sha256 (CRLF normalized), stored on every quote (`rho_version`).
Every row carries its source and class:

| Class | Rows |
|---|---|
| `measured_external` | **NBA:** nba-hub's measured table (`DEMON-X13/nba-hub`, `data/corr.json`, commit `3a3e298`): latent-normal MLE on binary outcomes at book lines, 5,276 games 2023–2026, jackknife-shrunk, PD-projected; **external, unreproduced; values cited, no file copied** (owner decision 1). 288 rows (its 302 less our self-plus-sum refusals). **NFL QB pass yds ↔ game total over 0.28** (`JXiong15/sports-parlay-generator` README; method not shown; low confidence). |
| `prior` | The owner's NFL, NHL and soccer starting values (Step 0 list). |
| `borrowed` | Applied at lookup, never a row: a cover without a measured row takes its moneyline's (decision 2); cfb uses the NFL rows, mcbb the NBA rows (decision 5). |

Lookup (decisions 2, 3): a player and his own club's line → `team|<kind>|<line>`;
the other club's line → `oppteam|…` if a row exists, else minus the own-club row
where the league has no draw (NBA, NFL, NHL, CFB, MCBB), else unmodeled (soccer);
two club lines → `game|cover|win` (other club: sign flipped where no draw); a
club line and the total → club-agnostic `game|…`. Anything else in the same
game: ρ = 0, flagged "unmodeled pair". The sheet shows a "prior" chip when any
pair's ρ is a prior or borrowed.

**A known inconsistency in the NFL priors:** `team|pass_yds|win` 0.15 and
`oppteam|pass_yds|win` 0.10 imply opposite signs for the same QB against the two
moneylines (his own win, his opponent's). An explicit row wins over the flip, so
both apply as written; a quote can never hold both moneylines (refused).

**Replacement path:** our own forward measurement from settled committed
parlays, under its own pre-registration (owner, Nov 11: later, not now).

## 4. Tables and flow (db/076, db/077)

The page inserts a `combo_requests` row (the owner's RLS insert; forced
`queued`; 2–25 legs `{market_ticker, side}`; at most 20 in any 10 minutes,
db/077, the refusal naming when the next slot frees). Its trigger queues one
`job_queue` `price_combo`; the worker's 10 s poller runs `combo_job.price_job`,
which reads the legs (`ledger.combo_legs`: the board, the events' combo terms,
`leg_consensus_for`), prices them and writes `combo_quotes` and the answer in
one transaction (refused or failed: the reason, no quote). The page polls and
shows "Pricing… N s". `combo_prune` deletes requests (quotes cascade) past 7
days, 03:20 Chicago. `kalshi_board_game(…, p_tickers)` (db/077) gives each
market's ticker suffix in Combo mode only (off, the 115 KB payload is unchanged).
Wags reads `ctx_combos()` (provider `combos`): the last 7 days' real quotes; the
sheet's "Ask Wags about this" opens on that quote.

**Latency (owner, Nov 11):** the done-when target is ≤ 20 s for an MC quote
(the poll wait plus 3–7 s of draws on the owner's machine, Step 0); 2-leg quotes
are instant after the poll. No faster poller.

## 5. Coverage (tonight's slates, Nov 11, the adopted table, the shipped classifier)

Market pairs within a game, every rung a leg; main slices = full-game lines
(winner, spread, total) and every prop:

| | NBA (10 games) | NHL (6 games) |
|---|---|---|
| Main slices | 91.0% measured, 6.3% unmodeled (mostly "points leader"), 1.7% nested, 1.0% refused | 2.7% prior, 1.0% borrowed, 94.5% unmodeled, 0.8% nested, 1.0% refused |
| Every binary leg | 39.6% measured, 58.6% unmodeled (period lines, team totals) | 96.1% unmodeled |

Sampled same-game quotes from the main slices (400 per league): NBA 2 legs:
prior chip 0%, unmodeled chip 5.8%; 3 legs: 0% and 12.8%. NHL 2 legs: prior
chip 3.2%, unmodeled 96.8%; 3 legs: 9.0% and 99.8%.
