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

1. **Candidates:**
   - every binary side on the board whose event is combo-eligible and whose game has not
     started;
   - only with a Kalshi price read in the last 20 minutes, and an ask between 5¢ and 95¢;
   - "YES only" events take YES only;
   - each side carries its consensus p (`leg_consensus_for`, as everywhere).
2. **Order:** the generator's order (`pt.candidates`): ask ÷ the raw Kalshi side mid,
   cheapest to trade first.
   - **Changed from this draft's "p ÷ (ask + fee)" (Nov 25):** on Nov 20, ordering by
     consensus p selected on ESPN's single-book p sitting above Kalshi's ask, an artifact,
     not value (owner-approved then for the generator).
   - The correlated fair chance still chooses among complete tickets (step 5).
3. **For each leg count k from 1 to 12:**
   - a greedy pass in that order keeps the generator's rules (`first_broken`): event cap,
     refused pairs, one prop per player, no unmodeled same-game pair, at most 3 legs from
     one game;
   - a leg is skipped when, once added, the remaining legs could no longer bring the price
     into the window (`reachable`, the generator's own check);
   - k = 1 is a single bet: when one side alone is priced near the target, no parlay is
     needed, and the answer says so.
4. **Each complete ticket is priced** by the correlated pricer (`price_ticket`: copula within
   a game, independent across games).
5. **The answer is up to 3 tickets,** ranked by fair p ÷ price, the highest chance to hit for
   that payout. Ties go to fewer legs.
6. **Nothing lands in the window:** the window widens both ways in steps (±25%, ±50%, ×2,
   ×4, ×10), and the answer is the nearest payout it can build, labelled.
   - A combo can't be priced under 1¢ (the generator's tick floor), so about 99x and up is
     always "nearest". The Nov 25 dry run: $5 → $500 answered $430–435.

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
