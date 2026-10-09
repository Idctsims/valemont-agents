# Valemont Agents — Architecture Contract

Approved build plan: docs/MASTER_PLAN.md. Product brief: docs/BUILD_BRIEF.md. Read both at the start of every session.

**Shell: native Windows PowerShell only. No WSL, no bash-only syntax.** It is
Windows PowerShell **5.1**: no `&&`, no `||`, no ternaries, no `??`. Chain with
`;` (or `A; if ($?) { B }`), set variables with `$env:X = 'y'`, and run Python
as `..\venv\Scripts\python.exe -m ...` from `workers/` (the venv stays at the
repo root).

Read this at the start of every session. It is the reason the codebase is shaped
the way it is. If a request in a session conflicts with something here, say so
before writing code.

---

## 1. What this is

A multi-agent paper-trading and forecast-tracking system with a live dashboard.

Four worker agents, one supervisor:

| Agent | Domain | What it commits to | Money |
|---|---|---|---|
| `crypto` | Crypto markets | Simulated positions, long and short | Paper; live only per this section. |
| `equities` | US stocks | Simulated positions | None. Paper only. |
| `prizepicks` | Player props | Proposed slips, handed to the operator | None. Never places bets. |
| `kalshi` pillar | CFTC-regulated event contracts: one agent per sport × market type (`nfl_ml`, `nfl_spread`, `nfl_props`, …; §8) | Shadow positions | None. Never places orders. |
| `chief_of_staff` | Supervision | Nothing. Writes briefs. | None. |

**Paper by default, everywhere. Real money moves only where the owner
explicitly flips one bot to live.**

- Eligible for automated live execution: the core crypto bot on Alpaca, and
  nothing else.
- Live requires all of: a separate live key pair that exists only in a
  separate Railway environment; per-bot `mode='live'` set by a typed owner
  confirmation in the dashboard; hard limits (daily loss, max position) in
  `bots.risk_limits`, enforced in core before any order; a kill switch that
  halts within one cycle. Starting live mode without live keys present must
  refuse loudly.
- Never automated: Kalshi bets and combos, PrizePicks or any other slip,
  memecoin trades (FOMO). The system generates, analyzes and tracks; the owner
  executes by hand and logs fills.
- No key with withdrawal or transfer permission is ever stored.
- Every money table carries `mode` (`'paper'`|`'live'`) — required for every
  money table created or altered from db/019 onward; existing ledger tables
  gain it there. Every metric groups by mode; paper and live never blend.

If a session proposes live execution outside these rules, stop and flag it.

---

## 2. The one rule that matters

**Commit before outcome. Never edit a commitment after the fact.**

The entire point of this project is an honest track record. Every agent follows
the same loop:

```
observe → form thesis → COMMIT (outcome unknown) → wait → resolve → score
```

A commitment is written to the ledger with a `resolves_after` timestamp. It
cannot be updated or deleted — the database enforces this with triggers, not
just convention. Resolutions are separate rows written after the fact and
linked back.

This means:
- No backdating. `committed_at` is set by the database, never by application code.
- No filling a paper trade at a price chosen in hindsight. Record the price
  available at commit time.
- No adding a leg to a slip after games start.
- If an agent was wrong, the row stays. That is the data.

If you find yourself writing an `UPDATE` against `commitments`, the design is
wrong. Write a new row instead.

---

## 3. Structure: one core loop, four thin adapters

A monorepo. Python lives under `workers/`; module paths elsewhere in this file
(`core/ledger.py`, `jobs/...`, `tests/...`) are relative to `workers/`.

```
apps/
  web/           Next.js 16 on Vercel (Chat 1 Phase 2). Reads Supabase
                 server-side (§5). Never writes ledger tables. Its own
                 README covers run, lint and Playwright.
workers/         Everything Python. The Railway image is built from here.
  core/            The shared machinery. Agents do not own logic.
    ledger.py         All database writes. Nothing else touches SQL.
    agent.py          BaseAgent — the observe/thesis/commit/resolve loop.
    orchestrator.py   Scheduler, agent registry, event emission.
    paths.py          REPO_ROOT and the one .env location. Repo-root files
                      (docs/, db/, .cache/) are reached through it, never
                      relative to the working directory.
  adapters/        Domain specifics ONLY. Thin.
    crypto.py
    equities.py
    prizepicks.py
    nfl_ml.py         Kalshi NFL moneyline (nfl_spread.py, nfl_props.py to follow)
  venues/kalshi/   Shared by every Kalshi agent: read-only client, per-series fee
                   regimes, the edge gate, KalshiContractAgent (resolve + capture
                   written once). No SQL, no orders.
  sports/nfl/      Everything NFL: nflverse schedule/kickoff, team codes, injuries,
                   models. A new sport is a sibling package, not a rebuild.
  jobs/            Scheduled work that is not an agent (weekly model fits).
  scripts/         One-off utilities, health checks, db_inspect.py (§5).
  tests/           Invariant suite. Stubbed ledger, no DB, no network. See §6.
  tests_live/      Live-database suite. See §6.
  main.py          Railway entry point. Dockerfile, requirements.txt beside it.
db/              Numbered SQL migrations, shared by app and workers. Committed.
docs/            Plans, design notes, pre-registrations (frozen texts), results.
reference/       Cloned third-party repos for reading. GITIGNORED.
.env             The only .env (gitignored); .env.example documents it.
venv/            Python 3.12 virtualenv, at the root. GITIGNORED.
```

**The smell test:** if an adapter starts growing its own version of something
that exists in `core/`, the abstraction is wrong. Fix `core/`, don't duplicate.

An adapter is responsible for exactly four things:
1. `observe()` — fetch domain data
2. `form_thesis(observation)` — reason about it
3. `build_commitment(thesis)` — produce one commitment, **or a slate of them**
4. `resolve(commitment)` — determine what actually happened

**On slates (hook 3).** `build_commitment` returns `Proposal`,
`Sequence[Proposal]`, or `None`. One commitment is the common case and the only
one crypto uses. A slate exists because forty independent player props on a
Sunday are forty independent commitments, and one-per-tick would either lose
thirty-nine or need forty ticks. Core commits each proposal **independently** —
a bad one at index 3 does not strand the rest — and refuses the **whole** slate
before writing anything if it exceeds `max_slate_size` or contains two legs
identical in subject, market, direction and line. Half a slate is permanently
indistinguishable from a complete one, which is why the refusal is total.
`max_slate_size` defaults to 25 and must be raised deliberately, the same way
`max_overdue` must be (§9.3).

Everything else — scheduling, persistence, scoring, event emission, error
handling, retries — belongs to `core/` and is written once.

**One optional fifth hook, `capture_close()`**, exists for domains that have a
market close worth snapshotting (§10). It is gated on `captures_close`, which
defaults to False, so the four-hook contract holds exactly for every adapter
that does not opt in — no stub, no `NotImplementedError` in production, no
`closes_at` on its commitments, and no capture job scheduled. Crypto opts out:
it trades 24/7 and there is no moment its market opinion is final.

Adapters may also *call* core helpers they find useful — `open_subjects()` to
avoid re-entering a position it already holds, for instance. Those are
capabilities core offers, not stages every adapter must implement, and they do
not count against the four.

Adding an agent should be one new file in `adapters/` and one `Registration`
in the orchestrator. If it takes more than that, `core/` isn't doing its job.

---

## 4. The chief of staff

Runs once or twice a day. Reads the ledger, writes a synthesis brief:
what each agent did, where a thesis was wrong and why, current running score.

**It does not supervise.** It cannot cancel, override, or instruct another
agent. It has read access to the ledger and write access only to `briefs`.
It is a reporter, not a manager. Do not give it decision authority — at four
agents that adds failure modes without adding capability.

It reports **per-agent, with no blended headline number** — see §9.2. That is
a constraint on the brief, not a stylistic preference.

---

## 5. Database rules

Supabase Postgres, on a separate account not linked to Cursor or to any MCP.
**The agent writes numbered `.sql` files into `db/` and I paste them into the
Supabase SQL Editor myself.** Never attempt to apply a migration
programmatically.

**The Supabase MCP is never used in this repo**, for anything. The one an
editor may have connected belongs to a different Supabase account, keeps write
access for other projects, and cannot see this database (verified 2026-10-08;
no project-level MCP config exists in this workspace). Do not call it, even to
read.

**Every database inspection goes through `workers/scripts/db_inspect.py`:**

```
..\venv\Scripts\python.exe scripts\db_inspect.py "SELECT ..."     (from workers/)
```

It connects only as `valemont_readonly` (db/018) via `DATABASE_URL_READONLY`,
never reads `DATABASE_URL`, and rolls back every transaction. The role has
SELECT on every public table (current, and future ones by default privileges)
and BYPASSRLS so RLS does not hide rows from it; it has no write grant
anywhere. That is enforced by the database, and
`tests_live/test_readonly_role.py` fails if any public table is unreadable or
writable by it. Never inspect through `DATABASE_URL`.

**apps/web reads Supabase server-side with the owner's session via
`@supabase/ssr`.** RLS on every table. App tables: `owner_id = auth.uid()`
policies. Ledger tables: a select-only policy for the owner, no write policies;
immutability triggers unchanged. Workers remain the only writers to ledger
tables, via `DATABASE_URL` (bypasses RLS). The secret/service-role key never
reaches the browser. Migrations stay numbered files in `db/`, pasted by hand.

**Status 2026-10-08 (apps/web live, see Current State): none of these policies exist yet.** Every table still has
RLS on with NO policies, so anon and authenticated see nothing. The owner
policies arrive in db/019+ before apps/web reads any table. db/019 (push_subscriptions, notifications) carries the first ones: owner-only, TO authenticated, anon revoked.

- Workers connect **only** from `DATABASE_URL` in `.env`. Never a hardcoded host.
- Use the **Session pooler** connection string (port 5432), not the direct
  `db.<ref>` host, which is IPv6-only.
- Schema changes **only** via numbered files in `db/`. Never via a GUI.
- `timestamptz` everywhere, store UTC. Four agents span crypto (24/7),
  US market hours, game slates, and contract settlement. Naive timestamps
  will burn us.
- Stay in the `public` schema. Supabase reserves `auth`, `storage`,
  `realtime`, `extensions`.
- Assume **not superuser**. No `COPY FROM` a server path, no exotic extensions.
- Migrations are paste-by-hand, so each file must run top-to-bottom in one go
  with no `BEGIN`/`COMMIT` (the SQL Editor supplies its own transaction) and
  no `psql` meta-commands like `\i` or `\copy`.
- `postgres` here is **not** a superuser (only `supabase_admin` is). On
  PostgreSQL 16+ an `ALTER ROLE` that so much as names `SUPERUSER` (even
  `NOSUPERUSER`) needs one, which is why db/018's first paste failed. Assert
  role attributes; don't re-set them.

---

## 6. Conventions

- Python 3.12, `psycopg` v3, `apscheduler`. Keep dependencies boring.
- All worker DB access goes through `core/ledger.py`; if SQL appears in an
  adapter, that's a bug. Exceptions: `scripts/db_inspect.py` (read-only role,
  §5) and `apps/web` (server-side, owner session, §5).
- **All worker Claude calls go through `core/ai.py`** (`complete()`, or
  `record_usage()` for batch results). It is the only importer of the
  `anthropic` SDK. Every call writes an `ai_usage` row costed from the
  response's usage and its dated `PRICING` table; the monthly budget
  (`AI_MONTHLY_BUDGET_USD`) downgrades non-critical calls at 80% and refuses
  them at 100%. A direct `anthropic.Anthropic()` anywhere else is a bug.
  - **Chat 2 adds the TypeScript twin** for Wags and other web-side calls. It
    reads and appends to the same `ai_usage` table through the owner's RLS
    policy (db/020) and keeps its own pricing file. A test keeps that file in
    sync with `core/ai.py` `PRICING`, so the two budgets can never disagree.
- Every agent action emits an event row. The dashboard is a consumer of that
  stream — build the stream first, the visuals last.
- Secrets in `.env`, which is gitignored. `.env.example` documents the keys.
- **Never print a secret value.** No unredacted variables, no connection
  strings, no keys or tokens in any output. Never run
  `railway environment config --json`; it echoes variable values. List
  Railway variables by name only, with values redacted. If a command can echo
  a secret, pipe it through redaction or don't run it. Why: on 2026-10-08 that
  command printed `DATABASE_URL` with its password into a session transcript,
  and the password had to be rotated.
- **Never use another account's CLI session for this repo** (Vercel, Railway,
  `gh`, Supabase or anything else). Before the first CLI call of a session,
  confirm the account by name only: `vercel whoami` must print `idctsims`.
  If it names anyone else, stop and ask the owner to log in. Never switch
  accounts yourself.
  - Why: on 2026-10-08 the Vercel CLI on the owner's machine was logged in
    as `toya-rn-7874`, which could not see the project. Diagnosis of the
    Drill 2 watchdog failure stalled on it.
  - `vercel logs` filters to the **current git branch** by default. Pass
    `--no-branch` (and `--environment production`) to see production.
- **Shell: native Windows PowerShell 5.1 only**, as the header of this file
  says. No `&&`, `||`, ternaries or `??`.
- **Merge PRs with a merge commit, never squash.** Each step's commit, and
  the evidence in its message, stays visible in `main`'s history.
- **Reports back to the planning chat state only what was actually verified.
  Anything not checked is recorded as pending, never as passed.** That
  includes anything taken from a template the owner pasted without filling
  in: say it was not provided, and verify it independently or leave it
  pending.
- Fail loudly. A silent exception in a worker that runs at 3am is the single
  most likely way this project quietly dies.
- **Run the suite before and after touching `core/` or an adapter.** From
  `workers/`, in PowerShell:

  ```
  ..\venv\Scripts\python.exe -m unittest discover -s tests -t .
  ```

  Stdlib `unittest`, no extra dependency, under a second. Every ledger
  function is stubbed, the real pool and the network raise, and every test
  fails if anything was written as a non-test agent. It covers the invariants
  this file states — §9 risk math and gaming band, §9.1 stop-as-exit, §10 CLV,
  bounded defer/void, close capture and tombstones, slate isolation and
  refusals, event kinds. Run it first so you know the baseline was green; a
  red suite before you start is a finding to report, not something to fix in
  passing. A verification claim that is not in `tests/` is a claim that exists
  only in a chat transcript — add the test.

- **`tests/` is structurally blind to SQL. Run `tests_live/` too when you
  touch a query or add a migration.** Also from `workers/`:

  ```
  ..\venv\Scripts\python.exe -m unittest discover -s tests_live -t .
  ```

  The stub ledger refuses the database on purpose, which is what keeps the fast
  suite fast — and it means a defect in a query, a `CHECK`, a trigger or a
  server-computed column cannot fail it. Two such bugs have shipped already:
  `a.is_test` missing from a `GROUP BY`, and the resolution sweep counting
  capture attempts against its own budget (silent permanent data loss, invisible
  to 108 green stub tests).

  `tests_live/` writes only as `_test` (`is_test = true`, seeded by `db/006`)
  and **skips rather than fails** when the database is unreachable or the
  migration is unpasted — a red suite should mean a broken invariant, not an
  unplugged cable. Its rows are permanent, like all rows here; quarantine is the
  only cleanup there is, and a test agent whose rows could be deleted would be
  testing a different database from the real one.

  Still outside both suites, and worth knowing before trusting a green run:
  live HTTP response shapes, migrations actually running in the SQL Editor,
  APScheduler firing on a real clock, and the SIGTERM drain. The last two
  have been seen working in production, not tested: system jobs on their
  intervals since 2026-10-08, and a clean drain on Railway at Drill 2
  (`caught SIGTERM`, `shutting down (drain=True)`, `orchestrator down`,
  2026-10-09 04:21:32 UTC).

---

## 7. Build order

Do not skip ahead. Each step is cheap to change; the ones before it are not.

1. Ledger schema + connection proven ✓
2. `core/` loop with a fake agent writing real rows ✓
3. `crypto` adapter, live data, paper positions ✓
4. Deploy to Railway — prove the 24/7 path with ONE agent running ✓
   (proven by the `_fake` canary)

From here the build order is **`docs/MASTER_PLAN.md` §6** (thirteen chats,
each in phases with a "done when" checklist). It supersedes the former steps
5–10.

- **Chat 1, Phase 1** (repo restructure, CLAUDE.md amendments, Railway redeploy
  from `workers/`) ✓, except Step 8 (24 h Railway usage check)
- **Chat 1, Phase 2** (Next.js app, owner-only auth, design tokens, Vercel) ✓
- **Chat 1, Phase 3** (PWA shell and push) ✓ (phone-verified 2026-10-08)
- **Chat 1, Phase 4** (db/020 shared tables, scheduler, `job_health`, push alerts) ✓ (Drills 1 and 2 passed; canary retired 2026-10-09; the Step 8 usage report is pending the owner's numbers)
- **Chat 1 complete.**
- **Chat 2, Phase 1** (Goals, plus the app shell navigation registry) ✓ (PR #8, merge `464c6ea`; the owner's phone checklist is pending)
- **Chat 2, Phase 2** (Ventures HQ) ← **you are here**, after the separate `fix-sweep-starvation` PR

Reason for deploying at step 4 and not at the end: "works locally, dies
silently at 3am in production" is the classic failure here. Hit it while
there's one agent to debug, not four.

---

## 8. Known open problems

- **PrizePicks has no public API.** The board comes from an internal endpoint
  or scraping, and both break. v1 may need a projection source plus manual
  board entry. Don't build deep on a fragile fetch.
- **Void rate is a health metric, not a log line.** It is the earliest signal
  that an adapter's data source has broken, and it moves *before* hit rate
  does: a dead settlement feed stops producing resolutions long before it
  produces wrong ones, and hit rate can't move on commitments that never
  resolve. A quiet adapter looks identical to a careful one on every other
  chart. The dashboard (step 10) surfaces per-agent void rate over time as a
  first-class number, not buried in the event stream — and `resolution_attempts`
  is the table that feeds it. Treat a rising line as an outage, not as data.
- **Per-game exposure across all agents is a first-class dashboard view.**
  `nfl_ml` on CHI, `nfl_spread` on CHI −3.5 and `nfl_props` on CHI's QB overs
  are correlated positions in three separate records. Per-agent scoring
  cannot see that, and §9.2 forbids netting them into one number. So the
  dashboard (step 10) shows, per game, every open commitment from every agent
  (side, entry, size, capital at risk) side by side, **without** summing them
  into a blended figure. The same correlation is why evaluation clusters
  standard errors by game (`docs/preregistration_nfl.md` §2.5).
- **Scoring methodology: CLV is the best available signal, and it is NOT
  profit.** Read both halves of that sentence before quoting either.

  CLV (§10) is the primary metric wherever a closing price exists — `kalshi`,
  `prizepicks` — for one reason: in a forward-running system with no backtest,
  it is the only measurement that lands on **every** commitment instead of only
  on resolved ones. Hit rate needs hundreds of resolutions before it separates
  skill from variance. We will not have hundreds for months. CLV says something
  on the first commitment. That is why it is primary.

  **A positive CLV record is not evidence this would make money.** Not weak
  evidence — not evidence. The two come apart in practice, and we have a
  measured case: `jackc625` ran a pre-registered 2025 holdout in which the
  single strongest statistical result in the entire project was a closing-line-
  value result on win probability, in the **same run** where no target came out
  profitable and the spread target returned −5.3%. He calls that divergence the
  headline finding rather than a return, and he is right to. See
  `docs/reference-analysis.md` §1.

  So: **§10 does not mean scoring is solved.** It means the *unit* is settled
  and the *signal* is the best one obtainable this early. If a future session
  reads §10 as "CLV solved scoring," that is the misreading this paragraph
  exists to prevent. Reporting CLV as though it were profitability — in a
  brief, on the dashboard, anywhere — is the specific error to avoid. When we
  eventually want a profitability claim it needs its own pre-registered
  measurement, on data not used to build anything, with a stated significance
  rule frozen before the number exists.

  **CLV does not cover `crypto` at all.** A 24/7 market has no moment its
  opinion becomes final, so there is no close to beat. Crypto is scored on the
  §9 R-multiple alone, which still needs hundreds of resolutions and still has
  no time dimension (§9.2). Do not report a blended "system CLV" that quietly
  omits the agents which have none. Finding crypto's equivalent — some fixed
  horizon reference price, perhaps — is the part of §8 that remains open.

- **Three independent builders lost to the market on liquid game lines. We
  commit into them anyway, deliberately, and let the ledger judge.**

  **Decision (owner, 2026-09-30):** the Kalshi pillar commits into
  **moneylines, spreads and player props**, each as a **separate agent with
  its own track record** (`nfl_ml`, `nfl_spread`, `nfl_props`, then the same
  split per sport). This supersedes the earlier ruling that game markets are
  benchmark-only. It was made with the evidence below in full view, not in
  ignorance of it. A session that finds the evidence alarming should re-read
  this paragraph, not re-open the decision.

  The evidence (full detail in `docs/reference-analysis.md` §1):

  - A real LOOCV search over Vegas/Elo blend weights chose **100% Vegas at
    every one of five checkpoints**. Betting the builder's own disagreements
    with Vegas returned **−36% ROI**: "actively harmful, not just unhelpful."
  - A second builder's pre-registered holdout produced **no profitable-clean
    target**, with the spread target at **−5.3%**.
  - A third, across six sports: **"no model beats the closing point spread
    reliably."**

  Three independent hobby-scale measurements against an efficient market,
  three losses. Ours are hobby-scale too. **The prior for `nfl_ml` and
  `nfl_spread` is that they lose**, and a losing record from them is the
  expected outcome, not a bug report. Props and thin markets remain where a
  model this size has the better chance.

  **What makes this defensible is the scoring, which is binding:**

  - **The ledger is the judge.** Each agent's own record, **net of fees**,
    decides whether it survives. No agent is kept on narrative, and none is
    cut before its record says so.
  - **Scored on edge against price plus fees, never on win rate alone.** A
    favourite-backing agent can hit 70% and lose money. `pnl` is the §9
    R-multiple with the fee inside `capital_at_risk`, and CLV (§10) is the
    early signal. Win rate may be reported, never as the headline.
  - **Separate track records, no blending** (§9.2). `nfl_ml` losing must not
    be hidden by `nfl_props` winning, or the reverse.
  - **A commitment fires only when the model beats price plus fees by that
    agent's stated margin.** Disagreeing with the market is not enough; the
    disagreement has to pay for its own costs.
  - **Pre-registered backtests first, wherever history exists** (Kalshi keeps
    settled-market candlesticks), and a profitability claim still needs its
    own pre-registered measurement, as the scoring bullet above says.

  Design: `docs/kalshi_nfl.md`. The NFL game-market price-path collector in
  `docs/kalshi_benchmark.md` becomes the shared candlestick layer those
  agents read.

  **`nfl_ml` result (owner decision, 2026-09-30).** The 2025 walk-forward
  dev fit found **no signal** (`docs/preregistration_nfl.md` A3): the
  largest adjustment its coefficients can produce is ≈ 1.6¢ against the
  3.1–4.3¢ a commitment needs. Its factors are **not** revisited. It stays
  registered as a **forward live-pipeline test** and will rarely, if ever,
  commit; a quiet `nfl_ml` is the expected state, not an outage. **Any new
  game-line idea requires a new pre-registration and forward-only
  validation**: no re-fitting the 2025 or holdout data until something
  appears.

  **Props result (2026-10-01).** Both pre-registered props strategies failed
  their one holdout run (`docs/preregistration_nfl.md` §7.4): the frozen V1
  blend earned zero weight against the Kalshi mid (as did the base rate), and
  P2's taker R interval spanned zero. **By the owner's rule, props do not
  continue.** As with game lines, any new props idea needs a new
  pre-registration and forward-only validation; the 2025 and holdout data are
  spent.

  **Disclosed ordering (recorded 2026-10-02):**

  - A4, A5 and A5a were committed at 04:55–04:57 UTC on 2026-10-01. That is
    *after* the `nfl_ml` holdout output on **the same 45 games**
    (`docs/backtests/nfl_ml-holdout-20260930T222441Z`, written 22:24 UTC on
    2026-09-30).
  - That output shows every game's home result and moneyline mid at t. It shows
    no player stats, but game script is correlated with yardage, so the props
    freeze was **not blind** to the holdout games' outcomes.
  - **The verdicts are unchanged.** Both props strategies failed. A
    contamination that could only have flattered them cannot explain a fail.
  - From db/017 onward, every runner refuses to write output unless its
    pre-registration's hash was DB-stamped first.

---

## 9. The pnl contract

Adapters decide the value of `resolutions.pnl`. That is correct — only the
adapter knows that a prop pays a multiplier and a contract settles at par.
But without a shared unit, a chief-of-staff brief that averages crypto dollars
against a prop multiplier is averaging nonsense, confidently.

**`resolutions.pnl` is return on declared risk: a dimensionless decimal.**

```
pnl = (proceeds - capital_at_risk) / capital_at_risk

capital_at_risk = |entry - invalidation| × size
```

**Every commitment declares an invalidation level at commit time.** Every
domain, every direction, no exceptions. The denominator is the distance to
that level. Never dollars. Never a payout multiplier. Never a percentage —
`0.05`, not `5`.

### This is §2, not a second rule

§2 says: state the thesis before the outcome is known, and never edit it
afterwards. An invalidation level is the other half of that same sentence.
A thesis that says what it expects but not what would refute it is only half
committed, and a track record built on it cannot distinguish "I was right"
from "I was never willing to be wrong."

So §9 is not a scoring convention bolted onto §2. It is §2 applied to risk:

> **Declare what would prove you wrong, before you find out.**

Everything below follows from that. The earlier version of this section
measured a long's risk as `entry × size` — the loss if price went to zero.
That number is imaginary. It describes an outcome that has never happened to
a major asset and would not be how the position actually ended. A stop
distance is what the agent actually claimed. Only one of the two is honest,
and scoring against the imaginary one made longs and shorts differ by more
than an order of magnitude for identical moves.

### Per domain

| Domain | entry | invalidation | capital_at_risk | reduces to |
|---|---|---|---|---|
| paper long | entry price | stop **below** entry | `(entry − stop) × size` | `(exit − entry) / (entry − stop)` |
| paper short | entry price | stop **above** entry | `(stop − entry) × size` | `(entry − exit) / (stop − entry)` |
| prop slip | stake | `0` — the stake is the stop | `stake` | `multiplier − 1`, or `−1` |
| event contract YES | price | `0` — settles worthless | `price × contracts` | `(settlement − price) / price` |
| event contract NO | `1 − price` | `0` — settles worthless | `(1 − price) × contracts` | `(price − settlement) / (1 − price)` |

**Fees are inside the risk (owner decision, §8).** For Kalshi contracts,
`capital_at_risk = (price + entry_fee) × contracts` and
`proceeds = settlement × contracts`: the fee is lost along with the stake, and
a record that left it out would overstate every R-multiple. The rows above
show the fee-free shape; the stop is still structural `0`.

Long and short are now the **same expression with a sign**:

```
pnl = signed_move / |entry - stop|      signed_move = exit-entry (long)
                                                      entry-exit (short)
```

**Props and event contracts needed no change.** They were always R-multiples;
we just hadn't noticed. A prop's stake *is* its stop — lose and it is gone —
and a YES contract settling at zero is a stop at zero. Their invalidation is
**structural**: supplied by the instrument, not chosen by the agent. That is
the only distinction that remains, and it matters solely for the gaming guard
below.

Worked, all four, same 1R favorable move:

```
long   entry 60000, stop 58200, exit 61800   ->  1800 / 1800  = +1.00
short  entry 60000, stop 61800, exit 58200   ->  1800 / 1800  = +1.00
prop   stake 1, stop 0, multiplier 2.0       ->     1 / 1     = +1.00
YES    price 0.50, stop 0, settles 1.00      ->  0.50 / 0.50  = +1.00
```

and all four fully wrong:

```
long stopped / short stopped / prop missed / YES settles 0   ->  -1.00
```

### Read points

- `-1.0` means **the stop was hit and the thesis was fully wrong**. It means
  exactly that in every domain. There is no clamp and no special case,
  because the denominator is defined to make it true.
- `+2.5` is two and a half times the declared risk. This is an R-multiple.
- `0` is a push, or a void that returned the stake. Write `0`, not `NULL`.
- `NULL` means **not scored**: the return never became computable. An
  abandoned commitment (§9.3) is `NULL`. Never a break-even.
- A commitment with **no declared invalidation cannot be scored.** Report
  `pnl=NULL`. Do not substitute notional and hope.

### 9.0 The gaming guard

A denominator the agent chooses is a denominator the agent can shrink. A 0.01%
stop would turn an ordinary move into `+500R`. The guard has three parts, and
the first is the one that matters:

1. **The stop must be volatility-derived, by a documented rule.** Not a
   per-commitment judgement call. crypto uses `1.5 × ATR(14)`; the rule name
   goes in the payload as `stop_rule` so a reviewer can confirm the same rule
   produced every row, rather than a number picked to flatter each one.
2. **Minimum stop distance: `max(1.0 × ATR(period), 0.5% of entry)`.** The ATR
   term is the real floor — a stop inside one average true range is inside
   ordinary noise and gets hit by a random walk rather than by the thesis
   being wrong. The percentage term is a backstop for a degenerate ATR
   (a halted market, a stablecoin, rounding-scale ranges).
3. **Maximum stop distance: 25% of entry.** The weaker guard, included for
   completeness: an absurdly wide stop deflates every result toward zero and
   conveniently means `-1.0` never appears. Declining to be wrong is not the
   same as being right.

Bounds 2 and 3 apply only where the stop is a **choice**. A structural
invalidation of exactly `0` — props, event contracts — is exempt, because
there is no free parameter there to exploit.

`core.agent.declared_risk()` enforces this and raises rather than recording an
unbounded multiple. An adapter that cannot produce a stop inside the band
should stand down, not widen until it fits.

### 9.1 The floor is a modeling assumption

`|entry − stop| × size` assumes the position is closed **at** the declared
level. Two things follow, and both must be honored:

**Resolution has to check whether the stop was hit.** Marking only to the
price at the horizon would let a position that blew through its stop record
`-2.5`, breaking the floor. An adapter with intraperiod data (highs and lows
over the holding window) checks it and records the exit at the stop with
`{"stop_hit": true}`. This is not clamping — the stop is a live exit, and
recording it is modeling the position that was actually declared.

**A real gap can still fill worse.** An exchange halt, a weekend move, a
squeeze. We record the modeled fill, not the gapped one, and say so:
`{"fill": "assumed_at_stop"}` in `resolutions.detail`, so a resolution that
relied on the assumption stays distinguishable from one that didn't need it.
For directional positions `-1.0` is a floor on what we **model**, and the
first real gap that violates it is the assumption surfacing, not a new bug.

### 9.2 Aggregation: not yet, and not across agents

Return on risk has **no time dimension**. `+0.05` over twenty minutes and
`+0.05` over three weeks are the same number and are not the same result.
Until §8 is resolved, that makes any blended figure misleading in a way that
looks authoritative.

Binding until §8 closes:

- **Do not average `pnl` across agents.** Not a portfolio return, not a
  system-wide hit rate weighted by pnl, not a headline number.
- Within-domain aggregation only. Crypto with crypto, props with props.
- The chief of staff reports **per-agent** and nothing else. If a future
  session asks it for one number for the whole system, that is the §8 problem
  wearing a disguise — refuse it and say why.
- Test agents are excluded from all of it (`agents.is_test`, §5).

The data to do better is already being kept: `committed_at` and `resolved_at`
give holding period, `resolutions.detail` gives domain-native figures, and
`legs.size` gives position size. A time-weighted metric is reconstructible
later. A blended number published now is not retractable.

### 9.3 Abandoned commitments

A commitment whose outcome never becomes knowable is closed by core, not by
the adapter: `outcome='void'`, `pnl=NULL`, with the reason in `detail`. See
§9 on `NULL` — it is not a zero, and it must not be counted as one.
The mechanism and its thresholds are core's, described in `core/agent.py`.
**A rising void rate is a broken adapter, not normal operation.**

**Every adapter sets its own `max_overdue`. The default is for a stuck feed.**
`DeferPolicy` defaults to 12 attempts / 24 hours, which suits a price feed
that has gone quiet and is wrong for anything on a sports calendar. A
postponed NFL game can legitimately return five days later; a 24-hour cap
would void a commitment that was about to resolve perfectly well.

A void is **permanent** — `resolutions.commitment_id` is UNIQUE, so once
abandoned, a commitment can never be resolved, even if the answer arrives ten
minutes later. There is no reversal path, by design. That makes an
under-tuned `max_overdue` a silent destroyer of real data.

So, when those adapters land: **`prizepicks` and `kalshi` must set
`max_overdue` deliberately, in their `Registration`. Do not let either inherit
the default.** Sizing rule: the threshold is "longer than the worst legitimate
delay this domain produces", not "how long we feel like waiting" — postponed
games and contract settlement disputes set the number, not convenience.

**Normalizing discards position size, deliberately.** `+0.50` on a token
position and `+0.50` on a large one are the same number here. That is the
cost of comparability, and it is paid back by keeping the raw figures:

- `legs.size` and `legs.line` hold the position as committed.
- `resolutions.detail` carries the domain-native numbers, so nothing is lost:

```json
{"unit": "usd", "capital_at_risk": 60000.00, "proceeds": 63000.00, "gross": 3000.00}
```

A size-weighted or EV-based metric can be reconstructed from those later.
Storing only a normalized number would have made that impossible; storing only
dollars would have made the brief meaningless. Store both.

---

## 10. Closing line value

The ledger knew two moments: commit and resolve. CLV needs a third.

```
observe → form thesis → COMMIT → [market closes: SNAPSHOT] → resolve → score
```

**Why this is the primary metric where it exists.** Hit rate needs hundreds of
resolutions before it distinguishes skill from variance. CLV produces a
measurement on *every* commitment, won or lost, the moment the market closes —
the difference between signal in weeks and signal in a season. A thesis that
consistently beats the close is finding something before the market does, and
that is visible long before the P&L is.

> **CLV is not profit, and this section does not claim scoring is solved.**
> A positive CLV record is not evidence the system would make money — there is
> a measured case of CLV-positive and profit-negative in the same run. Read §8
> before citing anything here as a result. This section settles the *unit* and
> the *mechanism*; §8 holds what they do and do not license you to say.

### The formula

```
clv     = close_price - entry_price
clv_pct = clv / entry_price
```

**Both prices are the price of the side we hold.** That one convention removes
the YES/NO branch entirely:

```
YES bought at 0.34, YES closes at 0.40  ->  +0.06   market came to us
NO  bought at 0.66, NO  closes at 0.60  ->  -0.06   market left us
```

A NO position is stored with `entry_price = 1 - yes_price`, normalized once by
the adapter at commit time. **Positive always means the market moved toward our
view**, in every domain, on either side.

`clv_pct` exists because six points of edge on a 0.10 contract is a different
achievement from six points on a 0.80 one, and the absolute number cannot tell
them apart.

**Stored, never derived on read.** A formula living in a query can be changed,
and changing it silently rewrites every historical measurement. Freezing the
number at capture time is the same principle that freezes the commitment —
§2 applied to the metric.

### `closes_at` means "earliest worth looking"

It sits on `commitments`, immutable, set at commit time, because the close time
is part of the claim: which market you are pricing against. A close time that
could be revised afterwards would let a thesis shop for a flattering
comparison point.

Games get postponed, so **the column is not a promise that the market closed
then.** `capture_close()` returns `None` while the market is still open and the
bounded-retry machinery keeps asking. Postponement is handled by the same
discipline that already handles a postponed resolution — no `UPDATE`, no
revision table, no gaming vector.

### A missed close is permanent

A missed resolution can be retried until it voids. **A missed close is data
loss.** The close happens once. Three consequences:

- Schedule capture **tighter** than the resolution sweep.
- `default_capture_policy` is more patient than `default_defer_policy`
  (48 attempts / 48h vs 12 / 24h).
- Every failure path fails *toward retry*. Even a failure to record an attempt
  leaves the commitment in the due set.

When capture does give up, it writes a `missed` tombstone with a reason rather
than leaving the row absent — absence and unrecoverable-loss must not look
identical. That row is louder than a void in the logs, and for the same reason
void rate is a health metric (§8), **rising missed-close rate is an outage.**

### 10.1 Factor attribution

Each commitment carries named signed adjustments in `commitment_factors`:
`injury -0.04`, `short_week -0.02`. Relational rows, not payload JSON, so the
question that matters is a join rather than an unnest:

```sql
SELECT f.name, count(*), avg(s.clv)
  FROM commitment_factors f
  JOIN closing_snapshots s ON s.commitment_id = f.commitment_id
  JOIN commitments c ON c.id = f.commitment_id
  JOIN agents a ON a.id = c.agent_id
 WHERE s.status = 'captured' AND a.is_test = false
 GROUP BY f.name
 ORDER BY avg(s.clv);
```

That query is how the model calibrates itself from its own record. A factor
that never beats the close gets cut on evidence, not on taste.

**Names are `snake_case` by database CHECK.** Free text would let `injury`,
`injuries` and `Injury` fragment into three factors, and every average would
then be computed over a third of the evidence — silently, and in the direction
of looking more significant than it is.

Factors are immutable, written in the same transaction as the commitment.
Adjusting an attribution after seeing the result is precisely the failure §2
exists to prevent.

### 10.2 Selections

The operator's pick lives in `selections`, **not** as a column on
`commitments`. Two reasons, and the second is the real one:

1. The slate is committed at T and picked at T+30min. A column would need an
   `UPDATE` against an immutable table.
2. **The pick is itself a commitment.** A selection recorded after the line
   moved is hindsight, not judgement, and would silently inflate any
   measurement of whether the operator beats the model.

So a trigger rejects a selection made after `closes_at`. This is
`resolutions_timing` inverted: a resolution cannot land too *early*, a
selection cannot land too *late*.

`selected` is an explicit boolean rather than presence-means-yes, because
declining is a decision. "I looked and passed" must not collapse into the same
absent row as "I never looked."

---

## Kalshi adapter notes

- **Never local `now()` + offset for `closes_at` or `resolves_after`.** The
  2026-09-30 skew audit found adapters deriving deadlines from the worker clock
  while the database compares against its own; harmless on crypto's 6h
  horizon, a real risk on short deadlines like a contract close.
- **`closes_at` = scheduled kickoff, as-of commit, from the sport's schedule
  source (nflverse for NFL), stored with source and fetch time. NOT Kalshi's
  `close_time`.** Verified 2026-09-30: sports markets trade in-play and
  `close_time` is the final whistle (props: two days later), so a snapshot
  there is the settled 0/1 price and CLV becomes the outcome. `occurrence_datetime`
  is not kickoff either. **`resolves_after` = the API's
  `expected_expiration_time`.** Detail: `docs/kalshi_nfl.md` §6.
- **Nothing on Kalshi voids.** Postponed > 48 h settles at a "fair price", a
  tie at $0.50, a prop player active-but-no-snap at the pre-game fair price.
  Score them as real settlements, never as `void`.

---

## Current State (2026-10-09)

- **Owner decisions, 2026-10-09:**
  - AI budget: **$20/month**. `AI_MONTHLY_BUDGET_USD` is set to it in Chat 2 Phase 4 (the code default is still $10).
  - Ventures **NCLEXCompass** and **Senior Care Systems** are dropped. Ventures HQ seeds the remaining seven.
  - Morning Brief push: **09:45 America/Chicago**.
  - Calendar (Google Calendar highlights) is **deferred**.
- **⏰ Reminders:**
  - **Tue 2026-10-13:** NFL props archive, week 5; also weeks 1–4, once (see RECURRING below).
  - **2026-10-16:** Railway decision (Free plan vs Oracle Cloud), once the Step 8 usage numbers are in.
  - **~2026-10-18:** the Railway trial ends. The worker stops, the watchdog pushes "Worker down", and it needs a **manual redeploy**.

- **Migrations:** **`db/001`–`db/012` and `db/014`–`db/018` applied.** 007–012 verified live by `workers/scripts/verify_migrations_007_012.sql` (20/20). 014–018 were pasted 2026-10-08 and verified by read-only SELECTs. `migration_log` (UTC): 015 04:41:03, 016 04:41:16, 017 04:41:30, 018 04:50:35. 014 predates the log; its tables and triggers are present.
  - **`db/013` (enable the production roster) stays unpasted.** It is go-live and out of scope until the owner says so.
  - Real dependencies: 016, 017 and 018 each need only 015 (`migration_log`). 014 needs only 011.
  - **`db/019_push.sql` applied 2026-10-08 20:06:52 UTC** (`migration_log`; owner-verified: RLS on, one policy per table, anon none, readonly SELECT).
  - **`db/020_shared.sql` applied 2026-10-08 20:47:09 UTC.**
    - It holds `app_settings` (the owner id, pasted by hand; the committed file keeps the `__OWNER_USER_ID__` placeholder), `job_health`, `ai_usage` and `job_queue`, plus `is_owner()`.
    - The owner has SELECT-only policies on 11 ledger tables.
    - `commitments.mode` and `origin`: all 1,346 existing rows read paper/agent.
    - A **TRUNCATE guard on all 19 append-only tables**: TRUNCATE fires no row triggers, so before this it could empty the ledger.
    - **anon has no privilege on any public table, now or in future**, and the signed-in role is read-only on ledger and archive tables.
  - **`db/021_goals.sql` applied 2026-10-09 17:21:18 UTC** (`migration_log`).
    - Next new file: `db/022`.
- **Immutability audit (2026-10-01, empirical).** UPDATE and DELETE were attempted on a `_test` row of every table, rolled back.
  - **Refused by trigger:** `commitments`, `events`, `resolution_attempts`, `commitment_factors`, `closing_snapshots`, `selections`, `model_versions`, `kalshi_markets`, `kalshi_candles`. `legs` UPDATE was refused too, by `legs_frozen`.
  - **ACCEPTED:**
    - `resolutions` UPDATE and DELETE: outcomes and pnl were rewritable;
    - `legs` DELETE;
    - `briefs` UPDATE and DELETE;
    - `runs` UPDATE on any row;
    - `agents` UPDATE on any column, including `is_test`.
  - **Refused only by a foreign key**, so not protected: `runs` and `agents` DELETE.
  - **Fix:** `db/015_close_mutation_gaps.sql`, pasted. `tests_live/test_mutation_gaps.py` now runs and passes.
  - **`db/015` applied_at: 2026-10-08 04:41:03.069615 UTC** (from `migration_log`). **Rows written before that timestamp in `resolutions`, `legs`, `briefs`, `runs` and `agents` were protected by convention only.** There is no history to prove none was altered.
- **Tests (2026-10-09, after Chat 2 Phase 1):** `tests/` **463** OK; `tests_live/` **150**, 0 skipped, **3 failing**. Run both from `workers/` with **`..\venv\Scripts\python.exe`**.
  - **The 3 failures predate Phase 1** (the baseline was 128 with the same 3 red). They are all in `test_attempt_budgets.py`: 109 permanent `_test` commitments are past close with no snapshot, beyond `due_for_capture`'s `limit=100`, oldest first, so a fresh test commitment never makes the page. Fix: the separate `fix-sweep-starvation` PR (see §8 when it lands).
  - Phase 1 added 22 to `tests/` (`test_goals.py`) and 22 to `tests_live/` (`test_goals_sql.py`, each test in one rolled-back transaction).
  - Web after Phase 1: `pnpm test:unit` 23; `pnpm test:e2e` logged out 45 pass, 21 skip (owner-only specs). Owner run: `pnpm test:e2e goals owner` 13 pass, 3 skip; `pnpm test:e2e screens --workers=1` 2 pass.
  - **Owner e2e rules, learned the hard way on 2026-10-09:**
    - Any supabase-js sign-out in a spec must pass `{ scope: "local" }`. The default, `global`, signed the owner out of every device, including their phone.
    - Goal cleanup is scoped to the worker that made the goals (`runPrefix()`, or the `e2e-seed` note). A shared "delete every e2e goal" cleanup deleted a parallel project's goal mid-test.
    - Wait for `aria-busy="false"` before a reload: actions queue, and a reload aborts the rest.
  - Phase 3 added 14 to `tests/` (`test_push.py`) and 13 to `tests_live/` (8 in `test_push_rls.py`, 5 in `test_push_ledger.py`).
  - Phase 4 added 61 to `tests/` (`test_system_jobs.py` 42, `test_ai.py` 19) and 27 to `tests_live/` (`test_shared_020.py` 14, `test_system_jobs_sql.py` 8, `test_ai_sql.py` 5).
  - Web: `pnpm test:unit` (7 watchdog logic specs, no server) and `pnpm test:e2e` (Playwright, 43 pass, 9 skip by design).
  - The push live tests touch app tables, not the ledger. `test_push_rls` runs every case in one rolled-back transaction. `test_push_ledger` deletes its `.invalid` fixture afterwards. Neither leaves a row: a leftover subscription would be a live push target.
  - The earlier "361" was a typo made in `cf18f9d`. That commit took the suite from 356 to 360 (4 tests in `test_nfl_ml.py`), and nothing has changed `tests/` since.
  - Kalshi jobs run one at a time. The archive's SQL path has no live test, because a test row would be permanent in the real archive; its first real run is the test.
- **Quote provenance and clock (2026-10-02):**
  - Every tick stores its worker→DB clock offset on the run row (`db/016`).
  - Every commitment carries `payload.quote_provenance`: `fetched_at_worker`, plus a separately named `fetched_at_db_estimate`. Worker time is never labelled DB time.
  - Local machine: the DB is ~373 ms behind it (RTT ~55 ms).
  - **Railway: the DB is +29.9 ms ahead of the worker (RTT 139.6 ms)**, measured by the first canary run on the `workers/` path (run 1688, 2026-10-08 05:37:14 UTC). The service is in region sfo; the pooler is aws-0-us-east-1.
  - The dashboard's "#9076" matches no row in production. Its source is unknown.
- **Pre-registration stamps (`db/017`):**
  - The holdout runners refuse unless their section's hash was DB-stamped before they run.
  - F1/F2 (§8) and CFB totals are registered by the migration itself. The CFB stamp postdates its run; the git commit is its pre-run evidence.
  - `tests/test_preregistration.py` fails if a registered text is edited.
- **Gate evaluations:** nfl_ml (and the probe) write one `gate_evaluated` event per game per tick inside the commit window. Each records side, model probability, mid, adjustment, threshold, edge and pass/fail, DB-stamped. Estimate ~1,250–1,550 rows a week, ~27 MB a season.
  - **⚠ Open for the owner:** nfl_ml's commit window is the whole 24 h before kickoff and it ticks every 15 min. It therefore takes ~96 looks per game and commits on the first pass. Preregistration §2.6 states a single instant, t = kickoff − 24 h. Repeated looks inflate false passes. These events now make it measurable; the code is unchanged pending a decision.
- **Kalshi crypto research track (2026-10-02), research only, no adapter code:**
  - **Analysis:** `docs/reference-analysis-crypto.md`. Eight repos in `reference/crypto/`. Only mrose105 measured against settlements, and **the market mid beat every model**, including a learned recalibration out of sample. Four backtests are circular. The two DVOL-benchmark studies have, respectively, a selection-on-outcome leak and no outcome scoring.
  - **Verified:**
    - `KXBTCD`/`KXETHD`/`KXBTC`/`KXETH` are hourly, `quadratic` (maker 0), from ~2024-10-28. 15-min from ~2025-12-09.
    - Settlement is the 60 s simple average of CF BRTI/ERTI. **It was a trimmed mean (top and bottom 20% dropped) until 2025-03-21; simple from 2025-03-22.**
    - Kalshi candles and prints are served back to Jan 2025.
    - **1-min DVOL covers only the last ~185 days, on a rolling window** (hourly back to 2021).
    - Free historical option IV exists per trade (history.deribit.com).
  - **Scan designed, NOT run:** `docs/dev/crypto_efficiency_scan.md`. Reserve: events closing ≥ 2026-08-03, unread. **Awaiting owner review.** 1-min DVOL ages out daily: run soon or archive it first.
- **Game lines:** `nfl_ml` no dev signal, holdout PASS 0/45 (plumbing only), kept as a forward pipeline test; `nfl_spread` no signal, not built.
- **Props:** frozen V1 blend and P2 both **FAILED** the holdout (§7.4); **props do not continue** (§8).
- **CFB totals: FAILED its pre-registered holdout** (2026-10-01, `docs/preregistration_cfb_totals.md` §9).
  - Taker R +16%, CI −15%…+51% (n = 276). CLV negative. The blend's Brier is worse than the mid's.
  - The scan's base-rate weight did not replicate (0.04 vs 0.63).
  - **CFB totals stop.** The 2026 CFB data through 2026-09-27 is spent.
- **`_kalshi_probe`:** one real contract per NFL week (is_test) to exercise settlement and close capture, which have never run on real data.
- **Railway (verified 2026-10-08):** service `valemont-agents`, project `accomplished-heart`, environment `production`. **Trial plan: $5 credit, ends ~2026-10-18.** Decision due by 2026-10-16: stay on Railway's free plan if the 24 h usage check says the worker fits its $1/month credit, otherwise move to an Oracle Cloud Always Free VM before Chat 1 Phase 4. No payment method on file.
  - **`CANARY` was removed at an unknown time, and `ROSTER` was never set.** With neither set, `main.py` refuses ("No agents registered"), sleeps 60 s and exits 78, in a loop. The last canary run was 2026-09-29 23:35 UTC.
  - Restart policy is ON_FAILURE with 10 retries, so the dashboard showed "Online" while nothing ran.
  - **Restored 2026-10-08 05:36 UTC on the `workers/` path.** The service builds from Root Directory `/workers` with config file `/workers/railway.json` (merge `8ae8f14`, deployment `fba3fb6b`). `CANARY=true` was set and deployment `8adfc44d` booted. The first canary run was 1688 at 05:37:14 UTC, carrying a clock offset. `ROSTER` stays unset and `db/013` unpasted.
  - **The canary path could not boot from `a7733ac` (2026-09-29 23:02 UTC) until Chat 1 Phase 1.** `_fake` opted in to close capture, and `main.py` gave it no capture trigger, so `register()` raised. The 09-29 23:xx `_fake` runs postdate that commit, so they most likely came from `scripts/run_fake.py` locally, not Railway. Now covered by `tests/test_canary.py`.
  - **Canary cadence: 60 s** (`CANARY_INTERVAL_S`, default 60, minimum 5, applied to run, sweep and capture). **~1 MB/day of permanent `is_test` rows**.
    - Each minute writes one tick run, plus a `resolution sweep` run and a `close capture` run whenever something is due. That is 2–3 `runs` rows per minute, within the same second, or ~2,900–4,300 a day, not 1,440.
    - Measured 2026-10-08 20:14–20:36 UTC. Clustered rows at one timestamp are these jobs, not a second worker: the `notes` column says which.
  - **PR #3 merge rebuilt the worker (`workers/requirements.txt` changed): deployment `5bdbc069` SUCCESS at 20:29 UTC.**
    - The earlier `209f58a8` (20:12 UTC, REMOVED) was most likely the redeploy from adding the VAPID variables.
    - The canary kept its cadence across the switch: every gap 58.3–60.0 s between tick runs (max 69.6 s since 19:30 UTC), 0 error runs in 2 h.
    - The new instance's worker→DB clock offset is ~33 ms (it was ~90 ms on the previous one).
  - **Canary retired 2026-10-09.** The owner removed `CANARY` on Railway. Verified by `db_inspect`: the last `_fake` run is 2026-10-09 04:42:36 UTC, with none in the 12 h to 16:54 UTC, while `heartbeat`, `health_monitor` and `job_queue` stayed green. The system jobs and the external watchdog now carry the liveness signal.
  - **Step 8 usage report: PENDING** the owner's Railway RAM/CPU numbers with the canary gone, for the 2026-10-16 decision.
- **Deploy: owner GO (2026-10-01); `prod-roster` merged to `main`.** Go-live (out of scope until the owner says so):
  1. paste `db/013`;
  2. record an `nfl_ml` fit (production has **no** `model_versions` row for it, so it stands down every window until one exists);
  3. set `ROSTER=production` and delete `CANARY` in the same change.
- **🔁 RECURRING, weekly, by hand:**
  - **Archive.** Every Tuesday after Monday night's game, run `..\venv\Scripts\python.exe -m jobs.archive_nfl_props --week N` from `workers/` for the week just played, weeks 5–18 (first: week 5 on Tuesday 2026-10-13). Run weeks 1–4 once, any time, for F2's settlement pool. Rerun a week later if it logged unsettled markets. Store-only; never query the archive before January (§8.1). Estimate for weeks 5–18: ~0.6–1.1M candle rows, ~0.1–0.4M prints, ~150–250 MB of the 500 MB tier.
  - **`nfl_ml` refit** (`jobs.fit_nfl_ml --lambda 10`). Preregistration §3.1 requires it and nothing schedules it.
- **Forward-only F1 (vacated usage, as amended by F1a: Questionable + game-day `INA`) and F2 (longshots under P2)** are pre-registered in `docs/preregistration_nfl.md` §8 and evaluated on 2026 weeks 5–18. **No interim look.**
- **⏰ JANUARY REMINDER — on or after 2027-01-20:** write the F1/F2 runner exactly to §8, commit it, then execute it **once**, reading the archive. It must refuse before 2027-01-20 and refuse if output exists. If data are missing from both the archive and Kalshi, record the hypothesis as unevaluable, which is a fail.
- **Market-efficiency scan** (`docs/dev/market_efficiency_scan.md`, run #3): no base-rate weight in liquid markets. Its one candidate, CFB totals, failed out of sample. **No current build candidate.**
- **The reserve:** Kalshi events dated **2026-07-01 onward** are unread, **except `KXNCAAFTOTAL` through 2026-09-27**, now spent on the CFB holdout. Do not read the reserve in exploration.
- **Monorepo (Chat 1 Phase 1, 2026-10-08):**
  - Python moved under `workers/` with `git mv`. `db/`, `docs/`, `.env` and `venv/` stay at the root.
  - Repo-root paths go through `core/paths.py`. Every frozen pre-registration text hashes identically to before the move.
  - Inspection goes only through `db_inspect.py` (§5).
- **Web app (Chat 1 Phase 2, 2026-10-08; PR #2, merge `7e01970`):**
  - **Production: https://valemont-command.vercel.app** (Vercel project **`valemont-agents`**, scope `idctsims-projects`, Root Directory `apps/web`, production branch `main`; on 2026-10-08 the owner changed the production **domain** from `valemont-agents.vercel.app` to `valemont-command.vercel.app`, and the project name did not change). Public privacy policy at `/privacy`; that is the URL for the Pinterest app.
  - **Stack:** Next.js 16.4 (`src/proxy.ts`, which replaces `middleware.ts`), TypeScript strict, Tailwind 4.3, `@supabase/ssr` 0.12.7, pnpm workspace. `cacheComponents` is off: every route is per-request.
  - **Env (Vercel and `apps/web/.env.local`):** `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, and `OWNER_USER_ID` (server-only, marked Sensitive).
  - **Auth model:**
    - Email and password only. Supabase sign-ups are **OFF**.
    - Every route except `/login`, `/privacy` and `/offline` needs a session verified with `getUser()` whose `user.id === OWNER_USER_ID`.
    - It is checked twice: in `proxy.ts` and again in `requireOwner()` in the `(app)` layout. A missing `OWNER_USER_ID` fails closed.
    - A valid non-owner session is signed out. "Not authorized." shows only after a rejected sign-in. Verified on production with a stranger account, which was then deleted.
    - Public pages never call Supabase, so `/privacy` survives an auth outage.
  - **Data:** no ledger reads. The first app tables and RLS policies are db/019 (push, Chat 1 Phase 3).
  - **Headers:**
    - A per-request CSP: script nonce plus `strict-dynamic`, and `connect-src` limited to self and the Supabase origin.
    - X-Frame-Options DENY, Referrer-Policy same-origin, HSTS, nosniff.
    - `X-Robots-Tag: noindex` on everything except `/privacy`, and `robots.txt` allows `/privacy` only.
  - **Token system:**
    - `apps/web/src/styles/tokens.css` is the single source of truth. NIGHT (default) and DAY are `--vm-*` properties on `[data-theme]`, mapped to semantic Tailwind utilities via `@theme inline`.
    - Tailwind's default palette, radii, shadows and type scale are cleared, so `gray-*` and `shadow-md` cannot be generated.
    - The theme cookie `vm-theme` is applied server-side, so there is no flash.
    - Fonts are self-hosted via next/font: Instrument Serif, Geist and Geist Mono. Silkscreen is used only on the installed-PWA cold-start splash and the 404.
    - `/design` is the owner-approved showcase.
    - Several brief colours were adjusted for WCAG AA. The measured before and after values are in commits `7b09e51` and `f35cf19`.
  - **`pnpm lint` (in `apps/web`) = ESLint + `check:tokens` + `check:contrast`:**
    - `check:tokens` fails on any raw hex, colour function, Tailwind arbitrary value, property or variable, or default grey anywhere under `src/` except `tokens.css`.
    - `check:contrast` reads `tokens.css` and holds every text pair, including each status on its chip fill, to AA.
    - A new colour or size goes into `tokens.css` as a token or an `@utility`, never as an exception.
  - **Tests:** `pnpm test:e2e` runs Playwright against a local production build on port 3100, in desktop and 390px phone projects. Result after Phase 3: 41 pass, 9 skip by design (6 owner-only, 3 run-once).
    - `e2e/owner.spec.ts` runs only with `E2E_OWNER_EMAIL` and `E2E_OWNER_PASSWORD` set in the shell session. Never put them in a file.
  - **Deploy isolation, verified with the docs-only commit that recorded this:**
    - Vercel builds only when `apps/web` or the root lockfile/workspace changes (`apps/web/vercel.json` `ignoreCommand`).
    - Railway rebuilds only on `/workers/**` (`workers/railway.json` `watchPatterns`); the PR #2 merge showed SKIPPED there.
- **PWA and push (Chat 1 Phase 3, 2026-10-08; PR #3, merge `860aa0a`; phone-verified the same day):**
  - **Owner's phone check, all pass:**
    - installed from the Home Screen, full screen, V icon;
    - notifications enabled, device shown as subscribed;
    - web test push arrived both locked and unlocked;
    - worker test push (`scripts.send_test_push`) arrived with the phone locked;
    - tapping the notification opened `/onboarding`;
    - airplane mode shows `/offline`.
  - **Database record (read via `db_inspect`):**
    - `notifications` #7 and #8 are the web sends (20:32:22, 20:33:03 UTC) and #9 is the worker send (20:33:22 UTC), all `sent` with no error. IDs 1–6 were tests_live fixtures, deleted as designed.
    - One subscription: `iPhone · Home Screen app`, active, never failed, last success 20:33:22 (the worker send).
  - **Service worker:** `@serwist/turbopack` 9.5.13 (Turbopack-native, no `--webpack`).
    - `src/app/sw.ts` is bundled by esbuild into a static route at `/serwist/sw.js` with `Service-Worker-Allowed: /`, as a classic IIFE script for iOS 16.4+.
    - Registration is off in `next dev`.
  - **Never cache private data:**
    - The precache is only hashed `/_next/static` files, `public/icons` and `/offline`.
    - Runtime caching applies only to same-origin GETs under `/_next/static/` and `/icons/`.
    - Navigations are NetworkOnly, with the precached `/offline` as fallback.
    - `SerwistProvider`'s `cacheOnNavigation` (on by default) is OFF.
    - Playwright audits every cache after browsing.
  - **Manifest:** colours come from `tokens.css` through `scripts/gen-theme-colors.mjs`. It writes `src/generated/theme-colors.ts`, which is gitignored and regenerated before dev, build and lint. That file is the only `check:tokens` exemption besides `tokens.css`.
  - **Icons:** `pnpm gen:icons` renders a "V" in Instrument Serif, colours from tokens, with Playwright Chromium. Outputs are committed.
  - **Push:** two senders, one pair of tables, the same JSON payload (`{title, body, url, tag}`) rendered by `sw.ts`.
    - **Web:** the owner session through RLS (`(app)/onboarding/actions.ts`, `lib/push/send.ts`, `web-push`).
    - **Worker:** `workers/core/push.py` (`pywebpush==2.5.0`). Its SQL is in `core/ledger.py`. It is not scheduled until Phase 4.
    - **Both:** 404/410 marks a device inactive, endpoints are logged truncated, and keys are never logged.
    - Local worker proof: `..\venv\Scripts\python.exe -m scripts.send_test_push` from `workers/`.
  - **VAPID:** `pnpm gen:vapid` (apps/web) writes both env files without printing values. It refuses to rotate without `--force`, because rotating orphans every subscription.
    - Set in Vercel (Production and Preview, private key Sensitive) and Railway (dashboard), 2026-10-08.
  - **`/onboarding`** (account menu, desktop rail): Add to Home Screen steps on iPhone Safari (iOS 16.4+), then enable notifications (permission requested only from that tap), then send a test push. On desktop it shows a phone-only note.
- **Scheduler, health and watchdog (Chat 1 Phase 4, 2026-10-08/09; PRs #4–#7):**
  - **System jobs always run** (`core/system_jobs.py`); ROSTER and CANARY govern only agents. Each records itself in `job_health` through `core/jobs.py` `run_tracked` (errors redacted, then re-raised).
    - heartbeat, 60 s;
    - `health_monitor`, 60 s, first pass 90 s after boot;
    - `db_size`, 03:00 America/Chicago and at boot; 15.2 MB on 2026-10-08;
    - `job_queue`, 10 s;
    - `health_drill`, only with `HEALTH_DRILL=true`.
  - **Health monitor:** one push per incident (2 failures in a row, or stale beyond 2× its interval) and one on recovery, linking to `/settings/health`.
    - It never judges its **own** staleness (PR #6). Every redeploy used to push a false "health_monitor needs attention" + "Recovered" pair.
    - The external `watchdog` row is watched with the monitor's own incident state and never its `alert_state` (PR #5). That field belongs to the Vercel route.
  - **External watchdog** `/api/watchdog` (Vercel, called by cron-job.org every 5 min with a bearer token):
    - pushes "Worker down" when the heartbeat is more than 5 min old (again at most every 30 min), and "Worker back" on recovery;
    - answers 200 ok/down, 503 when it can't read the heartbeat, 401 for a bad token;
    - a failed push never fails the request (PR #7): it's recorded in `job_health.last_error` and retried;
    - pings **healthchecks.io** (`WATCHDOG_PING_URL`, period 5 min, grace 5 min) on every run, and `/fail` when it can't read;
    - `SUPABASE_SECRET_KEY` is read only in `route.ts` (`check:secret-key`).
  - **Known Supabase issue:** PostgREST intermittently rejects the gateway's per-request token as **"JWT issued at future"** (PGRST303), because the gateway's clock runs ahead of PostgREST's.
    - Seen in production at 03:00, 03:05 and 03:30 UTC on 2026-10-09 (500s on the old route), and in local calls.
    - The route now retries once (rescued the 04:35 drill call). Persistent skew is a 503 plus healthchecks `/fail`.
    - The worker connects to Postgres directly and is unaffected. Worth reporting to Supabase if it continues.
  - **AI budget guard** `core/ai.py` (§6). There's no `ANTHROPIC_API_KEY` yet, so every AI call is refused loudly.
  - **Drill 1 (failure) PASSED 2026-10-09:**

    | UTC | Event |
    |---|---|
    | 02:41:00 | Deploy with `HEALTH_DRILL=true` |
    | 02:42:53 | "health_drill needs attention" (#39), about 92 s after boot |
    | 02:46:10 | Variable unset; "Recovered: health_drill" (#40) |

  - **Drill 2 (kill) PASSED on rerun 2026-10-09:**

    | UTC | Event |
    |---|---|
    | 04:20:44 | Last heartbeat |
    | 04:21:32 | Deployment `f8c246e5` removed; clean SIGTERM drain |
    | 04:25:03 | Cron 200 ok (heartbeat 259 s old) |
    | 04:30:06 | Cron 200 **down**: the first call past 5 min stale (562 s). "Worker down" sent 04:30:08 (#45). Read 373 ms, lookup 78, push 534, stamp 74, ping 325 |
    | 04:31:22 | Redeploy `7cb09719` created; booted ~04:31:36 |
    | 04:35:03 | Cron 200 ok; "Worker back" sent 04:35:04 (#46). The first read hit "JWT issued at future" and was retried (read 590 ms) |

    - Every cron call returned 200, every run pinged healthchecks with no errors, and there were no false alerts from the redeploy.
    - The first run, at 03:00 UTC, failed: two 500s from the Supabase clock skew. That's what PR #7 fixed.
- **Goals (Chat 2 Phase 1, 2026-10-09; PR #8, merge `464c6ea`):**
  - **db/021:** `goals` is an app table, edited in place except its carry history.
    - `carried_from` is UNIQUE, so carry-over is idempotent.
    - A trigger checks every carried row against its original.
    - The owner may UPDATE only title, notes, area, status, sort_order and completed_at.
    - `carry_over_goals(horizon, from)` refuses a period that has not ended in America/Chicago.
    - `goal_periods_to_roll(horizon)` is the catch-up list; `carry_goal(id)` is "move to next week".
  - **Worker:** `goals_rollover`, Mon 00:01 and the 1st 00:01 Chicago, and at boot, oldest period first. `goals_monday_push`, Mon 07:00, never at boot (`SystemJob.run_at_boot=False`).
    - Both seen in production: on deploy `9bf52dd3` (boot 18:26:29 UTC) `goals_rollover` ran OK at boot and `goals_monday_push` did not.
    - Scripts: `-m scripts.goals_rollover --horizon weekly --from <Monday>` prints the count only; `-m scripts.add_goal` inserts one goal.
  - **The slot rule:** a goal holds one of the week's 10 slots when it is open or done in this period; dropped and moved-on goals hold none. Web: `holdsSlot`/`weekTally` (`src/lib/goals/types.ts`). Worker: `ledger.WEEK_GOAL_COUNTS_SQL` and `WeekGoalCounts.held`.
  - **Web:**
    - `/` Home: the date is the hero, "Week 41 · Q4", then This week. The Morning Brief and feed slots are named and render nothing.
    - `/goals`: Week / Month / Long term / History.
    - `ensureRollover()` runs the same SQL catch-up while Home and Goals render.
    - Server actions with `useOptimistic`; "saving" plus `aria-busy` while any is queued.
    - "Moved on · N" fold; swipe gestures, each with a row-menu path.
    - `lib/nav.ts` holds all 16 pillars with `built`; only built pages render. The `[section]`/`[pillar]` placeholders are gone, so unbuilt pillars 404.
  - **Rollover end-to-end on production (2026-10-09 18:27 UTC):** `add_goal` "Rollover test" in week 2026-09-28, then `goals_rollover --from 2026-09-28` returned **1**, then **0**. The carried copy sits in 2026-10-05 with carry_count 1; it is a real row, the owner drops it.
- **Next:**
  - **`fix-sweep-starvation`** (owner-approved, its own branch and PR). Invariants:
    - no missed or void write with zero attempts;
    - never-attempted rows first, so none is starved past `sweep_limit`;
    - a written answer on whether `capture_close` reads the current price or the candle at `closes_at`;
    - `test_attempt_budgets.py` made deterministic, with no ledger deletes;
    - `tests_live` ends at 0 failures.
  - **Chat 2 Phase 2:** Ventures HQ (seven ventures).
  - **Carried from Chat 1:** the Step 8 usage report (owner's numbers), for the 2026-10-16 decision.
  - Still pending from before: owner review of the crypto track, owner go-live, and the weekly archive.
  - Any new idea needs a new pre-registration and forward-only validation.
