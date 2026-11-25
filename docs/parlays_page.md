# Phase 4.5: the Parlays page, the brief line and Wags

Chat 8 Phase 4.5 (`docs/parlay_generator_plan.md` §4.5). Step 0 was two rounds
on 2026-11-24. The owner rejected round 1's dense cards and gave the round-2
brief: no cents, the multiplier, the leg count and the stake, player photos or
team logos, a live count like PrizePicks, tap a leg to open its Game Day page,
far less on screen. On round 2's four open questions the owner said "you decide
what's best" (Nov 24). Those calls are recorded in §1.

Read with `docs/preregistration_parlays.md` (P2.1). Nothing here changes a
rule, a price, a payload or a score. Every screen is read-only: nothing bets,
and nothing ever bets automatically (CLAUDE.md §1).

## 1. Decisions

Owner, Nov 24:
- Legs are named in words, never by ticker.
- The PAPER tag goes on every ticket, on the page header and on every record.
- Records are per agent only, never a blended number (CLAUDE.md §9.2).
- P2 (cohort 1) and P2.1 (cohort 2) are shown apart, never pooled.
- CLV (proxy) is always shown with its close ages and labelled "not profit".
- An empty slot shows its binding rule.
- No cents on the page. A ticket shows its multiplier, its leg count and the
  paper stake.
- Photos and logos where they can be had.
- A live count per leg, like PrizePicks (§4).
- A leg opens its game on Game Day.

Agent's calls, owner-delegated (Nov 24: "you decide what's best"):
1. **Stake shown:** the ledger's own paper stake, `capital_at_risk` (100
   contracts, fee included), to win $100: Safer about $41, Mid about $16,
   Lotto about $3. The multiplier is $100 divided by that stake.
   - Why: it is the number the record is built on, so the card, the record
     and the brief agree. A flat display stake would be a number no row
     holds.
2. **Pushes:** one when a ticket hits. No push when a leg busts.
   - Why: a busted Lotto is the expected case (the stated prior is that
     these tickets lose), so a bust push would be noise.
3. **Record headline:** R leads, as the stamped §8 requires. A parlay's
   invalidation is structural 0, so 1R is the stake, and R is worded "per $1
   staked" ("−$0.26 per $1 staked"). The paper dollar total stands beside it.
   Its error band is clustered by slate day.
4. **The selection log** (the frozen `leg_reasons`) is shown verbatim, cents
   included, inside Details, two taps deep. It is the record, and it is not
   rewritten for display.

## 2. Where things are

- `/betting/parlays`: the Parlays tab. It takes Today's place in the nav, and
  `/betting/today` redirects there.
- `/betting/record`: the Record tab (it was a placeholder).
- **The brief:** a `parlays_brief` provider (a brief section). Its lines are
  worded in SQL, the `gameday_brief` pattern, so the model copies the numbers
  and cannot blend them. The brief also gets a fallback template.
- **Wags:** a `parlays` provider (not a brief section).

## 3. The page

**Ticket card**

- The tier, the sport, PAPER, and a ring of legs cleared ("1/4"). After
  settlement the ring gives way to "Hit · paid $100" or "Missed".
- The multiplier, large; "$41.34 to win $100"; the leg count, then the state
  ("2 games live", "first game 5:30 PM", "+$58.66").
- One row per leg:
  - the player's ESPN photo, or the two teams' logos for a game line, or
    initials;
  - the name, and the prop in plain words (§5);
  - the live count with a bar and the game clock, or the start time, or
    ✓ / ✕.
  - Tapping the row opens `/gameday/<id>` when the leg's game is linked;
    otherwise the row is not a link.
- Lotto and Mid cards show 5 legs, then "+ N more legs".
- "Details" opens a sheet on the phone and a side panel on desktop.

**Details**
- The tier, sport and slot; the multiplier; the paper stake; the payout if
  every leg hits; the leg count; whether it fits as one Kalshi combo.
- "Copy legs to the slip": the legs go to the Board's slip, so the owner can
  build the ticket on Kalshi by hand.
- The pricing:
  - the model's chance that every leg hits (`fair_p`, as a percentage);
  - the price paid as a chance (`scored_price`);
  - what was paid over the model (the relative cost over fair);
  - the expected quote, "pending (M1)".
- CLV (proxy), marked not profit, with its close ages (median and maximum).
- The cohort and rule version, and the commit time.
- The selection log, verbatim (§1.4), loaded only when it is opened.

**The page**
- Order: Live now, Starts later, Settled. Filters: All, Safer, Mid, Lotto.
  "All" is a list, never a total.
- Day arrows. A past day shows that day's tickets.
- The cross-sport Lotto carries "extrapolated fill" and sorts after the
  per-sport Lottos.
- **Empty slots:** one line, "N empty slots today", which opens a sheet. In
  it, each slot, tier and sport with its binding rule as recorded; a refusal
  at commit with its reason; and the sports with no eligible market folded
  into one row.

**Record**
- One card per agent: Safer, Mid, Lotto per sport, and the cross-sport Lotto
  (extrapolated fill).
- A switch between cohort 1 (P2) and cohort 2 (P2.1).
- Each card shows:
  - R per $1 staked, ± its standard error clustered by slate day (the
    headline);
  - the paper dollars on the dollars staked;
  - the number settled and the number hit;
  - CLV (proxy), not profit, with its ages;
  - the empty slots by binding rule (sports with no eligible market not
    counted).
- "By sport" folds open the same figures per sport. No figure adds tiers or
  cohorts together.

## 4. The live count

**Source:** ESPN's public game summary, the path Game Day already uses
(`lib/gameday/espn.ts`).
- In Next's data cache for 15 s while a game is on.
- While a game with an unsettled leg is live, the page polls it every 30 s
  through a server action.
- Never stored.

**Matching a leg to a player**
- A prop leg is matched to an ESPN athlete by player name, with accents and
  punctuation removed, within the leg's team.
- Example: Kalshi writes "Egor Dëmin" and ESPN writes "Egor Demin".
- A leg with no match shows no count, and the build measures the match
  rate.

**Before tip-off:** the summary lists no players, so photos come from ESPN's
team roster (`/teams/<id>/roster`), cached for a day.

**The count**
- A YES "N+" prop clears the moment the count reaches N.
- A NO "N+" leg (shown "N−1 or fewer") busts the moment the count reaches N,
  and clears when the game is final.
- Game lines are counted from the score: the winner, the margin for a
  spread, the combined or team points for a total.
- A period line (first half, a quarter) is counted only when ESPN gives that
  period's line score. Otherwise the leg shows the clock alone.

**What is final:** the count is a snapshot, as PrizePicks says of its own. The
ticket's ✓ / ✕ and its outcome come from the resolution (Kalshi's settlement),
never from the count.

**Stats counted**
- NBA and college basketball: points, rebounds, assists, threes, steals,
  blocks, points+rebounds+assists, steals+blocks.
- NHL: goals, assists, points.
- NFL and college football: the box score's yards, receptions and
  touchdowns.
- Anything else shows the clock only.

## 5. Legs in plain words

One phrasebook (`apps/web/src/lib/betting/parlay-words.ts`), deterministic,
unit-tested.
- **Input:** each leg's display fields (§6). Without them it reads the frozen
  label.
- **Examples:**
  - YES "Jalen Suggs: 1+" threes → "Jalen Suggs · 1+ threes"
  - NO the same leg → "0 threes" (N−1 or fewer)
  - YES "Cleveland wins" → "Cleveland wins"
  - NO "Elon wins" in a sport with no draw → "Furman beats Elon"
  - NO "Tie wins" → "No draw"
  - a NO total → "under"
  - NO first goalscorer → "Not the first goal: <player>"
  - a period line names its period: "Orlando wins the 2nd half by 5+".
- **Fallback:** anything the phrasebook does not know is shown as Kalshi
  titles it, with the side ("No: …").

## 6. Data (db/091)

- **`parlay_leg_display`** (app table): one row per parlay leg's market. It
  holds the series, kind, player, stat, line, team, period, game ticker and
  start, and the linked Game Day id.
  - Written by `parlay_leg_display_capture()` (worker only) from the board
    while the board still holds the market: the board prunes 6 h after a
    game starts.
  - Called by the worker's `parlay_legs_capture` job every 15 minutes, and
    once in the migration for the legs still on the board.
  - Not a money table and not a ledger table: no `mode`, and no score reads
    it.
- **`parlay_hit_pushes`** (append-only): one row per ticket pushed as a hit,
  so a hit is pushed once.
- **Readers** (SECURITY INVOKER, owner reads by RLS):
  - `parlay_day(day, tests)`: the day's tickets as cards, plus the slot
    results (empty, refused);
  - `parlay_ticket(id, tests)`: Details, the selection log included;
  - `parlay_record(tests)`.
- **Context:** `ctx_parlays()` (Wags) and `ctx_parlays_brief()` (the brief),
  provider rows sort 52.
- **The e2e agent:** `_e2e_parlays` (`is_test`). Its tickets are written once
  by `scripts.seed_e2e_parlays` through `core/ledger.py`, never from the web
  (apps/web never writes a ledger table).

## 7. The brief line

The brief is written at 06:15 Chicago, before the 10:45 slot, so the line
reports yesterday's two slots and what settled since yesterday's brief.
- **Tickets:** "Parlays (paper), yesterday: Safer 6 tickets, Mid 6, Lotto 4
  plus 2 cross-sport. Settled: Safer 2 hit, 2 missed (−$41.50); Mid 1 hit, 3
  missed (+$52.70)."
- **No ticket:** "Parlays (paper): no ticket yesterday. Both slots ran and
  every tier was empty. Safer: window 3, unmodeled pair 2. Mid: window 4.
  Lotto: too few candidates 2."
- **No slot ran:** "Parlays (paper): no slot ran yesterday. The last was Sun
  Nov 22, 4:45 PM." It is loud on purpose: a silent generator is an outage.
- Each tier's figure is its own (CLAUDE.md §9.2): never a total across tiers.
- The provider returns NULL (no section) while every parlay agent is
  disabled.
