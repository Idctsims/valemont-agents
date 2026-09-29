# Five-repo reference analysis

**Written:** 2026-09-29. **Audience:** whoever writes the `kalshi` and `prizepicks`
adapters (build order steps 7–8).

Design intelligence only. **No code from these repositories was copied into this
project, and none should be** — see Licensing below.

The five repositories live in `reference/` (gitignored):

| Directory | Author | What it is |
|---|---|---|
| `nfl-props` | Nicowirz | Log-linear player-yardage props, prices against **Kalshi** |
| `NFL-Prop-Model` | ctrax68-hash | TypeScript prop engine, projection → pricing → Kelly |
| `nfl-predict` | jackc625 | WP/ATS/O-U game models, DuckDB lakehouse, FastAPI |
| `nfl-prediction-model` | nemethb3 | Elo + EPA game/season/fantasy, all-free data |
| `EdgeLabs` | benbr11 | Six sports, walk-forward ratings, browser apps |

## Licensing

**None of the five has a `LICENSE` file.** Public on GitHub with no license means
default copyright: readable, not copyable. Everything below is a design
observation. Any actual code reuse needs the author's permission first. The
`reference/` directory is gitignored for this reason.

---

## 1. The finding that should shape our adapters

**Three independent builders measured themselves against the market on liquid game
lines and lost.** None coordinated; all three landed in the same place.

- **nemethb3** — a real LOOCV search over Vegas/Elo blend weights chose **100%
  Vegas at all five checkpoints** (weeks 1, 4, 8, 12, 16), contradicting their own
  hypothesis that Elo would take over late. Betting their own disagreements with
  Vegas returned **−36% ROI** — "actively harmful, not just unhelpful."
- **jackc625** — **no target came out `PROFITABLE_CLEAN`** on a pre-registered
  2025 holdout. WP flat-stake ROI +0.0144 with ROI p = 0.41 raw / 0.67
  BH-adjusted; ATS **−5.3%**.
- **EdgeLabs** — *"No model beats the closing point spread reliably."*

**Consequence:** the `kalshi` adapter should not target liquid game markets. The
defensible ground is thin markets and player props — which is exactly where both
prop-focused repos went. This is recorded in CLAUDE.md §8.

**Second consequence, subtler.** jackc625's single strongest statistical result in
the whole project was a **CLV result on WP** — in the same run where profitability
failed. He says outright that closing-line value is not profitability and calls
that divergence the phase's headline finding rather than a return. Our §10 makes
CLV the primary metric for good reasons; it is not a proxy for profit, and §8 now
says so in as many words.

---

## 2. Per-repo profiles

### `nfl-props` — Nicowirz

**Features.** One log-linear fit per stat category:
`log(yards + 10) ~ intercept + player_ability + opponent_defense + home_field`,
weighted ridge (closed-form normal equations) with `0.5^(days_ago / 180)` recency
decay. L2 penalty on player and opponent-defense coefficients only — not the
intercept or home-field term — which shrinks low-sample players toward the league
average automatically. Residual σ estimated per position group where ≥30 residuals
exist.

Two validated additions:
- **Usage-share covariate** (`rec_yds`, `rush_yds`): trailing recency-weighted
  share of the player's own team's targets or carries, computed leakage-safe (only
  games strictly before the predicted one contribute; a player who misses a week
  contributes nothing for that week).
- **Low-sample QB σ widening** (`pass_yds`): widens σ for a QB with <4 games at
  fit time.

Plus four Poisson-family rate models (receptions, pass/rush/rec TD) feeding a
Monte Carlo PPR simulator.

**Data.** nflverse (free, disk-cached). **Kalshi market data — free, no API key.**

**Validation.** Genuine walk-forward, refit weekly, never sees the predicted game.

| stat | model NLL | naive baseline |
|---|---|---|
| pass_yds | 0.4980 (n=589) | 0.5875 |
| rush_yds | 0.7642 (n=1122) | 0.8443 |
| rec_yds | 0.9152 (n=3405) | 1.0096 |

PIT calibration tables printed per stat.

**README admits.** No injury/inactive awareness. No depth-chart awareness — it
projected backup Flacco at 252.4 pass yards beside starter Burrow at 262.4 in the
same game. Legs priced as independent despite game-script correlation. Kalshi
real-time only *in their implementation*, so the backtest cannot prove it beats
Kalshi's close. Defaults (`--halflife 180`, `--reg 5.0`) never grid-searched. The
negative-binomial step is a reparameterization, not a fitted NB likelihood, and
the dispersion estimate has no degrees-of-freedom correction — biasing the
simulator slightly overconfident. The *combined* fantasy distribution is
unvalidated even though its four components are.

The most honest README of the five, by a distance.

### `NFL-Prop-Model` — ctrax68-hash

**Features.** Baselines → projections → distributions → edges → Kelly. Truncated
normal for yardage (location solved so the mean still equals the projection),
negative binomial for counts (NFL receptions are overdispersed relative to
Poisson). **Modelled σ**: `σ = w·σ_player + (1−w)·σ_league(μ)` with `w = n/(n+k)`
and the league relationship fit from data. Devigged fair price. Game script
interpolates smoothly rather than stepping at a ±6 spread threshold. Kelly with a
push term.

**Data.** nflverse free — weekly stats, `games.csv` (closing spreads/totals/roof/
temp/wind), snap counts. ESPN's unofficial site API for live scores, explicitly a
scoreboard that never feeds the engine. **The Odds API for real prop lines —
paid.**

**Validation.** Replay of 2023–24: 36 weeks, 2,694 bets, ~21,000 props scored,
**mean calibration error 3.16pp.**

**README admits.** Headline: *"There is no free source of historical player-prop
lines."* And: **"Against synthetic lines, ROI is circular and proves nothing"** —
the edge measured is the simulation's own noise read back. Zero-inflation not
implemented (~8% of graded props are players who played and recorded nothing). The
Odds API integration was **never exercised against the live service**. Supabase
connection unverified.

Three measured negative results that changed the defaults — worth copying the
*discipline*, not the code:
- **Void, not zero**, for players who never took the field: 22% of posted props
  have no stat line; settling them zero put calibration error at 9.41pp versus
  2.21pp voided. Snap counts distinguish "played and recorded nothing" from "did
  not play."
- **Team-share normalisation off by default**: measured a −11% to −16% bias across
  skill positions because ~a fifth of the candidate roster is inactive.
- **Sigma refit rejected**: raised calibration error 1.22 → 1.49pp, so the shipped
  fits stay the default.

### `nfl-predict` — jackc625

**Features.** Elo; twelve `rolling_*` team-form columns (EPA per play overall/pass/
rush, success rates, neutral pass rate, red-zone TD rate, third-down conversion,
CPOE, average drive start, neutral pace); weather (indoor zeroed; temperature,
wind, precipitation, severity families); contextual (rest, travel, divisional,
surface mismatch); composite QB quality `0.7·z(rolling_qb_epa) + 0.3·z(rolling_cpoe)`;
opponent-adjusted EPA; injury (`qb_out_flag`, `backup_quality_delta`,
`availability_fraction`, plus three coverage flags). Expanding-window normalisation
grouped by season, `min_periods=4`, prior-season bootstrap — explicitly replacing a
leakier within-season Z-score approach.

**Data.** nflverse (109 files reference it). open-meteo and Iowa State mesonet for
historical weather — free. Wikipedia/Wikidata for venue facts, revision-pinned.
**The Odds API — paid**; the `odds_timeline` trajectory table is described in the
source as "the Phase-29 purchase."

**Validation — the most rigorous of the five.** Three independent temporal-safety
layers: a `@runtime_checkable FeatureBuilder` Protocol with a mandatory
`as_of_datetime` fence; a `LeakageGate` that keyword-scans the combined feature
matrix and validates Elo chronological ordering; and a splitter that hard-fails if
train/validation/holdout seasons overlap. Blend weights tuned **strictly on
pre-2018 seasons**, temporally disjoint from the 2021–2024 backtest window. A
pytest AST import guard fails CI if `api/` ever imports from `models/`.
Per-target non-regression deploy gate that has **refused** promotions.

**README admits.** No target `PROFITABLE_CLEAN` (see §1). Explicitly out of scope:
player props, automated placement, in-game prediction.

### `nfl-prediction-model` — nemethb3

**Features.** Team Elo (carryover and Vegas-informed variants), EPA pipeline, SOS
adjustment, game-spread matchup adjustment, fantasy volume models, injury model,
playoff probability.

**Data.** nflreadpy / nfl_data_py only — **entirely free, no keys anywhere.**
Vegas lines come from nflverse schedules: *"one closing-line snapshot per game (no
intraday/opening-line history exists in this data source)."*

**Validation.** Fit on 2015–2023/24, hold out 2024/2025, LOOCV wherever a weight
or threshold is learned from the same data it is evaluated against. **No unit
tests** — validation is backtesting only.

**README admits.** Vegas beats the model at every checkpoint (§1). Blending loses
to the single strongest signal — the same pattern recurred independently **four
times**. Injury-severity and rest-day adjustments tested null and were correctly
*not* integrated. EPA win projections ~3.3× too narrow, proven unfixable by simple
rescaling — which is also *why* their confidence-based features underperform.
Percentile-across-correlated-candidates CIs were badly miscalibrated (12.5% actual
coverage vs 90% target) and abandoned.

**Relevant to us:** `data/locked_predictions/` plus a SQLite `weekly_tracking.py`
is a hand-rolled version of our ledger. Its own manifest says the snapshot is
*"kept for honest post-hoc accuracy comparison, **not literally immutable**."*
That sentence is the argument for our database triggers, written by someone who
did not have them.

### `EdgeLabs` — benbr11

**Features.** Six sports. Opponent-adjusted offence/defence ratings with
per-sport recency half-lives (NBA ~160d, NFL ~230d, NHL 70d, club leagues 400d),
mapped to win probability through a Gaussian point-margin model
`P(home) = Φ(proj_margin / SD)`. Dixon-Coles low-score correction for soccer
(ρ = −0.12). Opponent-quality Elo for UFC with a grappler premium. A decaying
preseason-consensus prior that fades as real games accrue.

**Data.** nflverse, football-data.co.uk, statsapi.mlb.com, api-web.nhle.com,
moneypuck — all free. `the-odds-api` appears only in probe/framework scripts, not
the live path.

**Validation.** Walk-forward with an explicit date cutoff. The backtest scripts
**re-implement** each model's rating math with a cutoff rather than reading the
live ratings files — `backtest_nfl.py` rebuilds ratings per week and breaks on
`if r["dt"] >= cutoff`. NFL 65.9% out-of-sample against a ~66% market benchmark.

**README admits.** *"No model beats the closing point spread reliably."*
Consensus priors for past seasons use the **current** preseason consensus as a
stand-in because no point-in-time historical consensus exists — self-flagged, and
the headline number is model-only. Situational data (injuries, goalies, weather)
is passed as live per-game flags, not baked into ratings. UFC method/round accuracy
not formally backtested.

---

## 3. Convergent features

Convergence across builders who did not coordinate is weak evidence a feature
matters. Weak evidence is still evidence.

**In all five — treat as settled:**

| Feature | Instances |
|---|---|
| Opponent adjustment / defence-allowed rating | nfl-props `opponent_defense` coefficient; EdgeLabs opponent-adjusted off/def; jackc625 `opponent_adj.py`; nemethb3 `sos_adjustment.py`; ctrax68 defensive rates |
| Exponential recency decay | nfl-props `0.5^(d/180)`; EdgeLabs 70–400d per sport; jackc625 rolling windows; nemethb3 Elo carryover |
| Shrinkage of low-sample units toward a league prior | nfl-props ridge L2; ctrax68 `w = n/(n+k)`; jackc625 `min_periods=4` + prior-season bootstrap; EdgeLabs regressed starting-pitcher factor |
| Home field | all five |
| Devig before comparing to a model probability | ctrax68 quantifies it: raw implied sums to ~1.05, erasing ~2.4pp of edge on a −110/−110 line — "most of the bets at a 3% threshold" |
| Distributional output, never a point estimate | log-normal / truncated-normal + NB / residual converters / Gaussian margin / Poisson |
| Calibration prized over raw accuracy | four of five state it explicitly |

**The strongest player-level convergence — and the one to build on for props:**

**Usage / volume share.** Three builders, three methods, one conclusion.
- nfl-props added it as a covariate and measured NLL improvement (rec_yds −3.3%,
  rush_yds −10.6%). One of only two things that passed their validation gate.
- ctrax68 builds target shares (and found that *normalising* them to team volume
  without an inactives list is harmful).
- nemethb3 found **volume beats EPA×volume** at RB, QB and TE. The RB case was
  dramatic: −0.504 → +0.651.

**Convergence on what does NOT work — more valuable than the positives:**

- **Blending several signals loses to using the strongest one alone.** nemethb3
  hit this four separate times, independently. jackc625's dynamic-vs-static blend
  comparison came back at margins of 1.3e-4 and 2.8e-5 — "indistinguishable
  rather than harmful."
- **Injury-severity adjustment.** nemethb3: null, after fixing a real
  duplicate-counting bug first. jackc625 built injury features and they arrive
  NULL on every historical row.
- **Rest days.** nemethb3: zero measurable effect on real backtest accuracy.
- **Pace / game-total adjustment.** nfl-props' `--pace-adjust` failed its gate and
  the `rec_yds` coefficient came back the *wrong sign* relative to the design
  hypothesis. Left as tested, working, inert infrastructure.
- **Beating the point spread.** All three game-model repos say they can't.

---

## 4. Free versus paid, drawn sharply

**FREE — no key, no account:**

- **nflverse / nflreadpy / nfl_data_py** — play-by-play, weekly player stats,
  schedules, rosters, snap counts, and **closing** spreads/totals/moneylines plus
  roof/temp/wind. All five repos. This is the foundation.
- **Kalshi market data** — `/markets`, `/events`, `/markets/trades`, and
  `/series/{s}/markets/{t}/candlesticks` all return 200 unauthenticated (verified
  live 2026-09-29).
- open-meteo (forecast + archive), Iowa State mesonet ASOS, ESPN's unofficial site
  API, football-data.co.uk, MLB and NHL official APIs, Wikipedia/Wikidata.

**PAID — and it is one thing, in two forms:**

1. **Historical player-prop lines.** ctrax68, verbatim: *"There is no free source
   of historical player-prop lines."* Their default provider is synthetic and they
   say the resulting ROI is circular by construction.
2. **Intraday / opening line history.** nemethb3: nflverse gives one closing
   snapshot per game, no intraday or opening history. jackc625 had to **buy**
   `odds_timeline` to get a trajectory.

**Proprietary, unavailable to anyone here:** DVOA. ctrax68 substitutes EPA-style
yards-per-play and names the column for what it actually is rather than for what
it stands in for.

**The line, precisely:** every model *input* is free. Every *market price history*
is paid — **except Kalshi's.**

That exception is the whole opening for this project. For Kalshi markets we get
the live price, a public trade tape, and OHLC bid/ask candlesticks, free. **None
of the five repos uses the last two.**

---

## 5. jackc625's market anchors, and Kalshi as a replacement

### What he does with them — which is now almost nothing

He *had* two anchor families:

- Five compressed features: `snapshot_spread`, `snapshot_total`,
  `snapshot_ml_prob_home_fair`, `spread_movement`, `total_movement` — the last two
  being opening→snapshot deltas.
- A richer `line_movement` family: `opening_total`, `total_drift`,
  `total_late_drift` (the "steam" feature, drift over the last 24–48h before the
  freeze), `total_abs_travel`, `total_reversals`, `total_range`,
  `line_movement_coverage`.

**Both families have been removed from every gold matrix.**
`features/market_anchors.py` says so in its own docstring: *"Since Plan 33.2-19
none of them is a model input."* `scripts/build_features.py` defines
`_SEAM_REMOVED_GROUPS = frozenset({line_movement, market})`.

**Why he removed them is the most instructive single finding in all five repos.**
He discovered that his stored 2018–2024 odds rows carried a **manufactured**
`snapshot_ts` — one constant per season, 18:00 ET on September 19, *"after 210
week-1/2 games had been played."* The "snapshot" line he had been feeding the model
as pre-game information was stamped later than the games it predicted. When he
required a genuinely recorded capture instant (`created_at`) at or before each
game's own lock, **no stored 2018–2025 row qualified.** The features became honest
NULLs with `basis="no_information"` — explicitly *"never a 0.0 or 0.5 stand-in"* —
and the columns were pulled from gold.

He found it himself, wrote `CLOSING-LINE-AUDIT.md`, and gave up the backtest
number rather than keep it.

**What survives:** lines as a **blend input only**. `models/market_probability.py`
fits `P_home = sigmoid(β · home_fav_margin)` — a one-parameter logistic with no
intercept — on *only* spreads owned at or before each game's lock, applied
**after** the model has predicted. The file opens with a paragraph addressed to a
future reader who finds a spread in it and concludes the fence was breached. The
previous arrangement used a devigged **closing** moneyline for the blend and
silently returned the unblended model probability when it was absent; both halves
were removed.

### Could we reproduce it with Kalshi price history?

Yes — and we would dodge his bug rather than inherit it. Verified live 2026-09-29:

```
GET /markets?series_ticker=KXNFLPASSYDS&status=open        -> 200
GET /markets/trades?ticker=...                             -> 200  {cursor, trades}
GET /series/{S}/markets/{T}/candlesticks
      ?start_ts=&end_ts=&period_interval=60                -> 200
    per candle: end_period_ts, price, yes_ask, yes_bid,
                volume_fp, open_interest_fp
    yes_ask/yes_bid: {open_dollars, high_dollars,
                      low_dollars, close_dollars}
```

(An earlier call without `start_ts`/`end_ts` returned 400 — a parameter problem,
not an access problem. `period_interval=1440` returned zero candles for a market
listed days earlier; `60` returned data. Worth re-probing before relying on daily
buckets.)

Four reasons it is a better substrate than sportsbook lines:

1. **Kalshi is natively a probability.** No devig, no spread-to-probability
   conversion, no β to fit. His entire `market_probability.py` problem disappears.
2. **Candlesticks carry `end_period_ts`** — a real capture time, which is exactly
   what he lacked. His problem was retrospective: he could not prove *when* a
   stored line had been known.
3. **We record forward.** `closing_snapshots.captured_at` is database-stamped and
   `capture_lag_seconds` is trigger-computed (§10, `db/005`). We cannot
   manufacture a capture time the way his backfill did — the schema forbids it.
4. **`volume_fp` and `open_interest_fp` arrive in the same response**, so a
   liquidity gate costs nothing extra.

Three caveats, the third binding:

- **Thin markets.** Nicowirz flags wide bid/ask on far-out-of-the-money contracts
  and caps model/book disagreement at 30pp — explicitly *"a plausibility
  heuristic, not a validated calibration result."* A candlestick on a market that
  traded twice is not a market opinion. Any movement feature needs a volume floor.
- **Bid/ask convention.** Nicowirz prices at the **ask** (`1 / yes_ask`), correct
  for a position you would pay for. For CLV, entry and close must use the **same**
  side or the bid/ask spread contaminates the measurement. §10's formula is
  indifferent to which you choose and unforgiving if you mix them.
- **We have no history today.** Movement features need a season of collection
  before they can be fitted. Collect first, fit later — which is what `db/004`
  already does. **Do not plan a line-movement feature into the kalshi adapter's
  v1.**

---

## 6. Nicowirz's Kalshi client — what to take from it

`nfl_props/kalshi.py`, **158 lines, stdlib `urllib` only**, pandas at the boundary.

**Mechanism:**

- `BASE_URL = https://external-api.kalshi.com/trade-api/v2`. No auth — the module
  docstring notes `/events` and `/markets` declare `security: []` in Kalshi's own
  API spec.
- Deterministic event tickers built from schedule fields:
  `{SERIES}-{YY}{MON}{DD}{AWAY}{HOME}` → `KXNFLRECYDS-26SEP13GBMIN`. Series:
  `KXNFLPASSYDS`, `KXNFLRSHYDS`, `KXNFLRECYDS`.
- `GET /events/{ticker}?with_nested_markets=true`, unwrapping the `event` key.
- **Skips `status != "active"`.** Settled markets return degenerate
  `yes_ask == no_ask == 1.0` — verified against a real settled market.
- `floor_strike` → line. `yes_ask_dollars` / `no_ask_dollars` → decimal odds via
  `1 / price`. **These arrive as strings** (`"0.3800"`), coerced through `float()`.
- Player name parsed from the title before the colon.
- Disk cache with a 5-minute TTL. A 404 returns `None` — not every game×stat
  combination is listed, and that is normal rather than an error.

**What is worth having** (as knowledge, not code): the ticker format, the
`status == "active"` filter, the string-price gotcha, and 404-is-normal. Each is a
live-verified fact that saves a discovery cycle.

**What we would do differently:**

1. **Access pattern.** He iterates games × 3 stats — roughly 48 requests for a
   slate. `GET /markets?series_ticker=...&status=open` returns tickers directly:
   **3 requests instead of 48.** Better for a 24/7 worker under a rate limit.
2. **Drop the `Leg` / roster-matching layer.** It is shaped for his parlay module
   and drags in pandas. Our `observe()` wants a domain snapshot; roster matching
   is a `form_thesis()` concern.
3. **Decide the price convention explicitly** — ask, mid or last — and use the
   same one at entry and at close.
4. **Add candlesticks and trades.** He has neither.

One design conflict to resolve: his `MAX_AGE_MINUTES = 5` disk cache means a price
can be five minutes stale with no timestamp check. Our crypto adapter treats
staleness as `broken` using the venue's own trade timestamp. The equivalent
freshness signal for a Kalshi market needs identifying before the adapter is
written — a stale probability is the same silent-confident-garbage failure the
crypto adapter was built around.

---

## 7. Fit to our four-hook `BaseAgent`

**Best fit — `nfl-props`.** Nearly one-to-one. `data.py` + `kalshi.py` →
`observe()`; ratings/predict → `form_thesis()`; `parlay.py` leg selection →
`build_commitment()`; actual-yards comparison against nflverse → `resolve()`. Add
`capture_close()` and it is our shape.

**Good fit — `NFL-Prop-Model`.** Its explicit project → price → select → size
staging maps cleanly. Its **void-not-zero** rule is the same insight as our
`outcome='void'` with `pnl=NULL`, reached independently. Kelly sizing has no home
in our model, which is fine: we do not size.

**Worst fit, instructively — `nfl-predict`.** A *batch* system: Friday 18:00 ET,
rebuild every feature matrix, run three models across the whole slate, blend,
write a cache. Its natural output is ~16 commitments at once. Its model *training*
and promotion gate have no hook and should not get one.

**Moderate — `nemethb3`.** Its locked-predictions + SQLite tracking is our ledger,
hand-rolled and self-described as not immutable.

**`EdgeLabs`** does not fit as a system, but `backtest_nfl.py` is the cleanest
example in the set of a walk-forward rating rebuild with a hard cutoff.

### Where we will fight the abstraction

**1. One thesis per tick versus a slate.** *(Addressed 2026-09-29: `build_commitment()`
now returns `Proposal | Sequence[Proposal] | None`.)* Every prop repo here produces
a ranked *board*, not a single pick. Forty independent props on a Sunday slate are
forty independent commitments.

**2. No hook for fitting, and there should not be one — but the gap is real.** All
five repos refit weekly. `observe()` is the wrong place to refit on every tick, and
a fitted artifact needs somewhere to live with a version and a promotion decision.
That is a deployment question, and it is a hole in the step-4 Railway story that no
current work touches.

**3. Cross-commitment correlation.** nfl-props and ctrax68 both admit legs are
priced independently despite game script. Our `legs` table handles a parlay
correctly as one commitment with one pnl. But two *separate* commitments on the
same game are correlated, and per-commitment scoring cannot see it. Adjacent to
§9.2's no-blending rule; the chief of staff will hit it.

---

## 8. Skepticism log

These are hobby repositories with survivorship bias. What follows is what would
not survive a hostile read.

**Real leakage, self-found, honestly fixed.** jackc625's manufactured
`snapshot_ts` (§5). Best-case handling: features removed rather than a backtest
number kept.

**Self-flagged look-ahead.** EdgeLabs' consensus prior uses the *current*
preseason consensus for historical seasons. The headline is model-only, correctly.
But the scoreboard's "at or above market" framing compares model accuracy against
a **closing-line** benchmark on the same games — a less clean comparison than the
presentation implies.

**A mis-aimed leakage guard.** `nfl-predict/models/train_wp.py:469` uses
`StratifiedKFold(n_splits=3, shuffle=True, random_state=...)` — random CV on
time-series data — with `hyperparameter_tuning="grid_search"` as the constructor
default. The test written to forbid exactly this,
`tests/unit/test_wp_trainer.py::test_wp_no_random_cv`, inspects
`models/trainers/wp_trainer.py`, a **different module**, which contains zero
occurrences. The guard passes while the module holding the pattern is unguarded.
`train_wp.py` is not dead code — `models/prediction_pipeline.py` imports
distribution converters from its siblings. Whether the shuffled-CV *path* is
reachable in what currently serves could not be established from static reading;
what can be stated is that the protection exists and does not cover the code
containing the pattern.

**Circular by construction, and said so.** ctrax68's ROI against synthetic lines.
Their calibration figure is the real result. Note the README quotes 3.16pp in one
place and 1.22pp in another — different windows and subsets; do not conflate.

**A weaker claim than it looks.** nfl-props' "15% lower NLL" hands the baseline
*the model's own fitted σ*. They say so. It measures whether knowing the opponent
improves the **mean**, not whether either distribution's width is calibrated. The
PIT table is the actual calibration evidence.

**Correctly hedged, easy to skim past.** nfl-props' `pass_yds` top calibration
decile holds 6.6% against a 10% target; the low-sample-σ fix moved it to 3.61% on
n=693, which they describe as "close to one standard error." "Validated" is doing
less work there than it appears to.

**No reported failures is itself a mild flag.** Four of five repos report at least
one failed validation gate (`--pace-adjust`, the sigma refit, two refused
promotions, injury and rest nulls). EdgeLabs is the one where every number in the
scoreboard is a success.

**Nobody has a live track record.** nemethb3's tracking begins 2026-09-16.
jackc625's profitability run is one unburned 2025 season with p-values that do not
clear alpha. All five are current-season artifacts. Impressive backtests here are
better-disciplined than most, and still backtests.
