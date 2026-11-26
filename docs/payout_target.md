# "Bet $X to win $Y" (E1): build the slip for a target payout

Draft, 2026-11-25. Research: `docs/drafts/betting-research-2026-11-24.md` §E1. It is built on
the Props tab's slip (`docs/props_tab.md`). It is a tool the owner uses by hand. It is not an
agent and commits nothing, so it needs no pre-registration, and it places no bet (CLAUDE.md
§1). It reuses the Parlay Generator's rule checks and its correlated pricer, and never changes
the generator's stamped search (`consensus/parlay_ticket.py`).

## 1. Owner decisions (Nov 25)

| | |
|---|---|
| "Win $Y" | **The total paid back** if every leg hits. $10 to win $100 means 100 contracts at about 9.4¢ (plus a 0.6¢ fee). |
| Scope | **Anything on the board:** every sport and game, as the cross-sport Lotto does. |
| Unreachable | **Show the nearest reachable slip,** labelled ("Can't reach $1,000 on $10 here; the closest is $640"). |
| Cap | **Any multiple,** with a plain warning past 100x: the longshot corner, where bettors lose the most. |
| Kalshi button | Dropped. Kalshi has no link that pre-fills a combo (owner, Nov 25). |

## 2. The arithmetic

A Kalshi contract pays $1. Stake S at a combo price c, with one taker fee f(c) = 0.07 c (1 − c)
per contract, buys n = floor(S ÷ (c + f(c))) whole contracts, and pays back n dollars. So a
target payout Y sets the price: **c + f(c) ≈ S ÷ Y.** The search accepts a ticket priced at
that c or up to 10% under it, so **the payout is at least Y, and up to about 10% more.** Because
contracts are whole, the payout moves in $1 steps, and the answer shows the exact n.
- **Why not ±10% (Nov 25):** the first dry run on the real board, with a ±10% window, paid
  $91–93 on a $10 → $100 ask; tickets landed above the target price, which buys fewer
  contracts. With the one-sided window: $101, $106, $103.

## 3. The search (worker, on request)

**Rebuilt Nov 25 evening (owner: on $1 -> $1,000 "the slips aren't even trying").** The first
version reused the Parlay Generator's greedy pass. That pass built tickets from 95¢ filler legs
plus one 5¢ longshot, and it mixed legs from one game: one ticket could not hit at all (0%),
and one paired nested legs priced as their product (a fake +245%). It also kept the
generator's 1¢ tick floor, so it answered "nearest, $86–89". Kalshi quotes combos below 1¢, on
a $0.0001 grid (the RFQ guide). The search is now E1's own. It reuses the generator's
candidate order and pricer, and never changes the generator.

1. **Candidates:**
   - every binary side on the board whose event is combo-eligible and whose game has not
     started;
   - only with a Kalshi price read in the last 20 minutes;
   - "YES only" events take YES only;
   - each side carries its consensus p (`leg_consensus_for`, as everywhere).
   - Asks: 5¢–95¢ for a single bet, and 10¢–90¢ on a multi-leg ticket.
2. **Order:** the generator's (`pt.candidates`): ask ÷ the raw Kalshi side mid, cheapest to
   trade first. (Not consensus p per dollar: on Nov 20 that order selected on ESPN's
   single-book p sitting above Kalshi's ask.)
3. **One leg per game,** and one prop per player. Legs from different games are independent,
   so the product of their asks is an honest estimate of the combo's price, and the fair
   chance is the product of theirs.
4. **For each leg count k from 1 to 12, legs sized to the target:**
   - each next leg aims at what is left of the price, spread evenly over the legs left
     ((target ÷ price so far)^(1 ÷ legs left));
   - it is the cheapest to trade within ×/÷1.15 of that aim;
   - the last leg is the one that lands the price at the target or up to 10% under it (else
     the nearest).
5. **Each ticket is priced** by the correlated pricer (`price_ticket`).
6. **The answer is up to 3 tickets inside the window,** ranked by cost over fair (the best
   chance to hit for the price), fewer legs on a tie. None inside: the nearest-priced tickets,
   marked `nearest`.

**Dry run on the real board after the rebuild (read-only, Nov 25 evening, 1,177 fresh sides):**
- $1 -> $1,000: $1,095, $1,017, $1,026, with 8–11 legs of about 30–60¢. About 1 in 1,200;
  expected return −10.5% to −14.3%.
- $10 -> $100: $101, $101, $100. The 3-leg one is 52/48/37¢. About 1 in 11; −7.6% to −9.0%.
- $20 -> $50: a single bet at 38¢ paying $50, then 2- and 3-leg tickets.

Budget: at most 20 s from request to answer (the combo quote's bar), and at most 12 tickets
priced.

## 4. What the owner sees

On the Props tab, under the slip: **Build to a payout**, with Stake $ and Pays back $, then
**Build**. Each answer shows:
- the payout it reaches (n contracts) and the multiple;
- **the chance to hit in plain words:** fair p, and "about 1 in N";
- the expected return after fees: fair p × payout − stake, shown as a percentage of the stake;
- each leg in words, its price, its age, and its source chip;
- Kalshi combo status (fits, or the reason it won't);
- **Load into slip,** which replaces the slip's legs. The slip's Price it then re-quotes live.

Past 100x, a line above the answers: "This is the longshot corner. Tickets this cheap lose
the most, on average, of anything on Kalshi."

## 5. Storage and limits (db/094)

Following the combo quote's pattern (db/076, db/077):
- `payout_requests`: an owner app table. The owner inserts stake and payout; an insert queues
  one `job_queue` row `build_payout`; the worker settles each request once.
- `payout_answers`: one row per answered request, never changed.
- At most 20 requests in any 10 minutes. Requests are pruned after 7 days.
- The worker writes; the owner reads through RLS. `is_test` comes from an e2e marker, never
  mixed with real rows.

Each request and its suggestions are kept for the 7 days only. A suggestion is not a
commitment and is never scored.

## 6. Done when

- $10 → $100 on a live board answers in under 20 s with up to 3 tickets inside ±10%, each
  priced by the copula.
- An unreachable target answers with the nearest ticket, labelled. Past 100x, the warning.
- Load into slip fills the slip, and Price it quotes it.
- Unit tests for the arithmetic and the search, on fixtures; `tests_live` for db/094; the
  owner's e2e on the phone; the screenshot gate.
