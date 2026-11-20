# Phase 4 build plan: the Parlay Generator (paper)

Written 2026-11-19, for the owner's OK. **No code until then.** It builds what
`docs/preregistration_parlays.md` (v1, draft) specifies and nothing more. Each
phase is its own branch and PR, merged by the owner with a merge commit. Each
closes on its own done-when list, with readings taken from the database and
the logs (CLAUDE.md §6: only what was verified counts).

The structure follows CLAUDE.md §3:
- **One adapter:** `adapters/parlays.py`, with the four hooks plus
  `capture_close` for the CLV proxy.
- **Shared logic:** the leg rules and the search live in `consensus/` (no SQL).
- **Writes:** every database write goes through `core/ledger.py`.
- **Registration:** three `Registration`s in the orchestrator, one per tier.

## Before Phase 4.1: owner decisions

1. **How the three agents run: APPROVED (owner, 2026-11-19).** Agents run
   only under `ROSTER=production`, and each also needs `agents.enabled =
   true` (two-gated, CLAUDE.md "Deploy").
   - The three rows are added **disabled** in db/085.
   - They are enabled by their own migration, and `ROSTER=production` is set
     on Railway by the owner, when 4.4 closes.
   - `nfl_ml` and `_kalshi_probe` stay off, and db/013 stays unpasted.
   - This is not the `nfl_ml` go-live, and it moves no money.
2. **Approval of the pre-registration text.** Wording changes go in before
   the stamp, never after it. Approved choices (owner, Nov 19): the relative
   cost over fair as the selection measure, and a nominal 100 contracts.

## 4.1: the stamp and the agents (db/085)

- db/085:
  - stamps the pre-registration's §0–§10 (`preregistrations`, one row per
    section);
  - adds the three agent rows (`parlay_safer`, `parlay_mid`, `parlay_lotto`),
    **disabled**;
  - carries a rolled-back self-test.
- `tests/test_preregistration.py` gains the file: an edited registered text
  fails the suite.

**Done when:**
- [ ] db/085 pasted; its `migration_log` row read; the 11 hashes on
      production equal the file's.
- [ ] The three agents exist, disabled, `is_test = false`.
- [ ] `tests/` green, with the new pre-registration test red on a deliberate
      one-character edit (then reverted).

## 4.2: the leg universe and the re-quote

- **Candidates** for a slot come from the board through one ledger reader:
  binary, not started, combo-eligible, with tier, consensus p, team, stat and
  the event's cap, YES-only and exclusive flags.
- **Fresh prices:** each candidate event is re-read from Kalshi with the
  existing client and pacer. A leg older than 20 minutes at selection is
  dropped.
- **Rules:** P1's rules 1–4 as pure functions over a candidate, each named,
  so an empty slot can say which rule bound.

**Done when:**
- [ ] Unit tests for every rule (each boundary inclusive or exclusive as
      written).
- [ ] A dry run on the owner's machine against the live board, writing
      nothing, prints per sport the candidates before and after each rule and
      the Kalshi calls, with 0 429s, or a 429 share reported if above 2%.
- [ ] The re-quote of one Sunday slot takes under 300 s with fewer than 300
      calls, or the figures are reported.

## 4.3: the ticket and its prices

- **The search:** the deterministic search in §3 (ticket rules 5–11), and the
  copula `fair_p` with the rho table's version and hash.
- **Prices:** `naive_p`; `scored_price` with the one taker fee at the nominal
  100 contracts; the maker-fee assumption; edge-leg tags (the M4 rule).
- **`expected_quote`:** an empty slot until M1 is frozen.
- **The cross-sport Lotto:** the search over the pooled candidates with the
  one-sport replacement step, `scope = cross_sport` and "extrapolated fill".
- **The one-line reason per leg:** the pre-registration's template, from the
  leg's own data; no AI call.
- **Pure, with no SQL:** given the same candidates, it builds the same ticket
  and the same reasons.
- **p is recomputed from the fresh quote before ordering and pricing (owner,
  2026-11-20).** This is a reading of the stamped rule, not a change to it: the
  §0 stricter reading of the Nov 9 decision ("re-quote and recompute before
  selecting").
  - **Found by the first dry run (Nov 20):** every kalshi-tier p was the board's
    mid from the 09:30 full pull, about 2.5 h old (the refresh only covers games
    within 3 h). So ask ÷ p ranked legs by how stale their board price was, and
    `fair_p` repeated that gap: relative costs ran to −58%.
  - **The fix:** `consensus/parlay_legs.with_fresh_p` applies the engine's own
    rules to the slot's fresh mids:
    - kalshi tier: the mid, normalized across a mutually exclusive event
      (0.95–1.05 band; ladder rungs plain);
    - books and espn: 0.7 × outside + 0.3 × that mid.
  - **The engine's 12 h outside flag** blocks a claim, never the blend, and
    the same holds here.
  - **One formula.** The engine is SQL (`consensus_leg`, `leg_consensus_priced`);
    the Python twin is held equal to it on real board rows by
    `tests_live/test_parlay_candidates_sql.FreshTwin` (Nov 20: 1,061 markets in
    5 games, all three tiers and 9 exclusive events, 0 differences).
- **Legs named in words (owner, Nov 20):** the Kalshi title, the stat for a
  prop, "wins" for a winner market, and the game's teams
  (`kalshi_board_events.game_title`). The ticker stays the key.

**Done when:**
- [ ] Unit tests: each ticket rule; the tie-breaks; the tick floor; the fee
      rounding; the same input gives the same ticket; the same input gives
      the same reason text, byte for byte; the cross-sport replacement step
      (all one sport, then replaced; none to replace with, then no ticket);
      a cross-sport ticket always carries "extrapolated fill".
- [ ] A dry run of two real slots (no commit) prints, per tier and sport, the
      ticket or the empty slot with its binding rule, the prices, the
      relative cost over fair, the leg reasons and the time per tier. The
      cross-sport Lotto is reported on its own line. The owner reviews it.
- [ ] Lotto at P1: the share of slots that draw one is reported. The 1¢ floor
      is expected to bind; the number is recorded, not tuned.

## 4.4: the paper commitment, resolution and the CLV proxy

- **`adapters/parlays.py`:**
  - `observe`: candidates plus the re-quote;
  - `form_thesis`: the search;
  - `build_commitment`: a slate, at most one ticket per sport, plus Lotto's
    one cross-sport ticket (at most 19; `max_slate_size` stays 25);
  - `resolve`: the product of the legs' settlement values, so `hit`, `miss`
    or `partial`;
  - `capture_close`: the CLV proxy from each leg's last candle before its
    kickoff.
- **The Registration:** `max_overdue` 10 days, set explicitly.
- **The commit:** the payload holds §4's fields and every leg's fetch times,
  and `closes_at` is the latest leg's kickoff, from the schedule source.
- **The refusals:** it refuses to commit unless the pre-registration is
  stamped, and refuses any leg whose game has started by the database clock.
- **No new money table is planned.** Of the ledger tables, only
  `commitments` carries `mode` (db/020; read Nov 19). Legs, resolutions and
  closing snapshots take it from their commitment. If a new money table
  becomes necessary, it gets its own migration, with `mode`.

### The slot's re-quote and the board refresh (owner, 2026-11-20; design, built in 4.4)

Measured Nov 20 (weekday board, owner's machine): the strict re-quote of
every eligible event is ~2,040 calls and 546 s at a 0.10 s floor (723 s at
0.3 s). The 8-minute target is **retired**. The rule that counts is the
stamped one: every leg at most 20 minutes old **at commit** (§3 rule 2).

- **Times.** The stamped commit times stay **10:45 and 16:45 Chicago**. The
  slot's re-quote starts at **:33** (10:33, 16:33): a cron job, `parlay_requote`,
  never at boot, misfire grace 5 minutes (owner, Nov 20): a re-quote that
  cannot start by :38 leaves the slot recorded empty with the binding rule
  `fresh`, rather than re-quoting late.
- **One re-quote per slot, shared by the three tiers.** `parlay_requote`
  reads the candidates and re-quotes once, latest start first, so the
  earliest games are read last. It keeps the slot's quotes in worker memory,
  keyed by slot. Each tier's `observe` at :45 takes that slot's quotes and
  never calls Kalshi itself. Three tiers re-quoting would triple the calls.
  Rule 2 is checked against each quote's own fetch time at the
  commit, by the database clock. A leg read at :33 is ~12 minutes old at :45.
- **The refresh yields.** A process-wide flag (`venues/kalshi` module level,
  a `threading.Event` set for the whole re-quote, cleared in `finally`):
  - **When the refresh is due during a re-quote,** `kalshi_board_refresh`
    raises `JobSkipped("slot re-quote running")`. `core/jobs.run_tracked`
    already records that as a success with the note `skipped: slot re-quote
    running`: no failure counted, no alert, and the staleness clock is reset
    (it counts from the last success). Nothing in `job_health` changes.
  - **When the re-quote starts during a refresh run** (the refresh runs on a
    15-minute interval from boot, not on the clock, so this will happen), the
    refresh checks the flag before each event and stops early. It keeps what
    it has written and records the same skip note with how many events it
    had done. The re-quote does not wait for it.
  - **Not a database lock.** One worker runs both jobs. During a redeploy two
    workers can overlap for well under a minute; at worst one refresh run
    overlaps one re-quote, which costs pacing, not correctness.
- **Pacing.** The re-quote keeps its own floor (0.10 s, AIMD unchanged); the
  refresh keeps `PACE_FLOOR` 0.3 s. With the refresh yielding, the re-quote
  is the only Kalshi traffic from the worker in its window. The full pulls
  (09:30, 15:30) end about an hour before. The worker's own answer times from
  Railway are unmeasured; 4.4 reports them.
- **What 4.4 reports:** each slot's re-quote calls, 429 share and seconds; the
  oldest leg's age at commit; and every refresh skip, read from `job_health`
  notes and the Railway log.

**Done when:**
- [ ] `tests/`: the refresh's skip while the flag is set (a success with the
      note, no failure); the mid-run yield; one re-quote serving three tiers;
      a leg older than 20 minutes at commit is dropped (rule 2).
- [ ] `tests/`: commit, slate isolation, resolve (hit, miss, partial; a miss
      scores −1.0), capture (two-sided only; missed when a leg lacks one),
      the stamp refusal, the started-game refusal.
- [ ] `tests_live`: one test agent's full loop on test rows (commit, then
      resolve, then capture), rolled back.
- [ ] Agents enabled by their migration and `ROSTER=production` set by the
      owner. The first real slot commits.
- [ ] Read from the database: the rows, `mode = 'paper'`, `committed_at`
      before every leg's start, and the payload's prices equal to the
      re-quote's.
- [ ] The first resolutions and CLV proxies read back. The missed-close and
      void counts are reported.
- [ ] The worker's RSS at rest is reported against the ~140–150 MB baseline.

## 4.5: the page, the brief and Wags

- **`/betting`:** a Parlays tab, one card per ticket, with the tier, sport,
  legs with their one-line reasons, scored price, fair p, naive p, the
  `expected_quote` slot, the CLV proxy (labelled), combo status and
  outcome. A cross-sport Lotto card carries the "extrapolated fill" label
  and is listed apart from the per-sport Lottos.
- **The brief and Wags:** one Parlays line in the brief, and a context
  provider so Wags can say what was committed. Both report per tier, with no
  blended figure.

**Done when:**
- [ ] Logged-out e2e passes; the owner's `e2e/parlays.spec.ts` (phone) and
      the screenshot gate pass; the owner's phone check is green.
- [ ] A brief carries the line, and Wags answers "what parlays went out
      today?" from the provider.

## 4.6: the switch to M5's rules

- When M5's 14 days end, run its frozen rule and report.
- On the owner's acceptance, write version 2 of the pre-registration (§3
  replaced, the M5 output's sha256 quoted), stamp it in its own migration, and
  switch at the next slot. Commitments carry `rule_version`.

**Done when:**
- [ ] The M5 report is accepted (or not, and P1 stays, recorded).
- [ ] Version 2 is stamped before its first slot. The first version-2 tickets
      read back with `rule_version = 2`, and the report shows the two cohorts
      apart.

Not in Phase 4: live money, RFQ quotes, Pinnacle, and any read of the 2026
NFL props archive.
