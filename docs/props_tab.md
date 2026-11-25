# The Props tab (E2), with "bet $X to win $Y" (E1) after it

Draft, 2026-11-25. Owner decisions are in `docs/drafts/betting-research-2026-11-24.md` §E.
Nothing here places a bet (CLAUDE.md §1). It is a display tab, not an agent, so it needs no
pre-registration. If it ever commits singles, that needs one first (§8).

## 1. What it is

A tab between Board and Parlays: **Board · Props · Parlays · Record**. It lists single
markets you can bet, YES and NO sides, from every game on the board, sorted into three tiers
by the price of the side, and each one can be added to the slip. The slip that already exists
(`components/betting/slip.tsx`) prices the slip as one combo. It already keeps legs across
games and sports, enforces the refusals and per-event leg limits, and shows the correlated
fair price beside the naive one. So "compile to a parlay" is the existing slip, reached from a
new list.

## 2. Owner decisions (Nov 25)

| | |
|---|---|
| Tiers, by the side's ask | Safer 60–85¢ (about 1.15x–1.6x), Mid 30–60¢ (1.6x–3.2x), Lotto 5–30¢ (3.2x–18x). Under 5¢ hidden by default, behind a toggle. Over 85¢: not listed. |
| Markets | Every binary single, with a **Props / Lines** filter. Scalar markets are never legs (db/072). |
| Slips | Any games, any sports. |
| Sort | Edge after fees, with **multiplier** as a toggle. |

**Sort, as built (owner: "you know what's best", Nov 25).** On the Nov 25 board, about
35,000 sides fell inside the bands, and only about 280 markets had an outside price (books or
ESPN). Only 18 of those were props, all NFL. For every other side, fair p is Kalshi's own mid,
so its "edge" is minus half the spread minus the fee: a cost, not a forecast. So within a tier:
1. **Sides with an outside price first,** by edge after the fee, with their tier chip. Only
   these can ever show a claimed edge, and only on fresh prices, as everywhere (db/074, db/080).
2. **Then every other side, by cost to trade,** labelled "cheapest to trade", never "best".

The multiplier toggle sorts the whole tier by payout instead.

## 3. Each row

- Who or what: the player and stat, or the game line in words. Use `legWords`, the Parlays
  page's labels: never a ticker.
- The game and its start; the side (YES / NO, worded as an outcome).
- **Multiplier at the taker ask, fee included** (payout ÷ cost of one contract). It respects
  the x / % unit choice (`betting-store`).
- Fair p and edge after the fee, with the tier chip (books / espn / kalshi). On kalshi-tier
  rows the edge is shown as the cost to trade, never as a claim.
- The price's age; a stale tag past 20 minutes.
- Combo eligibility: "YES only in a combo", or not combo-eligible (it can still be bet
  alone).
- **+ Slip**, which calls the store's `put`, the same call the game page makes.

## 4. Filters

- **Sport chips**, reusing the Board's chips.
- **Props / Lines.**
- **Tier:** the same full-width segmented bar as the Parlays page (`Segmented`; move it to
  `components/betting/` so both pages share it), with counts.
- **Search:** a player, team or stat ("Curry", "rebounds"). On a busy slate this is how
  anyone finds a leg among thousands.
- **Starts within:** next 3 h / tonight / 36 h (the board's window).

Each tier shows the top 50; "more" pages on. The filters ride in the URL, as on Parlays.

## 5. Data: read on demand, or a worker table (Step 0 decides)

The page cannot call `leg_consensus_for` across the board. A full read takes 6–20 s on
production, and the signed-in role stops at 8 s (Nov 14). Two shapes:

- **(a) A reader per request:** `kalshi_singles(sport, kind, tier, q, sort, offset, tests)`,
  which prices only the rows it returns. Kalshi-tier rows need no outside lookup: their fair
  p is the event-normalized mid, which is the expensive part. Outside rows come from
  `leg_consensus`, about 280 rows.
- **(b) A worker table** rewritten each board refresh, which the page only reads. That adds
  storage (about 35k rows; the database grows ~1.4 MiB/day and is at ~302 MiB) and churn.

**Step 0:** build (a) on a local copy of a busy board and time it per §5's rule (three runs,
the busiest sport and tier, before and after). Ship (a) if a page of 50 rows answers in under
500 ms warm. Otherwise build (b). Measure the payload too: aim for under 30 KB a page.

### Step 0 result (2026-11-25): shape (a), no worker table

A prototype `kalshi_singles(sport, kind, tier, q, sort, offset, limit, tests)` was tested on a
local PG 17 copy of the Nov 25 board (29,656 markets, 3,094 events, 300 `leg_consensus` rows),
JIT off. It prices inline: the event-normalized mid, the 0.7 / 0.3 blend, and the taker fee
formula from `kalshi_taker_fee`. It makes no per-row function call.
- **Timing, warm, one page of 50:** Basketball props, Mid tier 101–111 ms; Basketball lines,
  Safer 153–158 ms; a search 15–31 ms. Production has run about 2.7× the local copy (Nov 14),
  so expect about 300–430 ms: inside the 500 ms bar. Time it on production before the paste
  (§5).
- **Payload:** about 13 KB for 50 rows.
- **Agreement:** every side of Basketball, Football, Hockey and Soccer (32,930 sides, 505 with
  an outside price) has the same p, edge and tier as `leg_consensus_for`. 0 differ.
- **Fix for the real version:** apply the Props / Lines filter after the normalization window,
  never before, so an event's mids always sum over the whole event.
- **db/093 as written (production, the body run read-only, warm, three runs; the first after
  idle about 2 s):** Basketball props, Mid 300–345 ms; Basketball lines, Safer 400–430 ms;
  Football props, Lotto 180–195 ms; all Basketball, Lotto by multiplier 635–670 ms.
  Production ran 3–4× the local copy. Two changes got there from 440 / 620 / 840–940 ms: the
  materialized side table carries only numbers (a wide one spilled to disk), and the fee, edge
  and multiplier are computed for the asked tier only. Self-test; **23 mutations, 23 red.**
- **Decision (Nov 25):** the tab opens on **Props** (about 300 ms). "Both kinds, by multiplier"
  is the slowest view, at about 0.65 s: over the 500 ms bar, well inside the 8 s limit. The
  worker table (b) is the fallback if that view turns out to be a common one.
- **Labels:** the board's `label` is short for props ("Trey Lyles: 2+"). The page words a row
  from subject, stat and line (`legWords`), so the reader returns those fields.

## 6. Then E1: "bet $X to win $Y"

It sits on top of the same slip. Stake S and target payout Y set a target combo price, about
S ÷ Y less the fee. The search is the generator's window search, centred on that price instead
of a tier window. It ranks tickets by fair p ÷ price: the highest chance to hit for that
payout. The answer is shown with the chance in words ("about 1 in 11") and the expected return
after fees, and it loads into the slip for a re-quote. Its three open questions (scope,
unreachable targets, a cap on the multiple) are asked when E2 ships.

## 7. Done when

- The tab lists every in-band side for a sport, filtered, sorted as in §2, 50 at a time.
- Adding legs from several games and sports to the slip prices them as one combo, using the
  existing pricer and refusals.
- Under 500 ms warm per page, at most 30 KB a page.
- Unit tests for tiering, sorting and labels; `tests_live` for the reader against the raw
  board; owner e2e on the phone; the screenshot gate, phone and desktop, both themes.
