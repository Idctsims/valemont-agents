# Betting science audit (Chat 8, Phase 3.5, Workstream A, Step 0)

Read-only. No code, no schema, nothing stamped. Written 2026-11-13.

**Where the numbers come from.** Two sessions produced this file. The
evidence was gathered on 2026-11-12 (one read-only board snapshot at
20:01:50 UTC through `valemont_readonly`, a 12-call keyless Kalshi probe,
the source checks, and a 200-ticket-per-cell run through the shipped
pricer). That session ended before the write-up. This file was written on
2026-11-13 from its saved outputs. **Nothing was re-read or re-run on
Nov 13**, so every board number below is the Nov 12 snapshot.

**Every number in this file is the Nov 12 snapshot** unless it names
another source.

**Pending sources carry no weight (owner, 2026-11-13).** The E3 leg-count
figures, the maker-markup rise, E5 and the Bürgi detail (§2, §7) decide
nothing in any method until they are verified or measured by M1/M2.

The scratch scripts (`dump_board.py`, `analyze.py`, `kalshi_probe.py`) are
not committed. If Step 1 is approved they are rewritten under
`workers/scripts/research/` with the git hash in every output.

---

## 0. Summary

1. **The paper entry price is wrong in two ways, and the bigger one is not
   the fee** (D1, confirmed). The fee is charged per leg in our cost; Kalshi's
   combo series carry their own fee type, so it is charged once. That
   overstates cost by under 1% on a short ticket of favourites and by 10 to
   28% on long ones. The larger error runs the other way: we have no fill
   model at all, and the one outside study says real fills sit above the
   product of leg mids.
2. **98.9% of legs are Kalshi-tier** (15,435 of 15,613). On those, our
   "fair p" is Kalshi's own mid, so a ticket's expected value is exactly
   minus its costs. The page should show that cost, not a view (D4,
   confirmed).
3. **Which legs you pick moves the cost more than anything in the model.**
   Same leg counts, four ways of picking legs: a random Lotto has a
   leg-implied price below Kalshi's minimum tick on 100% of tickets. A Lotto
   of 0.75–0.95 legs with spreads of 3¢ or less costs about 25% over fair. A
   Safer ticket built the same way costs about 7% (D9, confirmed on our
   board; the real quoter markup is still to measure).
4. **No espn-tier edge survives a change of de-vig method** (D5, confirmed).
   Of 140 legs, 15 show a positive edge under power, 1 under all four
   methods, and 0 reach 2¢ under any. Both edges claimed on Nov 12 change
   sign under three of the four.
5. **The copula is not the problem at Lotto length** (D8, mostly refuted).
   The PSD shrink never fired in 2,400 tickets. Monte Carlo error is under
   0.6% on sensibly built Lottos. What is weak is coverage: 47 to 76% of
   same-game pairs on any-sport tickets are unmodeled.
6. **Parlay-level results cannot judge anything for years** (D11, confirmed
   by arithmetic: about 1,500 Lottos to tell a 5% hit rate from 3.5%).
7. **Six decisions are the owner's** before any measurement runs (§1,
   J1–J6). The main one: Kalshi's public history lists newest first, so
   reaching pre-July combos means paging past reserve-dated rows.

---

## 1. Data-rights table

Rules applied: CLAUDE.md "The reserve" (Kalshi events dated 2026-07-01
onward are unread in exploration); RECURRING "Archive" (2026 NFL props
archive is store-only until 2027-01-20); §8 (2025 NFL and the spent
holdouts are not re-fit); §5 (inspection only through `db_inspect`).

| Dataset | Range | Read for this purpose? | Rule that decides |
|---|---|---|---|
| Our `kalshi_board`, `leg_consensus`, `book_lines`, `combo_requests`, `combo_quotes` | current rows; `book_lines` from 2026-11-09 | **Yes** | The app's own operating tables, read through `valemont_readonly`. Open markets, no outcomes. |
| Kalshi settled combos (`/historical/markets`, MVE) | closed before 2026-07-01 | **Yes, with J1** | Before the reserve wall. |
| Kalshi settled combos | closed 2026-07-01 onward | **No** | The reserve. This blocks the before/after 2026-08-20 comparison (J2). |
| Kalshi settled singles and their candles, non-NFL | closed before 2026-07-01 | **Yes** | Before the wall; not a spent holdout. |
| Kalshi settled NFL singles and combos, 2025 season | 2025-09 to 2026-02 | **Owner call (J3)** | §8: 2025 NFL is not re-fit for a strategy. A fill model is not a strategy fit, but the outcomes are the same games. |
| `KXNCAAFTOTAL` through 2026-09-27 | spent | **No** | Spent on the CFB holdout. |
| 2026 NFL props archive (Postgres weeks 1–5, Storage weeks 6–18) | 2026 season | **No. Never.** | Store-only until 2027-01-20. |
| NBA box scores (nba_api) | 2023–24, 2024–25, 2025–26 | **Yes** | Not Kalshi data, not a holdout. |
| NHL box scores | 2023–24 to 2025–26 | **Yes** | Same. |
| NFL box scores (nflverse) | 2022–2024 | **Yes** | Before 2025. |
| NFL box scores | 2025 | **Owner call (J3)** | Same rule as above. |
| NFL box scores | 2026 | **No** | Forward data for F1/F2; no interim look. |
| Soccer box scores | any | **Not planned** | No measured source identified; the priors stay, labelled. |
| The Odds API, live | current | **Yes, inside 450 credits** | Phase 2 guard. No historical endpoint. |

Judgment calls for the owner:

- **J1. Paging past the reserve.** `/historical/markets` has no date
  filter and returned newest first (closes of 2026-09-12 on the default
  call, 2026-08-18 for `KXMVESPORTSMULTIGAMEEXTENDED`). Reaching April–June
  2026 means receiving July–September rows. Proposal: the crawler reads
  only `close_time` on a row, discards it unparsed if it is on or after
  2026-07-01, and logs the count discarded. Is that acceptable, or does
  receiving the rows cross the wall?
- **J2. The maker-fee comparison.** The before/after 2026-08-20 split in M1
  is entirely inside the reserve. Either it is dropped (E1's April–June
  numbers stand as a floor) or the owner opens combo fills from
  2026-07-01 to 2026-09-12 for the fill model only.
- **J3. 2025 NFL.** May 2025-season NFL combos and box scores be read for
  the fill model and for rho? Without them NFL rho comes from 2022–2024
  only and the NFL fill model from nothing before July.
- **J4. The Nov 12 probe printed reserve-dated combo titles.** For rows
  closing on or after 2026-07-01 the probe printed keys and the title (the
  leg labels), not results, prices or settlement values. Recorded here so
  it is on file; say if titles count as reading.
- **J5. Per-series crawl.** Older collections are separate series
  (`KXMVENBASINGLEGAME` returned 2026-06-14; `KXMVENFLMULTIGAMEEXTENDED`
  returned 2025-11-18). Crawling by series avoids most of J1 for the NBA.
- **J6. Pinnacle.** See M4: adding the `eu` region to NBA mainlines costs
  about +90 credits a month, taking the plan to about 430 of 450.

---

## 2. Source check

"Verified" means the source itself was opened and says this. "Snippet"
means only a search result's summary was seen. Nothing here was reproduced.

| | What it claims | Status | What it does not show |
|---|---|---|---|
| E1 | 8.93M settled combos, 2026-04-03 to 06-01; maker edge +3.3¢ equal-weight, +2.1¢ volume-weight; peak near +6¢ at 20–35¢; gone above 70¢; same-game +4.1¢ vs +2.9¢; fills +1.1¢ (cross-game) and +2.5¢ (same-game) over the product of leg mids | **Verified** against the repo README (jsteng19/kalshi-combos-research). Not reproduced. | Whether edge is net of fees (it says trade prices are before fees). Nothing after June 1, so nothing under the maker fee. No breakdown by leg count in the README. It used signed REST and a WebSocket collector for RFQ fills; we have no key. |
| E2 | Cross-game combos priced near 20¢ independent resolved YES about 16%; about 3¢ of the 4.7¢ gap is YES legs resolving under their mid | **Verified** in the same README. | Whether the remainder is dependence or selection. |
| E3 | Implied edge about 2.5% on 2 legs, about 25% past 10; $0.01 charged on combos up to 20 legs; 15-leg NFL combos at 26.4% implied vig (Citizens) vs 23.9% at DraftKings | **Snippet only.** rg.org, Gambling Insider and DeFiLlama all returned 403. | Method, sample, dates. |
| E3 | Combo maker fee from 2026-08-20 at 50% of the taker fee | **Snippet, plus our own probe:** `/series/KXMVESPORTSMULTIGAMEEXTENDED` returns `fee_type: quadratic_with_combo_maker_fees`, multiplier 1. | The rate itself in a primary document. Kalshi's fee schedule PDF was not opened. |
| E3 | Maker markups on uncorrelated combos rose from about 0.55% to about 3.1% | **Not verified.** Source page returned 403. | |
| E4 | Bürgi, Deng, Whelan: favourite–longshot bias, low-priced contracts win less than break-even, takers lose more than makers, 300k+ contracts | **Abstract level.** The PDF could not be read (no PDF library on this machine). | The "worst under 10¢" figure, any sports-only table. |
| E4 | Kalshi's calibration paper (Aug 2026), 2,243,741 markets, good aggregate calibration | **Snippet.** Sports section not read. | |
| E4 (added) | Le, arXiv 2602.19520: sports calibration slopes 0.90–1.10 at short horizons, 1.74 past one month | **Snippet.** | |
| E5 | Kalshi's de-vigged winner price a median 0.75 points from Pinnacle over 88 fixtures | **Not found.** One search, no source. Treat as unsupported. | |
| E6 | Pinnacle is in The Odds API's `eu` region, key `pinnacle`, "odds are from public website which may incur a delay" | **Verified** on the-odds-api.com. Cost = markets × regions is from our own Phase 2 Step 0, not that page. | Props coverage for Pinnacle. |
| E7 | Parlay EV is the product of leg ratios; straights beat parlays under Kelly | Arithmetic stands. The two citations were not opened. | |
| E8 | Practitioners measure rho from past games, pool, shrink, fit walk-forward | **Not verified.** nba-hub's landing page shows `data/`, `research/`, `models/`, `tools/` and no licence; its method is not visible there. The other repos were not opened. | |
| E9 | Settled combos with leg sets are public | **Verified by probe** (§3). One correction: the per-leg `yes_settlement_value_dollars` in the spec was **absent** on every sampled row. Legs carried `event_ticker`, `market_ticker`, `side` only. Leg outcomes must be joined from each leg's own market. | |

---

## 3. Kalshi's mechanics, from primary sources and the probe

Probe: 12 calls, keyless, 2026-11-12, through `venues/kalshi/client.py`.
`/historical/markets` backed off six times (2 s to 60 s) before answering.

| Question | Answer | Source |
|---|---|---|
| Combo taker fee basis | One fee on the combo's own price. Combos are their own series with `fee_type: quadratic_with_combo_maker_fees`, multiplier 1; the taker model is 0.07 × P × (1 − P) at the combo price. | Probe (`/series/…`); RFQ doc: the contract count is sized so cost "including your taker fee" fits the target. **The first real fill should confirm it.** |
| Maker fee | Exists on combo series (the fee type's name). Rate not in the API docs. | Probe. Rate: snippet only. |
| Minimum price and tick | Current collections: `center_deci_edge_centi_cent`, steps of $0.0001 below 1¢ and above 99¢, $0.001 between. Older collections: $0.001 throughout. | Probe (`/multivariate_event_collections`). |
| Maximum legs | Collection `size_min` 2, `size_max` 0 (no cap stated). Per event, `size_max` 1 on the sampled events. On our board 2,824 of 6,541 eligible legs sit in events capped at 1 leg; 3,717 have no cap recorded. | Probe; board. Help page states no maximum. |
| Settlement | "Combos resolve to the product of the underlying positions, paying out a maximum of $1.00." Any leg at $0 pays $0. The collection says "Scalar outcomes are multiplied (rounded down)". | help.kalshi.com/markets/combos; probe. |
| Did not play | The leg "resolves to its last fair price as determined by the Exchange". "A DNP does not on its own refund or cancel the combo." | Help page. |
| Protected props | Settle at fair price if the player plays the first half, not after, and has not reached the target. | Help page. |
| Postponed game | Not addressed on the combos page. | Pending. |
| Quoting | RFQ. Quotes private per maker; all combo markets use a 3 s confirm and 1 s execution window. | RFQ doc. |
| Public without a key | `/historical/cutoff`, `/historical/markets` (MVE included by default), `/historical/trades`, `/events/multivariate`, `/multivariate_event_collections`, `/markets?mve_filter=only`. `/communications/rfqs` returns 401. | Probe. |
| History cutoff | Markets and trades settled before 2026-09-13 are in the historical tier. | Probe. |
| `combo_quoters` | `active_quoters` was empty on all 914 eligible events. It carries no signal today. | Board. |

Field shapes: a settled combo carries `mve_collection_ticker`,
`mve_selected_legs` (list of event, market, side), `result`,
`settlement_value_dollars`, `last_price_dollars`, `volume_fp`,
`open_time`, `close_time`, `price_ranges`. A trade carries `ticker`,
`count_fp`, `yes_price_dollars`, `no_price_dollars`, `taker_outcome_side`,
`taker_book_side`, `created_time`, `is_block_trade`.

---

## 4. D1–D13

Board: 15,613 binary legs; 10,051 not started; 6,541 combo-eligible
(Basketball 3,312, Football 1,581, Hockey 1,182, Soccer 379).

Synthetic tickets: 200 per cell through the shipped pricer, four ways of
choosing legs:

- **A** any sport, random legs
- **B** NBA that night, full-game lines and props
- **C** any sport, the side's ask between 0.75 and 0.95
- **D** as C, and the leg's spread 3¢ or less

| | Verdict | Evidence |
|---|---|---|
| **D1** | **Confirmed**, both halves | (a) `consensus/combo.py:84-87` returns ask + fee per leg and the quote multiplies them. Fee once instead: the per-leg version is higher by a median 0.7% (C, Safer), 3.0% (C, Mid), 9.7% (C, Lotto), 19% (B, Mid), 28% (B, Lotto). (b) No fill model exists. **Only one real quote is on record, not 20** (request 89, 3 legs): stored cost 12.54¢; product of asks 11.33¢; fee once 12.03¢; our fair 16.67%. By our numbers that ticket is 4.6 points under fair, and it is a same-game ticket, where E1 puts fills 2.5¢ over the product of mids. |
| **D2** | **Confirmed** | `apps/web/src/lib/betting/combo.ts:128` prints "Build manually: …"; `combo.py:369-382` sets the status. A not-eligible ticket still gets a product-of-legs cost as if one ticket existed. |
| **D3** | **Structure confirmed; size cannot tell yet** | Cross-game joint is the product by design (`docs/correlation.md`). 98.86% of legs are Kalshi-tier. E2's shortfall is one unreproduced study; M3a measures it. |
| **D4** | **Confirmed** | Per-leg cost over p, Kalshi-tier, eligible, not started (median (ask + fee) / p): under 10¢ 1.42; 10–25¢ 1.20; 25–50¢ 1.09; 50–75¢ 1.05; 75–90¢ 1.036; 90–97¢ 1.026. With spreads of 3¢ or less: 1.28, 1.12, 1.07, 1.04, 1.022, 1.014. |
| **D5** | **Confirmed** | 140 espn legs recomputed under power, multiplicative, Shin, additive. Positive edge: 15, 14, 7, 7. Positive under all four: 1. At 2¢ or more under any method: 0. Power-positive that change sign elsewhere: 14 of 15. The two claimed that day (UMKC at George Mason, both sides) read +0.33¢ under power and −0.4 to −0.6¢ under the other three. Method spread in p: median 0.67¢, p90 2.3¢, up to 2.7¢ when the favourite is 0.8 or more. DraftKings' median overround on these: 4.3%. |
| **D6** | **Confirmed** | 0.7 / 0.3 and power are owner decisions of Nov 9 (`docs/consensus.md`). `book_lines` starts 2026-11-09 (1,755 rows): no history to fit on. Forward test in §6, M4. |
| **D7** | **Confirmed in kind; the 58.6% figure not reproduced** | Per `docs/correlation.md`: NBA rows external, the rest priors or borrowed. Measured here: NBA main slices 0% unmodeled (5,089 of 5,089 pairs measured, cell B Lotto); any-sport tickets 41% to 76% of same-game pairs unmodeled. The 58.6% in the brief is over all NBA binary legs, periods included, and was not recomputed. |
| **D8** | **Mostly refuted** | PSD shrink below 1: 0 of 2,400 tickets. Monte Carlo relative SE on C and D Lottos: median 0.23–0.29%, max 0.55%, never needing 1M draws. It breaks only on tickets that should not exist: random Lottos (fair p median 2e-7; SE up to 71%; 1M draws on 26 of 200). Fair over naive on a C Lotto: median 1.000, p10 0.968, p90 1.037. So the copula moves a well-built Lotto by about ±3%, and unmodeled zeros are the larger gap. |
| **D9** | **Confirmed on our board** | Lotto, cost at product of asks plus one fee, over our fair: A +823%; B +924%; C +92%; D +25%. Mid: A +112%, B +158%, C +38%, D +14%. Safer: A +55%, B +72%, C +16%, D +7%. Leg-implied price under 1¢: 100% of A and B Lottos, 0% of C and D. A and B Lottos sit below the $0.0001 tick itself (median 1e-6 and 7e-6). D Lottos land at a median 7.7¢ with fair p 6.5% (about 15×). None of this includes the quoter's markup. |
| **D10** | **Cannot tell yet** | No generator exists. It is a rule to write, with standard errors clustered by game. |
| **D11** | **Confirmed by arithmetic** | To tell a 5% hit rate from 3.5% (a 30% cost) at 5% significance and 80% power takes about 1,500 tickets. |
| **D12** | **Confirmed** | §3. A combo can settle fractional; nothing voids. Phase 4's resolver must take proceeds as settlement value × contracts and record a fractional result as `partial`. `resolutions.outcome` already allows it. |
| **D13** | **Cannot tell yet** | The board keeps no history. One cross-section (Nov 12, 20:01 UTC), events combo-eligible by hours to start: Basketball 0–3 h 15/32, 3–6 h 198/344, 12–24 h 12/54, over 24 h 116/228; Hockey 3–6 h 63/172, 12–24 h 0/36; Football 3–6 h 48/92. Eligibility is not simply "near game time". M5 logs it forward. |

---

## 5. What the brief missed, and the ranking

Added findings:

- **N1. Spread width is the largest cost we control.** On legs priced
  0.75–0.90, the half-spread is a median 2.0¢ against a 1.0¢ fee. Limiting
  legs to spreads of 3¢ or less cuts a Lotto's cost over fair from +92% to
  +25% with the same price band (C vs D).
- **N2. The board was stale when read.** 5,640 of 5,705 Basketball
  Kalshi-tier legs were flagged `stale_kalshi` at the snapshot, because the
  refresh covers only events within 3 hours of start. Any ticket priced
  from stored rows off-hours is priced on old asks. Phase 4's re-quote
  before selection (owner, Nov 9) is required, not optional.
- **N3. One leg per event.** 2,824 eligible legs sit in events capped at
  one leg, and 270 are YES-only. The generator needs both as hard
  constraints, or tickets come back "build manually".
- **N4. A ticket under the tick cannot be priced fairly at all.** Below
  $0.0001 leg-implied, any quote is a multiple of fair.
- **N5. `combo_quoters` is empty everywhere.** Drop it as a signal.
- **N6. The brief's "20 real quotes" do not exist.** One real quote is on
  record. Quote-vs-fair starts from zero.

Ranking, by how much each moves a ticket's stated probability or cost:

| Rank | Finding | Size |
|---|---|---|
| 1 | Leg selection (D9, N1, N4) | Cost over fair from +7% to over +900% at fixed leg counts |
| 2 | No fill model (D1b) | Unknown; E1 says +1.1¢ to +2.5¢ per ticket before the maker fee, which is 15–30% of a 7.7¢ Lotto |
| 3 | Kalshi-tier p is the mid (D3, D4) | E2 suggests about 3¢ per 20¢ on cross-game YES picks; unmeasured by us |
| 4 | Fee per leg (D1a) | +0.7% to +28% of cost |
| 5 | Stale prices (N2) | Up to the full spread per leg |
| 6 | De-vig method (D5, D6) | 0.7¢ median, 2.7¢ worst, on 1.1% of legs |
| 7 | Rho coverage (D7) | About ±3% of a well-built Lotto's p; more on same-game Safer tickets |
| 8 | Copula numerics (D8) | Under 0.6% |

---

## 6. Measurement plan for Step 1

All of it runs on the owner's machine. Kalshi calls are keyless and paced
at about one a second. Decision rules are written here, before any number
exists.

**M1. Fill model.**
- Question: what does a combo actually fill at, against the product of its
  legs' mids at fill time?
- Data: settled combos closed before 2026-07-01 (J1, J5), their trades
  (`/historical/trades?ticker=`), 1-minute candles for each leg.
- Method: first reproduce E1's headline on April–June 2026. Then fill
  minus product of mids by leg count (2, 3–5, 6–10, 11–20), same-game vs
  cross-game, combo price band, sport.
- Sample: about 20,000 traded combos, sampled uniformly by day. Roughly
  200 list pages, 20,000 trade calls, 60,000 candle calls: about 22 hours
  of paced calls, split over several nights. Zero Odds API credits.
- Decision rule: if our equal-weight maker edge on E1's window is within
  ±1.0¢ of +3.3¢, E1 is reproduced and `expected_quote` is frozen as the
  product of mids plus the measured premium per cell, with its interval.
  If it is outside ±1.0¢, stop and report before using either number. A
  cell with fewer than 300 combos gets no number: it borrows the next
  shorter leg-count cell and is labelled borrowed.
- Not answerable inside the wall: the effect of the maker fee (J2).

**M2. Leg calibration map.**
- Question: do Kalshi-tier mids need a correction before they are
  multiplied?
- Data: settled singles before 2026-07-01, NBA and NHL (NFL under J3), by
  family: winner, spread rung, total rung, player prop rung. Pre-game mid
  from the last 1-minute candle at or before the scheduled start.
- Sample: about 30,000 markets, one candle call each: about 9 hours.
- Method: realized rate against mid in ten price bands; fit g(mid) on the
  first 70% of dates, test on the last 30%.
- Decision rule: adopt g for Kalshi-tier marginals only if it lowers
  out-of-sample log loss by at least 0.5% relative to the raw mid in the
  same family and sport, and the improvement's bootstrap 95% interval
  (clustered by game) excludes zero. Otherwise keep the mid and publish
  the bias table as a display-only warning.

**M3a. Cross-game joint.**
- Question: is E2's shortfall selection or dependence?
- Data: M1's combos, with leg outcomes joined from each leg's own market.
- Method: realized combo rate against the product of the legs' realized
  band rates from M2 (which removes leg-level bias), for combos with
  every leg in a different game.
- Decision rule: if the realized joint is within its 95% interval of that
  product, cross-game stays independent and the whole shortfall is
  credited to M2's map. If it is below, a single cross-game haircut per
  leg-count cell is added to `expected` hit probability and labelled as
  measured selection, not dependence.

**M3b. Same-game rho.**
- Data: NBA 2023–24 to 2025–26 and NHL the same seasons; NFL 2022–2024
  (2025 under J3). Free public box scores; no Kalshi calls.
- Method: latent-normal (tetrachoric) correlation at representative
  lines, pooled by relationship class, shrunk by n / (n + 200), a
  bootstrap interval on every row. Reproduce 20 of nba-hub's NBA rows
  first.
- Decision rule: a measured row replaces a prior only if n is 500 games
  or more **and** its 95% interval excludes the prior. Otherwise the prior
  stays and keeps its label. If fewer than 15 of the 20 nba-hub rows
  reproduce within ±0.05, the NBA table is re-measured from scratch and
  the external one retired.

**M4. De-vig robustness and the blend.**
- Data: `book_lines` (ours), growing daily. No calls.
- Method: method spread by lopsidedness and book (already 1,755 rows:
  median 0.05¢ when the favourite is 0.5–0.6, 2.6¢ at 0.9 and above).
- Decision rule, fixed now: **an edge is claimed only if it is positive
  under all four methods and at or above the margin under the least
  favourable one.** On Nov 12 that rule would have claimed nothing.
- The blend weight is a forward test, not a fit: log loss of books-median,
  Kalshi closing mid, and blends at 0.5, 0.7, 0.9, on legs committed in
  Phase 4, by sport and family. Stopping rule goes in the draft
  pre-registration: no weight is changed before 500 resolved legs in a
  cell.
- Pinnacle (J6): `eu` on NBA mainlines only is about +90 credits a month
  (about 430 of 450). Not affordable for props.

**M5. Ticket construction and timing.**
- Problem: there are no archived boards. The board is pruned.
- Data: a lean snapshot of eligible, not-started legs at 10:30 and 16:30
  Chicago for 14 days, taken by a research script through
  `valemont_readonly` (about 1.5 MB each, about 42 MB of egress in total).
  Each snapshot also logs eligibility by hours to start (D13).
- Method: simulate each tier under candidate rules: ask band, spread cap,
  one prop per player, maximum legs per game, modeled pairs only,
  eligible only, one leg per capped event. Output per rule set: cost =
  `expected_quote` + fee − fair p, hit probability, payout multiple, how
  often the tick binds.
- Decision rule: per tier, the rule set with the lowest median cost over
  fair, among those that can draw at least 5 distinct tickets on 12 of the
  14 days and whose leg-implied price is above 1¢ on 95% of tickets. Ties
  go to the set with the larger share of modeled pairs.

Totals: 0 Odds API credits for M1–M5 as planned (+90 a month only if J6 is
approved). About 110,000 Kalshi calls over several nights. About 42 MB of
Supabase egress.

---

## 7. Not verified

Recorded as pending, not as passed:

- The Bürgi–Deng–Whelan numbers, Kalshi's calibration paper's sports
  section, every E3 figure except the maker fee's existence, E5, and E8's
  repos.
- The combo maker fee's rate, and that the taker fee is one quadratic fee
  on the combo price, in a primary document. The first real fill settles
  the second.
- How a combo settles when a game is postponed.
- That `/historical/markets` can be walked back to April 2026 by cursor
  alone (only the first page of each series was read).
- Nothing in this file was re-read on 2026-11-13.

---

## 8. Owner decisions, 2026-11-13

- **J5: yes.** Per-series crawl first, everywhere it works.
- **J1: yes, as a fallback only.** The crawler reads `close_time`, discards
  a reserve-dated row before any other field is parsed, logs the count and
  writes nothing. A test proves a reserve row never reaches the parser.
- **J2: the before/after split is dropped.** The reserve stays closed. The
  combo maker fee (50% of the taker fee, on the combo price) is added to
  `expected_quote` as a stated lower bound, labelled an assumption. A
  forward collector of combo fill prices and leg mids is designed, not
  started: prices only, outcomes unread until a pre-registration.
- **J3: yes, fitting only.** 2025-season NFL combos (fill model) and box
  scores (rho) may be read as training data. Nothing from 2025 is cited as
  validation anywhere. Recorded in CLAUDE.md §8.
- **J4: a disclosed touch, not a read.** Recorded in CLAUDE.md. Probes
  filter by `close_time` before printing from now on.
- **J6: no.** The plan stays at about 340 credits. M4 still reports what a
  sharp anchor would change, with no spend.
- **ESPN-tier legs never claim edge, effective now.** The number is
  displayed and nothing is claimed. Only the books tier can claim, and only
  if it passes the four-method test (§6, M4).
- **Step 1 crawl:** resumable, adaptive pacing, stop and report if 429s
  pass 2%; raw rows to a gitignored local folder, never Supabase. A report
  after each study; a surprise stops the line. M1 and M2 start first.
- **§6 as amended by these decisions:** M1 has no before/after 2026-08-20
  cell. M4 spends nothing on Pinnacle.
