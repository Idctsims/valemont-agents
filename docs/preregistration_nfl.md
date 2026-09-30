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
| A2 | 2026-09-30 | λ is chosen by **walk-forward, time-ordered validation** on 2025 only: each week from the 5th onward is predicted by a fit on strictly earlier weeks; score = mean squared error over validated rows. Replaces leave-one-week-out. | Leave-one-week-out trains on weeks after the one it validates. Owner-directed; no random folds anywhere. Tested (`LambdaIsChosenWalkForward`). |

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
