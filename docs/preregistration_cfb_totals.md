# Pre-registration: CFB totals (`cfb_total`)

**Version 1. Committed 2026-10-01, before any 2026 CFB price, settlement or
candle was read.** The git commit that introduces this file is the timestamp.

**Origin.** The market-efficiency scan (`docs/dev/market_efficiency_scan.md`,
run #3) found one cell with base-rate information beyond flattening on a book
that trades: `KXNCAAFTOTAL`, pooled weight 0.63 (event CI 0.21–1.00) on events
dated up to 2026-06-30. This file tests whether that survives **fees,
half-spread and a margin** on data the scan did not read.

**The stated prior is that it fails.** The scan priced no fee and no spread.
A 3¢ median spread and the taker fee may exceed whatever the blend moves the
price. Even a pass is a forward paper test on a market of about 70 contracts a
day (§7), not a profitability claim (CLAUDE.md §8).

The amendment rule is `preregistration_nfl.md` §0: once the holdout runs, any
change is a new version, evaluated only on games unseen when it was
committed. A rule that turns out ambiguous takes its stricter reading.

---

## 1. Data

| Split | Events (`KXNCAAFTOTAL`, by ticker date) | Use |
|---|---|---|
| **Pool / development** | every event dated **on or before 2026-06-30** (the 2025 season and its January 2026 bowls) | Base-rate pool only. Already read by the scan. |
| **Holdout** | every event dated **2026-07-01 through 2026-09-27** (2026 season to date; all settled at this commit) | Evaluated **once**, under §6. Unread: during the scan only event tickers (names and dates) were enumerated. |
| **Forward** | events dated 2026-10-01 onward | Only if the holdout passes, under a forward agent pre-registered separately. |

No holdout event is excluded. An event whose start time cannot be matched
(§2) is dropped and counted.

## 2. Commit instant and prices

- **Start time:** ESPN's college-football scoreboard. Matching is the scan's
  `match_team_game`: the listed ET date, then ±1 day if unique; ambiguous or
  unmatched events are dropped. **t = start − 60 min.**
- **Price at t:** the latest 1-minute candle with `end_period_ts ≤ t`, at most
  60 min old, with `0 < yes_bid < yes_ask < 1`; `mid = (bid + ask)/2`.
  Otherwise the rung is not priced.
- **Close:** the side-held mid of the latest 1-minute candle ending at or
  before the start.
- **Fees:** `venues.kalshi.fees`. The `KXNCAAFTOTAL` regime in force at t,
  from `GET /series/fee_changes`. Taker fee `0.07 × m × P(1 − P)`. Maker fee
  is that times the maker share (0.25 for `quadratic_with_maker_fees`).

## 3. The model, frozen

**Base rate b** is exactly the scan's (`jobs.scan_market_efficiency.base_rate`,
kind `total`, window W = 3). It is the mean binary settlement of
`KXNCAAFTOTAL` rungs with |floor − f| ≤ 3 points whose settlement was public
before t (`settlement_ts < t`), and 0.5 when fewer than 20 qualify. The pool
is **every** rung of every pool event, not the scan's 300-event sample (same
rule, more data), plus holdout rungs once their settlement is public before
t. Team-agnostic: b knows only the line.

**Forecast:** `p = 0.375 · mid + 0.625 · b`. **w = 0.625** is the scan's
pooled point estimate (0.6252), rounded and frozen. No refit on the holdout.

## 4. The gate (taker)

For every priced rung, both sides:

```
YES: edge = p       − (ask       + taker_fee(ask))
NO:  edge = (1 − p) − ((1 − bid) + taker_fee(1 − bid))
```

Because `ask = mid + half-spread`, the YES edge is `(p − mid) − half-spread −
fee`, and the NO side mirrors it. **The model has to beat the mid by
half-spread plus fee plus the margin.** A rung is a candidate iff:

1. `edge ≥ 0.03` (**margin 3¢**);
2. `ask − bid ≤ 0.06` (**spread ≤ 6¢**);
3. the quote is fresh (§2).

**At most one commitment per game:** the candidate with the largest edge.
Nominal size 100 contracts. R is size-invariant (§7 covers capacity).

## 5. Scoring

- **Taker R** (§9 of CLAUDE.md, fee inside the risk):
  `cost = entry + taker_fee(entry)`, `R = (settle_side − cost) / cost`, where
  `settle_side` is the settlement value for YES and 1 − value for NO.
  Fair-value settlements are scored at their value, kept and counted.
- **Maker shadow** (reported, never gating): separately per game, the rung and
  side with the largest `p_side − limit − maker_fee(limit)` ≥ 3¢, with spread
  ≤ 6¢. The limit is that side's bid at t. **Filled** only if prints strictly
  through the limit total ≥ 200 contracts (2 × size) in [t, start). That is
  the trade-through rule of `preregistration_nfl.md` §7.
  `R = (settle_side − limit − maker_fee) / (limit + maker_fee)` on fills.
- **CLV** (reported): close mid of the side held minus entry price.
- **Uncertainty:** every CI is a 95% bootstrap over **whole games** (2,000
  draws, seed **20261002**). With one taker trade per game, the game is the
  unit.

## 6. Holdout evaluation (run once)

**Pass ⇔ the taker mean R's 95% CI has a lower bound above 0**, with at least
**20 trades** across **10 games**. Fewer than that, or a CI that touches 0, is
a **fail**. Inconclusive is a fail.

Reported regardless of the result:

- trade count, YES/NO split, mean taker R with CI, win rate (never the
  headline), fair-value count;
- maker orders, fill rate, maker R on fills with CI;
- mean CLV with CI;
- **H2-style sanity:** with w = 0 (forecast = mid) the gate fires **zero**
  times, because mid − ask − fee < 0 always. Any commitment there is a bug,
  and the run is void;
- on all binary holdout rungs: the Brier score of mid, b and p; and the
  pooled base-rate weight with its event CI, as a descriptive replication of
  the scan;
- coverage: events in the holdout, start-matched, priced; skips by reason;
  the in-play check (share of rungs whose mid moved > 25¢ in the 2 h before
  t).

**Pass ⇒** a `cfb_total` paper agent may be specified under its own forward
pre-registration (margin, gate and model frozen as here). **Fail ⇒** CFB
totals stop under this design. Any new idea needs a new pre-registration and
forward-only validation; the holdout is spent.

## 7. Capacity, stated before the run

The scan put the median `KXNCAAFTOTAL` rung at about **72 contracts traded in
the 24 h before t**, with open interest about 84 and a 3¢ spread. A
paper record says nothing about size, so capacity is reported, not assumed.

**Rule:** a trade's capacity is **10% of the contracts traded on that rung in
the 24 h before t**, times its entry cost. That is the most one participant
could plausibly take without becoming the market. Candles carry no book depth,
so displayed size at t is unknown, and this is disclosed.

**Expectation at the scan's medians:** 10% of 72 ≈ 7 contracts at about
$0.50 ≈ **$3.50 per trade**. A heavy Saturday of about 60 games, a third
gated, is ≈ **$70 a day**.

Reported from the holdout:

- per trade: capacity in contracts and dollars (median, p90);
- per calendar day (ET): total capacity in dollars across that day's trades
  (median, max);
- the share of trades whose rung traded **fewer than 100 contracts** in the
  24 h before t. On that share, the nominal 100-contract size was never
  available.

**Binding reading:** if the median daily capacity is under **$100**, then
whatever the R result, `cfb_total` is a **research track record only**. Its
result may never be cited as a strategy that scales.

## 8. Leakage controls (tested before the run)

1. Every quote comes from a candle with `end_period_ts ≤ t`.
2. b uses only rungs with `settlement_ts < t`.
3. Nothing dated after 2026-09-27 and nothing before 2026-07-01 enters the
   holdout. Pool events are dated on or before 2026-06-30.
4. The runner refuses without `--execute`, refuses if any output exists, and
   is committed before it runs.

---

## 9. Execution log

2026-10-01 23:36 UTC, `python -m jobs.holdout_cfb_totals --execute`, run
once, alone (`docs/backtests/cfb_totals-holdout-20261001T233623Z.json`, with
CSV). Tests passed before scoring. The w = 0 sanity check made **0**
commitments, so the run is valid.

**Result: FAIL.** CFB totals stop under this design (§6).

| | |
|---|---|
| Taker | n = 276 trades in 276 games (126 YES / 150 NO). Mean R **+16%**, 95% CI **−15% … +51%**. The lower bound is not above 0. Win rate 16%. |
| CLV | **−0.33¢**, CI −0.49¢ … −0.12¢. The market moved *against* the entries. |
| Maker shadow | 276 orders, 8 filled (2.9%), all 8 lost (R −1.0). |
| Brier (5,235 binary rungs) | mid **0.1855**, base rate 0.2010, blend 0.1912. The blend is worse than the mid. |
| Replicated weight | base rate vs mid **0.04**, CI 0.00 … 0.26. Scan estimate 0.63, CI 0.21 … 1.00. **The signal did not replicate.** |
| Capacity | per trade: median 7.1 contracts / **$0.72**, p90 $40. Daily: median **$142**, max $1,191. 52% of trades were on rungs that traded fewer than 100 contracts in the 24 h before t. |

**Reading.**

- **The gate fired in every priced game.** With w = 0.625, a base rate that
  ignores the matchup pulls tail rungs a long way from the mid. That makes
  large paper "edges" on cheap contracts: a 16% win rate and a $0.72 median
  stake.
- The positive point estimate is longshot variance, not edge. The CI spans
  zero, CLV is negative with a CI below zero, and the blend's Brier is worse
  than the mid's.
- The scan's CFB-total cell was probably the about-one-in-22 false positive
  that its spec warned of, or a 2025-specific effect.

**Disclosed: coverage.** ESPN start-time matching dropped **264 of 540**
holdout events (49%; the scan dropped 16–19% on 2025 data). The likely
cause is team abbreviations for smaller programs. These events were dropped
by name-matching before any price was read, so the drop cannot depend on
outcomes. It may still tilt the sample toward larger programs. **Under §0's
stricter reading, this cannot rescue the result:** the verdict stands, and
the remaining 276 games were the evaluation.
