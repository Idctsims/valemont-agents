# Pre-registration: the Parlay Generator (`parlay_safer`, `parlay_mid`, `parlay_lotto`)

**Version 1, DRAFT for the owner's read. Not stamped.** Written 2026-11-19,
before any ticket exists. Amended the same day, still unstamped (owner): the
one-line reason per leg, and the cross-sport Lotto. Sources:
`docs/parlay_generator_step0.md` and the owner's decisions of Nov 19 on it; `docs/betting_science_audit.md` §4–§6 and
§8; CLAUDE.md §1, §2, §8–§10.

When the owner approves it, §1–§10 are DB-stamped one section at a time
(the `preregistrations` table, db/017's pattern), and the generator refuses to
commit unless every section's hash is stamped. The git commit that introduces
the approved text is its timestamp.

**The stated prior: these tickets lose money.** 98.9% of legs are Kalshi-tier,
where a ticket's expected value is minus its costs (audit D4), and three
outside builders lost on liquid game lines (CLAUDE.md §8). This is a
fill-cost and calibration record first. It is not a profitability test (§9).

---

## 0. Amendments

Any change to §1–§10 is a new version. It is stamped before it applies, and it
applies only to tickets committed after it. Tickets under two versions are
separate cohorts, reported apart and never pooled. A rule that turns out
ambiguous takes its stricter reading, and the reading is recorded. The one
change already scheduled is §7's switch.

## 1. Agents and money

- Three agents, one per tier: `parlay_safer` (3–5 legs), `parlay_mid` (6–10)
  and `parlay_lotto` (11–20). Each keeps its own track record. Sport is a
  field on every ticket, and so is `scope` (`sport` or `cross_sport`, §2).
- Results are never averaged, summed or blended across tiers (CLAUDE.md §9.2),
  nor across the two cohorts of §7, nor across scopes. The cross-sport Lotto
  is its own cohort.
- **Paper only.** Every commitment carries `mode = 'paper'`. Nothing places an
  order: Kalshi combos are never automated (CLAUDE.md §1). A ticket the owner
  builds by hand is a separate manual record and never changes these rows.

## 2. Schedule and volume

- Two slots a day: **10:45 and 16:45 America/Chicago**. That is after the
  consensus runs (10:15, 16:15) and M5's snapshots (10:30, 16:30).
- Per slot, each tier commits **at most one ticket per sport** (scope
  `sport`), and only when §3's rules can draw one. A sport is the board's
  sport tag of the legs' series. A per-sport ticket's legs share one sport.
- **The cross-sport Lotto.** Per slot, `parlay_lotto` also commits **at most
  one extra ticket whose legs span two or more sports** (scope
  `cross_sport`), under the same rules 1–11.
  - It carries **"extrapolated fill"** on the ticket and in the payload: the
    only cross-game fill sample is 2025 NFL (audit §9).
  - It is its own cohort in reporting, never pooled with the per-sport Lottos.
  - Safer and Mid have no cross-sport ticket.
- **Slate size.** A tier's slate in one slot is at most one ticket per sport,
  plus one for Lotto. The registry holds 18 sports (7 with combo-eligible
  events on Nov 19), so a slate is at most 19 and `max_slate_size` stays 25.
- A slot where a tier (or the cross-sport Lotto) draws nothing is recorded as
  an empty slot, with the reason (the rule that bound), never skipped
  silently.

## 3. Leg rules, version P1 (provisional)

A leg is one side (YES or NO) of one market. A candidate leg must meet all of
these:

1. **Universe.** A binary market (never scalar) whose game has not started, in
   a combo-eligible event. A YES-only event gives YES legs only.
2. **Fresh.** Its price is re-read from Kalshi in this slot, before selection,
   and is at most 20 minutes old when the ticket is committed. A leg that
   cannot be re-read is dropped, never priced from storage.
3. **Ask band.** The chosen side's ask is between 0.75 and 0.95, inclusive.
4. **Spread.** The market's YES ask minus its YES bid is 3¢ or less.

A ticket must meet all of these:

5. **Leg count:** within the tier's range.
6. **Event caps:** no more legs from one event than its `combo_size_max`. A
   recorded cap of 1 means one leg. An absent cap means no cap of its own,
   and rules 7–10 still apply.
7. **No refused pairs:** none of the pricer's refusals. Those are both sides
   of one market, two outcomes of one mutually exclusive event, rungs that
   cannot both hit, a stat beside a sum that contains it, and two legs of one
   first-scorer event.
8. **One prop per player.**
9. **Modeled pairs only.** Every same-game pair has a row in the rho table
   (measured, prior or borrowed). No pair is priced at an assumed 0.
10. **At most 3 legs from one game.**
11. **The tick floor.** The product of the legs' side asks is 1¢ or more. The
    owner accepts that this pushes Lotto toward fewer, safer legs until §7.

**Selection.** Candidates are ordered by ask ÷ consensus p, lowest first; ties
go by market ticker, then by side. The search is deterministic:

- For each leg count in the tier's range, the ticket is built greedily in that
  order, skipping any leg that breaks rules 5–11.
- Each complete ticket is priced (§4).
- The ticket with the **lowest relative cost over fair**, (scored price ÷ fair
  p) − 1, is committed.
- Ties go to the larger share of measured (not prior) pairs, then to fewer
  legs, then to the lexicographically first list of market tickers.

**The cross-sport Lotto's selection** uses the same order over every
sport's candidates pooled:

- For each leg count from 11 to 20, the ticket is built greedily as above.
- If the legs chosen all share one sport, the last leg added is replaced by
  the first remaining candidate, in order, from another sport that keeps
  rules 5–11.
- If there is none, that leg count yields no cross-sport ticket.
- The lowest relative cost over fair wins, with the same tie-breaks.

**The one-line reason per leg** (MASTER_PLAN §6, Chat 8, item 4) is built
from the leg's own data by a fixed template, with no AI call. It is
deterministic, so the same candidates give the same text:

> `<side> <market label>: <tier> consensus p <p, 3 places>, ask <ask>¢, ask÷p <ratio, 3 places>, ranked <n> of <N>; <k> ranked higher were not taken (<rule>: <count>, ...)`

- **`n` and `N`:** the leg's position in the slot's candidate order (§3)
  among the N candidates that passed rules 1–4 for this ticket's scope.
- **`k`:** the candidates ranked above it that the ticket does not hold.
- **The rule counts:** for each of those, the first of rules 5–11 it broke,
  counted by rule. On a cross-sport ticket, a leg removed by the one-sport
  replacement step counts as `replaced for a second sport`.

The reason is frozen in the payload at commit, like the prices.

## 4. Prices recorded on every ticket

All of these are frozen at commit, in the commitment's payload, with each
leg's fetch time:

| Field | Definition | Scored? |
|---|---|---|
| `scored_price` | the product of the legs' side asks at commit | **Yes: the entry price (§5)** |
| `taker_fee` | the Kalshi taker fee on `scored_price`, charged once on the combo price: 0.07 × P × (1 − P) per contract, the order's fee rounded up to the cent at the nominal size (§5) | **Yes: inside the risk** |
| `maker_fee_assumed` | 50% of the taker fee, a stated lower bound (audit J2) | No; shown, labelled an assumption |
| `fair_p` | the copula pricer's joint probability, with the rho table's version and sha256 | No |
| `naive_p` | the product of the legs' side mids (two-sided quotes) | No |
| `expected_quote` | the product of mids plus M1's measured premium for the ticket's cell, once M1 is frozen; until then absent, shown as "pending (M1)" | **Never scored** |
| `edge_legs` | legs that are books-tier, fresh, positive under all four de-vig methods and at or above 2¢ under the least favourable (audit M4) | No; a tag only |
| `combo_status` | eligible as one ticket, or "build manually" | No |
| `leg_reasons` | one line per leg, by §3's template | No |
| `scope` | `sport` or `cross_sport` (§2) | — (a reporting key) |
| `fill_note` | "extrapolated fill" on every cross-sport ticket; absent otherwise | No |
| `rule_version` | `P1` here; §7's switch makes the next one | — |

## 5. The commitment

- **Entry:** `scored_price`.
- **Size:** a nominal 100 contracts. The fee is computed on that order and
  rounded up to the cent, as Kalshi rounds.
- **Risk (§9):** `capital_at_risk = (scored_price + taker_fee per contract) ×
  100`. The invalidation is structural: 0.
- **Proceeds:** the ticket's settlement value × 100. A ticket settles to the
  product of its legs' settlement values. A leg settled at a fair price (did
  not play, protected) makes it fractional.
- **Outcome:** `hit` (value 1), `miss` (0), `partial` (strictly between).
- **pnl:** (proceeds − capital_at_risk) / capital_at_risk. A miss is −1.0
  exactly.
- **Nothing voids on Kalshi.** A postponed game settles too.
- **`resolves_after`:** the latest leg's expected expiration, from the API.
- **`max_overdue`:** 10 days, set in the Registration, never the default
  (§9.3). Past it, core's give-up applies as written.
- **Commit-before-outcome:** a ticket is committed only if every leg's game
  starts after the commit, by the database clock. Nothing in it is edited
  afterwards (CLAUDE.md §2).

## 6. The CLV proxy

The combo has no observable closing price on paper. **The CLV proxy**,
labelled "CLV (proxy)" everywhere it appears, works as follows:

- **The close:** each leg's two-sided mid (0 < bid ≤ ask < 1) in the last
  1-minute candle at or before that leg's scheduled kickoff, taken for the
  leg's side. The product of those is the ticket's close proxy.
- **Entry for the proxy:** `naive_p` at commit (mids against mids).
- **The values:** `clv = close proxy − naive_p`; `clv_pct = clv / naive_p`.
- **`closes_at`:** the latest leg's scheduled kickoff, from the sport's
  schedule source, as of commit.
- **A missing input:** if any leg lacks a two-sided candle in its last 5
  minutes before kickoff, the proxy is not computed. A `missed` close is
  recorded with the leg named.

The proxy is not a profit measure, and CLV is not a profit measure either
(CLAUDE.md §8, §10).

## 7. The switch to M5's rules

- **What M5 picks:** M5 (audit §6, frozen) snapshots the board for 14 days
  from the day db/084 deploys. Its rule picks, per tier, the rule set with the
  lowest median cost over fair among those that draw at least 5 distinct
  tickets on 12 of the 14 days and stay at or above 1¢ on 95% of tickets.
- **When:** the generator switches to M5's chosen rule sets at the **first
  slot after the owner accepts the M5 report**. If it is not accepted, P1
  stays.
- **How:** the switch is version 2 of this file. §3 is replaced by the chosen
  sets, and the M5 output's sha256 is quoted in it. It is stamped before its
  first slot.
- **No pooling:** P1 tickets and version-2 tickets are separate cohorts,
  reported apart and never pooled.
- **Nothing else changes a rule before the switch.**

## 8. Reporting

- **The headline,** per agent and per sport (the cross-sport Lotto as its own
  line), separately per cohort: the §9
  R-multiple net of fees, with standard errors clustered by slate day (a
  ticket spans games; one day's slates share games).
- **Leading measures:** relative cost over fair at commit, and the CLV proxy
  (labelled).
- **Hit rate:** reported, never as the headline.
- **Health:** the empty-slot rate by binding rule, the missed-close rate and
  the void rate. A rise in either of the last two is an outage (CLAUDE.md §8).
- **The chief of staff** reports per agent only. No figure combines tiers.

## 9. What these tickets cannot claim

- **No profitability claim.** Telling a 5% hit rate from 3.5% takes about
  1,500 Lottos (audit D11), which is years at two slots a day. A positive CLV
  proxy is not evidence of profit.
- **No rule changes after the switch** before 500 resolved legs in a
  tier-by-sport cell (audit §6, M4's stopping rule, applied here).
- **Any profitability claim** needs its own pre-registration, on data not used
  to build anything.

## 10. Data rules

- The 2026 NFL props archive is never read.
- Nothing dated in the reserve (2026-07-01 onward on Kalshi's settled
  history) is read for fitting.
- 2025 NFL data is fitting-only (J3). The rho table's priors are labelled as
  such on every ticket.
- Settlement for scoring is read only after a leg's market has settled. Prices
  for commitment are read only before its game starts.
