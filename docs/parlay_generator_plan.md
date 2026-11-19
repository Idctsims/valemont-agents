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

1. **How the three agents run on the worker.** Agents run only under
   `ROSTER=production`, and each also needs `agents.enabled = true`
   (two-gated, CLAUDE.md "Deploy").
   - **Proposal:** add the three to the production roster, enabled by their
     own migration (db/013's pattern).
   - `nfl_ml` and `_kalshi_probe` stay disabled (db/013 stays unpasted).
   - `ROSTER=production` is then set on Railway by the owner when 4.4 closes.
   - This is not the `nfl_ml` go-live, and it moves no money.
2. **Approval of the pre-registration text.** Wording changes go in before
   the stamp, never after it.

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
- **Pure, with no SQL:** given the same candidates, it builds the same ticket.

**Done when:**
- [ ] Unit tests: each ticket rule; the tie-breaks; the tick floor; the fee
      rounding; the same input gives the same ticket.
- [ ] A dry run of two real slots (no commit) prints, per tier and sport, the
      ticket or the empty slot with its binding rule, the prices and the
      relative cost over fair, and the time per tier. The owner reviews it.
- [ ] Lotto at P1: the share of slots that draw one is reported. The 1¢ floor
      is expected to bind; the number is recorded, not tuned.

## 4.4: the paper commitment, resolution and the CLV proxy

- **`adapters/parlays.py`:**
  - `observe`: candidates plus the re-quote;
  - `form_thesis`: the search;
  - `build_commitment`: a slate, at most one ticket per sport;
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

**Done when:**
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
  legs, scored price, fair p, naive p, the `expected_quote` slot, the CLV
  proxy (labelled), combo status and outcome.
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
