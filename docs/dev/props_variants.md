# nfl_props development variants — specified before any run

**Written 2026-09-30, before any variant below has been run.** The commit that
adds this file is the timestamp. Owner direction: bounded iteration, 2025
development data only, walk-forward, no 2026 data. **Every run is logged at
the bottom, pass or fail.**

This is development, not the pre-registered holdout. Nothing here licenses a
claim; it decides whether props continue to a pre-registered holdout.

---

## Common to every variant

- **Data:** 2025 Kalshi yardage props (`KXNFLPASSYDS`, `KXNFLRSHYDS`,
  `KXNFLRECYDS`, from week 6), historical tier; nflverse 2024–2025.
- **Commit instant:** t = kickoff − 75 min. Prices: the latest 1-minute candle
  ending at or before t, at most 60 min old.
- **Eligibility** (unchanged from the first dev run): report publication
  verified before t (A1c); not Out or Doubtful; ≥ 2 of the team's last 4
  games with an opportunity.
- **Distribution:** negative binomial, size r per stat × position, fitted
  walk-forward on earlier weeks only.
- **Evaluation set: 2025 weeks 9 onward** (including playoffs), for every
  variant, so V2's calibration layer always has ≥ 3 prior weeks and every
  variant is scored on the same rungs.
- **Metrics, each variant:**
  1. MAE of the model mean vs actual yards per stat, and the market-implied
     median's MAE on the same games;
  2. Brier of P(over) vs settlement, and of the Kalshi mid, on binary
     settlements (fair-value excluded), per stat and overall;
  3. reliability by decile;
  4. taker R: one candidate per (player, stat), the largest edge with
     edge ≥ 4¢ and spread ≤ 8¢, entry at ask + quadratic fee; mean with a
     95% CI by bootstrapping whole games (2,000 draws).
- **Best variant:** lowest overall Brier on the evaluation set. Ties within
  0.0005 go to the simpler variant.

## The variants

**V0 — baseline (reference only).** The first dev run's model, re-scored on
the evaluation set.

**V1 — usage-weighted prior (fix the low bias).**
`mean = opp × ypo × opponent_factor`, where
- `opp` = the player's exponentially weighted opportunities per game
  (targets / carries / attempts; weight 0.5^(days/90)), **not** shrunk
  toward an all-player average (that shrinkage is the suspected low-bias
  source: it pulls starters toward a mean that includes low-usage players);
- `ypo` = yards per opportunity, `(Σw·yards + K·ypo_pos) / (Σw·opp + K)`, K = 20
  pseudo-opportunities, `ypo_pos` = the position's league yards per
  opportunity before t;
- `opponent_factor` as before (6-game pseudo-count).

**V2 — V1 + walk-forward calibration layer.** Per stat,
`p' = σ(a + b·logit(p))`, fitted by maximum likelihood on all binary-settled
rungs from weeks before the week being priced. Refit weekly.

**V3 — V2 + environment term.** `mean × (implied team points / league mean
team points)`. Implied team points from the game's Kalshi ladders at t:
fit `Normal` to the total ladder (`KXNFLTOTAL`, P(total > X)) and the spread
ladder (`KXNFLSPREAD`, home margin), then home = (T + M)/2, away = (T − M)/2.
League mean = actual points per team-game in games that kicked off before t.
Missing or unfittable ladders → factor 1.0, counted. The calibration layer is
refit on V3's probabilities.

**V4 — V3 + `vacated_usage`.** For receiving and rushing: teammates in the
same opportunity pool (receiving: WR/TE/RB targets; rushing: RB carries)
listed **Out or Doubtful** on the final report (A1c-verified) whose weighted
share of team opportunities is ≥ 15% vacate that share. Each remaining player
gains it in proportion to their own share:
`opp × (1 + vacated × share / Σ shares of remaining players)`. Passing: no
change (documented limitation). **Proxy, disclosed:** game-day inactives are
not in nflverse, so "ruled out at inactives" is approximated by the final
report.

## Market-blend diagnostic (best variant only)

`p_blend = w·p_model + (1 − w)·p_mid`, w on a 0.05 grid.
- **Walk-forward:** each evaluation week uses the w minimizing Brier on all
  earlier weeks; report the out-of-sample blend Brier against the mid.
- **Pooled:** the w minimizing Brier over the evaluation set, with a 95% CI by
  bootstrapping whole games (2,000 draws).
- **Proposed decision rule (owner decides):** props continue to a
  pre-registered holdout only if the pooled w's 95% CI **excludes 0**. If it
  includes 0, the model adds nothing the market does not already price.

## Maker, pre-registered trade-through rule (§2.4), with trade prints

- **Order:** a resting bid at the side's bid at t, 100 contracts, live from t
  until kickoff.
- **Fill:** only if trades printed **strictly through** the limit (YES bid:
  `yes_price < limit`; NO bid: `yes_price > 1 − limit`) with cumulative count
  ≥ 200 (2 × size) in [t, kickoff). Trades read as-of from Kalshi's trade
  prints. Props are `quadratic`: maker fee 0.
- **Two selections, same count:**
  - *model:* best variant, one per (player, stat), largest maker edge
    `p_side − limit ≥ 4¢`, spread ≤ 8¢;
  - *random:* uniformly sampled (rung, side) pairs from all rungs with
    spread ≤ 8¢, seed 20260930.
- **Report each:** fill rate; maker R on fills (mean, game-clustered CI);
  **adverse selection** = mean side outcome and would-be R for filled vs
  unfilled orders. If model and random maker R are indistinguishable, the
  model adds nothing beyond spread capture.

---

## Run log

| # | When (UTC) | Run | Result | Notes |
|---|---|---|---|---|
