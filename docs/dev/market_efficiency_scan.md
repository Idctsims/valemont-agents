# Market-efficiency scan — specified before any run

**Written 2026-10-01, before any price, settlement or candle in scope has been
read.** The commit that adds this file is the timestamp. Owner direction:
descriptive only, no models. This scan picks the next build.

**Question.** For each Kalshi sport × market type with settled history, does a
**player- and team-agnostic base rate** earn positive blend weight against the
Kalshi mid at a fixed pre-game time? And how liquid is the market type?

This is not a pre-registered test. **It licenses nothing.** A cell that shows
positive weight becomes a candidate for a build that gets its **own**
pre-registration and its **own** holdout, on data this scan did not read
(§2).

---

## 1. What was read before this file

Only metadata, during API discovery on 2026-10-01:

- the series catalogue (`GET /series?category=Sports`);
- event tickers per series (names and dates; event objects carry no prices
  or results);
- for one MLB, NHL and CFB event each, plus 8 sampled 2025 events per sport,
  `expected_expiration_time`, compared with official start times;
- the `result` of the two `KXMLBGAME-25JUL08CHCMIN` markets, displayed while
  testing an endpoint.

**No candle and no price in scope was read.** One 2025 MLB game result was
seen. It is disclosed here and left in scope, because one game cannot steer a
pooled weight.

## 2. Scope and the reserve

| | Rule |
|---|---|
| **NFL** | **Excluded.** Kalshi's NFL markets begin at the 2025 preseason; 2025 is development data and 2026 is holdout/forward. No untouched NFL season exists. No `KXNFL*` request is made. |
| **Scan window** | Events whose ticker date is **on or before 2026-06-30**. |
| **Reserve** | Events dated **2026-07-01 onward** are **not read**: no market listing, no candle. They are the untouched data for whichever build this scan picks (MLB 2026 July–September, tennis July onward incl. the US Open, CFB 2026 season). NHL has no 2026 games after June, so an NHL build would validate forward only. |

Event tickers for the reserve are enumerated (names only) to find the window's
edge, and are then discarded unread. The code asserts the date guard before
every market request.

## 3. Cells

| Sport | Market type | Series | Kind |
|---|---|---|---|
| CFB | game | `KXNCAAFGAME` | winner |
| CFB | spread | `KXNCAAFSPREAD` | margin ladder |
| CFB | total | `KXNCAAFTOTAL` | total ladder |
| NHL | game | `KXNHLGAME` | winner |
| NHL | spread | `KXNHLSPREAD` | margin ladder |
| NHL | total | `KXNHLTOTAL` | total ladder |
| NHL | player goals | `KXNHLGOAL` | prop ladder |
| NHL | player points | `KXNHLPTS` | prop ladder |
| NHL | player assists | `KXNHLAST` | prop ladder |
| MLB | game | `KXMLBGAME` | winner |
| MLB | spread | `KXMLBSPREAD` | margin ladder |
| MLB | total | `KXMLBTOTAL` | total ladder |
| MLB | hits | `KXMLBHIT` | prop ladder |
| MLB | strikeouts | `KXMLBKS` | prop ladder |
| MLB | total bases | `KXMLBTB` | prop ladder |
| MLB | home runs | `KXMLBHR` | prop ladder |
| MLB | hits+runs+RBIs | `KXMLBHRR` | prop ladder |
| Tennis | ATP match | `KXATPMATCH` | winner |
| Tennis | WTA match | `KXWTAMATCH` | winner |
| Tennis | ATP total games | `KXATPGTOTAL` | total ladder |
| Tennis | WTA total games | `KXWTAGTOTAL` | total ladder |
| Tennis | ATP game spread | `KXATPGSPREAD` | margin ladder |

Left out, and why: series with fewer than 100 events in the window (aces,
total sets, saves); Challenger tennis, which is thin and duplicative; team
totals and RBIs/hits-allowed, to bound run time. MLB spreads, totals and props
exist only from 2026, so for them the window is March–June 2026.

## 4. The fixed pre-game time

**t = scheduled start − 60 minutes.** The start comes from the **sport's own
schedule source**, never from Kalshi. This is the rule CLAUDE.md already
applies to NFL kickoff. A discovery check found Kalshi's
`expected_expiration_time` − 3 h exact for MLB and NHL. For CFB it was an
hour **late** on one game (Indiana at Penn State), which would put t at
kickoff, and 2.5 h early on another: TV slots are set after listing.

| Sport | Schedule source | Match |
|---|---|---|
| MLB | `statsapi.mlb.com/api/v1/schedule` | date ±1 day, both team codes |
| NHL | `api-web.nhle.com/v1/schedule/{date}` | date ±1 day, both team codes |
| CFB | ESPN college-football scoreboard | date ±1 day, both team codes (one code if unique) |
| Tennis | ESPN ATP / WTA scoreboard, `timeValid` only | date ±2 days, both surnames |

A doubleheader or any ambiguous match is **dropped, not guessed**. Unmatched
events are counted per cell and reported as coverage. Tennis is the weakest
link: ESPN's time may be an order-of-play estimate. **Disclosed check, reported
per cell:** the share of rungs whose mid moved more than 0.25 between t − 2 h
and t. In-play pricing leaking into t would show up there.

## 5. Sampling

Per cell, seed **20261001**:

- **Pool events:** up to 300 window events, sampled uniformly. All their
  markets are listed, with results, and they form the base-rate pool.
- **Priced events:** the first 150 of those, in sample order.
  - Winner kind: **one** of the two complementary markets, by seeded coin.
  - Ladder kinds: up to **5 rungs**, sampled uniformly. They are chosen
    before any price is read, so selection cannot depend on price.

## 6. Quantities

**Price at t.** The latest 1-minute candle with `end_period_ts ≤ t`, at most
60 min old, with `0 < yes_bid < yes_ask < 1`. Then `mid = (bid + ask)/2`.

**Base rate b**, walk-forward and agnostic to player and team. It is the mean
binary settlement of pool rungs from the **same series** whose settlement was
**public before t** (`settlement_ts < t`), matched on a key, with **0.5 when
fewer than 20 match**. The key is:

| Kind | Key |
|---|---|
| winner | whether YES is the **second-listed** participant in the event code (home in team sports; a structural slot in tennis) |
| margin ladder | second-listed flag of the YES team, and floor within ±W |
| total / prop ladder | floor within ±W |

W = 3 for CFB spread and total; W = 0 (exact floor) everywhere else.

**Weight** (closed form, as in run log #7 and A4). The weight is `w*` minimising
the Brier score of `(1 − w)·mid + w·b` over binary-settled priced rungs, which
is `−Σ(mid − y)(b − mid) / Σ(b − mid)²`, clipped to [0, 1]. Its 95% CI is a
bootstrap over **whole events** (2,000 draws, seed 20261001). The same is
reported for the constant 0.5 (the pure "flatten the mid" null). A cell
"earns positive weight" iff the base rate's CI lower bound is above 0. The
gap between b's weight and 0.5's is the part of b that is information rather
than flattening.

**Also per cell:** n events and rungs priced; binary vs fair-value
settlements; share of b at the 0.5 fallback; Brier of mid and of b; coverage.

**Liquidity at t**, over the sampled rungs:

- quoted share (fresh, two-sided quote);
- median spread, and share with spread ≤ 3¢ and ≤ 8¢;
- **volume:** median contracts traded in [t − 24 h, t];
- **depth proxy:** median open interest at t (candles carry no book depth);
- the series' `fee_type`.

## 7. What the result can and cannot say

- Positive base-rate weight means a structural prior improves the mid's
  calibration at t on this sample. It is **not** a trading edge: no fee, no
  spread, no fill is modelled. A cell where the blend moves the price by less
  than half-spread + fee is untradeable whatever w is.
- Clustering is by event. Cross-event correlation (one team's season, one
  tournament) is not modelled, so CIs are somewhat optimistic.
- 22 cells at 95%: about one false "positive" is expected by chance. **A cell
  is a candidate, not a finding.**
- Choosing the next build uses w, its CI **and** liquidity together. A
  positive w in a market no one trades is not a build.

---

## Run log

| # | When (UTC) | Run | Result | Notes |
|---|---|---|---|---|
