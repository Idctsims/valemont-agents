# Pre-registration: Kalshi NFL agents (`nfl_ml`, `nfl_spread`, `nfl_props`)

**Version 1. Committed 2026-09-30, before any 2026 feature was computed.**
The git commit that introduces this file is the timestamp that matters.

This fixes, in advance, what each agent does, what data it may see, how it is
evaluated, and what result counts as what. Written under CLAUDE.md §8 (owner
decision): the ledger is the judge; each agent is scored on edge against price
plus fees, net of fees, never on win rate alone; no blending across agents.
**The stated prior for `nfl_ml` and `nfl_spread` is that they lose.**

Design reference: `docs/kalshi_nfl.md`. Where the two disagree, this file
wins for anything evaluated.

---

## 0. Amendment rule

- **Before the first 2026 feature is computed:** amendments are allowed, each
  as a dated git commit to this file with a one-line reason. Choices made while
  developing on 2025 data (§3.4) are expected to land here this way.
- **After:** frozen. Any change to a definition, threshold, model family or
  evaluation rule is a new version (v2), and **the holdout is burned**: v2 is
  evaluated only on weeks that were unseen when v2 was committed.
- A pass/fail rule may not be reinterpreted after the result is known. If a
  rule turns out ambiguous, the stricter reading applies.

### Amendments

| # | Date | Change | Reason |
|---|---|---|---|
| A1 | 2026-09-30 | Injury status is the team's final report for the game week, not "reports dated before t's day". | nflverse injury files carry no report date; the day-level rule could not be enforced. Made before any 2026 feature was computed. |
| A1c | 2026-09-30 | **Owner condition on A1:** publication before t is verified per game against the NFL's latest permitted release (4:00 p.m. ET: Wednesday for Thursday games, Thursday for Saturday, Friday for Sunday, Saturday for Monday). If that deadline is after t, or the game is on another weekday, or `location` is Neutral (international, Super Bowl), the injury factor and prop eligibility-by-report are **missing**, never assumed. | A report that may not have been public at t is a leak. Thanksgiving 12:30 p.m. kickoffs fail the check (commit Wednesday 12:30 < Wednesday 4:00 p.m.). Tested in `tests/test_nfl_injury_timing.py`. |
| A4 | 2026-10-01 | **Props: V1 frozen, blend w = 0.30 frozen, stricter continue rule** (§7.1). | Owner decision after the dev variants (`docs/dev/props_variants.md`). Committed before any further analysis. |
| A5 | 2026-10-01 | **Props: P2 (ladder overconfidence) pre-registered** (§7.2). **Discovered on 2025 dev data** (run log #7), so 2025 cannot test it; holdout and forward only. | Owner decision. Committed before any further analysis. |
| A5a | 2026-10-01 | Base-rate pool admits a rung only once its **settlement** was public before t, not once its game had **kicked off**; V1's NB size pairs use strictly earlier weeks. | Found reviewing the runner before any run: a 1 p.m. game has kicked off but not settled at a 4:25 game's commit instant, so "kicked off before t" leaked. Strictly tighter; made before the holdout ran. |
| A3 | 2026-09-30 | **`nfl_ml` λ = 10.0** (§3.1, §3.4 item 1). Walk-forward on 2025 only (282 of 285 games; weeks validated from the 5th on, 218 out-of-sample rows). MSE by λ: 0.01 → 6.720e-4, 0.1 → 6.610e-4, 1 → 6.536e-4, **10 → 6.530e-4**, 100 → 6.554e-4. Full output: `docs/dev/nfl_ml-dev-fit-2026-09-30.json`. | Development-data result, recorded before any 2026 feature is computed. See the development note below the table. |
| A2 | 2026-09-30 | λ is chosen by **walk-forward, time-ordered validation** on 2025 only: each week from the 5th onward is predicted by a fit on strictly earlier weeks; score = mean squared error over validated rows. Replaces leave-one-week-out. | Leave-one-week-out trains on weeks after the one it validates. Owner-directed; no random folds anywhere. Tested (`LambdaIsChosenWalkForward`). |

**Execution log (not a rule change).** 2026-09-30 ~16:40 CT: the first
`python -m jobs.holdout_nfl_ml --execute` passed H1, then aborted while
building walk-forward training rows, when a Kalshi request stayed rate-limited
through all backoffs (three Kalshi-heavy jobs had been started concurrently).
It scored no holdout game, produced no output, and no result was seen. The
client's backoff was lengthened and the holdout was re-executed alone; that
re-execution is the one run.

**Development note on A3 (not a rule change).** The 2025 walk-forward fit
at λ = 10 has coefficients line_movement +0.00116, line_movement_late +0.00046,
rest −0.00130 per day, injury −0.00632. Out of sample it improves close-move
MSE over predicting zero by 1.5% (6.530e-4 vs 6.630e-4) and Brier over the mid
by 0.00003 (0.21793 vs 0.21796): no meaningful signal. The largest adjustment
these coefficients can produce with extreme feature values is ≈ 1.6¢, below the
3.1–4.3¢ a commitment needs (fee + half-spread + 2¢ margin). **Consequence,
stated in advance: `nfl_ml` is expected to make zero or near-zero commitments
on the holdout and forward, and H2, H3 and H5 are expected to pass trivially.**
A pass will mean the plumbing is sound, not that the agent does anything. This
is the §8 prior showing up in the fit, and it is recorded here so the holdout
result cannot be read as more than that.

---

## 1. Data

### 1.1 Splits

| Split | NFL games | Use |
|---|---|---|
| **Development** | 2025 regular season + playoffs | Fit, explore, choose among the options this file leaves open (§3.4). Props: only weeks where the series exists (yards from week 6, receptions from week 9, passing TDs from week 12, anytime TD all weeks). |
| **Holdout** | 2026 regular season **weeks 1–3** (all settled as of this commit), **excluding BAL@DAL, LA@DEN and PHI@CHI (week 3)** | Evaluated once, under §5. **45 games.** |
| **Forward** | 2026 week 4 onward, live paper | The track record of §6. |

**Holdout exclusions, and why three games rather than one:** during API
verification on 2026-09-30 I (Claude) displayed data from three 2026 week-3
games: 1-minute Kalshi candles around kickoff for BAL@DAL, and the final
margins plus nflverse closing moneylines for BAL@DAL, LA@DEN and PHI@CHI (along
with `KXNFLGAME` settlement results for all three and one finalized
`KXNFLSPREAD` rung on PHI@CHI). The owner specified excluding BAL@DAL; the
other two are excluded under the stricter-reading rule. **Also disclosed:**
aggregate liquidity figures (median volume and spread per series) pooled
2025 and 2026 markets without computing any per-game feature; the 2026
schedule was read to count which weeks are settled.

### 1.2 Sources and as-of rules

Every input is read **as of the commit instant t**. Nothing observed after t
may influence a decision at t.

| Input | Source | As-of rule |
|---|---|---|
| Kickoff | nflverse `games.csv` (`gameday` + `gametime`, America/New_York → UTC) | Live: fetched at commit, stored with source and fetch time. Backtest: nflverse's current value (schedule history is not archived; disclosed). |
| Kalshi prices | 1-minute and hourly candlesticks (live and historical tiers) | Only candles with `end_period_ts ≤ t`. Live commits use the live market snapshot at t. |
| Rest days | nflverse `away_rest`, `home_rest` | Schedule-derived, known in advance. |
| Injury status | nflverse injury reports (`injuries_{season}.csv`) | **Amended 2026-09-30 (A1).** The file has no report date, only season and week, so the status used is the team's **final report for that game week** (`report_status`), **used only when its publication is verified to precede t (A1c)**: the policy deadline for the game's weekday at 4:00 p.m. ET must be ≤ t; otherwise the value is missing. Live: the report as fetched at t, same check. Backtest: week-level; a status changed after publication cannot be distinguished (disclosed). |
| Player stats, snaps | nflverse weekly/player stats | Games with kickoff < t. |
| Fees | `GET /series/{s}` + `GET /series/fee_changes?show_historical=true` | The fee regime in force at t (e.g., `KXNFLGAME` became `quadratic_with_maker_fees` on 2026-01-01; before that date its maker fee is 0). |

**Price convention.** `mid = (yes_bid + yes_ask) / 2` of the latest candle
with `end_period_ts ≤ t`. A candle older than **30 min** (ml/spread) or
**60 min** (props) at t is stale: no commitment for that market.

---

## 2. Shared rules (all three agents)

### 2.1 Entry, cost, edge

```
entry_price  = yes_ask (buying YES) or no_ask (buying NO)       [side held]
taker_fee    = 0.07 × fee_multiplier × P × (1 − P)               P = entry_price
entry_cost   = entry_price + taker_fee
edge         = p_model(side) − entry_cost
```

`no_ask` in a backtest is `1 − yes_bid` from the same candle. Fees use the
schedule's formula with the series' `fee_type` and `fee_multiplier` in force at
t; there is no hard-coded per-series coefficient.

### 2.2 The gate

A commitment fires iff **all** hold:

1. `edge ≥ margin(agent)`, with margins **`nfl_ml` 2¢, `nfl_spread` 3¢,
   `nfl_props` 4¢**.
2. Spread at t ≤ **3¢** (ml, spread) or ≤ **8¢** (props).
3. Price not stale (§1.2).
4. Live only: `size ≤` displayed ask size (`yes_ask_size_fp` /
   `no_ask_size_fp`). The backtest cannot see depth and assumes it; disclosed.

At most **one commitment per game** for `nfl_ml`, **one per game** for
`nfl_spread`, and **one per (player, stat)** for `nfl_props`: the candidate
with the largest edge.

### 2.3 Size

Nominal **100 contracts**. Live: `min(100, displayed size)`. The R-multiple is
size-invariant, so size affects only fee rounding (negligible) and realism.

### 2.4 Scoring (the record)

```
capital_at_risk = entry_cost × contracts
proceeds        = settlement_value × contracts
pnl             = (proceeds − capital_at_risk) / capital_at_risk      [§9, fee inside risk]
clv             = close_mid(side) − entry_price(side)                 [§10, both side-held]
```

- **`close_mid`**: side-held mid of the last 1-minute candle with
  `end_period_ts ≤` actual kickoff.
- **Settlement values between 0 and 1** (tie $0.50; postponement > 48 h,
  forfeit before kickoff, venue swap, prop player inactive or active-no-snap:
  "last fair price"): scored as settled at that value. Leg outcome
  **`settled`** (fair value), commitment outcome `partial`. **Never void.**
  Included in pnl and CLV means. **Excluded from calibration metrics**
  (a fair price is not an outcome a probability can be scored against), and
  counted and reported separately.
- **Maker shadow** (not a commitment, not in `pnl`): for each commitment, a
  resting bid at the side's `bid` from commit to kickoff; **filled** only if
  trades printed strictly below the limit (`price.low < limit`) and volume in
  those candles totals ≥ 2 × size. Maker fee = the series' maker rate at t
  (0 for `quadratic`; 0.25 × taker for `quadratic_with_maker_fees`). Recorded
  in `resolutions.detail.maker`.

### 2.5 Uncertainty

**All standard errors are clustered by game** (props within a game are
correlated; so are an agent's same-game rungs). The unit of independence is
the NFL game.

### 2.6 Timing

| Agent | Commit instant t |
|---|---|
| `nfl_ml`, `nfl_spread` | **kickoff − 24 h** (primary). kickoff − 72 h is reported as a secondary and cannot replace the primary. |
| `nfl_props` | **kickoff − 75 min** (after inactives, ~kickoff − 90 min) |

### 2.7 Deadlines (from Kalshi contract terms, read 2026-09-30)

| Agent | `closes_at` | `resolves_after` | resolution `max_overdue` | capture `max_overdue` |
|---|---|---|---|---|
| `nfl_ml` | kickoff as-of | API `expected_expiration_time` | **10 days** (FOOTBALLGAMEWIN: expiration ≤ 1 week after the game, settlement next day, outcome review) | 72 h |
| `nfl_spread` | kickoff as-of | API `expected_expiration_time` | **17 days** (FOOTBALLSPREAD: expiration ≤ 15th day after the game) | 72 h |
| `nfl_props` | kickoff as-of | API `expected_expiration_time` | **17 days** (FOOTBALLENTITYSTAT: ≤ 15th day) | 72 h |

A game postponed more than 48 h never kicks off in the contract's sense:
capture raises `CloseUnavailable` ("postponed beyond 48h; settled at fair
price"), and resolution scores the fair-price settlement.

---

## 3. Models

### 3.1 `nfl_ml`

**Assumption, stated:** the closing mid is a better probability estimate than
the mid at t. So the model predicts the move to the close and adds it:

```
p_model(home YES) = mid_home(t) + Σ_i β_i · x_i
p_model(away YES) = 1 − p_model(home YES)
```

No intercept: a constant home drift is not a named factor and gets no credit.

| Factor name (`commitment_factors.name`) | x | Definition |
|---|---|---|
| `line_movement` | `mid_home(t) − mid_home(kickoff − 6 d)` | NULL if no candle at or before kickoff − 6 d, or < 100 contracts traded between kickoff − 6 d and t |
| `line_movement_late` | `mid_home(t) − mid_home(t − 24 h)` | NULL if either candle is stale |
| `rest` | `clip(home_rest − away_rest, −7, 7)` | days |
| `injury` | `qb_out(away) − qb_out(home)` | `qb_out(team) = 1` if that team's starting QB is listed Out or Doubtful on the team's final report for the game week (§1.2, A1), else 0. Starting QB = most pass attempts over the team's previous 3 games with kickoff < t (week 1: previous season's final 3). NULL if the team has no report that week. |

Each factor row's `value` is `β_i · x_i` in probability points. A NULL factor
contributes nothing and gets no row.

**Fitting.** Target `y = close_mid_home − mid_home(t)`. Ridge regression, no
intercept, λ chosen by walk-forward validation on 2025 development data (A2)
and then fixed (recorded here by amendment before the holdout). Rows with a NULL x are
dropped from that coefficient's fit (per-factor complete cases). **Refit
weekly**, walk-forward, on every game with kickoff before the refit instant.

**Both sides are evaluated**; the larger edge that passes §2.2 is committed.

### 3.2 `nfl_spread`

- **Ladder at t:** every "TEAM wins by over X.5?" rung for both teams, as a
  margin statement about M = home score − away score.
- **Fit** `M ~ Normal(μ_t, σ_t)` by least squares on `Φ⁻¹(mid)` over rungs with
  0.10 ≤ mid ≤ 0.90; at least 3 rungs, else no commitment.
- **Adjust** `μ_model = μ_t + Σ_i γ_i · x_i`, with the same four factors as §3.1,
  fitted by ridge on target `μ_close − μ_t` (μ_close from the ladder at close),
  same λ procedure and walk-forward refit. σ_t is not adjusted.
- **Price** every rung under `Normal(μ_model, σ_t)`; evaluate YES and NO on
  each; commit the single candidate with the largest edge passing §2.2.
- Factor rows: `value = P_model(rung) − P_t(rung)` split across factors in
  proportion to `γ_i · x_i`.

### 3.3 `nfl_props`

**Series:** `KXNFLPASSYDS`, `KXNFLRSHYDS`, `KXNFLRECYDS`, `KXNFLREC`,
`KXNFLPASSTDS`, `KXNFLANYTD`.

**Eligibility at t** (a player failing any of these is not evaluated):
- Not listed Out or Doubtful on the team's final report for the game week (§1.2, A1).
- Live: on the game's active list at t. **A player ruled out is never
  committed on**, because Kalshi settles such markets at a pre-news fair price
  (FOOTBALLENTITYSTAT, entity withdrawal clause).
- At least 2 of the team's previous 4 games with ≥ 1 opportunity in the stat
  (target, carry or attempt).

**Distribution per player-stat:**
- **Counts** (receptions, passing TDs, anytime TD = `P(TD ≥ 1)` from a total-TD
  count): negative binomial.
- **Yards:** negative binomial on integer yards, or gamma on `yards + 10`,
  **chosen per stat on development data** by walk-forward log-likelihood
  (§3.4).
- **Mean** = opportunity × efficiency × opponent × environment:
  - opportunity and efficiency: exponentially weighted over the player's games
    with kickoff < t, weight `0.5^(days/90)`, shrunk toward the position mean
    with a 4-game pseudo-count;
  - opponent: `(opponent's allowed per game to the position / league mean)`,
    shrunk with a 6-game pseudo-count;
  - environment: `(team's implied points at t / league mean)`, where implied
    points come from the game's `KXNFLTOTAL` and `KXNFLSPREAD` mids at t;
    1.0 if unavailable.
- **Dispersion** per stat × position, fitted on development data.
- **Refit weekly**, walk-forward.

**Pricing:** `P(X > floor_strike)` for every rung; evaluate YES and NO; commit
the largest edge per (player, stat) passing §2.2. Slate cap raised to 150.

### 3.4 Left open for development data, to be fixed by amendment before the holdout

1. Ridge λ for §3.1 and §3.2 (walk-forward, A2).
2. Yards family per stat (negative binomial vs gamma) and prop dispersions.
3. Nothing else. Margins, timing, factors, gates, series and evaluation are
   fixed now.

---

## 4. Leakage controls (each must be a passing test before the holdout runs)

1. **Perturbation:** replace every candle with `end_period_ts > t` by random
   values, and every nflverse row dated at or after t's day by random values:
   every decision and every feature must be bit-identical.
2. **Walk-forward:** no model version used at t has `data_through ≥ t`.
3. **Close is a target, never a feature:** no feature function receives a
   candle later than t (enforced by one entry point, `features_asof(…, t)`).
4. **Timezone:** 2026-09-27 16:25 ET resolves to 20:25Z; a December game
   resolves under EST.
5. **Replay determinism:** the same inputs produce the same commitments twice.

---

## 5. Holdout evaluation (45 games, run once)

With ~45 games per game-market agent, **this cannot establish an edge.** It
is a bug and calibration filter. Each agent passes if **all** of these hold:

| # | Check | Rule |
|---|---|---|
| H1 | Leakage | All §4 tests pass. |
| H2 | Market-only sanity | With every β/γ set to 0, `nfl_ml` and `nfl_spread` make **zero** commitments (edge = mid − ask − fee < 0 always). |
| H3 | Calibration, game markets | Brier(p_model) ≤ Brier(mid at t) + 0.005 over all evaluated games (fair-value settlements excluded). |
| H4 | Calibration, props | Walk-forward PIT deciles on development weeks each within [5%, 15%]; holdout reported, not gated (too few). |
| H5 | Plausibility | Commits on ≤ 50% of eligible games (ml, spread); holdout mean net R not above +1.0. Either breach is treated as a bug until explained. |

Reported for every agent regardless of pass or fail: commitment count, mean
net R (taker) with game-clustered 95% CI, mean CLV with CI, win rate (never as
the headline), fair-value settlement count, maker-shadow fill rate and maker
R, and the baselines (market-only; random side at the same count).

**Pass ⇒ the agent may run forward on paper. It licenses nothing else.** Fail
⇒ fix, amend (v2), and evaluate v2 on unseen weeks.

---

## 6. Forward record: what survives

Evaluated per agent, separately, at the end of each full regular season
(first: end of 2026 week 18 for commitments from week 4), on commitments made
live under this version:

- **Primary metric:** mean net R-multiple (taker), game-clustered 95% CI.
- **Secondary:** mean CLV (game-clustered CI); maker-shadow R.
- **Survival rule, fixed now:**
  - CI entirely **below 0** → the agent is retired. Its rows stay; the record
    is the data.
  - CI entirely **above 0** → it may be described as having positive net
    expectancy *on this record*, with the CI quoted. That is still not a
    profitability claim beyond the record (§8).
  - CI **straddles 0** → it runs one more season under the same version.
- **Not allowed:** pooling agents; headline win rate; dropping fair-value
  settlements; restarting the clock by editing a version.

Selections (`selections`, owner picks before kickoff) are evaluated as a
separate record per agent under the same metrics.

---

## 7. Props amendments (A4, A5) — committed 2026-10-01 before further analysis

Both strategies are evaluated on the **same holdout as `nfl_ml`**: 2026 weeks
1–3 minus BAL@DAL, LA@DEN and PHI@CHI (45 games; §1.1), every eligible
yardage-prop player-game in those games (`KXNFLPASSYDS`, `KXNFLRSHYDS`,
`KXNFLRECYDS`). Commit instant, eligibility, price convention, fees and
scoring as in §2 and §3.3 (t = kickoff − 75 min; quotes ≤ 60 min old; A1c
report check; ≥ 2 of the last 4 team games active). One runner, run once,
results to `docs/backtests/`.

**Shared definitions.**
- *Taker selection:* per (player, stat), the (rung, side) with the largest
  `p_side − (ask + taker_fee)`, kept if ≥ 4¢ and spread ≤ 8¢. Entry at the
  ask; R per §2.4, fee inside the risk.
- *Maker selection:* per (player, stat), the largest `p_side − limit`, limit =
  that side's bid at t, kept if ≥ 4¢ and spread ≤ 8¢. **Filled only under the
  trade-through rule:** Kalshi prints strictly through the limit totalling
  ≥ 200 contracts (2 × 100) in [t, kickoff). Fee 0 (props are `quadratic`).
  R on fills.
- *Uncertainty:* every CI is a 95% bootstrap over whole games (2,000 draws,
  seed 20260930).
- *Base rate* `b(rung)`: the mean binary settlement of eligible rungs of the
  same stat with |floor − f| ≤ 5 yards among rungs **whose Kalshi settlement
  was public before t** (2025 development rungs plus holdout rungs; A5a);
  0.5 when fewer than 20 such rungs. The forecaster of run log #7 (which
  used earlier weeks only, so it never met the case A5a closes).

### 7.1 A4 — V1 and the frozen blend

- **Model frozen:** V1 as specified in `docs/dev/props_variants.md` and
  implemented in `sports.nfl.props_variants.usage_mean` (half-life 90 days,
  20 pseudo-opportunities, 6-game opponent pseudo-count), negative binomial
  with size per stat × position fitted on all earlier player-games (2025 plus
  earlier holdout weeks). No calibration layer.
- **Blend frozen:** `p_blend = 0.30 · p_V1 + 0.70 · p_mid`.
- **Continue rule (owner, stricter):** on the holdout, compute the pooled
  closed-form weight against the mid for V1 (`w_V1`) and for the base rate
  (`w_base`). **Props continue iff the game-clustered 95% CI of
  `w_V1 − w_base` (paired bootstrap: the same resampled games for both) has a
  lower bound above 0.** This is the stricter reading of "the model's weight
  exceeds the base rate's" (§0); the point comparison is reported alongside.
- **Reported:** Brier of the blend vs the mid; `w_V1`, `w_base` and the
  difference with CIs; taker R and maker R of blend-selected trades.

### 7.2 A5 — P2, ladder overconfidence

**Discovered on 2025 development data:** a constant 0.5 and a player-agnostic
base rate both earned positive blend weight against the Kalshi mid (run log
#7), suggesting the mid at t is overconfident. The 2025 data that suggested
it cannot test it.

- **Forecast:** `p_P2 = 0.855 · p_mid + 0.145 · b(rung)`. The weight 0.145 is
  frozen from run log #7.
- **Trades:** taker and maker selections as above, using `p_P2`.
- **Extremity buckets** by the rung's YES mid at t: [0, 0.2), [0.2, 0.4),
  [0.4, 0.6), [0.6, 0.8), [0.8, 1]. Taker and maker R are reported per
  bucket.
- **Pass rule:** P2 passes iff the **taker** R's game-clustered 95% CI has a
  lower bound above 0. Maker R is reported, not gating (secondary; it uses
  the same selection idea and would otherwise be a second bite). Too few
  trades to compute a CI = not passed.
- **Reported:** Brier of `p_P2` vs the mid; taker and maker R overall and per
  bucket; fill rate.

### 7.3 What a pass licenses

As in §5: running forward on paper under a frozen version, scored by its own
net-of-fee record. With ~45 games, a fail is far more likely than a pass,
and **an inconclusive result is a fail.**

### 7.4 Execution log

2026-10-01 05:26 UTC, `python -m jobs.holdout_nfl_props --execute`, run once
(`docs/backtests/nfl_props-holdout-20261001T052631Z.json`): 45 games, 43 with
priced props, 4,731 rungs (4,692 binary).

- **A4 (frozen V1 blend): FAIL.** Pooled blend weight against the mid:
  V1 0.00, base rate 0.00 (both clipped at zero; neither improves the mid);
  difference CI [−0.045, 0.000], lower bound not above 0. Brier: mid 0.1554,
  blend 0.1579, V1 alone 0.1713. Taker R −47% (CI −72%…−21%, n = 102).
  Maker: 11% filled, R on fills +60% (CI −17%…+154%, n = 44).
  **Per the continue rule, props do not continue.**
- **A5 (P2): FAIL.** Taker R +29% (CI −51%…+117%, n = 150), lower bound not
  above 0. Brier 0.1566 vs mid 0.1554. Nearly all taker trades fall in the
  [0, 0.2) bucket (n = 115, +54%, CI −53%…+169%): buying cheap longshots,
  high variance, not significant. Maker: 3% filled.
