# Crypto efficiency scan (hourly BTC/ETH) — DESIGN ONLY, NOT RUN

**Written 2026-10-02 for owner review. Nothing here has been executed, and no
Kalshi price, candle or settlement in its scope has been read.** Facts it
relies on are verified in `docs/reference-analysis-crypto.md` §1.

**Question.** At fixed times before expiry, does a **DVOL-lognormal
probability** earn positive blend weight against the Kalshi mid for
`KXBTCD`/`KXETHD` hourly contracts? Broken down by **time to expiry** and
**strike distance**, and is any of it left **net of fees and half-spread**?

This is descriptive, like `market_efficiency_scan.md`, and licenses nothing.
A cell that shows something becomes a candidate for its own pre-registration,
DB-stamped (db/017), evaluated on the reserve below.

**Stated prior: weight ≈ 0.** mrose105 measured the market mid beating
Gaussian, Student-t and vol-rescaled models on recorded settlements, and
losing to none (`reference-analysis-crypto.md` §2.1). If weight appears, the
first suspects are the benchmark's tail bias and thin books, not edge.

---

## 1. Scope and the reserve

| | Rule |
|---|---|
| **Series** | `KXBTCD`, `KXETHD` only. `KXBTC`/`KXETH` (ranges) are out: a band's probability is a difference of two `KXBTCD`-type probabilities, so they would add strata, not information. `KXBTC15M`/`KXETH15M` are **reference only, never requested** (§3d of the analysis). |
| **Events** | Hourly events (open 1 h before close). The 17:00 ET event opens 25 h out and is a **separate stratum**. |
| **Rule regime** | Close **on or after 2025-03-22** (simple-average settlement). Earlier events use the trimmed-mean rule and are excluded. |
| **Primary window** | Close from the **earliest 1-minute DVOL available on the run date** (about 2026-03-30 on 2026-10-02; it moves forward daily, §1.4) through **2026-08-02**. The actual start is recorded in the output. |
| **Secondary window** | 2025-03-22 → the primary start, with **hourly** DVOL (the bar ending at or before t). Reported separately; it can only confirm or weaken the primary. |
| **Reserve, unread** | Every event with close **≥ 2026-08-03** (the most recent 60 days at 2026-10-02). No market listing, candle, trade or settlement in it is requested. Guarded in code before every request and tested, as in `scan_market_efficiency.assert_in_scope`. It is the holdout for anything this scan nominates. |

The reserve is fixed by date, not "the last 60 days at run time". A later
run therefore cannot quietly eat into it.

## 2. Inputs at decision time t

All inputs are as-of, fenced on **`end ≤ t`**. The repos' bugs (§2.1:
selection on the close spot; ±5-minute "nearest" joins; bar-start
timestamps) are each excluded by a named test.

| Input | Source | As-of rule |
|---|---|---|
| Kalshi mid, spread | 1-minute candles (`/historical/markets/{ticker}/candlesticks`) | Latest candle with `end_period_ts ≤ t`, **≤ 2 min old**, `0 < bid < ask < 1`. Otherwise the rung is "unquoted at t" (a liquidity statistic) and excluded from the weight. |
| Spot S | Coinbase Exchange 1-minute candles, `BTC-USD` / `ETH-USD` (BRTI-constituent proxy) | **Close of the bar whose end (start + 60 s) ≤ t.** Never the bar containing t. |
| σ | Deribit DVOL (`get_volatility_index_data`), resolution 60 (primary) or 3600 (secondary), divided by 100 | Latest row whose **period end ≤ t**. Row timestamp semantics are verified before the run; until then, rows are treated as period-start and shifted by one period. |
| Strike K | The market's `floor_strike` (`T…99.99`), "above" = strictly greater | From market metadata. **The ladder is never filtered by any spot later than t.** |
| Outcome | `settlement_value_dollars` ∈ {0, 1} | Read only after the forecast is computed. Non-binary settlements are counted, not scored. |

## 3. The benchmark, frozen

The benchmark is risk-neutral, driftless and lognormal, priced against the
settlement **average**:

```
τ      = (T − t) in years (T = the event's close time)
τ_eff  = τ − (2/3)·w,  w = 60 s, for τ > w     # variance of the log of the 60 s average
p_dvol = Φ( [ ln(S/K) − ½ σ² τ_eff ] / (σ √τ_eff) )
```

There is **no fitted parameter.** DVOL's 30-day horizon is a known mismatch.
It is the thing being tested, not corrected for. A second, *reported-only*
benchmark is the constant 0.5, the "flatten the mid" null.

## 4. Sampling

Seed **20261002**, per series:

- **Events:** 400 hourly events sampled uniformly from the primary window,
  plus 100 17:00-ET events; 200 from the secondary window.
- **Decision times** (hourly events): t = T − {55, 45, 30, 20, 10, 5, 2} min.
  For 17:00-ET events: T − {24 h, 12 h, 6 h, 1 h, 10 min}.
- **Rungs per event:** the ladder at the first decision time, then **9 rungs
  chosen by strike rank around the median strike of the listed ladder.** The
  median comes from Kalshi's own listing, never from spot. All 9 are kept at
  every decision time.
- **Requests:** one 1-minute candle request per rung covers every decision
  time, so about (400 + 100) × 9 × 2 series ≈ 9k requests, plus Coinbase and
  Deribit. About 1 h at 0.3 s pacing. Kalshi jobs run one at a time.

## 5. What is reported

**Strata:**

- **Time to expiry:** 55–45, 45–30, 30–20, 20–10, 10–5, 5–2 min; 17:00
  horizons separately.
- **Strike distance:** z = |ln(K/S)| / (σ√τ_eff), in buckets < 0.5, 0.5–1,
  1–2, > 2.

**Per stratum and series:**

- **Blend weight** of `p_dvol` against the mid. Closed form, Brier-optimal,
  clipped to [0, 1], as in `scan_market_efficiency.weight_with_ci`.
  - 95% CI by bootstrap over **events** (2,000 draws). All rungs of an hour
    share one BRTI path, so the event is the unit.
  - A second CI clustered by **day**, because hours within a day share a vol
    regime.
  - "Positive" requires **both** lower bounds above 0.
- **The same weight for the 0.5 null.** Weight on DVOL close to the null's
  weight means flattening, not information.
- **Brier** of mid, `p_dvol` and 0.5; reliability by decile.
- **Net of costs.** Weight is a calibration statistic, not money. For every
  rung:

  ```
  edge_yes = p_dvol       − (ask      + 0.07·ask·(1−ask))
  edge_no  = (1 − p_dvol) − ((1−bid)  + 0.07·(1−bid)·bid)
  ```

  This gives the share of rungs with `max(edge) ≥ 2¢`, and **the realized
  taker R** of that hypothetical gate (one rung per event and decision time,
  largest edge), with the event-clustered CI.
  - **Maker:** fee is 0 on `quadratic`, but mrose105 measured a −15.7 pp
    adverse-selection gap. So maker R is reported **only** with the §7
    trade-through fill rule from trade prints, never assumed filled.
  - **Half-spread is inside "ask"**: `edge_yes = (p − mid) − half_spread −
    fee`, stated explicitly in the output.
- **Liquidity:**
  - quoted share and median spread;
  - share with spread ≤ 2¢;
  - contracts traded in the 60 min before t (from candle volume);
  - open interest at t.

  A positive cell on a book that does not trade is labelled untradeable, as
  in the sports scan.
- **Leakage canaries:**
  - the share of rungs whose mid moved > 25¢ in the 2 min before t, which
    flags quotes that already "know";
  - a **placebo**: the same weight computed with S taken 5 min *after* t. It
    must come out higher than the real figure, or the fencing is wrong.

## 6. How the result is read (fixed now)

- **Nothing passes or fails here.** A stratum is a **candidate** only if:
  1. the DVOL weight's lower bounds (event- and day-clustered) are both above
     0;
  2. the DVOL weight exceeds the 0.5 null's weight;
  3. the realized taker R of the 2¢ gate has a positive point estimate; and
  4. median contracts traded in the hour before t ≥ 100.
- With about 2 series × (6 TTE + 5 daily) × 4 distance strata ≈ 88 cells,
  **several false candidates are expected at 95%.** One candidate cell is
  noise until a pre-registered test on the reserve says otherwise.
- **Any candidate goes to:**
  - its own pre-registration (frozen model, margin, gate, pass rule),
    DB-stamped via db/017;
  - evaluated once on the 2026-08-03+ reserve;
  - then forward-only.

## 7. Before it can run (owner decisions)

1. **Approve the scan.** It reads about 4 months of primary-window Kalshi
   data, which is then spent for development.
2. **1-minute DVOL is disappearing at a day per day.** Either run soon, or
   first archive 1-minute DVOL (and Coinbase 1-minute bars) in a store-only
   job like `jobs/archive_nfl_props.py`. The second preserves the window
   without reading Kalshi.
3. **Vol source for any later build.** DVOL is 30-day. Short-dated ATM IV can
   be rebuilt free from Deribit history-API option trades (§1.4). That would
   be a second benchmark, a new pre-registered choice made *before* this scan
   runs, not after.
