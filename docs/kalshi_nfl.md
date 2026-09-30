# Kalshi NFL agents: design

**Status:** design only, 2026-09-30. No code until reviewed.
**Decision this implements:** CLAUDE.md §8 (owner, 2026-09-30). The Kalshi
pillar commits into moneylines, spreads and player props as separate agents
with separate track records. The ledger is the judge: each agent's record,
net of fees, decides what survives. **The prior for `nfl_ml` and `nfl_spread`
is that they lose** (reference-analysis §1). That is written down so a losing
record reads as the expected result, not as a bug.

**Roadmap:** NFL → college football → NHL → tennis → MLB. A new sport is a new
module, not a rebuild (§2).

---

## 0. What the market actually offers (verified 2026-09-30)

Live API, unauthenticated. "History" = candlesticks retrievable after
settlement via `/historical/markets/{ticker}/candlesticks`. Volume is
contracts traded over a market's life; spread is bid/ask in the last
pre-kickoff minute on each game's highest-volume markets (NFL, 2025 sample)
or the lifetime-hourly median (other sports, rougher).

| Sport | Winner | Spread | Total | Player props | History survives |
|---|---|---|---|---|---|
| NFL | `KXNFLGAME` — 4.4M/mkt, 1¢ | `KXNFLSPREAD` — 29k, 1¢ (p90 4¢) | `KXNFLTOTAL` — 26k, 1¢ (p90 8¢) | `KXNFLPASSYDS` `RSHYDS` `RECYDS` `REC` `ANYTD` `PASSTDS` (+ ladders, first TD) — 0.5k–5k/mkt, **5–6¢** (ANYTD 2¢) | yes, all |
| CFB | `KXNCAAFGAME` — 321k, 2¢ | `KXNCAAFSPREAD` — 2.5k, ~8¢ | `KXNCAAFTOTAL` — 2.4k | **none** — team props only (`KXNCAAFTEAM*`, live tier) | yes |
| NHL | `KXNHLGAME` — 294k, 2¢ | `KXNHLSPREAD` — 3k | `KXNHLTOTAL` — 9k | `KXNHLGOAL` `PTS` `AST` — since 2026 playoffs, 67–735/mkt | yes |
| Tennis | `KXATPMATCH` / `KXWTAMATCH` — 290k–486k, 1–2¢ | `KXATPGSPREAD` (games) — 1k | `KXATPGTOTAL` — 1k | `KXATPACES` — negligible | yes |
| MLB | `KXMLBGAME` — 969k, 2¢ | `KXMLBSPREAD` — 27k, 1¢ | `KXMLBTOTAL` — 45k, 1¢ | `HIT` `HR` `KS` `HRR` `RBI` — 18–2.4k/mkt | yes |

**2025 NFL history, by week (complete enumeration):** winner, spread, total and
anytime-TD cover weeks 1–18 plus playoffs (~285–311 events each); passing,
rushing and receiving yards from **week 6** (~200 events); receptions from
week 9; passing TDs from week 12. **Props can be backtested for free** on
roughly 13 weeks of 2025 plus everything 2026 has settled.

Consequences for the roadmap: **CFB has no player-prop market to commit
into**, so it gets `cfb_ml` / `cfb_spread` only. NHL props have one playoff's
history. Tennis is a match-winner sport on Kalshi. MLB props are thin.

Structural facts every agent depends on (detail in `kalshi_benchmark.md` §1):

- **Spreads and totals are ladders.** `KXNFLSPREAD-26SEP28PHICHI-CHI31` is
  "CHI wins by over 30.5?" — one YES/NO contract per half-point threshold per
  team. Totals likewise ("over 69.5?"). Half-points: **no pushes.** Buying NO
  on "CHI by over 3.5" is PHI +3.5.
- **Props are ladders too:** `…-NYGMNABERS1-130` = "Malik Nabers: 130+
  receiving yards" (`floor_strike 129.5`).
- **`close_time` is not kickoff** — game markets trade in-play and close at the
  final whistle; props close two days after. **Kickoff comes from the sport's
  schedule source** (nflverse for NFL). `occurrence_datetime` is not kickoff
  either.
- **Nothing voids.** Postponed > 48 h → "fair price". Tie → $0.50. Prop player
  "active but never takes a snap" → "the fair market price before game start".
  Every one of these is a real settlement value, handled in §6.
- **Fees:** taker 0.07 × P × (1 − P) per contract, pinned by Kalshi's own worked
  example. `KXNFLGAME`, `KXNFLSPREAD`, `KXNFLTOTAL` are
  `quadratic_with_maker_fees`. **Maker coefficient pending from owner.**

---

## 1. Three agents, one data layer, one collector

```
             nflverse (schedule, stats, injuries, rosters)      Kalshi API
                         │                                        │
                 sports/nfl/  ── data layer ──┐     venues/kalshi/ ── client, fees,
                         │                    │           │           settlement, capture
                         ▼                    ▼           ▼
   adapters/nfl_ml.py   adapters/nfl_spread.py   adapters/nfl_props.py
        │                    │                        │
        └─── each a KalshiContractAgent (BaseAgent) ──┘
                         │
                 core/ledger.py  (separate agents rows → separate records)

   collectors/kalshi_candles.py   weekly, writes benchmark_* tables (kalshi_benchmark.md)
   jobs/fit_models.py             weekly, writes model_versions (§5)
```

| Agent | Markets | Commits to | Typical slate |
|---|---|---|---|
| `nfl_ml` | `KXNFLGAME` | YES on one team, or nothing | 0–16 / week |
| `nfl_spread` | `KXNFLSPREAD` (+ `KXNFLTOTAL`, see below) | YES or NO on one ladder rung per game | 0–16 / week |
| `nfl_props` | yards, receptions, TDs | YES or NO on individual ladder rungs | tens / week |

**Totals:** same machinery as spreads (a distribution over a ladder). Proposed
as a fourth agent, `nfl_total`, so its record stays separate — §8 says one
agent per market type, and a spread record padded with totals would blend two
measurements. Cheap to add once `nfl_spread` exists; not in the first cut.

Each agent has its own `agents` row, its own `Registration`, its own margin
(§4), its own `DeferPolicy`, and therefore its own track record. **No blended
number across them, ever** (§9.2) — `nfl_props` winning must not hide
`nfl_ml` losing.

---

## 2. Sport-agnostic core vs sport module

The test: **adding NHL = `sports/nhl/` + one adapter file per market type +
one `Registration` each + one `agents` migration.** Nothing in `core/` or
`venues/kalshi/` changes.

### `venues/kalshi/` — shared by every Kalshi agent, every sport

- **Client:** stdlib HTTP, live/historical tier routing by
  `GET /historical/cutoff`, field-name normalization (`close_dollars` vs
  `close`, `volume_fp` vs `volume`), 5,000-candle chunking, pacing and 429
  backoff, string-price parsing.
- **Fee model:** `taker_fee(p)`, `maker_fee(p)` (coefficient pending),
  `entry_cost(side, quote)` = price + fee, driven by the series' `fee_type` and
  `fee_multiplier` read from the API — never hard-coded per sport.
- **Ladder pricing:** given a model distribution for a quantity (margin, total,
  yards), price every rung: `P(X > floor_strike)`. Shared by spreads, totals and
  props in every sport.
- **`KalshiContractAgent(BaseAgent)`** implements the two hooks that are
  identical everywhere: `resolve()` from Kalshi settlement and `capture_close()`
  from candlesticks (§6). Sport adapters implement only `observe()`,
  `form_thesis()`, `build_commitment()`.
- **Edge gate:** `edge = p_model − entry_cost` and the per-agent margin test
  (§4). One implementation, so no agent can quietly compute edge before fees.

### `sports/nfl/` — everything that is NFL

- **Schedule and kickoff:** nflverse `games.csv` (`gameday` + `gametime`,
  America/New_York → UTC once, with `zoneinfo`), `game_id`, `away_rest` /
  `home_rest`, flex changes.
- **Ticker mapping:** Kalshi ↔ nflverse team codes (`JAC`↔`JAX`, …), date
  tolerance ± 1 day (three 2025 tickers carry a date one day off the game).
- **Player data:** weekly stats, snap counts, target/carry shares, depth, the
  official injury report — for the prop distributions and the `injury` factor.
- **Calendar:** regular season / playoffs, bye weeks, when the final injury
  report and inactives land (the commit-timing anchors, §4).
- **Postponement norms:** sets the NFL `DeferPolicy` (§6).
- **Model definitions:** the ML/spread factor set and the prop distributions.

### What each new sport has to supply

| Supplies | NFL | CFB | NHL | Tennis | MLB |
|---|---|---|---|---|---|
| schedule/kickoff source | nflverse | CFBD API (free key) / cfbfastR | NHL public API | tournament draws; Kalshi event + Sackmann history | MLB Stats API |
| postponement norm | rare; 48 h rule | weather, rare | rare | rain delays, **retirements** (check Kalshi rule) | **rainouts, doubleheaders** — tighter handling |
| props | yes | none on Kalshi | goals/points/assists | none | hits/HR/Ks |

---

## 3. Models

### `nfl_ml` and `nfl_spread` — anchored to the market

**The price is the prior.** Kalshi's implied probability (mid) is the starting
estimate; the model's only job is to adjust it by named, signed factors
(§10.1), each stored as a `commitment_factors` row:

```
p_model = p_market(mid, at commit time) + Σ factor_i
```

That structure is the honest response to reference-analysis §1: a hobby model
cannot out-predict the market from scratch, so it does not try. It asks
whether *specific, named information* moves the probability further than the
market has. Each factor then earns or loses its place on the §10.1
attribution query.

For spreads, the anchor is the ladder itself: fit a margin distribution (normal,
mean and scale solved from the mid prices of the rungs nearest 50%), shift its
mean by the factors (converted from probability to points at that ladder's
slope), and re-price every rung. The commitment is the single rung with the
largest edge net of cost.

| Factor | What it measures | Free source | Reliable enough? |
|---|---|---|---|
| `line_movement` | Drift of the Kalshi mid over fixed windows before commit (anchor at T − 6 d, late drift over the last 24 h), liquidity-gated | Kalshi candlesticks (our collector) | **Yes** — exchange-timestamped, verified retrievable; the one differentiated input. Thin early (median ~480 contracts in the 24 h ending T − 7 d), so it is volume-gated and `NULL` below the floor. |
| `rest` | Rest-day differential; short week / off a bye | nflverse `away_rest`, `home_rest` | **Yes** — schedule-derived, deterministic. But the market prices it well; expect a small or zero coefficient. |
| `injury` | Status changes for high-leverage players (QB above all) since the market's reference point | nflverse injury reports (official NFL report, dated by day) | **Partly.** Content is official; **timing is day-resolution**, not a capture timestamp (jackc625's failure mode). Use only reports dated strictly before the commit day, `NULL` when the week's report is missing, never 0. Late scratches are invisible to it. |

Factor *sizes* are not hand-set: each is a coefficient fitted walk-forward on
the prior seasons' CLV (does this factor, when non-zero, predict the close
moving its way?), shrunk toward zero, refit weekly (§5).

### `nfl_props` — per-player distributions

Unlike game markets, the model here is **not** anchored to the price: it builds
a distribution for the player's stat and prices the whole ladder from it.

- **Counts** (receptions, passing TDs, anytime TD): **negative binomial**, with
  Poisson as the nested special case; walk-forward log-likelihood on
  development data chooses per stat. Anytime TD = `P(TD ≥ 1)`.
- **Yards** (pass/rush/rec): negative binomial on integer yards is the
  requested default; a gamma / log-normal alternative (Nicowirz's
  `log(yards + 10)` shape) is fitted alongside, and the pre-registration picks
  one per stat by out-of-sample log-likelihood and PIT calibration, **before**
  the holdout is scored.
- **Mean:** opportunity × efficiency — expected targets/carries/attempts (from
  recent snap and share, exponentially decayed, shrunk toward position prior)
  × per-opportunity rate × opponent adjustment × game environment (the Kalshi
  total and spread for that game as the pace/script input — market-derived,
  pre-commit, and not a leak because they are observed at commit time).
- **Dispersion:** per stat and position, shrunk; this is where every reference
  repo found calibration problems (reference-analysis §8, nfl-props' σ), so the
  PIT table is a required output of every refit.
- **Walk-forward, weekly refit:** fit on every game with kickoff before the
  refit instant; never on the week being predicted.

Prop legs are **one commitment per rung**, independent. The game-script
correlation between a QB's yards and his receivers' yards is real and is not
modelled in v1; it is a scoring caveat (§8), not a hidden assumption.

---

## 4. When a commitment fires

### The gate

```
entry_cost = ask + taker_fee(ask)            (for the side being bought)
edge       = p_model(side) − entry_cost
commit iff edge ≥ margin(agent)  AND  liquidity gate passes
```

Buying NO uses `no_ask` and `p_model(NO) = 1 − p_model(YES)`. Edge is in
probability points **after** fees and after crossing the spread; disagreeing
with the mid is not enough.

| Agent | Proposed margin | Why |
|---|---|---|
| `nfl_ml` | 2¢ | 1¢ spread, deepest book; the prior says any edge is small and fragile |
| `nfl_spread` | 3¢ | wider tails on the ladder, p90 spread 4¢ |
| `nfl_props` | 4¢ | 5–6¢ spreads, thin books, the least-validated distributions |

These are starting values, **frozen in the pre-registration** before the
backtest is scored, and set in each agent's `Registration`-level config so a
change is a visible, dated commit rather than a quiet edit.

**Liquidity gate:** spread at commit ≤ 3¢ (ml/spread) or ≤ 8¢ (props), and
our size ≤ the displayed ask size (`yes_ask_size_fp`, read live at commit and
stored in the payload). A paper fill larger than the book is a fiction.

### Timing (recommended; frozen in pre-registration)

- **`nfl_ml`, `nfl_spread`: once per game, at T − 24 h.** After the final
  injury report (Friday for Sunday games), so `injury` can fire; late enough
  that the price reflects the week's news; early enough that some movement
  remains (measured 2025: mean |move| to close 1.5¢ from T − 24 h vs 6.0¢ from
  T − 7 d). The trade-off is explicit: earlier has more room and much less
  liquidity. The backtest reports T − 72 h alongside as a **secondary** — one
  primary, chosen now, so the lock cannot be picked after seeing results.
- **`nfl_props`: T − 75 min**, after inactives are announced (~T − 90 min).
  A prop without the inactive list is a bet on the injury report, and a player
  who is active but never snaps settles at the pre-game fair price anyway.

Mechanically this is ordinary scheduling: the agent ticks every 15 minutes;
`observe()` returns the games whose commit instant has passed and that it has
not already committed to (`open_subjects()`); everything else is `idle`.

### What gets committed

| Field | Value |
|---|---|
| `kind` | `event_contract` |
| `legs[0]` | `subject` = market ticker, `market` = series, `direction` = `yes`/`no`, `line` = **entry price of the side held** (NO stored as `1 − yes`, §10), `size` = contracts |
| `payload` | `invalidation: 0` (structural, §9.0), `quote` (bid/ask/sizes at commit), `fee_per_contract`, `entry_cost`, `p_model`, `p_market`, `edge`, `margin`, `model_version`, `kickoff_asof` + `kickoff_source` + `kickoff_fetched_at`, `stop_rule: "structural_zero"` |
| `factors` | every non-zero factor, signed, in probability points |
| `closes_at` | `kickoff_asof` (§6) |
| `resolves_after` | the market's `expected_expiration_time` from the API |

---

## 5. Model fitting: a weekly job, not a hook

Every reference repo refits weekly, and the four hooks have no place for it
(reference-analysis §7, "no hook for fitting, and there should not be one").
So:

- **`jobs/fit_models.py`** runs Tuesday after settlement, fits each agent's
  model walk-forward on data with kickoff before the fit instant, and writes an
  append-only **`model_versions`** row: agent, version, `data_through`,
  `fitted_at` (DB-stamped), parameters (JSON), and the calibration outputs
  (PIT deciles, log-likelihood).
- **`form_thesis()` loads the latest version whose `data_through` precedes the
  commit instant**, and the commitment's payload records `model_version`, so
  every commitment is reproducible from the row that produced it.
- Promotion is not automatic judgement: a refit whose PIT calibration breaks a
  pre-registered tolerance is written but not used, with the reason logged.

---

## 6. Close, resolution, postponement, selections

### `closes_at` is kickoff, as-of commit — **not** Kalshi's `close_time`

CLAUDE.md's Kalshi notes said `closes_at` should come from the market's close
time in the API. **Verification shows that would be wrong:** `close_time` is
the final whistle (game markets trade in-play; props close two days later). A
snapshot there records the settled 0/1 price, turning "CLV" into the outcome.
The market's final *pre-game* opinion is the price at kickoff.

So `closes_at = kickoff_asof`: the scheduled kickoff from the sport's official
schedule source, fetched at commit time, stored with its source and fetch time.
The principle the note protects still holds — **never local `now()` +
offset** — and the as-of value makes the comparison point part of the claim
(§10: a close time that could be revised later would let a thesis shop for a
flattering comparison). `resolves_after` does come from the API
(`expected_expiration_time`). CLAUDE.md's Kalshi note is corrected to match.

### `capture_close()` — from candlesticks, so a late capture loses nothing

At `closes_at`, read the 1-minute candles and take the side-held mid of the
last candle with `end_period_ts ≤ actual kickoff`. Two properties make this
unusually safe:

- **The close is recoverable after the fact** — candlesticks survive
  settlement — so a capture that runs an hour late gets the identical number.
  §10's "a missed close is permanent" is still honoured by the tombstone path,
  but a slow capture is not a loss here.
- **Postponement:** if the schedule source now shows a later kickoff than
  `kickoff_asof`, return `None` (not closed yet) and keep asking. If the game
  never starts within 48 h (Kalshi settles at fair price), raise
  `CloseUnavailable("postponed beyond 48h; settled at fair price")` — a
  `missed` tombstone with an honest reason. There is no pre-game close to beat
  for a game that was never played.

The snapshot `detail` records `kickoff_asof`, actual kickoff, and the candle
used.

### `resolve()` — from Kalshi settlement

Return `None` until the market is `finalized` (defer), then:

```
capital_at_risk = entry_cost × contracts            (price + fee: the fee is lost too)
proceeds        = settlement_value × contracts
pnl             = return_on_risk(capital_at_risk, proceeds)
```

- `settlement_value` 1 → hit, 0 → miss.
- **Tie (0.50), postponed-beyond-48h fair price, prop active-no-snap fair
  price:** a real settlement at a value between 0 and 1. Scored as settled
  (`outcome = partial`, pnl from the formula; the leg is recorded `push` with
  `actual` = the settlement value, see §9 strain 8), with
  `detail.settlement = "tie" | "fair_price"`. **Not void** — Kalshi did not
  void it, and a paper record that voided what the exchange paid out would be
  a lie in the flattering direction or the other one.
- `detail` also carries the domain-native figures §9 requires, and the maker
  model result (§7).

### `DeferPolicy` — sized to Kalshi's 48-hour rule, set deliberately

| Policy | Measured from | `max_overdue` | Reason |
|---|---|---|---|
| resolution | `resolves_after` (≈ kickoff + 6 h) | **10 days** (`nfl_ml`); **17 days** (`nfl_spread`, `nfl_props`) | From the contract terms (read 2026-09-30): FOOTBALLGAMEWIN expires ≤ one week after the game, FOOTBALLSPREAD and FOOTBALLENTITYSTAT ≤ the 15th day; settlement the next day; plus possible outcome review. Worst legitimate delay, not convenience (§9.3). Matches `preregistration_nfl.md` §2.7. |
| capture | `closes_at` (scheduled kickoff) | **72 hours** | a postponed game that starts within Kalshi's 48 h, plus margin. Beyond that the game is fair-priced and capture raises `CloseUnavailable`. |

`max_attempts` is sized to the sweep cadence (hourly sweep → 24 per day: 264 for 10 days, ~430 for 17).
Both set in each `Registration`, never inherited (§9.3).

### Selections — your picks

Every qualifying edge is committed by the agent; your pick is a separate,
earlier-than-close decision on top (§10.2):

- The day's open commitments are listed (a script now, the dashboard at step
  10); you record `selected = true` or `false` for each via
  `ledger.record_selection`, before kickoff. The `selection_before_close`
  trigger refuses anything after `closes_at`, which is kickoff — **your pick
  must be in before the game starts**.
- Declines are recorded, not left absent ("looked and passed").
- That yields two comparable records per agent: the agent's full slate, and
  your selected subset. Whether you beat the model is then a query, not an
  impression.

---

## 7. P&L: taker of record, maker as a stated assumption

**The `pnl` of record is the taker model:** filled at the displayed ask at
commit, up to displayed size, taker fee included. Conservative and
unambiguous — the quote and size are recorded in the payload at commit.

**The maker model is computed alongside, in `resolutions.detail`, never as the
headline** until it has its own evidence:

- **Order:** a resting bid at `bid` (or `bid + 0.01` if that improves the
  book), placed at commit, cancelled at kickoff.
- **Fill rule (the §9-style modelling assumption, stated as such):** filled
  only if trades printed **strictly through** the limit (`price.low <
  limit`) within the window, and cumulative volume at-or-through ≥ 2 × our
  size (we do not know our queue position). A touch is not a fill.
- **Adverse selection is the point, not a footnote.** A resting bid fills
  preferentially when the market is moving *against* it — the fill *is* the
  bad news. So the maker result is reported with: fill rate; CLV of filled vs
  unfilled candidates (if filled orders show systematically worse CLV, that
  gap is the adverse-selection cost, measured); and the maker fee
  (coefficient pending from you).
- `detail` carries `{"maker": {"filled": bool, "fill_price", "maker_fee",
  "pnl", "fill": "assumed_maker_trade_through"}}`, so a maker figure can never
  be mistaken for an observed fill.

Why not make maker the record now: an unfilled maker order is a legitimate
non-position, and the only way to record it in the current ledger is `void` —
which would pollute void rate, the health metric that is supposed to mean
"a data source broke" (§8). See §9, strain 3.

---

## 8. Backtests first, pre-registered, where history exists

**Gate before any live paper commit:** a pre-registration file per agent is
committed to git (its commit timestamp is the evidence), then the backtest is
run once against it.

- **Pre-registration fixes:** market scope, commit timing, factor definitions,
  model family per stat, margin, liquidity gate, fee assumptions (maker
  coefficient once you supply it), the fill rule, and the evaluation metrics
  and thresholds below.
- **Data:** 2025 is **development** (fit, explore, choose). **2026 weeks
  already settled are the holdout** — untouched so far except one game's
  candles pulled during API verification (disclosed in
  `kalshi_benchmark.md`). Props: 2025 weeks 6–18 dev (yards), 2026 holdout.
- **Replay, not a second implementation:** the backtest drives the *same*
  `observe()` / `form_thesis()` / `build_commitment()` code over an as-of view:
  candles with `end_period_ts ≤ t`, nflverse stats for games with kickoff < t,
  injury reports dated before t's day, the model version fitted before t. The
  entry price is the historical ask at t from the candle and the fee comes
  from the schedule. **The one thing replay cannot see is historical book
  depth** (candles carry no sizes), so backtest fills assume the displayed
  size was sufficient. Stated, not hidden; live commits do record depth.
- **Results do not go in `commitments`.** A backtest row committed after the
  outcome is known is exactly what §2 forbids. They go to
  `docs/backtests/<agent>-<date>.md` plus a CSV, referenced from the
  pre-registration.
- **Metrics:** per agent, net-of-fee R-multiple mean with a week-clustered CI;
  CLV mean; calibration (reliability / PIT); fill-rule sensitivity; and the
  baselines (always-favourite, random side, market-only i.e. factors zeroed).
  **Win rate reported, never headlined.**

**What the backtest licenses, honestly:** with ~4 settled 2026 weeks (~60
games per game-market agent), it cannot establish an edge — it is a **bug and
calibration filter**. It must show: no leakage (the perturbation test from
`kalshi_benchmark.md` §6 applies to every agent), calibration inside tolerance,
and no absurd result (a +40% ROI is a bug until proven otherwise). Passing
means "safe to run forward on paper", not "profitable". **Significance comes
from the forward record, which is the judge** — and it needs a season or more
(one season detects only r ≳ 0.17 on game markets).

---

## 9. Where the four-hook contract strains

**The contract fits all three agents.** `observe` / `form_thesis` /
`build_commitment` / `resolve`, plus the opt-in `capture_close`, map directly;
slates handle props; a shared `KalshiContractAgent` base class keeps
`resolve` and `capture_close` written once. The strains are real but
peripheral, and none requires a fifth hook:

1. **Model fitting has no hook, correctly** — handled by a weekly job and
   versioned `model_versions` rows (§5). It is a new table and a new job, not a
   change to the loop.
2. **Fees inside `capital_at_risk`.** §9's table lists a YES contract's risk as
   `price × contracts`. Net-of-fee scoring (§8, owner decision) means
   `(price + fee) × contracts`. CLAUDE.md §9 gets a one-line note; core's
   `return_on_risk` is unchanged.
3. **Maker orders that never fill.** No ledger outcome means "order placed, no
   position". Recording it as `void` would corrupt void rate. v1 avoids it with
   taker-of-record and maker-in-detail (§7). If maker ever becomes the record,
   the ledger needs an explicit `unfilled` outcome excluded from void rate —
   a schema change to decide then, not a hack now.
4. **`closes_at` ≠ the API's close time.** Resolved in §6 by storing kickoff
   as-of; CLAUDE.md's Kalshi note corrected.
5. **Slate size for props.** A Sunday of prop edges can exceed the default
   `max_slate_size = 25`; `nfl_props` raises it deliberately in its
   `Registration`, as §3 requires. The duplicate-leg refusal (same subject,
   market, direction, line) is exactly right for ladders.
6. **Cross-agent correlation.** `nfl_ml` on CHI and `nfl_props` on CHI's QB
   overs are correlated positions in separate records. Per-commitment scoring
   cannot see it, and §9.2 forbids blending anyway. The chief of staff must
   report them per agent and name the overlap, not net it.
7. **Structure (CLAUDE.md §3).** "One file in `adapters/`" still holds for a
   new market type within a sport. A new *sport* adds `sports/<sport>/`, and
   the venue layer lives in `venues/kalshi/`. §3 needs those two directories
   (and `collectors/`, `jobs/`) added when this is built.
8. **Leg vocabulary has no "settled at a fair value".** `LegResult` is
   `hit | miss | push | void`. A tie or fair-price settlement is recorded as
   `push` with `actual` = the settlement value and `pnl` computed normally.
   `push` there does not mean "stake returned". Acceptable while rare (4 of
   333 2025 game events were ties); if fair-price settlements turn out common
   on props (active-no-snap), add a `settled` leg result by migration rather
   than overloading `push`.

---

## 10. Open items

1. ~~Maker fee coefficient~~ **Confirmed (owner, 2026-09-30):** maker
   0.0175 = 0.25 × taker on `quadratic_with_maker_fees` series, 0 on
   `quadratic`. The per-series `fee_type` fetched from the API is
   authoritative (`venues/kalshi/fees.py`).
2. Paste `db/007`; then `db/008` (collector tables, `kalshi_benchmark.md` §4),
   `model_versions`, and `agents` rows for `nfl_ml`, `nfl_spread`, `nfl_props`
   (and `kalshi_benchmark` for the collector) with `enabled = false` until each
   is meant to run.
3. Confirm Kalshi's rule for a prop player ruled **inactive** (the observed
   rule text covers "active but never takes a snap"; the inactive case was not
   seen on a market checked today).
4. Pre-registration files, one per agent, before any 2026 holdout is scored.
5. **Prop candle storage does not fit the free tier as designed.** 2025 alone
   had ~40k NFL prop markets across the six series (receiving yards: 10,254).
   At the collector's game-market cadence that is ~10M rows, several GB. The
   options, to decide before `db/008`: (a) archive props only for the rungs an
   agent actually evaluated, plus the commit and close instants (both already
   stored on the commitment and snapshot), and fetch the rest from Kalshi on
   demand for backtests, accepting the undocumented-retention risk for
   unevaluated rungs; (b) archive every rung at a coarse cadence (the close
   only); (c) a paid tier. Recommendation: (a).
6. Build order inside step 8: collector + backfill → `nfl_ml` (simplest
   ladder-free market, proves `KalshiContractAgent`) → `nfl_spread` →
   `nfl_props`.

---

## Sources

Kalshi docs (verified 2026-09-30):
[API environments](https://docs.kalshi.com/getting_started/api_environments.md) ·
[Get Market](https://docs.kalshi.com/api-reference/market/get-market.md) ·
[Candlesticks](https://docs.kalshi.com/api-reference/market/get-market-candlesticks.md) ·
[Historical candlesticks](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks.md) ·
[Historical data](https://docs.kalshi.com/getting_started/historical_data.md) ·
[Market lifecycle](https://docs.kalshi.com/getting_started/market_lifecycle.md) ·
[Settlement](https://docs.kalshi.com/getting_started/market_settlement.md) ·
[Fee rounding](https://docs.kalshi.com/getting_started/fee_rounding.md) ·
[Series (fee_type)](https://docs.kalshi.com/api-reference/market/get-series.md) ·
[Rate limits](https://docs.kalshi.com/getting_started/rate_limits.md).
Series, market, rules-text, volume and spread figures: live unauthenticated
API probes (`/series?category=Sports`, `/markets`, `/historical/markets`, both
candlestick endpoints), 2026-09-30. nflverse schedule:
[games.csv](https://github.com/nflverse/nfldata/blob/master/data/games.csv).
Reference repos: `docs/reference-analysis.md`.
