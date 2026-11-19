# Phase 4 Step 0: the Parlay Generator, on paper from day one

Chat 8 Phase 4. Written 2026-11-19. **No code.** For the owner's OK. Sources:
`docs/betting_science_audit.md` (§4 D1–D13, §5 N1–N6, §6 M1–M5 frozen, §8),
`docs/research/` (M4 and M3b of Nov 19, the M5 build), CLAUDE.md §1, §2, §8–§10.

## 1. What it is

Twice a day the generator builds Safer (3–5 legs), Mid (6–10) and Lotto
(11–20, props included, every sport) tickets from the Kalshi board and
**commits each one to the ledger as a paper commitment before any leg's game
starts**. It never places an order: Kalshi combos are never automated
(CLAUDE.md §1). The owner may build a ticket by hand in the Kalshi app and log
the fill; that is a separate, manual record.

Every ticket shows the cost of the ticket and how likely it is to hit. It does
not show an "edge" headline: 98.9% of legs are Kalshi-tier, where a ticket's
expected value is exactly minus its costs (D4).

## 2. Provisional leg rules, written before any ticket exists

These are the rules on day one. They are frozen in the pre-registration
(§6) with a version number, and every commitment carries that version.

**Every tier:**

1. **The leg universe.** Binary markets only (scalar never), not started,
   in a combo-eligible event. YES-only events take YES only. An event with
   a leg cap (`combo_size_max`) gives at most that many legs; a cap of 1
   gives one (N3).
2. **Fresh prices.** Every candidate leg is re-quoted from Kalshi before
   selection, and again at commit (owner, Nov 9). A leg whose read is older
   than 20 minutes is dropped, not priced (N2).
3. **Ask band and spread.** The chosen side's ask is between 0.75 and 0.95,
   and the leg's spread is 3¢ or less: the D cell, the cheapest of the four
   tested (D9, N1).
4. **No refused pairs.** The pricer's refusals stand: both sides of one
   market, two outcomes of one exclusive event, rungs that cannot both hit,
   a stat beside a sum that contains it, and two legs of one first-scorer
   event.
5. **One prop per player.**
6. **Modeled pairs only.** No same-game pair without a rho row (measured or
   prior). An unmodeled pair is priced at rho 0, and 47–76% of same-game
   pairs on any-sport tickets were unmodeled on Nov 12 (D7).
7. **At most 3 legs from one game** (provisional; M5 decides).
8. **The tick floor.** The leg-implied price, the product of the side asks,
   is 1¢ or more (N4: under the tick, every quote is a multiple of fair).
9. **The objective.** Among the tickets these rules allow, take the lowest
   cost over fair: `expected_quote` + one taker fee − fair p. Ties go to
   more modeled pairs, then to more measured (non-prior) pairs.

**Per tier:** the leg counts above, and one ticket per tier per sport per
slot, only when the rules can draw one. A day with no legal Lotto has no
Lotto, recorded as such.

**Edge legs (M4's rule, binding on the label only):** a leg is called an edge
leg only if it is books-tier, both prices are fresh, its edge is positive
under all four de-vig methods, and it is at or above 2¢ under the least
favourable. It is a tag on the ticket, never a reason to pick a leg. On Nov 19
that rule passed 0 of 37 books-tier legs, and 0 of 1,096 consensus lines lose
2¢ or more to the method alone, so expect almost none.

## 3. Prices on every ticket

| Shown | Source | Until it exists |
|---|---|---|
| Fair p | the copula pricer (the rho table, 2026-11-19.1) | always present |
| Naive p | the product of the legs' side mids | always present |
| `expected_quote` | the product of mids plus M1's measured premium for the ticket's cell, with its interval | **an empty-state slot, "fill estimate pending (M1)"**, until M1 reproduces E1 and the owner freezes the premiums |
| Taker fee | once, on the combo price: 0.07 × P × (1 − P). Not per leg (D1a) | always present |
| Maker fee | 50% of the taker fee, a stated lower bound labelled an assumption (J2) | always present |
| Cost over fair | `expected_quote` + fees − fair p | blank while `expected_quote` is |
| Combo status | eligible, or "build manually" (D2) | always present |

A cross-game ticket outside 2025 NFL carries "extrapolated fill" until data
exists (§9 of the audit).

## 4. The paper commitment

- **Agents:** one per tier: `parlay_safer`, `parlay_mid`, `parlay_lotto`.
  Each has its own track record, and sport is a field on every ticket. The
  three are never averaged together (§9.2).
- **Slate:** one tick commits that tier's tickets for the slot as a slate
  (`max_slate_size` stays 25: at most one per sport).
- **Entry price, as of commit (§2):** each leg's bid and ask read at commit,
  stored in the payload with fetch times. The declared paper fill is chosen in
  the pre-registration, before the first ticket (owner's call, §7, item 1).
- **Risk (§9):** `capital_at_risk = (price + fee) × contracts`, with a
  structural stop at 0, so a lost ticket scores −1.0.
- **Settlement:** a ticket settles to the product of its legs' settlement
  values. A leg that settles at a fair price (DNP, protected) makes it
  fractional, which is recorded as `partial` with proceeds = value ×
  contracts (D12). Nothing voids on Kalshi; a postponed game settles too.
- **Timing:** `resolves_after` is the latest leg's expected expiration.
  `max_overdue` is set deliberately at 10 days, longer than a postponement
  plus settlement. It never takes the default (§9.3).
- **`closes_at` and CLV:** a combo has no observable closing price on paper.
  The proposal: snapshot each leg's two-sided mid at its own kickoff, and take
  their product as the ticket's close (a proxy, labelled so). Owner's call
  (§7, item 3).
- **Mode:** `paper` on every row; nothing here is eligible for live.

## 5. The switch to M5's rules at day 14

M5 snapshots the board for 14 days from the day db/084 deploys. Its frozen
rule picks, per tier, the rule set with the lowest median cost over fair
among those that draw 5+ tickets on 12 of the 14 days and stay above 1¢ on
95% of tickets.

**Pre-registered now, before any ticket exists:**

- **When:** the generator switches to M5's chosen rule sets at the first slot
  after the owner accepts the M5 report. If the report is not accepted, the
  provisional rules stay. Nothing else changes a rule before then.
- **How it is recorded:** the switch is a new rule version. The M5 output's
  hash and the switch slot go into the pre-registration's stamped addendum.
- **No pooling:** tickets under the provisional rules and under M5's are
  separate cohorts, reported apart, never pooled.
- **After the switch:** no rule changes before 500 resolved legs in a cell
  (§6 M4's stopping rule, applied to the generator too).

## 6. Pre-registration and scoring

- **Pre-registration:** `docs/preregistration_parlays.md`. It holds the
  rules in §2, the price definitions in §3, the paper fill, the switch in
  §5, the metrics and the stopping rule. It is DB-stamped (db/017's pattern)
  before the first commitment, and the generator refuses to commit unless it
  is stamped.
- **Scoring:** per tier and per sport, the §9 R-multiple net of fees, with
  standard errors clustered by game. Hit rate is shown, never as the
  headline. Cost over fair at commit is the leading measure.
- **No profitability claim from these tickets.** Telling a 5% hit rate from
  3.5% takes about 1,500 Lottos (D11); that is years at this cadence. The
  record is a fill-cost and calibration record first.

## 7. Decisions for the owner before any code

1. **The paper fill.** (a) The product of the legs' asks plus one taker fee:
   conservative and observable today. Or (b) `expected_quote` once M1 is
   frozen, with (a) recorded beside it until then. The recommendation is (a)
   as the scored price from day one, with (b) shown beside it.
2. **Agents.** One per tier, with sport as a field (the recommendation), or
   one per tier × sport (up to about 15 records, most of them thin).
3. **The CLV proxy** in §4, or no CLV for parlays (hit rate and cost only).
4. **The slots.** 10:45 and 16:45 Chicago is the proposal: after the
   consensus runs (10:15, 16:15) and M5's snapshots (10:30, 16:30).
5. **The provisional numbers** in §2: the 0.75–0.95 ask band, the 3¢ spread
   cap, 3 legs per game and the 1¢ floor, all taken from the D cell of D9,
   and "modeled pairs only".

Not in Phase 4: live money, RFQ quotes (no key), Pinnacle (J6: no), and any
use of the 2026 NFL props archive.
