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
| 1 | 2026-10-01 18:42 | smoke, `KXNHLGAME` only | ran | Checked coverage and runtime before the runner was committed. Output deleted; the cell is reproduced exactly from cache in #2 and #3. Pacing raised 0.2 → 0.3 s after 429s. |
| 2 | 2026-10-01 18:46 | all cells | ran; **tennis broken** | Every tennis event "start unmatched". ESPN answers each queried date with the whole tournament, so one match arrived five times and the matcher read it as ambiguous. Fixed (de-duplicate, tested); no threshold or rule changed. `market-scan-20261001T201612Z.json` kept as the record; its CSV, a subset of #3's, was dropped. |
| 3 | 2026-10-01 20:16 | all cells, after the fix | ran | **The result below.** The 17 non-tennis cells are identical to #2 field for field (all cached). `market-scan-20261001T202546Z.json` / `.csv`. |

## Result (run #3)

**w** = the base rate's blend weight against the mid, with 95% CI clustered by
event. **w½** = the same for the constant 0.5. When w½ is close to w, the
"signal" is only flattening an overconfident mid. **Vol** = median contracts
traded in the 24 h before t. **OI** = median open interest at t. **≤3¢** =
share of quoted rungs with spread ≤ 3¢. **Quoted** = share of sampled rungs
with a fresh two-sided quote at t.

| Sport | Market | Rungs (events) | w [95% CI] | w½ | Brier mid / base | Quoted | Median spread | ≤3¢ | Vol 24h | OI |
|---|---|---|---|---|---|---|---|---|---|---|
| CFB | game | 126 (126) | 0.06 [0.00, 0.29] | 0.00 | 0.160 / 0.237 | 97% | 1¢ | 100% | 63,939 | 79,819 |
| CFB | spread | 554 (131) | 0.16 [0.00, 0.55] | 0.23 | 0.230 / 0.251 | 84% | 4¢ | 50% | 56 | 82 |
| **CFB** | **total** | 558 (125) | **0.63 [0.21, 1.00]** | 0.16 | 0.231 / **0.225** | 89% | 3¢ | 59% | 72 | 84 |
| NHL | game | 150 (150) | 0.65 [0.00, 1.00] | 0.51 | 0.250 / 0.246 | 100% | 1¢ | 97% | 34,157 | 32,290 |
| NHL | spread | 591 (150) | 0.12 [0.00, 0.60] | 0.12 | 0.196 / 0.203 | 98% | 2¢ | 72% | 374 | 373 |
| NHL | total | 729 (150) | 0.00 [0.00, 0.40] | 0.03 | 0.184 / 0.195 | 97% | 2¢ | 74% | 434 | 424 |
| **NHL** | **player goals** | 160 (73) | **0.61 [0.23, 1.00]** | 0.03 | 0.160 / 0.155 | **21%** | 7.5¢ | 13% | **0** | **0** |
| NHL | player points | 113 (51) | 0.46 [0.00, 1.00] | 0.01 | 0.204 / 0.206 | 15% | 7¢ | 18% | 0 | 0 |
| NHL | player assists | 181 (66) | 0.00 [0.00, 0.45] | 0.07 | 0.207 / 0.224 | 24% | 7¢ | 9% | 0 | 0 |
| MLB | game | 147 (146) | 0.00 [0.00, 0.72] | 0.00 | 0.238 / 0.250 | 99% | 1¢ | 100% | 15,894 | 15,410 |
| MLB | spread | 567 (150) | 0.00 [0.00, 0.09] | 0.00 | 0.181 / 0.201 | 76% | 1¢ | 99% | 1,703 | 1,604 |
| MLB | total | 683 (149) | 0.00 [0.00, 0.33] | 0.00 | 0.164 / 0.177 | 92% | 1¢ | 95% | 1,171 | 1,127 |
| MLB | hits | 572 (146) | 0.17 [0.00, 0.76] | 0.06 | 0.170 / 0.173 | 76% | 2¢ | 71% | 0 | 0 |
| MLB | strikeouts | 655 (145) | 0.00 [0.00, 0.16] | 0.00 | 0.155 / 0.180 | 89% | 2¢ | 67% | 341 | 330 |
| MLB | total bases | 564 (144) | 0.00 [0.00, 0.32] | 0.06 | 0.164 / 0.170 | 77% | 4¢ | 44% | 0 | 0 |
| MLB | home runs | 404 (140) | 0.35 [0.00, 1.00] | 0.04 | 0.108 / 0.109 | 54% | 1¢ | 93% | 446 | 446 |
| **MLB** | **hits+runs+RBIs** | 574 (140) | **0.71 [0.26, 1.00]** | 0.01 | 0.185 / 0.181 | 78% | 5¢ | 31% | **0** | **0** |
| **Tennis** | **ATP match** | 136 (136) | **0.34 [0.005, 0.73]** | **0.35** | 0.239 / 0.252 | 96% | 1¢ | 93% | 9,404 | 8,589 |
| Tennis | WTA match | 128 (128) | 0.15 [0.00, 0.46] | 0.16 | 0.212 / 0.252 | 95% | 1¢ | 91% | 4,584 | 4,525 |
| Tennis | ATP total games | 206 (110) | 0.44 [0.00, 0.94] | 0.18 | 0.245 / 0.246 | 51% | 30¢ | 22% | 2 | 4 |
| Tennis | WTA total games | — | no events in the window (series starts 2026-08) | | | | | | | |
| Tennis | ATP game spread | 234 (112) | 0.25 [0.00, 0.59] | 0.04 | 0.226 / 0.240 | 55% | 17.5¢ | 21% | 0 | 0 |

Coverage: start-time matching dropped 0–24 of 150 events per cell (CFB
worst, 18–24; ESPN abbreviations). The in-play check (mid moved > 25¢ in the
2 h before t) was 0–3% everywhere, so t was pre-game. Base-rate fallback to
0.5 was under 12% except ATP total games (68%: too few prior rungs per
floor, so its w says little).

### Reading

1. **The liquid markets show no base-rate weight.** That covers every game
   line, MLB spreads and totals, MLB strikeouts and NHL totals: four- to
   five-figure daily volume, 1–2¢ spreads, w at or near 0. A
   team-agnostic prior adds nothing there, which agrees with §8's three
   builders.
2. **Four cells clear zero, and liquidity divides them:**
   - **NHL player goals** and **MLB hits+runs+RBIs** are positive on paper
     on books that **do not trade**: median 24 h volume 0, open interest 0,
     and only 21% of NHL goal rungs quoted at all. A stale quote nobody
     trades is easy to beat and impossible to fill. **Not builds.**
   - **ATP match** clears by 0.005, and its constant-0.5 weight is the same
     (0.35 vs 0.34). That is flattening an overconfident mid, not
     information in the base rate, and the bound is fragile. **Not a
     build.**
   - **CFB total** (w 0.63, CI 0.21–1.00) is the only cell where **the base
     rate alone beats the mid** (Brier 0.225 vs 0.231) and where w is well
     above w½ (0.63 vs 0.16), so it is not just flattening. It also has a
     book that trades: 3¢ median spread, ~70 contracts a day, OI ~80. That
     is thin, but real.
3. With 21 cells at 95%, about one false positive is expected. Four cleared,
   and on inspection two are untradeable and one is flattening.

### Candidate for the next build: CFB totals (owner decides)

- **Why:** the one cell with base-rate information beyond flattening on a
  book that trades.
- **Untouched data exists now.** The 2026 CFB season (August onward) is in
  the reserve and unread: about five settled weeks for a pre-registered
  holdout, then forward through the bowls.
- **Against it:** about 70 contracts a day is tiny capacity, so it is fine
  for paper and a question for anything more. `KXNCAAFTOTAL` is
  `quadratic_with_maker_fees`, so the taker fee is paid in full. This scan
  priced no fee and no spread, and a 3¢ spread plus the fee may exceed
  whatever the blend moves.
- **The required next step is a pre-registration, not code.** It needs the
  frozen base-rate rule, t, the edge gate net of fees, the holdout (2026
  weeks 1–5) and a pass rule, written before any 2026 CFB price is read.
