# Reference analysis: Kalshi crypto (BTC/ETH hourly; 15-min for reference)

**Written 2026-10-02.** Research only: no adapter code, no commitment. Eight
public repos were cloned into `reference/crypto/` (gitignored), read at the
code level where a claim depended on it, and checked against Kalshi's own API
and contract terms and Deribit's public API. Same discipline as
`docs/reference-analysis.md`: every number below is either verified here (§1)
or attributed to a repo and labelled as its claim.

**Bottom line first.**

- The one repo that measured honestly found that **nothing beat the Kalshi
  price**: mrose105, settlement-resolved, on recorded books. Five model
  families, plus a learned recalibration of the market itself, lost to the
  mid out of sample.
- **Four of the eight backtests are circular.** They price their simulated
  "Kalshi" quotes from the same model family they then trade against.
- **The two DVOL-benchmark studies each have a disqualifying flaw.** One selects
  contracts on the outcome-time spot; the other never scores against outcomes
  at all.
- The prior, as for NFL game lines (CLAUDE.md §8), is that **a public-data
  model loses to this market.**

---

## 1. Verified facts (2026-10-02)

### 1.1 Series, fees, history

From `GET /series/{ticker}` and `GET /series/fee_changes?show_historical=true`:

| Series | Title | Frequency | `fee_type` | Multiplier | Fee changes | Contract terms | Earliest events (probed) |
|---|---|---|---|---|---|---|---|
| `KXBTCD` | Bitcoin price Above/below | hourly | `quadratic` | 1 | none | `contract_terms/BTC.pdf` | ~2024-10-28 |
| `KXETHD` | Ethereum price Above/below | hourly | `quadratic` | 1 | none | `contract_terms/ETH.pdf` | ~2024-10-28 |
| `KXBTC` | Bitcoin range | hourly | `quadratic` | 1 | none | `contract_terms/BTC.pdf` | ~2024-10-28 |
| `KXETH` | Ethereum range | hourly | `quadratic` | 1 | none | `contract_terms/ETH.pdf` | ~2024-10-28 |
| `KXBTC15M` | Bitcoin price up down | fifteen_min | `quadratic` | 1 | none | `contract_terms/CRYPTO.pdf` | ~2025-12-09 |
| `KXETH15M` | ETH 15M price up down | fifteen_min | `quadratic` | 1 | none | `contract_terms/CRYPTO.pdf` | ~2025-12-09 |

- **Fees:** `quadratic` means the taker pays `0.07 × P × (1 − P)` per
  contract, and **makers pay 0** (`venues/kalshi/fees.py`, `MAKER_SHARE`). No
  series has a recorded fee change.
- **Earliest dates** come from a binary search over 11:00 and 17:00 ET event
  tickers, accurate to a few days.
- **Listing structure:**
  - Hourly `KXBTCD` events open **1 hour** before close.
  - The 17:00 ET event opens **25 hours** before (e.g. `KXBTCD-26JUN1517`
    opened 2026-06-14T20:00Z and closed 2026-06-15T21:00Z).
  - `expected_expiration_time` = close + 5 min.
  - Strikes sit at `.99` (`T63999.99`), so there are no ties at round numbers.
- **Liquidity has grown sharply.** The busiest `KXBTCD` 11:00 rung traded
  7,457 contracts on 2025-01-15 and 534,473 on 2026-06-15.

### 1.2 Settlement rule — and it changed

**Contract terms** (`assets.kalshi.com/contract_terms/BTC.pdf`, read
2026-10-02):

> The Underlying … is the spot price of one Bitcoin in U.S. dollars at
> `<time>`, according to a simple average of the CF Bitcoin Real-Time Index
> ("BRTI") for the minute (60 seconds) prior to `<time>`. … If no data is
> available on the Expiration Date at the Expiration Time, then the market
> resolves to No.

- **"Between" is inclusive at both ends** (`≥ lower and ≤ upper`). mrose105
  models bands as `[lo, hi)`, which is wrong at the edge, though the `.99`
  caps make it nearly moot.
- `ETH.pdf`: the same, on CF's ETHUSD_RTI (ERTI in the market rules).
- **`CRYPTO.pdf`** (15-min series): "a simple average of the CF
  `<cryptocurrency>` `<index>` for the 60 seconds prior to `<time>`"; "above"
  is strictly greater; minimum tick $0.001.

**The per-market `rules_primary` shows a rule change that no repo mentions:**

- `KXBTCD-25JAN1517`: "If the average of the sixty seconds of … BRTI before
  5 PM EST, **excluding the top 20% and bottom 20% of values**, is above …"
  That is a **trimmed mean**.
- `KXBTCD-25JUN1517` onward: "If the **simple average** of the sixty seconds of
  … BRTI …"
- A binary search over daily 17:00 events puts the switch between
  **2025-03-21** (last trimmed) and **2025-03-22** (first simple).
- **Any study spanning that date mixes two settlement rules.** Use
  post-2025-03-22 data, or model each regime separately.

**15-minute structure:**

- `KXBTC15M-26JUN151715` settles YES iff the 60-second BRTI average before
  17:15 is **at least** (`greater_or_equal`) the 60-second average before
  17:00.
- The "strike" (`floor_strike`) is **set at the window's open**.

### 1.3 History retention

| Data | Verified |
|---|---|
| Kalshi 1-min candles | Served for settled markets back to at least 2025-01-15 (`KXBTCD-25JAN1517`, historical tier). Sparse on quiet rungs (55–120 candles in the last 2 h). |
| Kalshi trade prints | Served back to at least 2025-01-15: 57 prints in the final hour on the busiest `KXBTCD-25JAN1511` rung; 5,725 on `KXBTCD-26JUN1511`. |
| Kalshi order books | **No history.** Live only. mrose105 records its own for this reason. |
| Historical cutoff | `GET /historical/cutoff` → `market_settled_ts` = 2026-08-02. Markets settled before it are served only from `/historical/…` (`venues/kalshi/client.py` routes this). |

Kalshi does not document how long candles and prints are kept. Absence
here is not proof of permanence; if it matters, archive it, as for F1/F2.

### 1.4 Deribit volatility history

| Source | Verified |
|---|---|
| **DVOL**, `public/get_volatility_index_data` | Daily history from **2021-03-24** (BTC). Resolutions `3600`, `43200` and `1D` return data for 2025. **Resolutions `60` and `1` return nothing older than about 185 days.** On 2026-10-02 the 1-minute series begins about **2026-03-30**. That is a **rolling window**: every day, a day of 1-minute DVOL history disappears. ETH behaves the same. |
| **Historical option IV, free** | **Yes, per trade.** `history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time?kind=option&include_old=true` returns expired-option trades with `iv`, `mark_price`, `index_price` and instrument (e.g. `BTC-17JAN25-95000-P`, iv 59.14, 2025-01-15 14:04Z). A short-dated ATM IV can therefore be reconstructed from trades of the nearest expiry. It is irregular, trade-driven sampling, not a surface. |
| Option chain or surface snapshots | Live only (milanroundraise is right on this). Deribit open interest is live only, as mrose105 notes. Tardis.dev sells full history and gives the first day of each month free; not verified here. |

### 1.5 Spot proxy for BRTI

**BRTI itself is CF Benchmarks' product.** Its page is public; no free
historical API was found.

- **Coinbase** is a BRTI constituent (SiddhaBasu's notes, citing CF
  Benchmarks; not independently verified here). Coinbase Exchange
  `/products/{BTC,ETH}-USD/candles?granularity=60` serves 1-minute history
  (verified April 2025 and April 2026).
- **Each row's timestamp is the bucket START.** A join must use the bar whose
  *end* ≤ t. This is exactly the bug wfquiroz has (§3).

---

## 2. The repos

| Repo | What it is | License |
|---|---|---|
| `milanroundraise/kalshi-mispricing` | BS digital on `KXBTCD`, Deribit DVOL vol, 1 week, 4 checkpoints per hour | **none** |
| `giannandreadestefano/btc-prediction-market-efficiency` | MSc thesis: Kalshi `KXBTC` + Polymarket vs a DVOL-BS benchmark, Mar 2024 – Jun 2026 | **MIT** (code); full data not redistributed |
| `mrose105/kalshi-btc-hourly-contracts-trading-bot` | Paper bot on `KXBTC` hourly range bands; buys NO on OTM bands; extensive recorded-data audits | **none** |
| `SiddhaBasu/kalshi-bot` | `KXBTC15M`: GBM plus XGBoost, walk-forward, Platt calibration, paper trading | **none** (README shows an MIT badge, but there is no LICENSE file) |
| `hamad-khawaja/kalshi-trading-bot` | `KXBTC15M` and `KXETH15M`: 16-signal heuristic model, multi-feed | **MIT** |
| `brandononchain/kalshibot` | `KXBTC15M`: GBM plus a Polymarket "arbitrage", JS | **none** |
| `falihfaizal95/kalshi-btc` | `KXBTCD` and `KXBTC`: DVOL lognormal blended 40/60 with XGBoost on technicals | **none** |
| `wfquiroz/kalshi-btc-arbitrage` | `KXBTCD` daily: BS with hourly realized vol plus XGBoost; Jon Becker dataset | **none** |

### 2.1 Findings, skeptically

**mrose105: the only settlement-resolved, model-vs-market measurement.**

- **The market beats every model.** On 9,110 recorded contract-observations
  (2026-08-12 → 08-21, 87 expiries), Brier scores (lower is better):
  - market mid **0.1611**;
  - Student-t (df 3) 0.1632;
  - Gaussian 0.1726;
  - Gaussian with vol rescaled ×0.4–×2.5: best 0.1712.
- **A learned recalibration of the market itself lost out of sample**
  (0.1525 vs the market's 0.1506; fit on 43 expiries, scored on 44). Adding
  features made it worse.
- **The Gaussian understated P(YES) in every bucket**, and the signal "was
  partly detecting its own bias".
- **Resting orders lose to adverse selection.** They saved about 2.4% of
  spread, but P(win | filled) was 76.0% against 91.7% when unfilled, a −15.7
  pp gap.
- **Its synthetic backtest is circular, and the author says so.**
  `build_ladder()` "manufactures quotes from a lagged-vol version of the same
  model family". The live NO strategy produces **zero** trades in it.
- **The live lane's own result is inconclusive.** Expiry-clustered BCa CI
  **[−7.8%, +12.3%]** on n = 88; five sweeps were run on the day it was
  produced; "it lost to buying BTC" over the same window.
- **Kalshi lags spot.** Correlation of a past Coinbase move with Kalshi's
  later repricing peaks at about 20 s (+0.180), positive on all days. But "a
  round trip pays two spreads, about 20% of a 20¢ contract, against the ~9.5%
  the lag delivers". Not tradeable directly.
- **It is honest work**: settlement-resolved, expiry-clustered, and it
  reports what failed. It is still a 9-day sample of one instrument in one
  vol regime (a strong rally), with recordings that miss 21% of hours,
  concentrated overnight.

**milanroundraise: BS digital plus DVOL, `KXBTCD`.** Its claims: the gap
between Kalshi-implied and Deribit-implied vol has p ≈ 5.6e−9 (n = 162), and
the trading rule had a 24% win rate with the top 5 trades carrying 58% of
profit. **Disqualifying flaws, from the code:**

1. **Selection on the outcome.** Each hour's contract is chosen as the strike
   nearest the BTC-PERPETUAL price **at the hour's close**
   (`collect_historical_data.py`, `nearest_atm_market = min(… abs(floor_strike
   − historical_spot_price))`, where `historical_spot_price` is taken at
   `close_ts`). The studied contract is, by construction, one that **finished
   at the money**. Every calibration figure is conditioned on the outcome.
2. **Look-ahead in every join.** The Kalshi candle, DVOL value and spot are
   each "nearest to the checkpoint" within ±5 min. That can be up to 5 minutes
   *after* t, which is material at the 2-minute checkpoint.
3. **No settlement handling.** It uses the Deribit perpetual or index, not
   BRTI, and time to `close_time`, not to the 60-second averaging window. No
   fees ("just the bid-ask spread").
4. **DVOL is the wrong horizon** (30-day constant maturity), as the author
   says. One week, one asset.

**giannandreadestefano (thesis): `KXBTC` vs a DVOL-BS benchmark.**

- **Its result:** Kalshi's first trade within 30 minutes of a daily event
  opening sits **+6.3 pp above** the DVOL benchmark on average (event-cluster
  CI +6.5 to +7.7 pp, 3,135 contracts, 485 events). Mean price 0.18 against
  benchmark 0.12.
- **What it does right:** as-of joins use `merge_asof(direction="backward",
  tolerance=5min)`, so there is no look-ahead; clustered bootstrap.
- **But it never scores either probability against settlement.** Its notebooks
  contain no Brier, log-loss or outcome term. A gap to a benchmark is
  **disagreement, not mispricing**. mrose105 measured exactly this benchmark
  family (Gaussian) understating P(YES) in every bucket. **The +6.3 pp may be
  the benchmark's tail error, not Kalshi's.**
- The price is a single trade print about 24 h out, spanning the 2025-03-22
  rule change.

**SiddhaBasu: `KXBTC15M`.**

- **The good:** walk-forward, Platt calibration, feature-parity checks, a
  realistic fill simulation, and an honest research log (`prior_findings.md`).
- **Backtest:** XGBoost "beats the live market on Brier" in 5 of 5 folds
  (0.130).
- **Live:** n = 15, Brier 0.2406 (CI 0.197–0.284), **win rate 26.7%,
  −$12.33**, and a "live/backtest Brier gap … real, not small-sample noise".
- **The textbook in-sample-to-live collapse.** A gap that large usually means
  the backtest saw something live does not: train/serve skew, of which they
  fixed several, or leakage.
- It found and fixed the BRTI 60-second TWAP mismatch mid-stream (Round 32);
  earlier data are not comparable.

**hamad-khawaja: `KXBTC15M`/`KXETH15M`.** Its backtest prices a **synthetic**
order book (fixed 4¢ spread, depth 100) around a fair value
`compute_fair_value_from_prices(...)` computed from the same BTC history its
model uses (`backtest/backtester.py:395–425`). **Circular.** There is no
model-vs-market measurement in the repo.

**brandononchain: `KXBTC15M`.**

- **Its backtest builds the claimed edge into the market it simulates:** a
  lagged BTC price, and "15% of the time, Kalshi price is very stale"
  (`backtest.js`).
- **It still loses.** In its own simulation, all three configurations are
  negative (−$99, −$17, −$20) with 72–82% win rates.
- **The author's validity notice:** "the simulator uses future within-slot
  price data … and applies settlement P&L before the simulated settlement
  time."
- **The 3–7 s lag claim is unmeasured here.** mrose105 measured about 20 s,
  and found it untradeable.

**falihfaizal95: `KXBTCD`/`KXBTC`.** `backtest/simulate.py` sets
`kalshi_implied = clip(ln_prob + uniform(−0.03, 0.03))`. **Circular by
construction**, and the docstring says so: "any P&L the backtest reports
comes from betting against the injected noise". The XGBoost part is trained
on technical features against real outcomes, but it is never compared with
the market.

**wfquiroz: `KXBTCD` daily.**

- **The headline is a win rate:** 58.3% on n = 24, +3.81% a trade.
- **Costs are understated:** "0.5¢ per side", while the real taker fee at
  50¢ is 1.75¢.
- **Two look-ahead bugs:**
  1. **BTC price up to an hour in the future.** BTC is taken from yfinance
     hourly bars indexed by bar **start**, via `searchsorted(open_time)`. That
     returns the bar *starting* at or after the open, whose `close` is up to an
     hour after entry. Vol, momentum and RSI read from the same future bar.
  2. **Sibling-outcome leak.** `rolling_win_rate = resolved_yes.shift(1)…`
     over markets sorted by `open_time`. All strikes of an event share an
     open time, so the "previous" rows are **sibling strikes of the same
     event**, whose outcomes are unknown for 25 h.
- Entry and exit prices are means of the first and last 3 **trade prints**,
  not quotes.

### 2.2 Tally

| | Circular or simulated market | Look-ahead or selection bug | Scored vs settlement | Compared against the market price | Fees modelled correctly |
|---|---|---|---|---|---|
| mrose105 | synthetic backtest only (acknowledged) | fixed several (documented) | **yes** | **yes: market wins** | yes |
| milanroundraise | no | **yes** (selection on close spot; ±5 min joins) | partly (calibration) | implied-vol gap only | no fees |
| giannandreadestefano | no | no | **no** | gap only | n/a |
| SiddhaBasu | no | fixed several | yes | backtest yes; live loses | yes |
| hamad-khawaja | **yes** | — | — | no | — |
| brandononchain | **yes** (edge simulated in) | **yes** (author's notice) | — | no | — |
| falihfaizal95 | **yes** | — | — | no | — |
| wfquiroz | no | **yes** (bar-start join; sibling leak) | yes | win rate | **understated ~3.5×** |

---

## 3. Answers

### a. Which model prices `KXBTCD` best against an independent benchmark, and how?

**None has been shown to beat the market price, and only one study
measured it properly.**

- **Against settlement, the best non-market model on record** is mrose105's
  Student-t (df 3) lognormal with EWMA realized vol: Brier 0.1632, against
  0.1611 for the market mid. It still **loses**. That was on `KXBTC` range
  bands, not `KXBTCD`, but it is the same underlying, horizon and settlement.
- **The best-specified independent benchmark** is the thesis's DVOL-BS
  `Φ(d₂)` with backward as-of joins. It was never scored against outcomes, so
  "best" there means "cleanest", not "most accurate".
- **No repo implements the 60-second BRTI average correctly in an hourly
  pricer.** Only SiddhaBasu (15-min) approximates it, with a Coinbase
  trailing mean.

**What a correct pricer needs** (this goes into the scan design):

- **Underlying:** a BRTI proxy. Coinbase 1-minute bars fenced on bar *end*.
  The real BRTI is not freely available.
- **Horizon:** the settlement average over [T − 60 s, T]. Under GBM, the log
  of that average has variance ≈ σ²·(τ − 2w/3), with w = 60 s and τ the time
  to T. That matters only in the last few minutes.
- **Vol:**
  - DVOL (30-day) is the wrong horizon.
  - The free alternative is short-dated ATM IV rebuilt from Deribit
    history-API option trades.
  - 1-minute DVOL exists only for the last ~185 days, on a rolling window.
- **Distribution:** lognormal understates the tails (mrose105). Student-t
  closes much of that gap but not all of it.

### b. What mrose105's measured result says about where edge is NOT

It is NOT in any of these:

1. **A better probability from public price history.** Five model variants
   and a learned recalibration all lost to the mid out of sample.
2. **Model vs market at entry.** No NO-cost region from 0.55 to 1.00 shows
   edge (8,866 observations, 88 expiries).
3. **Earning the spread passively.** The spread saved by resting is about 4×
   smaller than the adverse-selection cost of being filled.
4. **The Kalshi-vs-spot lag, taken directly.** It is real (~20 s) and
   structural, but two spreads cost about twice what it pays.
5. **Win rate.** 80–85% win rates sit on an 81% break-even. The honest CI
   contains zero.

Its own conclusion is the right operating assumption: "**the Kalshi price is
the best available forecast**. … An edge would require an information source
the price does not already contain — cross-venue lead-lag, a faster feed than
the exchange consensus, or order-flow microstructure." Those are latency and
microstructure games. They are not something a 1-minute paper tick with no
order placement can test. **That is the most important constraint on this
whole track.**

### c. What data and code are reusable (license check)

| Item | Status |
|---|---|
| `giannandreadestefano` code | **MIT.** Notebooks reusable with attribution. Full data is not redistributed; `data_sample/` is illustrative only. |
| `hamad-khawaja` code | **MIT.** Reusable with attribution; its backtest is not (circular). |
| Jon Becker `prediction-market-analysis` (dataset used by wfquiroz) | Repo **MIT** (verified via the GitHub API). The **dataset's** redistribution terms are not stated there, and the underlying data is Kalshi's. **Verify before relying on it.** |
| All other six repos | **No LICENSE: all rights reserved by default.** Read for ideas only; copy no code. SiddhaBasu's "MIT" badge is not a license. |
| `milanroundraise/backtest_results.csv` | Unlicensed, and **tainted by selection on the outcome**. Do not use. |
| mrose105 recordings (`recordings/`) | Gitignored; **not in the repo**. Its findings are citable; its data is unavailable. |
| Public APIs (Kalshi, Deribit, history.deribit.com, Coinbase) | Free, unauthenticated, verified above. This is where our data comes from. |

**Reusable ideas (not code):**

- record the uncensored ladder before any filter (mrose105);
- cluster CIs by expiry;
- reject a "closed" status as a settlement;
- use backward as-of joins (the thesis);
- run feature-parity checks between live and offline (SiddhaBasu).

### d. Does the hourly market fit the four-hook contract with a 1-minute tick? Where does 15-min break it?

**Hourly `KXBTCD`/`KXETHD`: it fits, with three cautions.**

| Hook | Fit |
|---|---|
| `observe()` | Ladder snapshot (`GET /markets?event_ticker=…`) plus a spot proxy plus vol, once a minute. Fine. |
| `form_thesis()` / `build_commitment()` | A slate of rungs. **Caution 1: the slate cap.** An hourly event lists up to ~190 rungs; `max_slate_size` (25) forces a deliberate choice, or one rung per event. The rungs share one BRTI path, so they are not independent commitments (cluster by event, as §8 already requires for games). |
| `resolve()` | Clean. Settles within minutes (`expected_expiration` = close + 5 min). **But** the contract allows expiration up to **one week** if data is unavailable, so `max_overdue` must be ≥ 8 days (§9.3), not a feed-sized default. |
| `capture_close()` | **Caution 2: what is the close?** As with NFL, trading runs to `close_time`, and in the final minute the price converges on the 60-second average already forming. A snapshot at `close_time` is the outcome, not a close. `closes_at` needs a fixed reference before the averaging window, e.g. T − 5 min, set at commit, never `close_time`. This does give crypto a meaningful CLV, which the `crypto` adapter lacks (CLAUDE.md §8). |
| §2 timing | **Caution 3: commit and quote clocks.** A 1-minute tick against a market that lags spot by ~20 s, with a worker-to-DB offset now measured (~0.4 s locally), is workable. `quote_provenance` is now mandatory (db/016). `resolves_after` must come from the API's `expected_expiration_time`, never worker `now()` + offset (Kalshi adapter notes). |

**Row volume at a 1-minute tick:** `gate_evaluated` events at one per
evaluated rung per tick would be about 60 ticks × ~190 rungs ≈ 11k rows an
hour. They need summarizing, at one per event per tick, before any agent
runs.

**15-min `KXBTC15M`/`KXETH15M` breaks it in four places:**

1. **The strike does not exist before the window opens.** It is set from the
   60-second average before the open. No commitment can be formed before
   t₀, and the whole thesis has to be formed and committed inside 15 minutes.
2. **The horizon is shorter than our tooling's resolution.** A 1-minute tick
   gives at most ~14 decision points. Capture and defer policies built in
   hours (48 attempts / 48 h) are meaningless. A close reference "before the
   averaging window" leaves under 14 minutes between commit and close.
3. **The edge, if any, is latency and microstructure** (§3b). Every 15-minute
   repo's thesis is a lag of 3–20 s. A paper agent with a 1-minute tick,
   DB-clock commits and no order placement **cannot represent that trade**.
   It would measure our latency, not the market's efficiency.
4. **Volume:** 96 windows a day per asset, each its own event. That is a
   different operating scale for sweeps, captures and events.

**Hence 15-min is reference only**, as directed.

---

## 4. What this means for the next step

- **The scan design** (`docs/dev/crypto_efficiency_scan.md`) takes these
  findings as constraints:
  - fence on bar *end*;
  - never select a strike by outcome-time information;
  - backward as-of only;
  - post-2025-03-22 only (simple-average rule);
  - score against settlement;
  - cluster by event;
  - fees and half-spread in every tradeable figure.
- **The stated prior is that DVOL-lognormal earns ~0 blend weight** against
  the mid, as mrose105 found for its models, and as three NFL builders found
  for game lines. If it earns weight, it most likely does so where the market
  is thin, as in the sports scan. Check the liquidity of any positive cell
  before believing it.
- **Time-sensitive: 1-minute DVOL ages out daily.** If the scan is approved,
  either run it soon or archive 1-minute DVOL first (store-only, like the
  F1/F2 archive).
