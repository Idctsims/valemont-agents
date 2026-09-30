# Kalshi NFL game-winner benchmark: design

**Status:** design only, 2026-09-30. No code until reviewed.
**Decision this implements:** CLAUDE.md §8. NFL game-winner markets are a
**benchmark, not a commit target.** We record their price paths, closes and
settlements so `line_movement` can be tested walk-forward. Committing into game
markets requires recorded evidence of signal from this data first.

---

## TL;DR

- **All the data we need is public and survives settlement.** Kalshi serves
  1-minute bid/ask/trade candlesticks with no auth, including for settled
  markets via a separate historical endpoint. The full 2025 season (333
  events, preseason through the January 2026 playoffs) is still retrievable at
  1-minute resolution today.
- **So: a weekly post-game backfill, not a 24/7 collector.** What a live poller
  would add (order-book depth, our own capture timestamps) is not needed to
  test `line_movement`.
- **Not a `BaseAgent`.** It never commits, and the whole agent loop exists to
  commit, resolve and capture against commitments. It is a collector job that
  writes through `core/ledger.py` into three new append-only tables.
- **Leakage is blocked by the schema, not by intent.** A trigger refuses any
  candle ending after kickoff, so in-play prices cannot exist in the database.
  Features read only candles at or before a per-game lock. A perturbation test
  proves that changing anything after the lock leaves every feature unchanged.
- **The measured numbers are sobering** (random 60-game sample, 2025 regular
  season, home-team side): the price moves a mean absolute **6.0¢** between
  7 days out and kickoff, **2.3¢** from 3 days out, and **1.5¢** in the last
  24 hours. Entering at the ask and paying the taker fee costs about **2.25¢**
  near 50¢. Movement is only big enough to matter early, and early is exactly
  when these markets are thin (median ~480 contracts traded in the 24 hours
  ending 7 days out, minimum 0).
- **One season detects only a moderate effect.** Unit = game, ~270 regular
  season games, so the smallest detectable correlation is r ≈ 0.17–0.20. 2025
  is the development set; 2026 is the pre-registered holdout.

---

## 1. Kalshi API facts (verified 2026-09-30)

Every claim here was checked against the docs **and** against the live API
unless marked "docs only".

### Base URL and auth

| | |
|---|---|
| REST, production | `https://external-api.kalshi.com/trade-api/v2` [(docs)][env] |
| REST, demo | `https://external-api.demo.kalshi.co/trade-api/v2` [(docs)][env] |
| Auth for market data | **None.** `GET /markets/{ticker}`, `GET /series/{s}/markets/{t}/candlesticks` and `GET /historical/markets/{t}/candlesticks` all declare `security: []` in the OpenAPI spec [(market)][mkt] [(candles)][cndl] [(hist candles)][hcndl], and all returned 200 unauthenticated from this machine. |
| Rate limits | Documented only for authenticated tiers (Basic: 200 read tokens/s, most requests cost 10) [(docs)][rl]. **The unauthenticated limit is not documented.** kalshi.com returned 429 to a scripted PDF download during this research, so the collector paces itself (≤ 2 req/s) and backs off on 429. |

### How NFL game-winner markets are identified

- **Series:** `KXNFLGAME` ("Professional Football Game"), `fee_type =
  quadratic_with_maker_fees`, `fee_multiplier = 1` (from `GET /series`, live).
- **Event ticker:** `KXNFLGAME-{YY}{MON}{DD}{AWAY}{HOME}`, e.g.
  `KXNFLGAME-26SEP27BALDAL`. `mutually_exclusive = true`.
- **Markets:** two per event, one YES contract per team, with the team code as
  suffix: `KXNFLGAME-26SEP27BALDAL-BAL` and `…-DAL`. They are mirror images
  (two order books, prices ≈ complementary). **The unit of analysis is the
  game, not the market.**
- **Matching to nflverse needs a code map and date tolerance.** Kalshi uses
  `JAC` where nflverse uses `JAX` (all 18 Jacksonville games, 16 regular
  season and 2 playoff, failed to match without it). Some ticker dates are a
  day off the game day: two week-18 games (`26JAN04CARTB`, `26JAN04SEASF`) and
  the `26JAN18SFSEA` divisional game. Match on (team pair, date ± 1 day), and
  log anything unmatched as a health metric, never drop it silently. With the
  `JAC→JAX` fix alone, 270 of 272 2025 regular-season games matched on exact
  date.

### Timing fields and what they actually mean

| Field | What it is | Trap |
|---|---|---|
| `open_time` | Listing. Batch-listed; 2025 regular season lead before kickoff: min 5.9 d, p5 7.0 d, median 11.9 d, p95 110 d (week-1 games list in spring). | "Opening price" has **no consistent horizon**. See §5, jackc625's WR-09. |
| `close_time` | When trading stops. `can_close_early = true`, `early_close_condition = "This market will close and expire after a winner is declared."` | **Not kickoff.** These markets trade in-play and close at the final whistle (BAL@DAL: kickoff 20:25Z, `close_time` 23:46:53Z). |
| `occurrence_datetime` | Present on live-tier markets, `null` on historical ones. | **Not kickoff.** BAL@DAL shows 23:25Z, three hours after kickoff. Do not use it. |
| `expected_expiration_time` | "Time the event is likely to resolve (for a sports game, typically a few hours after the scheduled start)" [(lifecycle)][life]. | Observed as kickoff + 6h on three games. Undocumented; don't derive kickoff from it. |
| `latest_expiration_time` | Latest possible expiration. | Observed as kickoff + 48h, matching the postponement rule below. |
| `settlement_ts`, `settlement_timer_seconds` | Settlement instant; timer = 60 s after determination. | Settled 3–6 minutes after `close_time` on the games checked. |

**Kickoff therefore comes from nflverse** (`games.csv`: `gameday` + `gametime`,
an **America/New_York wall clock**, converted to UTC exactly once at ingest
with `zoneinfo`). Verified: 2026-09-27 16:25 ET = 20:25Z, which is when the
BAL@DAL price leaves its flat 0.62/0.63 quote and starts swinging.

### Settlement fields and rules

- `status` enum: initialized, inactive, active, closed, determined, disputed,
  amended, finalized [(market)][mkt]. `result`: `yes`, `no`, `scalar`.
- `settlement_value_dollars`: `1.0000` / `0.0000`, or **`0.5000` on a tie**:
  8 of 666 historical markets (4 events).
- From `rules_secondary` (live, verbatim): *"If the game is postponed but
  begins within 48 hours from its originally scheduled start time, the market
  will remain open and resolve based on the official final result. If the game
  is not started within 48 hours, the market will resolve to a fair price."*
  So **there is no void**; a long postponement settles at a price Kalshi
  chooses. The benchmark excludes postponed games from the test (§6), but
  still stores them.

### Price history

| | |
|---|---|
| Live tier | `GET /series/{series}/markets/{ticker}/candlesticks?start_ts&end_ts&period_interval` [(docs)][cndl] |
| Historical tier | `GET /historical/markets/{ticker}/candlesticks` (same params) [(docs)][hcndl] |
| Which to use | *"Candlesticks for markets that settled before the historical cutoff are only available via `GET /historical/markets/{ticker}/candlesticks`"* [(docs)][cndl]. The cutoff is `GET /historical/cutoff` → `market_settled_ts`, currently **2026-08-01T00:00Z** (live), *"regularly updated, advancing forward over time"* [(docs)][hist]. |
| Granularity | `period_interval` ∈ {1, 60, 1440} minutes [(docs)][cndl]. |
| Per-request cap | **5,000 candles** (docs say nothing; the live API returned 400 `"max candlesticks: 5000"`). 1-minute history therefore needs chunking beyond ~3.5 days. |
| Depth after settlement | 2025 season, 1-minute: present (4,730 candles in a 5,000-minute window for `KXNFLGAME-25NOV30LACAR-LA`; minutes with no activity have no candle). Earliest `KXNFLGAME` market: 2025-07-31 preseason. **Retention is not documented** [(docs)][hist]. |
| Candle fields | `end_period_ts` (epoch s); `yes_bid`, `yes_ask` OHLC; `price` (trade) OHLC + mean + previous; volume; open interest. **The two tiers use different names:** live `close_dollars` / `volume_fp` / `open_interest_fp`, historical `close` / `volume` / `open_interest`. Prices arrive as strings (`"0.6300"`). |
| Candle semantics | A candle with `end_period_ts = t` covers the period ending at `t`. Fencing on `end_period_ts <= L` therefore never admits a period that straddles `L`. |

Observed market quality near kickoff: bid/ask spread 1¢ (median and p90, max
2¢ across 60 games); tens of millions of contracts of lifetime volume per
market. This is a liquid market, which is why §8 makes it a benchmark.

### Fees

- **Taker fee per contract = 0.07 × P × (1 − P)**, times the series
  `fee_multiplier` (1 for `KXNFLGAME`). The docs' own worked example pins the
  coefficient: model fee `$0.00363825` on a one-contract trade at `$0.055` is
  exactly 0.07 × 0.055 × 0.945 [(fee rounding)][fee].
- **Rounding:** per fill, up to $0.000001, with a per-order accumulator. Retail
  balances align to $0.01 and the excess comes back as a rebate
  [(fee rounding)][fee]. In practice it is the formula, not "round up to a cent
  per contract".
- **Maker fee:** `KXNFLGAME` is `quadratic_with_maker_fees`, so resting orders
  also pay. Secondary sources give 0.0175 × P × (1 − P) [(1)][s1] [(2)][s2].
  **Not confirmed from the primary PDF**, which returned 429; check it by hand.
- **Settlement fees:** waived for plain yes/no settlement [(settlement)][sett].

| Price | Taker fee / contract | + half-spread (0.5¢) = entry cost |
|---|---|---|
| 0.50 | 1.75¢ | 2.25¢ |
| 0.40 / 0.60 | 1.68¢ | 2.18¢ |
| 0.30 / 0.70 | 1.47¢ | 1.97¢ |
| 0.20 / 0.80 | 1.12¢ | 1.62¢ |
| 0.10 / 0.90 | 0.63¢ | 1.13¢ |

**How fees change results.** The benchmark commits nothing, so it has no P&L.
But its signal criterion (§6) is net of costs, because a paper result that
ignores them would be a lie:

- **Hold to settlement:** cost = entry fee + half-spread (table above).
  Settlement is free.
- **Exit at the close:** cost doubles (fee and half-spread again on the way
  out), ~4.5¢ round trip near 50¢. That exceeds the average move from 3 days
  out (2.3¢) and from 24 hours out (1.5¢). Only the 7-day window (6.0¢, 57% of
  games moving more than 4.5¢) clears it.
- **For the future commit adapter (§9):** a YES contract's
  `capital_at_risk` should be `(price + fee) × contracts`. The fee is lost
  along with the stake. `proceeds = settlement × contracts`. Recording the
  stake without the fee would overstate every R-multiple.

---

## 2. Backfill or live collector?

**Recommendation: weekly backfill.** Run Tuesday 15:00 UTC, after Monday Night
Football has settled. It is idempotent: each run fetches every settled
`KXNFLGAME` market not yet stored, so a missed week self-heals.

What a live collector would add, and why each item doesn't matter here:

| Only a live poller gets | Matters for testing `line_movement`? |
|---|---|
| Order-book depth (`yes_bid_size_fp`, `yes_ask_size_fp`) | No. Candles carry bid/ask OHLC, and the feature is a price path. |
| Our own DB-stamped capture time for each price | Weakly. Kalshi's `end_period_ts` is the exchange's own timestamp, not a backfill guess. jackc625's failure was a *manufactured* capture time (§5), not a vendor one. |
| Metadata as it looked at time t (`close_time` edits, postponement notices) | Only for postponed games, which the test excludes. |
| A quote in minutes with no activity | No. Carry the last candle forward. A live poll would read the same resting quote. |
| **Insurance against Kalshi purging history** | **Yes, the one real risk.** Retention is undocumented. A **weekly** cadence (not end of season) keeps exposure to about a week. Our tables become the archive. |

Cost: a backfill needs no always-on worker. At the recommended cadence it makes
~64 candle requests per NFL week, plus ~1,150 once for the 2025 season.

---

## 3. Collector job, not an adapter

**It is a separate collector job, not a `BaseAgent`.** Forcing it into the
four-hook contract means fighting it at every hook:

- `build_commitment()` would return `None` forever, so the run stream would be
  an endless `idle`, which §6 treats as a health signal and would drown.
- `resolve()` and `capture_close()` operate on `commitments` rows. There are
  none. `closing_snapshots.commitment_id` is a foreign key to a commitment, so
  the §10 capture machinery cannot hold a benchmark close at all.
- `DeferPolicy`, void rate and the due-set sweeps all measure commitments.

What it does reuse from `core/`:

- **All SQL goes through `core/ledger.py`** (§6): new
  `record_benchmark_market`, `record_benchmark_candles`,
  `record_benchmark_close`, and read helpers for the analysis.
- **`runs` and `events`** for health. It gets its own `agents` row,
  `kalshi_benchmark`, with `is_test = false` (it writes no track record to
  pollute) and **`enabled = false`**, so the orchestrator's new two-gate rule
  can never schedule it as an agent.
- **Health metric:** per run, count unmatched markets, postponed games and
  fetch failures. It plays the role void rate plays for agents (§8): a rising
  unmatched count means the ticker format or team-code map broke.

Where it lives: `collectors/kalshi_nfl.py`, run by a Railway **cron** service
(or by hand) as `python -m collectors.kalshi_nfl`. The orchestrator only
schedules `BaseAgent`s. Adding a generic job type to `core/` for one weekly
script is premature; revisit if a second collector appears. **CLAUDE.md §3
needs one line adding `collectors/`** when this is built.

**The later commit adapter fits cleanly:** if `line_movement` earns commit
rights, a `kalshi` `BaseAgent` reads these tables in `observe()` /
`form_thesis()` and commits through the normal four hooks. The collector
becomes its data source; nothing about it strains the contract.

---

## 4. Storage

Three new tables in one numbered migration (`db/008`, since `db/007` is
already written). **All three are append-only** with `reject_mutation`, RLS
enabled and no policies (§5). They are written only **after settlement**, so
every row is complete when written and nothing ever needs updating.

```
benchmark_markets      one row per Kalshi market (ticker UNIQUE)
  ticker, event_ticker, series_ticker, team, opponent, is_home,
  nflverse_game_id, game_type, kickoff timestamptz NOT NULL,
  kickoff_source ('nflverse'), postponed boolean,
  open_time, close_time, settlement_ts, result, settlement_value,
  captured_at timestamptz DEFAULT now()

benchmark_candles      UNIQUE (market_id, period_minutes, end_period_ts)
  market_id FK, period_minutes CHECK IN (1, 60), end_period_ts timestamptz,
  yes_bid_{open,high,low,close}, yes_ask_{open,high,low,close},
  trade_close, trade_mean (nullable), volume, open_interest,
  source CHECK IN ('live', 'historical'), captured_at DEFAULT now()

benchmark_closes       one row per market (market_id UNIQUE)
  market_id FK, rule ('last_1m_mid_at_or_before_kickoff'),
  close_bid, close_ask, close_mid, candle_end_ts, captured_at DEFAULT now()
```

The load-bearing trigger:

```
benchmark_candles_pregame  BEFORE INSERT
  refuse unless NEW.end_period_ts <= (kickoff of NEW.market_id)
```

**In-play prices cannot enter the database at all.** That is the first leakage
guarantee (§6), and it costs one lookup per insert. `benchmark_closes` exists
because §10 says a measurement is **stored, never derived on read**: the close
is frozen once, under a named rule, the same way a CLV figure is.

Re-runs use `INSERT … ON CONFLICT DO NOTHING`, which never touches an existing
row and is compatible with `reject_mutation`. One transaction per market
(market + candles + close), so a half-backfilled market cannot exist (the same
atomicity `tests_live` proves for commitments).

**Cadence stored:**
- **Hourly** from `max(open_time, kickoff − 14 d)` to kickoff (≤ 336 rows).
  Nothing in the test reaches further back than 6 days. The cap bounds storage
  for the week-1 markets that list 110 days early.
- **1-minute** for the final 3 hours before kickoff (≤ 180 rows), to pin the
  close precisely.
- **Nothing after kickoff.** It isn't fetched (`end_ts = kickoff`) and the
  trigger refuses it anyway.

**Row volume**, both sides stored (the mirror is a free data-quality check:
`bid_A ≈ 1 − ask_B`):

| | markets | rows |
|---|---|---|
| per NFL week (~16 games) | 32 | ~15,000 |
| per season (~285 games incl. playoffs) | ~570 | ~270,000 |
| 2025 one-time backfill | ~570 (+ preseason, stored but excluded) | ~270,000 |

At ~250 bytes per row including the unique index, that is **~65 MB per
season**, against a ledger that is 1.3 MB today and Supabase's 500 MB free
tier. Storing only the home side halves it if space ever matters.

---

## 5. What the reference repos teach (analysis already on file)

`docs/reference-analysis.md` §5–§6 already ran this analysis on 2026-09-29.
Re-checked against the source today.

### jackc625 (`nfl-predict`): opening and Friday-snapshot anchors

From `features/line_movement.py` and `features/market_anchors.py`:

- He anchored movement between an **opening** line and a **freeze** snapshot:
  originally the preceding Friday 18:00 ET, later replaced by a **per-game
  lock at 18:00 ET the day before each game's own kickoff**. Features:
  `opening_total`, `total_drift`, `total_late_drift` (the last 24–48 h before
  the freeze), `total_abs_travel`, `total_reversals`, `total_range`, plus a
  coverage flag.
- **The Friday freeze leaked:** a Friday-afternoon kickoff froze *after* it
  started, carrying post-kickoff values into three games. Hence the per-game
  lock and a `min(as_of, lock, kickoff − 1 s)` fence.
- **A naive `datetime.now()` relabelled as UTC** moved the fence by four hours:
  it dropped the freeze snapshot on an ET machine and would have admitted
  post-cutoff rows east of UTC.
- **"Opening" had no horizon control** (his WR-09): first capture ranged from
  8.8 days (median) to 68.6 days (p90) before kickoff, so "drift" meant a week
  for one game and four months for another.
- **The stored snapshot times were manufactured** (one constant per season,
  after week 1–2 games had been played). When he required a genuinely recorded
  capture instant, no row qualified, and **both anchor families were removed
  from every model**. The true closing line was always reserved for CLV
  grading, never a feature.

**Adopted here:** a per-game lock relative to that game's own kickoff;
**fixed-horizon anchors (kickoff − 6 d), never "opening"**; epoch timestamps
from the exchange (Kalshi's `end_period_ts` cannot be tz-mislabelled), with the
nflverse ET wall clock converted exactly once; missing coverage as `NULL` plus
a flag, never `0`; the close never a feature.

### Nicowirz (`nfl-props`): Kalshi fetch

`nfl_props/kalshi.py`, 158 lines, stdlib `urllib`: builds
`{SERIES}-{YY}{MON}{DD}{AWAY}{HOME}` tickers, calls
`GET /events/{t}?with_nested_markets=true`, **skips any market whose status is
not `active`** (settled markets show degenerate `yes_ask == no_ask == 1.0`),
parses string prices through `float()`, caches for 5 minutes, treats 404 as
"not listed".

**Not reusable as code, three ways over:**
1. **No license**: default copyright, readable but not copyable (reference
   analysis, "Licensing").
2. **Wrong shape**: it reads the *current* price of *active* markets only. The
   benchmark reads the *history* of *settled* markets. It has no candlestick or
   historical-tier call.
3. **Wrong granularity**: one request per game × stat, pandas at the
   boundary, prop-specific `Leg` objects.

**Reusable as knowledge:** the ticker format (confirmed for `KXNFLGAME`),
string prices, 404-is-normal, and settled-market prices being degenerate
(which is why the benchmark uses candles, never the market snapshot, for
settled markets).

---

## 6. Testing `line_movement`

### The question, stated so it can fail

> Does the movement of a game's Kalshi price between a fixed early anchor and
> a per-game lock predict its movement between the lock and the close?

The target is CLV for a position opened at the lock, so this measures exactly
what a commit adapter would later be scored on. **It is a claim about
movement, not about profit** (§8).

### Definitions (to be frozen in a pre-registration file before 2026 data is examined)

- **Unit:** one game, measured on the home-team market. The away market is its
  mirror and is not an independent observation.
- **Price convention:** mid = (yes_bid + yes_ask) / 2 of the latest candle at
  or before t, carried forward. The same convention everywhere, so the spread
  cannot contaminate the target (reference analysis §5).
- **Kickoff K:** nflverse scheduled kickoff, UTC.
- **Anchor A = K − 6 d.** It exists for ≥ 95% of games (p5 listing lead is
  7.0 d); games listed later get `NULL` features plus a coverage flag, and are
  excluded, never zero-filled.
- **Primary lock L = K − 72 h.** One primary lock, fixed in advance.
  Reporting T − 24 h or T − 7 d alongside it is fine, but choosing the lock
  after seeing results is the forking-paths error. Why 72 h: moves after it
  average 2.3¢, around the entry cost; at 24 h (1.5¢) nothing is tradeable,
  and at 7 d the anchor window shrinks to nothing for late-listed games.
- **Features (at L, from candles with `end_period_ts <= L` only):**
  `drift = mid(L) − mid(A)`; `late_drift = mid(L) − mid(L − 24h)`;
  `abs_travel` (sum of absolute hourly changes A→L); `reversals` (hourly sign
  flips); `window_volume` (contracts traded A→L).
- **Target:** `clv_L = close_mid − mid(L)`, from `benchmark_closes`.
- **Liquidity gate** (pre-registered): exclude a game if `window_volume` is
  below a floor, or if the spread at A or L exceeds 3¢. A stale quote on a
  market that traded twice is not a market opinion.
- **Exclusions:** postponed games (kickoff moved > 1 h from original schedule,
  or Kalshi's 48 h rule invoked), preseason, ties (the target doesn't depend
  on settlement, but ties are flagged).

### Walk-forward

- **2025 regular season + playoffs (~280 games): development.** Explore
  features, fix the gate thresholds, choose the model. Everything learned here
  goes into the pre-registration.
- **2026 regular season (~270 games): holdout.** The pre-registration file is
  committed to git **before any 2026 feature is computed**; the commit
  timestamp is the evidence. *Disclosure:* verifying the API today pulled
  1-minute candles for one 2026 game (BAL@DAL, week 3), around kickoff only.
  No features were computed.
- **Procedure:** for each 2026 week w, fit on 2025 plus 2026 weeks < w, predict
  week w, record the out-of-sample prediction. The model is deliberately tiny:
  `clv_L ~ α + β·drift` (optionally plus `late_drift`), fixed in advance. No
  tuning on the holdout.

### What counts as signal

All of the following, on 2026 out-of-sample predictions:

1. **Correlation between predicted and realized `clv_L` > 0**, one-sided
   p < 0.05 with **week-clustered** standard errors (games in the same week
   share news shocks, so they are not independent).
2. **It beats the baselines:** zero prediction; the constant development-set
   mean (the 2025 sample shows a small positive home-side drift, +1.4¢ from
   T − 7 d, t ≈ 1.5, which a model must not get credit for rediscovering); and
   a naive momentum sign rule.
3. **It survives costs:** among games where |predicted `clv_L`| exceeds that
   game's entry cost (fee + half-spread at mid(L), §1), the mean realized
   `clv_L` in the predicted direction exceeds that cost, with a CI excluding
   zero.

Pass all three and the recorded evidence exists that §8 requires, making a
commit adapter a legitimate proposal. Fail and `line_movement` is cut on
evidence (§10.1). Either result gets written into CLAUDE.md.

### Sample size

Target SD at L = K − 72 h is ~3.5¢ (sample above). Detecting a correlation
r with 80% power at one-sided α = 0.05 needs roughly n ≈ 6.2 / r² games:

| true r | games needed | seasons (~270 games) |
|---|---|---|
| 0.30 | ~70 | a quarter season |
| 0.20 | ~155 | ~0.6 |
| 0.15 | ~275 | ~1 |
| 0.10 | ~620 | ~2.3 |

Week clustering inflates these; count on ~1.3×. **One holdout season can
detect r ≳ 0.17. A real but smaller effect needs 2027 too.** Don't read a
mid-season 2026 number before the pre-registered end point. Peeking is also
the forking-paths error.

### No leakage: how it is guaranteed, not just intended

| Leak path | What stops it |
|---|---|
| In-play prices as features | **Schema:** `benchmark_candles_pregame` refuses any candle ending after kickoff. The collector also never requests past kickoff. |
| The close (or anything after L) as a feature | **One entry point:** features are computed only by `features_asof(candles, L)`, whose first line filters `end_period_ts <= L`. **Perturbation test:** replace every candle after L with random values and assert all features are bit-identical. That test is the proof. |
| A candle straddling L | `end_period_ts <= L` admits only periods that ended by L (candle semantics, §1). |
| Timezone relabelling (jackc625's bug) | Kalshi times are epoch seconds; nflverse ET is converted exactly once with `zoneinfo`; a unit test pins 16:25 ET on 2026-09-27 to 20:25Z (EDT) and a December game to EST. |
| Manufactured capture times (jackc625's bug) | Prices carry the exchange's `end_period_ts`, never a time we assign. `captured_at` is DB-stamped and used only for provenance, never as the observation time. |
| Future data in model fitting | The walk-forward harness fits week w only on rows with kickoff < week w's first kickoff. A test asserts no training row's K is at or after any prediction row's K. |
| Sportsbook closes | nflverse closing moneylines are closes. They may be reported as a comparison benchmark, never used as features. |
| Choosing the lock or features after seeing 2026 | Pre-registration committed before 2026 features exist. |

---

## 7. What is deliberately not in v1

- **No commitments, no `selections`.** Operator picks attach to commitments
  (§10.2); there are none to pick. If you want your own picks on these games
  recorded as a benchmark, that needs its own design, not a column here.
- **No `rest` or `injury` factors.** They belong to a model of the game, not
  of the price path. `line_movement` is tested alone so a result is
  attributable to it.
- **No other Kalshi series.** Props and thin contracts come later as separate
  agents, per the original scope.

## Open items before build

1. Confirm the maker-fee coefficient against the primary fee-schedule PDF (it
   returned 429 to scripted fetches).
2. Paste `db/007`, then write `db/008` with the three tables and triggers.
3. Decide Railway cron versus manual weekly runs.
4. Write the pre-registration file (definitions in §6 plus gate thresholds)
   **before** backfilling 2026.

---

## Sources

- [env]: https://docs.kalshi.com/getting_started/api_environments.md — API environments and base URLs
- [mkt]: https://docs.kalshi.com/api-reference/market/get-market.md — Market object fields, `security: []`
- [cndl]: https://docs.kalshi.com/api-reference/market/get-market-candlesticks.md — live candlesticks, intervals, historical-cutoff note
- [hcndl]: https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks.md — historical candlesticks
- [hist]: https://docs.kalshi.com/getting_started/historical_data.md — historical tier and cutoff
- [life]: https://docs.kalshi.com/getting_started/market_lifecycle.md — statuses, `close_time`, `expected_expiration_time`
- [sett]: https://docs.kalshi.com/getting_started/market_settlement.md — settlement, settlement fees
- [rl]: https://docs.kalshi.com/getting_started/rate_limits.md — rate-limit tiers
- [fee]: https://docs.kalshi.com/getting_started/fee_rounding.md — fee rounding and the worked example that pins 0.07
- [series]: https://docs.kalshi.com/api-reference/market/get-series.md — `fee_type` / `fee_multiplier`
- [s1]: https://polykal.net/kalshi-fees-explained/ — secondary: taker and maker coefficients
- [s2]: https://www.botforkalshi.com/blog/kalshi-fees-explained — secondary: maker fee on `quadratic_with_maker_fees`
- Primary fee schedule (not retrieved, 429): https://kalshi.com/docs/kalshi-fee-schedule.pdf
- nflverse schedule: https://github.com/nflverse/nfldata/blob/master/data/games.csv
- Live API probes: `GET /series?category=Sports`, `/markets?series_ticker=KXNFLGAME`,
  `/markets/{t}`, `/events/{e}`, `/historical/cutoff`, `/historical/markets`,
  both candlestick endpoints, run 2026-09-30 unauthenticated.

[env]: https://docs.kalshi.com/getting_started/api_environments.md
[mkt]: https://docs.kalshi.com/api-reference/market/get-market.md
[cndl]: https://docs.kalshi.com/api-reference/market/get-market-candlesticks.md
[hcndl]: https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks.md
[hist]: https://docs.kalshi.com/getting_started/historical_data.md
[life]: https://docs.kalshi.com/getting_started/market_lifecycle.md
[sett]: https://docs.kalshi.com/getting_started/market_settlement.md
[rl]: https://docs.kalshi.com/getting_started/rate_limits.md
[fee]: https://docs.kalshi.com/getting_started/fee_rounding.md
[s1]: https://polykal.net/kalshi-fees-explained/
[s2]: https://www.botforkalshi.com/blog/kalshi-fees-explained
