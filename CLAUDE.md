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
  sports/espn.py   The sports data layer (Chat 7; docs/sports_data.md): ESPN
                   fetch, schema validation, parse to sports_events rows.
                   sports/jobs.py (sports_schedule, sports_live) and
                   sports/provider.py (the API-Sports fallback, wired to nothing)
                   beside it. No SQL: ledger.sports_events_upsert writes.
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

- **Vercel never holds a database URL** (owner, 2026-10-10). The web app
  reaches Supabase only through the publishable key and the owner's session
  (RLS applies), plus the watchdog's scoped secret key (`SUPABASE_SECRET_KEY`,
  read only in `/api/watchdog`, `check:secret-key`). `DATABASE_URL` and
  `DATABASE_URL_READONLY` were found on Vercel Production and Preview, unused
  by `apps/web`, and deleted from every environment on 2026-10-10. Never add
  either back.
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
- **Every migration that creates or replaces a trigger or function ships a
  rolled-back self-test DO block that exercises it** (the db/032 and db/035
  pattern): do the work inside a nested `BEGIN … EXCEPTION` block, check
  each behaviour, end with `RAISE EXCEPTION 'selftest-rollback'` caught
  outside it, so no row survives and a wrong result fails the whole paste.
  - Why (2026-10-11): db/034's widened `content_items` freeze trigger pasted
    cleanly and broke **every** UPDATE on `content_items` (the nightly
    prune's embedding clear among them). Only the live suite caught it;
    db/035 fixed it.
- **A function with an empty `search_path` must not rely on unqualified
  extension operators or types.** pgvector lives in `extensions`: qualify
  types (`extensions.halfvec`), and compare vectors by their text form
  (`v::text`), not with `=` / `IS DISTINCT FROM`, which need pgvector's
  operators. That was db/034's bug (2026-10-11).
- **No staging project: decided against, 2026-10-13 (owner, Chat 4
  planning).** It reverses the same day's "staging first" decision, which
  was never set up. Migrations stay paste-to-production. The guard is the
  rolled-back self-test DO block above: a wrong result raises, the SQL
  Editor's transaction rolls the whole file back, and nothing is applied.
  - Keep db/042's lesson (2026-10-13): its first paste failed in its own
    self-test on production and applied nothing, because TRUNCATE checks
    foreign keys before any TRUNCATE trigger. **A TRUNCATE assertion on a
    table that other tables reference must name every referencing table in
    the same statement.**

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
- **Owner e2e credentials only through `Read-Host`, the password as a
  SecureString; never a typed literal.** Exactly this form, one line per
  step (apps/web/README.md, "Owner suite"):

  ```
  Set-Location C:\Users\12563\Projects\valemont-agents\apps\web
  $env:E2E_OWNER_EMAIL = Read-Host 'Owner email'
  $env:E2E_OWNER_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR((Read-Host 'Owner password' -AsSecureString)))
  pnpm test:e2e <exact spec path> --project=phone
  Remove-Item Env:E2E_OWNER_PASSWORD, Env:E2E_OWNER_EMAIL
  ```

  - Why (2026-10-11): PSReadLine saves every typed line to its history
    file, so `$env:E2E_OWNER_PASSWORD = '...'` leaves the password on disk.
    A `Read-Host` prompt is not a command line, and `-AsSecureString` masks
    it. Hand the owner this form, never a literal to fill in.
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
- **Never use `git stash` inside a commit flow.** Before every commit,
  `git status --short` must show the expected file set; after it,
  `git show --stat HEAD` must match that set. A mismatch is a stop: fix it
  before anything is pushed.
  - Why: on 2026-10-10 a `git stash push --keep-index` in a commit command
    silently set aside the 22 modified files of Chat 2 Phase 4 (including
    `package.json` and `core/ai.py`), so both commits were missing them.
    It was caught before the push and recovered from the stash and a
    backup branch.
- **One Claude Code session per checkout (owner, 2026-11-05).** A second
  session works in its own git worktree
  (`git worktree add -b <branch> ..\valemont-agents-<topic> origin/main`),
  never in the same directory. Before a branch's first commit, check its
  base: `git log --oneline origin/main..HEAD` must list only this branch's
  own work.
  - Why: on Nov 5 two sessions shared this checkout. PR #87's commit
    `d4d3cb6` was made while `chat7-phase3` was checked out, so it landed
    under Chat 7 Phase 3's first commit and rode into PR #88. It was
    noticed only at a later push.
  - **A worktree has no `.env`** (gitignored; `core/paths.ENV_FILE` is the
    worktree's own root). `tests_live` there skips every class ("DATABASE_URL
    is not set"), and a run that skips everything proves nothing. Run the
    gate from the worktree with the main checkout's `.env` loaded into the
    process (`dotenv.load_dotenv(<main>\.env, override=False)`, nothing
    printed), or from the main checkout. Never copy `.env` into a worktree.
    The same holds for `apps/web/.env.local`. (Nov 5: db/069's first gate
    attempt ran 0 tests.)
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
  ..\venv\Scripts\python.exe -m unittest discover -v -s tests_live -t .
  ```

  **Always `-v` (owner, 2026-10-13):** a quiet run reports "skipped=1" with
  no name or reason, and a skip that cannot be named cannot be cleared. With
  `-v` every skip prints its test and reason; keep the output. No merge with
  an unnamed skip.

  **A full `tests_live -v` run is the normal merge gate again (owner,
  2026-10-15), because one now costs ~2 MB of Supabase egress.** Measured on
  PR #48 with every connection metered (`core/egress.py`'s cursor): 391 tests,
  ~2.21 MB read. Before egress fix 4 one run read ~90 MB (est.), and runs were
  restricted to named classes while the project was over the Free tier's 5 GB
  a month. Keep it cheap: a test reads its own rows (filter by its ids, in the
  database), never a whole real table, window or `feed_for_you(…, 100000)`
  with wide columns; the twin-order tests read every row by design, narrow
  columns only. If a full run measures over ~10 MB, fix that before merging.

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
  from `workers/`) ✓ (Step 8, the Railway usage check, closed 2026-10-09: stay on Free)
- **Chat 1, Phase 2** (Next.js app, owner-only auth, design tokens, Vercel) ✓
- **Chat 1, Phase 3** (PWA shell and push) ✓ (phone-verified 2026-10-08)
- **Chat 1, Phase 4** (db/020 shared tables, scheduler, `job_health`, push alerts) ✓ (Drills 1 and 2 passed; canary retired 2026-10-09)
- **Chat 1 complete.**
- **Chat 2, Phase 1** (Goals, plus the app shell navigation registry) ✓ (PR #8, merge `464c6ea`). **Owner's phone checklist DONE, 2026-10-09: all items green**, except the two that need the real clock: the Monday 00:01 rollover and the 07:00 "Set your week" push. **Both seen on Mon 2026-10-12** (Current State, "Carried out of Chat 2").
- **Sweep starvation fix** (PR #10, §8) ✓
- **Chat 2, Phase 2** (Ventures HQ) ✓ (merged 2026-10-09; see Current State). **Owner's phone checklist: ALL GREEN, 2026-10-09.**
- **Follow-up, own branch and PR:** the "never abandon an untried commitment" database invariant (db/024, db/025) ✓
- **Chat 2, Phase 3** (Capital Tracker, paper) ✓ (PR #14, merge `95a7c2a`, live 2026-10-10). Owner's signed-in runs on the final code: `capital.spec` 9/9, `screens-capital` 2/2, `goals.spec` 6/6 (phone project).
- **Chat 2, Phase 4** (context layer and Wags, with the TypeScript twin of the AI budget guard) ✓ (PR #16, merge `7709d22`, live 2026-10-10; owner's real call and phone checklist green). Owner runs on the mock model: `wags.spec` 10/10, `screens-wags` 2/2.
- **Chat 2, Phase 5** (Morning Brief generator on Railway, sections switching on as pillars register) ✓ (PRs #20, #21). **First scheduled brief pushed Sun 2026-10-11 09:45 Chicago; owner confirmed arrival.**
- **Chat 2 complete (2026-10-11).**
- **Chat 3** (Learning engine, Home feed, Sports News; MASTER_PLAN §6) ✓ **(closed 2026-10-13: Phases 1–3 GREEN, PR #27 merge `922e22d` live; the close-out with evidence and carried items is in MASTER_PLAN §6, Chat 3).** The db/044 entities follow-up is the last open piece.
- **Chat 4, Phase 1** (Tech Hub: official changelogs and Hacker News) ✓ **GREEN 2026-10-14 (owner)**; of its three watch items, latency and memory are closed (owner, Oct 14) and the why-it-matters day cost is pending (Current State, "Tech Hub"). PRs #29–#34; migrations db/045–db/048.
- **Chat 4, Phase 2** (GitHub trending, "why this matters to you") ✓ **GREEN 2026-10-14 (owner)**: screenshot gate approved (phone, desktop, both themes); Wags' repo question green; day one graded 7/8 concrete (`docs/tech_hub.md` §7.2b). PRs #36–#39; migrations db/049–db/050. **Two watch items, non-blocking, over the Oct 15–16 runs:** a Not interested on a repo topic drops that kind of repo; no repo surfaces twice outside the resurface rule.
- **Chat 4, Phase 3** (Screening Room core: TMDB, shelves, where to stream, the trailers pillar), not closed (two items carried, below). PRs #42–#43 merged (live Oct 15, worker deployment `fd68c9d9`); db/051–db/052 applied. **Owner, Oct 15: phone checklist green, the screenshot gate green, `e2e/screening.spec.ts` green** (6 + the fixed hidden-title check). **(1) Trailers within one cycle: CLOSED GREEN (owner, 2026-10-16).** The YouTube outage (~23:50 UTC Oct 14) ended before 24 h; all 15 channel feeds answered again by 05:20 UTC Oct 15. Of the 36 trailers ingested from then to 00:10 UTC Oct 16, 29 were first-fetch backfill or seeded (not latency samples); **all 7 live trailers were first seen within one cycle of publication: median 21.2 min, max 21.4 min** (read via db_inspect, fetched_at minus published_at). **(2) RAM at rest: pending, closes with Phase 4's on the same reading (rule below).**
- **Chat 4, Phase 4** (Screening Room recommendations: seeds, profile, For you, the seen-it pass, Trailers For you, TMDB trailers, release alerts, In the news; `docs/screening.md` §7), open on the six readings below. db/053 applied. **PR #46 merged 2026-10-15 19:53:58 UTC, ahead of the egress follow-up it was to wait for; the owner kept it ("fine, keep it")**. Live on the worker since the 19:58 UTC boot: the first `screening_recs` run (20:08 UTC) scored 294 titles, 271 eligible, and wrote the 30-title seen-it pass. **Owner checks green, Oct 15:** day one's For you (8+ of 12 fit the seeds, each with its "because"); the seen-it pass (30/30: 12 seen-loved, 18 not seen); reactions (3 Not interested on cards; 3 Loves: Pretty Woman's title page and two trailers), each queuing a re-rank (`screening_rec_runs` 7–11); shelves (2 Watching, 2 Finished). **Not run (pending):** `e2e/screening-recs.spec.ts`, the screenshot gate, the test release push (read via db_inspect and this checkout: no rows or files from any of them). **Not yet reported:** In the news, the Brief's Screening line with Wags' "what to watch tonight", Trailers For you. Detail: MASTER_PLAN §6, Chat 4.
  - **Chat 4 closes only when these six readings are in (owner, 2026-10-16):** (1) recs shift after the reactions, at the next refresh; (2) nothing marked seen appears in recs, across the next 2 refreshes; (3) the real release push, the first morning a watchlisted title drops; (4) the RAM 6 h curve on #50's worker after 07:15 UTC Oct 16 (the new baseline if flat); (5) the egress meter's Oct 16 total, after 05:00 UTC Oct 17; (6) Phase 2's repo watch items, the Oct 15–16 runs. Draft close-out: `docs/drafts/chat4-closeout.md`.
  - **RAM rule (owner, 2026-10-16): the ~15 MB band was a leak check, not a budget.** Read the worker at rest over 6+ hours; if the curve is flat (no upward trend), that reading is the new baseline and both RAM items (Phase 3, Phase 4) close on it. Investigate only if it climbs past ~160 MB at rest. Readings so far: 138 MB (Phase 2); the fix-1 + #46 worker (19:58 UTC Oct 15 to 00:06 UTC Oct 16) crept from 143 MB to 151–154 MB at rest, ingest peaks 157–165 MB; the 6 h read is taken on the worker from #50's deploy (01:10 UTC Oct 16), which carries #48 and #50.
  - **Supabase egress over the Free quota (owner, 2026-10-15):** 5.76 GB of 5 GB in the cycle ending Oct 18, ~1.7–1.9 GB a day since Oct 12. Cause: worker reads returning vectors as text (~4.7 KB each), chiefly ingest's 48 h candidate set every 30 min (~17 MB a run, ~800 MB a day), then full `tests_live` runs (~90 MB each). **Fix 1 (PR #47, merged 19:58 UTC):** candidates cached in worker memory, a full reload at boot and daily; measured on Railway: boot ~16,583 KB, then incremental runs ~79, ~28, ~28 KB. **Follow-up (PR #48):** binary vectors (6× smaller, measured), summarizer pools without vectors, `tests_live` reading its own rows, and the egress meter (`core/egress.py`; the `egress` job, one push a day past 150 MB). Full `tests_live -v` on #48, every connection metered: 391 tests, ~2.21 MB.
  - **Phase 3 news categories: SHIPPED STATE recorded 2026-10-13** (owner: final round, no more tuning). The enum plus one business-of-sports yes/no question (`feed/category.py` `MONEY_QUESTION`, `decide`), the last re-run, the day's cost ($0.046755 over 93 Haiku calls) and the misfits left for later (no broadcasters or MLB/NWSL/WNBA/NCAA in the entities table; LeBron features answered yes): `docs/feed_checks/recategorize-2026-10-13-final/README.md`.
  - **Phase 1** (sources, ingest, dedupe, summaries, embeddings) **GREEN 2026-10-13** (owner): For you page one summarized (Home 30/30, Sports 29/30, the one a designed headline-only retirement), and worker RAM flat at ~130 MB at rest, ~155 MB ingest peak, returned after each ingest (readings in Current State, "Railway").
  - **AI SDK stays on the maintained v6 line** (`ai` 6.0.303, `@ai-sdk/anthropic` 3.0.129, `@ai-sdk/react` 3.0.306, pinned). v7 was latest on 2026-10-10; **upgrade in Chat 13 hardening**, never a major bump mid-phase (owner, 2026-10-10).
- **Chat 5, Phase 1** (the Arsenal: pool, verification, outcome check, daily drop, Power Word; `docs/arsenal.md`) ✓ **GREEN 2026-10-21 (owner)**. PRs #53–#57 and `chat5-fixes`; migrations db/054–db/057. Evidence, read via db_inspect on Oct 21: **drop 331** (10:30:00 UTC; refs 199 film_tv / negotiation, 711 finance_business / boardroom, 205 sports / homies, each with an outcome check on record (5, 15, 7, all `nnn`); word 161 *incumbent*, pitch; rooms distinct; no top-up); **brief 1168** (Sonnet, not a fallback; Arsenal line "Power Word: incumbent. Strongest reference: See's Candies, for the boardroom."; Screening line "Tonight's top pick: Love Actually on Netflix, because you loved Holidate."), pushed 14:45:00 UTC (09:45 Chicago), delivery 440 → notification 603 `sent`. Owner runs: `arsenal.spec` 8/8 (phone), `screens-arsenal` phone and desktop, both themes.
  - **Carried, non-blocking:** worker RSS 150 MB at rest ~20 h after the 21:51 UTC Oct 20 boot (141 MB at 22:53 UTC; the 6 h read is pending); `feed_prune` cleared 544 embeddings on Oct 21 against the 5,572 reported blocked Oct 18–19 (the gap is unexplained); verifier yield (~40% of a batch clears); the over-cap why-line trim.
- **Chat 5, Phase 2** (Arm Me, My Arsenal saves, the Monday quiz; `docs/arsenal.md` §9) ✓ **CLOSED 2026-11-03 (owner).** Evidence (read via db_inspect, Nov 3): 5 real Arm Me requests (25, 28–31; Sonnet; 9.2–11.5 s total, first card 4.8–7.5 s; ~$0.020 each; no error), all within the bar, which is now "well under a minute" (owner decision Nov 3; the 10 s target dropped); verify-on-save settled all 4 saved arm cards on the first attempt (1852 corrected, 1965 verified, both `nnn`; 1962 and 1964 unverified, both sources insufficient); requests 28 and 29 showed 2 cards, fixed in PR #69 (ask for 4, show the first 3 that pass, fill an empty slot with one more call; Arsenal context capped to 30) and replayed in `e2e/arsenal-unit.spec.ts`. **Saves, unsave, Undo, My Arsenal and the quiz: verified by specs, per the owner (Nov 3)**: the owner's Oct 21 runs (owner-reported Nov 3): `arsenal-arm` 7/7, `arsenal-saved` 6/6 on rerun, `arsenal-quiz` 3/3, `arsenal` 8/8, `screens-arsenal-p2` phone and desktop pass. The Undo fix that came after them: **not re-run (owner)**. **Carried:** (1) the 3-card check on real requests rides on the owner's next natural Arm Me uses: report the first 3 (0 since #69 deployed 20:02:16 UTC Nov 3); (2) the first real Monday quiz: it needs 4+ saves, and the owner has 7 (all references), 0 real quizzes as of Nov 3. History: PR #62; db/059 applied 19:52:26 UTC Oct 21; merge gate `tests_live -v` 453 OK, 0 skipped, ~3.31 MB. **Owner e2e on db/059 (Oct 21, phone):** `arsenal-arm` 7/7, `arsenal-quiz` 3/3, `arsenal` 8/8, `screens-arsenal-p2` phone 1/1 and desktop 1/1; `arsenal-saved` 1 passed, 1 failed (a real missing Undo), 4 not run (the owner's rerun that day: 6/6, owner-reported Nov 3). **The pre-merge fixes (the Undo, Arm Me's Source link, the quiz's masked title and "Right answer" label, "Saved") were not re-run (owner, Oct 21)**: unit tests, lint and tsc only; the owner checks them on the phone after the deploy. Pending: those phone checks, the first real Arm Me call (**superseded 2026-11-03, owner: the 10 s bar is dropped; the bar is "well under a minute"**; the first 5 real requests took 9.2–11.5 s and pass; `docs/arsenal.md` §9.3), 5 real requests, verify-on-save results, Monday Oct 26's quiz line, worker RSS, the Arsenal jobs' KB read; then the `Categories` egress fix, its own PR.
  - **As of Nov 2:** 0 real Arm Me requests; 2 saves (the quiz needs 4; no quiz yet). The verifier yield fix is open: 41 unverified references wait on it. (Nov 3: 5 real requests, 7 saves; the `Categories` egress fix shipped in PR #68.)
- **Chat 5, Phase 3** (the Screening Room hook: a finished film or show drafts its references into the Arsenal, once per title; `docs/arsenal.md` §10) ✓ **CLOSED 2026-11-04 (owner), on the backfill** (no extra Finished needed). PR #71 (merge `b621ac0`, 21:27:04 UTC Nov 3); db/062 applied 20:48:02 UTC Nov 3. Deploy seen: the worker rebooted ~21:27:33 UTC with `arsenal_title_hook` and `arsenal_title_hook_sweep` registered (job_health), Railway's GitHub deployment `success`; Vercel production Ready 21:27:12 UTC. Merge gate: `tests/` 1151 OK; full `tests_live -v`, metered, 471 OK, 0 skipped, ~2.80 MB. **Owner runs (owner-reported, Nov 3):** `arsenal-hook` phone, `screens-arsenal-hook` phone and desktop, passed. **The 390 px "From <title>" ellipsis check (added after, `e72aeec`): not run (owner).**
  - **Backfill (owner decision, Nov 3; read via db_inspect):** `scripts.arsenal_title_hook --backfill --by "owner Nov 3"` queued hooks 57–64 for the 8 real titles Finished before db/062 (dry run first: the same 8); every job done on its first attempt, 23–80 s each. **28 cards, all film_tv: 8 cleared (all corrected, outcome `nnn`, none held), 20 unverified** (5 insufficient, 5 contradicted, 5 wrong page, 5 outcome conflicts at verification, by the Nov 4 diagnosis). (The Nov 3 report said 10 and 18: a summing error; its per-title rows were right.) Words: 5 verified, 2 unverified (no etymology), Pretty Woman none (it repeated *leverage*). Kissing Booth 1 and 2 kept 1 card each (3 near-duplicates of Kissing Booth 3's). **Cost $0.305** (Sonnet 8 calls $0.2625; verify 41 Haiku $0.0184; outcome 69 Haiku $0.0242), ~$0.038 a title, inside the $0.035–0.045 estimate; Sonnet input ran ~10,000–10,700 tokens, about twice the estimate (the never-repeat list).
  - Retrieval misses seen: Power's second source was Wikiquote's page on the concept of power; an Odyssey card's was the soundtrack page. They go to the verifier yield fix.
- **Next.js security (owner, Nov 4):** PR #72 `next` 16.4.0 → 16.4.5 (two critical unauthenticated RCE advisories and a high DoS, fixed from 16.4.1), merge `0f273cd` 05:05:10 UTC Nov 4. PR #73 (merge `e85ad39`, 05:08:07 UTC): the service-worker precache limited to build output, `public/icons` and `/offline` (`/tmdb-logo.svg` had been precached since Oct 14; `pwa.spec` red on main since then, found Nov 3), `zod` 4.6.6 and `undici` 6.29.1 (pnpm overrides; AI SDK untouched); logged-out e2e 95 passed, 0 failed. `pnpm audit --prod` still lists `minimatch` and `glob` (open).
- **Chat 5's last item, the verifier yield fix: STOPPED by the owner, Nov 4.** The clear rate does not block: an unverified card never drops, the refill rule covers supply, and precision held (known-bad 7/7 every run). PR #75 (db/063) and PR #76 (db/064) shipped. The fresh batch on #76 (runs 151–153) inserted 33 of 36, with 0 invalid references, and cleared 32%. The comparison and the re-verifications are skipped (`docs/arsenal.md` §11.7–§11.9). PR #77: the outcome control reruns once before refusing and logs each failed control's votes and reason. On Nov 4 the controls refused 14 of 19 worker passes. Which control failed there is only in Railway's logs, not read. In 3 read-only passes, **Custer** was wrong twice (retrieval reads the 1936 film serial's page). The worker's retries still checked all 14 cleared references from runs 120 and 151, all `nnn`.
- **Chat 5 CLOSED 2026-11-04 (owner).** Carried, each pending until read:
  - verifier recall on source-thin cards;
  - the 125 unspent re-verifications (they stay flagged);
  - the worker's next outcome run with the pinned controls (PR #78; Custer's page pinned, 3 read-only passes 9 of 9): its control result;
  - the 3-card check on real Arm Me use (0 as of Nov 4);
  - the first real Monday quiz, Mon Nov 9;
  - the Undo fix re-run, the 390 px From-line check, worker RSS at rest;
  - **#77 deployed** (merge `4b9c47d` 18:38:41 UTC; the worker's boot runs 18:39:06 UTC). **#78's pins live on the worker: pending until it deploys.**
- **Chat 7, Phase 1** (ESPN adapter, `sports_events`, `sports_schedule` and `sports_live`; `docs/sports_data.md`) ✓ **GREEN 2026-11-04 (owner).** PR #79 (merge `6b73d87`) and PR #80, the live cache fix (merge `89bbf12`, 20:26:06 UTC Nov 4; gate `tests/` 1238 OK, full `tests_live -v` metered 483 OK, 0 skipped, ~2.79 MB); db/065 applied. Evidence (read via db_inspect and the job notes, sampled every 60 s):
  - **One game end to end:** PHI at DET (nba 401909922) scheduled 20:00:26 UTC; live first written 00:13:39 UTC (Q1 11:55, 0–0, at the real tip); every change written in its minute through end Q1 00:41, halftime 01:27, Q3 01:42, end Q3 02:11; final 02:57:45 UTC (PHI 112–106), not polled after.
  - **0 unchanged writes:** 380 live runs after the fix, every row sent was written (the 81 tennis re-sends at 20:01 were before it).
  - **ESPN at peak:** ~214 calls an hour (00 UTC; 01 UTC 212), ~3.4 MB over the wire; off-peak 60–174.
  - **Supabase reads:** ~0 KB a live run, ~12 KB a schedule run.
  - **RAM: new baseline ~155 MB at rest** (owner): 148–158 MB from 21:00 to 02:40 UTC with one 161 MB heartbeat; 151 MB at 03:29 UTC, past the 6 h mark: flat, under ~160, closed. Busiest-job peak 160 MB.
  - **The 60-day ceiling on the seen-rows map** (whatever its status; the settled-and-14-days prune is the normal path): **owner-accepted.**
  - **Watch items:** golf's live `score` (position and thru for Scheffler and Koepka) on PGA round 1, Thu Nov 5 (fixture-verified only so far); the F1 sprint shape at the first sprint weekend (an unknown session type fails loud).
    - **Nov 5 (read via db_inspect and ESPN):** the World Wide Technology Championship R1 row (206) is live, and its `score` parses on production data: 5 leaders, the first Chris Kirk, pos 1, thru 18, −9. **`followed` is `[]`, correctly:** neither Scheffler nor Koepka is in the 120-player field. **The followed check stays pending** until a week either one plays.
    - **Found the same day: `sports_schedule` fails PGA on every run since 03:31 UTC Nov 5** (`events[1].competitions[0].competitors: missing`; the other 13 leagues write). ESPN's dated range lists the next tournaments (Austin, the RSM) with no `competitors` key until their field is set. Fix: PR #84 (the field optional only while `pre`), merge `1db01e7` 20:14:58 UTC. **Verified:** the worker booted 20:15:25 UTC; the boot `sports_schedule` ran OK at 20:16:05 UTC (pga 8 rows, Austin's 4 rounds inserted; 0 failures); "Recovered: sports_schedule" was pushed at 20:16:48 UTC (notification 930), `alert_state` ok.
- **Chat 7, Phase 2** (Kalshi mapping: `kalshi_map`, db/066; `docs/kalshi_mapping.md`): ✓ **GREEN 2026-11-05 (owner),** on the 22:00 UTC `kalshi_map` run (read via db_inspect, `job_health`): 22:00:00–22:00:07 UTC, ok, 0 failures; **0 sent / 0 written / 0 conflicts** (sent = changed: nothing moved since the 21:35 boot run); 15 Kalshi calls, 0 429s, 7.5 s, ~95 KB read; RSS 141 MB, job peak 143 MB. **USC at Indiana linked** (db/067): row 10190 → `KXNCAAFGAME-26NOV14USCIND`, `team_pair`, not swapped, `linked_at` 21:34:35 UTC (#87's boot run); cfb linked 130. PR #83, rebased onto #82 with no conflict (both CLAUDE.md records kept), `tests/` 1314 OK; merge `d174095`, 20:03:28 UTC. The worker booted 20:03:58 UTC (`capital_snapshot` and `db_size` boot runs), with `kalshi_map` registered (21,600 s). An earlier boot at 20:01:14 UTC, before the merge, carried no `kalshi_map`. Read via db_inspect and the Railway log:
  - **First run** 20:04:29–20:05:17 UTC, chained from the boot schedule: 802 sent, 802 written (all first states), 0 conflicts; contract 14/14 series ok. **Per run: 33 Kalshi calls (4 answered 429; the run did not fail), 48.4 s, ~97 KB read from Supabase, RSS 102 MB, job peak 106 MB.**
  - **Per league:** cfb linked 129, not_listed 1, not_linked 1; nfl 29 / 1 / 0; nba 25 / 28 / 0; f1 linked 1, out_of_scope 1 (Qual: KXF1RACE lists the race winner only); pga linked 3; mcbb 178 / 215 / 55; nhl 49 / 10 / 0; epl 10; mls 15; atp 3 / 1 / 4; wta 8 / 13 / 7; ufc 14 / 0 / 1. Kalshi unmatched: cfb 124, mcbb 1.
  - **Every NFL and NBA game inside Kalshi's listed window is linked** (NFL 29, to Nov 17; NBA 25, to Nov 9). Each not_listed starts after the series' latest listed event (NFL 1, Nov 20; NBA 28, Nov 10–13). Last night's 14 NBA finals (Nov 5 00:00–03:30 UTC) have no state: they started before `EVAL_BACK` (6 h), by design.
  - **Every not_linked row has its reason:**
    - cfb: USC at Indiana (10190), "no KXNCAAFGAME event for USC/IU". **This is an alias gap:** Kalshi lists `KXNCAAFGAME-26NOV14USCIND`, and `sports_team_aliases` has `IU → IND` for mcbb but not for cfb. Fixed by db/067 (applied 20:22:22 UTC Nov 5): linked at 21:34:35 UTC.
    - mcbb 55: no Kalshi event within ±1 ET day. The opponents read as non-Division I, but they were not checked one by one.
    - atp 4 and wta 4: started before their first check.
    - wta 3 and ufc 1 (Bashi / Calderon): no event.
  - **0 relinks** (`sports_event_relinks` empty) and **0 link conflicts.**
  - **NFL and CFB out to 14 days:** both to Nov 20. The boot schedule run inserted 79 rows.
  - **After #84's reboot (20:15:55–20:16:05 UTC):** sent 333, written 4, 0 conflicts; 29 Kalshi calls, 0 429s, 9.9 s, ~95 KB read, RSS 104 MB (peak 108). By design a restart re-sends every non-linked row once (the sent map is process memory). **The database wrote only the 4 that changed** (Austin's new rounds, not_listed), so it wrote 0 rows without a change. Neither run failed on a 429: the client backs off by itself.
  - **The alias sweep, Nov 5** (the FOR REVIEW method over all 56 not_linked team_pair rows, against open Kalshi events): one alias, cfb `IU → IND`. The 55 mcbb rows have no unclaimed Kalshi event on their date: all non-Division I opponents. KXNCAAMBGAME's one unclaimed event, `…26NOV08PITTPSU`, has no ESPN row on Nov 7–9, with or without groups=50: an ESPN gap, not an alias.
  - **Sent = changed: done** (the 22:00 UTC run, above).
  - **Carried, not part of the GREEN:** RSS at rest past 6 h, against the ~155 MB baseline. #84's curve was cut by the #87/#88 reboots; it restarts at #88's boot (21:34:48 UTC Nov 5), so the read is due after ~03:35 UTC Nov 6 (#89's deploy restarts it again: recorded there). **Closed Nov 6 on #91's boot: baseline ~140–150 MB at rest** (Chat 7 close-out, below).
  - **`job_health` cuts a note at 500 characters,** so this run's RSS and KB suffix is only in the Railway log line.
- **Chat 7, Phase 3** (Game Day `/gameday`, the detail page, the Brief's slate, Wags; `docs/gameday.md`): **merged 2026-11-05 (owner): PR #88, merge `419a4ba`, 21:34:05 UTC**, after PR #87 (merge `0add93c`, 21:33:19 UTC; its commit `d4d3cb6` had also ridden into #88, see §6). db/068 applied 21:25:58 UTC (Current State, Migrations). db/067's PR #86 merged first (merge `5a40a83`). Deployed (read Nov 5): Vercel production Ready on `419a4ba`; Railway deployment `eae78dcd` SUCCESS on `419a4ba`, the worker's boot runs at 21:34:48 UTC, `job_health` ok, 0 failures. **The RSS 6 h curve restarts at that boot.**
  - **Merge gate (db/068): `tests_live -v`, metered: 502 OK, 0 skipped, 1,132 s; KB not captured** (wrapper bug: it read egress's `total` property as a method after the tests had passed; fixed in `24e9511`, `scripts.tests_live_metered`). Prior full runs read 2.7–2.9 MB. The fix is proven on db/069's gate run instead (owner, Nov 5).
  - **Step 0 (read only):** no worker parser change needed: CFB rank (ESPN `curatedRank`, the CFP committee's since its first release), `conference_id` (SEC `8`) and team id (Alabama `333`) were already stored. Owner answers, Nov 5: ranked as ESPN gives it, no AP fetch; tiers broadcast > national cable > streaming/regional > none; services CBS → Paramount+ and TNT/truTV/TBS → HBO Max yes, CBSSN and Prime Video (Local) no, in one file (`apps/web/src/lib/gameday/services.ts`).
  - **Verified before handover:** db/068 on a scratch PG 17 holding production's schema and sports rows (read as `valemont_readonly`): the self-test passes as one transaction; 18 mutations each red; `tests_live/test_gameday_sql.py` 9/9 there. `tests/` 1329 OK; `pnpm test:unit` 183; lint, tokens, contrast, tsc clean; `e2e/gameday.spec.ts` logged out 4/4. Measured on the scratch copy: the page 29 KB (the five, Thu), 50 KB (All sports), a live poll ~48 B a game, the detail ~0.9 KB.
  - **Pending:** the kalshi.com link form (unverified, 429 to scripts); the owner's phone checks and screenshot gate; the next brief's slate; Wags. (The KB on production: measured, Current State, db/068.)
  - **Found:** db/051 `e2e_marker_ok` returns NULL (not false) while `e2e_markers` is empty, so a marker gate would open; production holds 1 marker row, so not open today. db/068's helpers `coalesce` it; **db/069 makes the function fail closed (owner, Nov 5; PR #89).**
  - **Polish: PR #91** (merge `5cbd991`, 01:05:06 UTC Nov 6; no migration): the owner's Nov 5 phone findings 1–5 (item 6 not provided). Railway `ca77bfbc` SUCCESS, boot 01:05:37 UTC Nov 6. Evidence and today's brief: MASTER_PLAN §6, Chat 7.
- **Chat 7 COMPLETE 2026-11-06 (owner).** Phases 1–3 (PRs #79, #80, #83, #84, #86–#89, #91); migrations db/065–db/069, all applied. Close-out with evidence: MASTER_PLAN §6, Chat 7. Read Nov 6:
  - **Worker RSS: new baseline ~140–150 MB at rest** (16 h from #91's boot, 9,180 log lines: boot 77 MB; hourly heartbeat medians 140–152 MB; peaks 165 MB at the 10:00 UTC cluster; slope −0.26 MB/h). Closes Phase 2's carried RSS item.
  - **Sports jobs:** `sports_live`, `sports_schedule`, `kalshi_map` ok, 0 failures, no failed run since boot; no sports alert since #84's "Recovered" (20:16:48 UTC Nov 5).
  - **The first brief with Game Day** (brief 1650, Sonnet, not a fallback, 249 words, 4 CFB lines). Its 09:45 push failed: **push subscription 17 was retired by Apple (410) at 14:30 UTC Nov 6** on the release push (notification 993), so `morning_brief_push` found no active subscription at 15:45:01 UTC. Re-subscribed as 794 (17:19:56 UTC; test push 994 sent 17:20:07 UTC, arrival owner-confirmed); the brief pushed by hand, delivery 638 → notification 995 `sent` 17:20:50 UTC, Late. **Closed by the test push plus Sat Nov 7's 09:45 push (pending).**
  - **Railway:** Hobby funded Nov 5; the Railway CLI is logged in (`railway whoami` names the owner's account).
  - **Carried, each pending:** Scheffler/Koepka's live position (the first week either plays); the F1 sprint shape (the first sprint weekend); Chat 4's release-push reading (fired Nov 6, undelivered); the first briefs on db/070 and on the word-cap protection (both merged in #92, Nov 6: NBA always in, by time within a tier; a race weekend's F1 line and a followed golfer's line are spared, the lowest CFB line trimmed instead); RaceFans retired (PR #94, open); phone item 6 (not provided). **The #91 screenshot re-run: owner-approved (Nov 6).** **Owner-verified Nov 5 (reported Nov 6):** the Kalshi link opened the right market; Wags answered "what's on tonight?"; `gameday.spec` 9/9, `screens-gameday` 2/2; week 9 props archive Tue Nov 10, then each Tuesday to Jan 12; the first real Monday quiz Nov 9; Chat 5's carries (outcome run with pinned controls, 3-card Arm Me, Undo re-run, 390 px From-line); retention follow-ups parked until the database passes ~350 MB.
  - **Board, Chat 13 (owner):** alert when the last push subscription goes inactive (Home banner + email via healthchecks).
- **Chat 8, Phase 1** (the Kalshi board: every open Kalshi sports market for the next 36 h, every sport, fees, per-event combo eligibility and leg limit; `/betting` Board tab; `docs/kalshi_board.md`): **built, db/071 applied (20:37:04 UTC Nov 6), not merged.** Branch `chat8-phase1`.
  - **Owner decisions, Nov 6:** `/markets` per event (≤2 window events) or per series, never a comma list (Kalshi answers 200 with no markets); `/series/{t}` once per Chicago day, not the 6.5 MB bulk list (+41 MB held); a window series with events but no open market fails the run after the rest is written; prune at `coalesce(starts_at, close_time)` + 6 h; kinds prop / game_line / other from Kalshi's fields; `combo_size_max` NULL = absent, never coerced; protected props noted for Phase 4.
  - **First real run (owner's machine, Nov 6, `scripts.kalshi_board --full`; its fetch stamp 23:32:04 UTC):** 36,048 markets, 4,844 events, 603 series, all written; Football lines 12,974 / other 496; Basketball lines 6,762 / props 4,896 / other 199; Soccer lines 5,048 / other 2,356; Hockey lines 861 / props 547 / other 30; Chess 560, Motorsport 432, Tennis 210, Boxing 176 (other or lines); combo-eligible 20,146 markets in 1,926 events (cap 1 on 1,696, absent on 230, 0 with a quoter, 216 YES only); linked 2,430 events to `sports_events`; parse misses 88 (escalator 85, ATP game spread 3); NO complement 0/36,048; 0 empty series; 1,603 Kalshi calls, 0 429s, ~89.8 MB from Kalshi, 422 s. **Peak RSS +26.7 MB over the pull's start (Windows working set; the then budget +20, now +35 on the worker)**; against the scratch copy +23.8 MB at a 1,500-row flush and +22.8 MB at 500, so not the batch. **The worker's Linux RSS: pending its boot run after the merge.**
  - **Postgres (read via db_inspect after the run):** `kalshi_board` 12.4 MB (heap 9.0, indexes 3.3; rows avg 193 B, the two tickers 63 B of it), events 3.1 MB, series 0.3 MB: **~15.8 MB, over the 10 MB target then, under the revised 25 MB (itself revised to 35 MB, Nov 8)**; the database +15.9 MB, to 268,348,563 bytes. The steady state under the start + 6 h rule (rows of games started in the last 6 h stay): **not measured yet**, expected a little above one window.
  - **Page payloads (real rows):** sport chips 1.3 KB (15 sports); one sport collapsed: Soccer 56 KB (336 games), Basketball 48 KB, Football (Saturday CFB, 113 games) 22 KB; one NBA game expanded: 86–101 KB (870–1,034 markets in 45–46 events).
  - **Merge gate (`scripts.tests_live_metered`, after the run):** run 1: 517, 516 OK, 1 failure (the new test's import-time clock; fixed, `tests_live` reads `now()` per test); run 2: **517 tests, 516 OK, 1 failure, 0 errors, 0 skipped; 3,227,840 bytes (~3.15 MB); 1,193 s.** The failure, `test_repos_sql.TheRulesInSql.test_the_guard_refuses_the_same_repo_again_today`, counts the whole `stories` table while the production feed ingest writes it (27,451 vs 27,450); by name 5/5. Pre-existing, not this branch: a finding. `tests/` 1,389 OK; `pnpm test:unit` 194; lint and tsc clean; logged-out e2e 6/6.
  - **Budgets revised (owner, Nov 6, at the merge): the board ≤ 25 MB in Postgres at steady state (tables plus indexes; ≤ 35 MB from Nov 8, owner, after 4c); the pull's peak ≤ +35 MB over its start on the worker (Linux RSS).** The 10 MB and +20 MB targets are retired, and there is no db/072 for them. The row shrinks (the market ticker's suffix only, `fillfactor` 90, a short kind code) are parked until the database passes ~350 MB, the same trigger as the retention follow-ups (`docs/kalshi_board.md` §8).
  - **The repo-guard flake** (`test_repos_sql`, above): fixed in its own PR, never in #97 (owner). The test counts only its own fixtures (§6: a test reads its own rows). Gate: that class alone 5/5, plus the full metered run on the next merge.
  - **Merged (PR #97, merge `4f4f635`) and deployed; the readings, Sat Nov 7** (Railway log, `job_health`, db_inspect):
    - **4a, the full pulls on #97's worker** (deployment `960589ed`, boot 05:54 UTC): the boot run 05:56:23–06:10:56 (873 s, 29,728 markets, 1,794 calls, 157 429s, ~74.6 MB) and the 09:30 Chicago run 15:30:00–15:47:12 (1,033 s, 49,041 markets, 2,051 calls, 178 429s, ~121.6 MB). **Peak over the start: +22 MB (142 → 164) and +20 MB (147 → 167): PASS (owner, ≤ +35).**
    - **4d, the old refresh (6 h, by series), 14:00–19:41 UTC:** 23 runs, 0 failures (one idle during the full pull); 452–631 s a run (median ~524), 626–864 calls, ~10% 429s, 44–61 MB from Kalshi. **Rebuilt (owner), below.**
  - **PR #98 (the repo-guard flake), merge `c1dc2d5`, 20:05:19 UTC Nov 7.** Gate: metered `tests_live -v` 517 OK, 0 skipped, ~3.0 MB, 1,177 s.
  - **PR #99, the hotfix (owner), merge `1ab0698`, 21:03:41 UTC Nov 7.** The 20:08 UTC boot pull failed: `KXNFLPASSYDSPRECISE` (a per-yard duplicate of KXNFLPASSYDS, 1,204 markets a game, 16,856 in 14 events) overflowed the 60-page cap. Now excluded by name (`EXCLUDED_SERIES`), and a page-cap overflow skips that series, writes the rest and then fails the run naming it; the refresh keys on "the board was written". Its boot pull 21:06:35–21:16:24: ok, `excluded=1, overflowed=0`, 40,735 markets, 0 429s, +21 MB.
  - **4b, the 15:30 Chicago (21:30 UTC) pull: failed on `empty_series`** (KXATPMATCH 7, KXATPDOUBLES 3): milestones listed tomorrow's matches before their markets' `open_time` (21:35–21:36 UTC). The rest was written: 40,189 markets; **NFL 3,311 lines, 9,928 props, 2,960 other**; parse misses 458; 0 429s; +13 MB (161 → 174). No push (the boot pull had passed).
  - **PR #100, merge `28f8d4b`, 22:06:35 UTC Nov 7 (db/072).** Gate: metered `tests_live -v` 519 tests, 0 failures, 1 skipped (`test_claim_retry_and_finish`, the poller race; its class by name 10/10), ~3.0 MB, 1,204 s; `tests/` 1,415 OK. It carries:
    - **The refresh, rebuilt:** per event only, events starting within 3 h and not started, soonest first; ≤ 300 calls and ≤ 300 s a run (no call starts after 280 s, 15 s timeout); adaptive pacing (AIMD, 0.3–4 s); the 429 rate on every note. **Owner, Nov 7: it stays as built; the skip count is expected on a busy slate** (the page shows source time; Phase 4 re-quotes every leg at commit). No change unless the median run over Sunday's slate passes 300 s or 429s pass 2%.
    - **Scalar markets:** ladders and escalators are `payout = 'scalar'` (db/072), never legs; Kalshi's `market_type` says `binary` on them, so `custom_strike.scalar_cap` decides. **Phase 4's leg universe is `payout = 'binary'` only.**
    - **Not yet open is not empty:** an empty window series's events are asked once for `status=unopened`; those count as `not_yet_open`, never a failure.
    - The parser (the "Seth" prefix, the ATP games spread) and the note (`empty_series`, `not_yet_open`, `excluded`, `overflowed`; top 10 misses).
  - **On #100's worker (deployment `c486d271`):** the boot pull 22:09:25–22:18:58: ok, **parse misses 0** (binary; owner's target < 50), `empty_series=0, not_yet_open=0`, 38,970 markets, **414 scalar** (escalator 202, ladder 212), 0 429s, ~111 MB. **One Saturday refresh** (22:31:55): 300 calls (the cap; 756 of 1,056 events skipped, from 00:00 UTC), **0 429s**, pace 0.30 s, ~3.4 MB, 106 s.
  - **4c, the steady state, read 06:01 UTC Nov 8** (db_inspect; MB = 2^20): `kalshi_board` 22.5 MB (heap 15.8, indexes 6.7; 54,608 rows), events 5.0 MB (6,416), series 0.6 MB (745): **28.1 MB**, over the Nov 6 25 MB; the database 284,953,747 bytes (~272 MB). More rows than one pull (39–49k) because games started in the last 6 h stay until the prune; not bloat (4,227 dead rows, ~8%, auto-vacuumed). **Owner, Nov 8: the board budget is ≤ 35 MB at steady state (tables plus indexes); 25 MB retired. The shrinks stay parked until the database passes ~350 MB; no db/073 for this.**
  - ~~**Pending:** the refresh median over Sunday's slate; `not_yet_open` on live data (0 so far); the owner's `e2e/betting.spec.ts` (6 markets, the Scales chip) and phone check.~~ Read Mon Nov 9 (below).
  - **Close-out reads, Mon Nov 9** (Railway log, `job_health`, db_inspect; worker `c486d271` up since 22:07 UTC Nov 7, no restart):
    - **Sunday refresh** (08:00–23:00 Chicago): 58 runs, median 97 s, worst 283 s; 10,742 calls, 31 429s (0.29%); ~210 MB from Kalshi; 45 runs skipped (18,565 events), 18 at the time cap. **Both thresholds pass.** The 13:55–18:05 UTC collector saved nothing; read from the log instead: 15 runs, median 92 s, 0.18% 429s.
    - **Full pulls:** Sat 22:09, Sun 15:30, Sun 21:30 ok (+19, +20, +8 MB over the start; 0 parse misses; `not_yet_open` 0 on each). **Mon 15:30 UTC failed** on `empty_series` (4 Copa del Rey series, 3 events each), the rest written (13,233 markets). No push (1 failure).
    - **Board 30.4 MB** (tables plus indexes, 13,379 live rows: the size holds the busiest slate's pages) against 35 MB; the database 290,696,339 bytes. **Worker RSS at rest 137–149 MB** (hourly medians, last 24 h).
    - **Briefs pushed** Sat, Sun, Mon 09:45 Chicago (deliveries 660, 675, 676, all `sent`; subscription 794): **the push-subscription carry closes** (database side). Mon's brief carried "Quiz ready: 10 on your saved Arsenal."; no real `arsenal_quizzes` row at 20:06 UTC (built on first open).
  - **Owner checks, Nov 9: phone checklist green; `e2e/betting.spec.ts` phone 4/4 (32.8 s).**
  - **Chat 8 Phase 1: GREEN (owner, 2026-11-09).**
  - **The hotfix (owner, Nov 9), PR #102: not open is not empty, in any status; zero markets is not broken** (`docs/kalshi_board.md` §3). Each event of an empty window series is asked once with no status filter: markets in any non-open status count as `not_open=<n>` by status (series named), zero markets as `no_markets=<n>` (series named); neither fails alone. The run fails only past 2% zero-market events of the window, or when a cfb/nfl/nba/f1/pga **game series** (the registry's `kalshi_series`: KXNCAAFGAME, KXNFLGAME, KXNBAGAME, KXF1RACE, KXPGATOUR; correction, owner Nov 9, PR #103: props and other series under the 2% share only) has every window event at zero. Read at 20:17 UTC: the Copa del Rey BTTS, SPREAD and TOTAL events `active` (opened 19:06 and 20:06 UTC; `status=unopened` returned 0 for them), the 3 `KXCOPADELREYADVANCE` events zero markets of any status. Gate: `tests/` 1,420 OK; `tests_live.test_kalshi_board_sql` 11/11, 0 skipped. **#102** merge `ff25fa6` 20:25:50 UTC (Railway deployed it twice, `527fc6dc` then `8091e966`; the first worker's boot pull was cut); **#103** merge `9b97589` 20:36:19 UTC, worker `3592ab5c`. Boot pulls 20:35:29 (#102) and 20:42:39 (#103): ok.
  - **The 21:30 UTC Nov 9 pull, on #103: ok** (21:30:00–21:32:26, 146 s; `job_health` ok, 0 failures, no push): 13,712 markets, 1,711 events, 223 series; **`no_markets=0, not_open=4 (finalized 3, initialized 1; KXCOPADELREYADVANCE, KXNBAH2HPTSMARGIN)`**: the first `not_open` on live data, **Phase 1's last read, closed**; excluded 1 (KXNFLPASSYDSPRECISE), overflowed 0; parse misses 2 (KXNBAH2H3PT); 0 429s, 388 calls, ~47 MB from Kalshi; RSS 149 MB, job peak 152 MB. ADVANCE, zero markets at 20:17 UTC, read as `finalized` from 20:35.
  - **Found the same day:** every redeploy sends a false `sports_live` "needs attention" + "Recovered" pair (notifications 1080–1085, 20:27–20:39 UTC Nov 9, one per deploy; Sat 21:05 too). Fix: its own PR (a boot grace window, the PR #6 shape).
- **Chat 8, Phase 1: GREEN 2026-11-09 (owner); its last read closed on the 21:30 UTC pull (above).** Next: Phase 2 (the consensus engine), Step 0 when the owner sends The Odds API key.
- **Chat 8, Phase 2** (the consensus engine: every binary board market's consensus probability, tier, ask, fee and net edge per side; `docs/consensus.md`): **built, db/073 applied (22:47:33 UTC Nov 9), not merged.** Branch `chat8-phase2`.
  - **Owner decisions, Nov 9:** POWER de-vig (multiplicative a comparison only); books tier = the median of ≥ 2 books at the EXACT line, espn tier = ESPN's DraftKings `close`, else kalshi; p = 0.7 × outside + 0.3 × Kalshi mid; the mid normalized across a mutually exclusive event, mids summing 0.95–1.05, ladder rungs plain; edge = p(side) − (ask + taker fee); **a kalshi-tier leg never claims edge; `edge_claimed` (> 0) is display only, the edge-leg margin (2¢ net of fee) is Phase 4's pre-registration**; no rung interpolation (the rung model is Phase 4's, on `book_lines`); anytime TD kalshi tier (books price Yes only); Option B credits (~340/month: NBA mainlines daily, NFL mainlines and props: receptions, rec yds, rush yds on NFL game days; 16:15 free `/events` first); the guard refuses at remaining < 50 or a month past 450, pushes at 400; `book_lines` an app table collapsed to the last pre-start row (~14 MB/year est.); `mutually_exclusive` read once a Chicago day with `/series`.
  - **Step 0 (11 credits):** cost = markets × regions; 9 US books; ESPN one book (DraftKings) for cfb, nfl, nba, mcbb, nhl, epl, ucl; matching NFL 1/1, NBA 8/8; exact equivalents: moneylines all, spread/total rungs NFL 2/44, NBA 14/496; MNF props receptions 14/14 main lines on a rung (14/86 rungs), rec yds 5/20, rush yds 1/10, pass yds 0 at ≥ 2 books, anytime TD Yes only. Detail: `docs/consensus.md` §3.
  - **First real run (owner's machine, 23:02 UTC Nov 9, branch code):** 9 credits (NBA 3, NFL 3, MNF props 3), 480 left on the account; Odds API events NBA 10/10, NFL 1/1, 0 misses; ESPN 81/113 games; 113 games, 2,419 markets; **legs books 58, espn 181, kalshi 13,235; claimed 23; kalshi-tier positive 0**; written 239; `book_lines` +636; 9 s; RSS 70 MB (peak 72); ~301 KB read. **The claimed edges were mostly a stale board price** (MEM at TOR: the board's 21:49 price vs Kalshi's live 0.54–0.55; the 22:58 refresh skipped events from 00:18 UTC at its 300-call cap); Phase 4 re-quotes at commit.
  - **Merge gate:** `tests/` 1,462 OK; `tests_live.test_consensus_sql` 10/10; full metered `tests_live -v`: **529 tests, 528 OK, 1 failure, 0 errors, 0 skipped; 2,953,026 bytes (~2.82 MB); 1,261 s.** The failure, `test_feed_rank_sql.WorkerQueries.test_mean_vector_is_averaged_in_the_database` (2,276 vs 2,275), counts the whole real 7-day home window while the feed ingest writes it; by name 5/5. Pre-existing, not this branch (the #98 pattern): a finding for its own PR. Web: tsc, lint clean; `pnpm test:unit` 198; logged-out `betting.spec` 2/2.
  - **Pending:** the merge and the worker's first scheduled runs (10:15 and 16:15 Chicago); the first full pull's `mutually_exclusive` read (its 429 rate, said if > 2%); the owner's `e2e/betting.spec.ts` (the consensus row) and phone check; a compact `q` (db/074 decision: 102 KB of DEN at HOU's 195 KB; an array `[tier, p, side, edge]` measures 25.5 KB); Step 0's 11 credits are not in `odds_api_calls`.
- **Next: Chat 8 (betting: consensus engine and Parlay Generator; MASTER_PLAN §6).** Its prerequisite, Railway Hobby, is met (Nov 5). The sequence (owner, 2026-11-02): Chat 7 (done), then Chat 8 (betting; **Railway Hobby funded first**: done 2026-11-05), then Chat 9 (slips, manual entry, live tracker: it needs Chat 8's consensus engine and generated bets), then Chats 10–12 (crypto). Chat 6 (Lookbook) moves last, before Chat 13. In short: 7 → 8 → 9 → 10–12 → 6 → 13. MASTER_PLAN §6.

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
- **Sweep starvation: FIXED (PR #10, 2026-10-09).** A sweep reads one page
  (`sweep_limit` 50) of its due set. Ordered by deadline alone, a backlog
  bigger than the page re-served the same rows while the rest were never
  asked. Expiry is measured from the deadline, so past `max_overdue` core
  then voided, or tombstoned the close of, rows it had **never once
  attempted**. That is permanent loss from bookkeeping. Two invariants now,
  each tested:
  - **No void and no `missed` at zero recorded attempts.**
    `DeferPolicy.expired` never answers at 0 attempts, and `_abandon` and
    `_miss` raise `AbandonWithoutAttempt` (`tests/test_first_look.py`).
    **And in the database (db/024):** a trigger refuses any void resolution
    with no `resolve` attempt on record, and any `missed` close snapshot with
    no `capture` attempt, from any writer. There is no `is_test` exemption:
    the harness's `seal()` records an attempt first
    (`tests_live/test_no_untried_abandon.py`).
    **The rule stays strict (owner, 2026-10-09: no `pnl IS NULL` carve-out);
    core satisfies it by construction (db/025).** `ledger.add_resolution` and
    `ledger.add_closing_snapshot` write the attempt that produced the answer
    in the **same transaction**, before it: `answered` for every outcome
    (hit/miss/partial/push/void) and every captured close, `error` for a
    CloseUnavailable tombstone. So an adapter's first-look void is accepted.
    Only core's give-ups (exhausted budget) pass `attempt=None`: they asked
    nothing, and must stand on the attempts already recorded. An `answered`
    row is never written alone. **Anything that reads `resolution_attempts`
    as failures (void or retry rate) must filter `result IN
    ('deferred','error')`.** Tests: `tests/test_first_look.py`
    (`EveryAnswerCarriesItsAttempt`; the stub ledger models the trigger),
    `tests_live/test_answered_attempts.py`.
  - **Never-attempted rows first, then the oldest last attempt**, in both
    due queries. Every due row is attempted within ceil(n / limit) sweeps
    (`tests_live/test_sweep_order.py`: red on the old order, green on the
    new).

  Kalshi's `capture_close` reads candle history (the last 1-minute mid at or
  before kickoff), not the current price, so a late first look recovers the
  close rather than losing it. It was found because 109 leaked `_test`
  fixtures (a parked fixture's close was `now + 1 day`) pushed fresh rows
  off `due_for_capture`'s page and turned `test_attempt_budgets.py` red.
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

- **2026-11-02 (owner back after Oct 23 – Nov 2; read via db_inspect unless said otherwise):**
  - **The worker ran the whole time.** There was no gap over 16 min in the 15-min egress samples from Oct 21, no "Worker down" push, and two `job_queue` blips that recovered within 2 min (Oct 22, Nov 1). Drops ran on all 11 days Oct 23 – Nov 2, with no top-up. The brief was pushed on all 12 days Oct 22 – Nov 2. **The Railway CLI is logged out on this machine**, so the deployment, uptime and credit are unread; the owner logs in. **Closed Nov 6: logged in (`railway whoami` names the owner's account).**
  - **Arsenal pool rescue.** On Nov 2 the ready pool was down to 4 references in 2 categories (a drop needs 3), and the Nov 1 batch lost 15 of 36 cards to near-duplicates.
    - Re-verified the 53 unverified: 12 cleared and were written through db/055 (`scripts.arsenal_reverify --write`, changed verdicts only). Outcome check 3/3 controls, 0 held.
    - **The other 41 are HELD, not re-verified (owner): they wait for the verifier yield fix.**
    - Batch run 65 (40 references, the new quotas and full avoid list): 38 inserted, 3 near-duplicates, 15 cleared, $0.131. The worker held ref 1379 (Jay-Z and Tidal, an outcome conflict).
    - Ready pool after: 30 references, 28 words. music_culture had 2 (film_tv 3, finance_business 3).
  - **PR #63** (merge `3473f6a`, 21:15:32 UTC): `would_join` retired; summary cap 40 words (Tech too).
  - **PR #64** (merge `36a63c6`, 21:15:35 UTC): `arsenal_pool` every 6 h; any category under 3 ready or in flight refills, at most every 12 h; quotas toward the thinnest categories; the avoid list is every reference ever proposed.
    - The worker rebooted ~21:17 UTC (`capital_snapshot` and `db_size` boot runs, RSS 75 MB).
    - **A false "arsenal_pool needs attention" push went out at 21:18 (notification 713).** The new 6 h interval made Nov 1's last run "stale". The boot run cleared it: "Recovered" at 21:24 UTC (notification 715).
    - **Both PRs are live:**
      - #64: the 21:30 UTC run's note reads "category short (music_culture 2), but the last batch was 1.0 h ago (refill gap 12 h)".
      - #63: 0 `would_join` rows since the reboot.
    - **The first refill the rule triggers: pending.** The gap ends ~08:30 UTC Nov 3, so the 09:30 UTC (03:30 Chicago) run should submit it.
  - **Retention decision (owner): unreacted kept stories at 21 days** (was 90); protected stories are never touched. The dry run on production found 1,544 stories, 1,782 items, 1,697 scores and 4,638 similarity rows, with 0 trailer links. PR #65 (`feed-retention-21d`) adds a trailer guard (title_trailers cascades from content_items) and `scripts.feed_prune --dry-run`; no migration. Gate: `tests_live -v` 455 OK, 0 skipped. **Not merged until the owner has seen the counts.** Clearing centroids and a 14-day similarity log are NOT approved: the readers are listed in `docs/feed_retention_readers.md`, decide after.
  - **Database 223 MB** (87.6 MB on Oct 13; ~6.8 MB a day, almost all feed). The NFL props archive at ~34 MB a week cannot go into Postgres (MASTER_PLAN §3).
  - **Claude API spend: October $3.49 (5,625 calls; feed_summarize $2.28); November to Nov 2 $0.33.**
  - **Sequence change (owner): after Chat 5 → Chat 7 (sports data layer) → Chat 8 (betting) → Chat 9 (slips and live tracker; needs Chat 8) → Chats 10–12 (crypto); Chat 6 Lookbook moves last.** Railway Hobby is required before Chat 8. MASTER_PLAN §6.
  - **PR #65 merged 22:13:22 UTC (merge `95ca30e`).** The first 21-day prune runs at 03:15 Chicago (09:15 UTC) Nov 3. Baseline at 22:13 UTC Nov 2: database 235,916,435 bytes (225 MB); stories 27,508, content_items 35,984, story_scores 51,083, feed_similarity_log 105,005 rows. **Rows actually deleted against the dry run: pending.**

- **Owner decisions, 2026-10-09:**
  - AI budget: **$20/month**. `AI_MONTHLY_BUDGET_USD` is set to it in Chat 2 Phase 4 (the code default is still $10).
  - Ventures **NCLEXCompass** and **Senior Care Systems** are dropped. Ventures HQ seeds the remaining seven.
  - Morning Brief push: **09:45 America/Chicago**.
  - Calendar (Google Calendar highlights) is **deferred**.
- **⏰ Reminders:**
  - ~~**Tue 2026-10-13:** NFL props archive, week 5; also weeks 1–4, once~~ **Done 2026-10-13** (see RECURRING below). Next: week 6 on Tue 2026-10-20.
  - **Sun 2026-10-26: the Love-effects watch, extended (owner, 2026-10-20).** The Oct 19 read (`docs/drafts/feed-review-2026-10.md` §1) found Loves lifted their topics on Tech, while Home (1 real Love) and Sports (none) had nothing to measure. The owner reacts on Home and Sports this week; read /settings/feed and the profile history again on Oct 26. Same day: the 3a shadow-join report (`would_join` rows, db/058).
  - ~~**Sun 2026-10-19:**~~ (read Oct 20; the review is in `docs/drafts/feed-review-2026-10.md`) the owner's one-week watch of Love effects on /settings/feed closes (Chat 3 Phase 2 learning item; MASTER_PLAN checklist, `docs/feed_ranking.md` decision 7). Same day: the shadow `drop` review (likely retire it) and the watch on Skins Game–style duplicate merges, now including cross-source clustering of official tech items (OpenAI's tier change as stories 8317 and 8941; MASTER_PLAN, Chat 3 close-out). **And the summarizer's out-of-attempts stories (owner, 2026-10-14):** 28 Home stories in the 48 h to 05:16 UTC Oct 14 ended headline-only after two real Haiku calls each, 22 of them Variety, Deadline or THR entertainment pieces. No attempt was spent without a call, and they were not reset (owner). Read their rejection reasons from the Railway logs (the `feed summarize: story N:` lines, names only, nothing else echoed), and if the copy check is the cause, propose a summarizer fix for long entertainment teasers. No code until then. **And repo why lines that pass only by naming Claude Code (owner, 2026-10-14):** day one's universal-modder line ("agent-driven workflows in Tsims's Claude Code setup") names the tool, not an action; if that is a pattern, the dev-workflow target needs "names what it would do" (`docs/tech_hub.md` §7.2b). No change until then.
  - ~~**Mon 2026-10-20:**~~ **Done 2026-11-04** (weeks 6–8 in Storage; RECURRING, Archive): design the NFL props archive's move to Supabase Storage (Step 0) **before** week 6 is archived: at week 5's ~34 MB, weeks 5–18 fill the 500 MB tier (RECURRING, Archive).
  - ~~**Decided 2026-10-13 (owner): a free Supabase staging project for migration dry runs.**~~ **Reversed 2026-10-13 (owner, Chat 4 planning): no staging project**; migrations stay paste-to-production, guarded by the rolled-back self-test (§5).
  - **The Railway trial ended and removed the worker, ~20:06 UTC Mon 2026-10-19** (owner, 2026-10-20). The Oct 13 "Railway is on Hobby" note was wrong: the account was still on the trial. **Decided (owner, 2026-10-20): Free until the owner funds Hobby, before the credit runs out**; the owner checks Railway's usage page weekly and upgrades below $0.25 left (Current State, "Railway").

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
  - **`db/022_ventures.sql` applied 2026-10-09 22:20:20 UTC; `db/023_job_health_retired.sql` applied 22:22:06 UTC** (`migration_log`). The 022 verify was checked via db_inspect: 7 ventures in order; SBC with 5 workstreams (2 parked) and 1 log row; 0 anon grants; `v_venture_today` `security_invoker=true`.
  - **`db/024_no_untried_abandon.sql` applied 2026-10-09 23:27:30 UTC** (`migration_log`).
  - **`db/025_answered_attempts.sql` applied 2026-10-09 23:36:41 UTC** (`migration_log`; CHECK read back via db_inspect: deferred, error, answered). It adds `answered` to `resolution_attempts.result`.
  - **`db/026_capital.sql` applied 2026-10-10 00:52:43 UTC; `db/027_capital_is_test.sql` 02:18:31; `db/028_readonly_views.sql` 09:56:58** (`migration_log`).
  - **`db/029_wags.sql` applied 2026-10-10 17:23:55 UTC** (`migration_log`). The context layer and Wags (below).
  - **`db/030_morning_brief.sql` applied 2026-10-10 22:17:09 UTC; `db/031_brief_shape_null.sql` 22:24:10** (`migration_log`). 031 fixes 030's morning-shape CHECK, which a missing field made NULL (a NULL CHECK passes).
  - **`db/032_feed.sql` applied 2026-10-11 23:10:36 UTC; `db/033_feed_calibration.sql` 23:58:01; `db/034_feed_reset.sql` 2026-10-12 00:54:44; `db/035_frozen_fix.sql` 01:19:10** (`migration_log`). Chat 3 Phase 1's feed: sources, items, stories, rules, the calibration log, title embeddings; 034 deleted the first ingest's mis-clustered rows (214 stories, 853 items, 10 seeded) and 035 fixed 034's freeze trigger.
  - **`db/036_feed_review_rules.sql` applied 2026-10-12 03:07:17 UTC; `db/037_feed_junk_rules.sql` 03:13:03; `db/038_feed_boat_rules.sql` 03:19:10** (`migration_log`, rules read back via db_inspect). Rule rows only: film/TV/album reviews (036, extended in 037); Complex shop/tag pages, dash-led and bare section-name titles (037); Boat International directory and profile pages (038). 49 active feed rules.
  - **`db/039_feed_ranking.sql` applied 2026-10-12 04:25:22 UTC** (`migration_log`; self-test notice shown; verified via db_inspect: RLS and grants, follows 7/2/9 with Alabama the one floor, 3 entity links, provider `feed` sort 40 brief_section, seeds 9/6/0). Chat 3 Phase 2: feed_reactions (append-only), interest_profiles, entities/entity_links/follows, story_scores, stories.why_it_matters, feed_for_you(), ctx_feed(). Design: `docs/feed_ranking.md`; the owner's brief: `docs/chat3_owner_brief.md`.
  - **`db/040_venture_taglines.sql` applied 2026-10-12 05:36:53 UTC** (`migration_log`; verified via db_inspect the same day: the 7 live ventures carry the owner's one-line taglines, and `ctx_ventures()` returns `tagline` on each of its 7 `active` entries, with the archived "Test" venture under `archived`). Chat 3 Phase 2: the feed's "Why it matters" job reads what each venture does from here (`feed/why.py`), and Wags and the brief see the taglines too.
  - **`db/041_feed_seed_film.sql` applied 2026-10-12 21:59:10 UTC** (`migration_log`; verified via db_inspect the same session: home seeds love 10, not interested 7; love 7 is "Hollywood and US film/TV dealmaking: …", love 8 is "major film festivals: …", and not interested 7 is "international and foreign-market TV and film sales, …"; sports still empty). Seed text only, no filter rule: the owner's film/TV love split into US dealmaking and festivals, with international and regional TV/film markets as a not-interested seed. The rebuild at 22:00 UTC used the new seeds (`docs/feed_checks/seed-041-2026-10-12/`).
  - **`db/042_sports.sql` applied 2026-10-13 20:26:05 UTC** (`migration_log`; the 1,522-line file, self-test NOTICE shown once). Chat 3 Phase 3: boost_windows, entity_link_proposals and entity_link_ends (append-only), entity_links append-only, `entities.is_test`, `stories.category`, `feed_for_you(pillar, limit, offset, sport, category)` with the window boost and Sports entity cap, `ctx_sports_feed` (provider `sports_feed`, sort 45, not a brief section), sports seeds and entities, the Golf Digest course-directory rule (feed_rules 146). **Verified via db_inspect the same session:** RLS on the three new tables plus entity_links (policies 1/2/2/2), anon none, readonly reads all and executes the 5-argument `feed_for_you` (the 3-argument db/039 one is gone); boost_windows cfb 6 (Alabama, 1 active now) and f1 6; follows tier 1: 7, tier 3: 23, no tier 2; 3 links, all current; sports seeds love 8 / not interested 2, home unchanged 10 / 7; 50 active feed rules; 3,292 real stories, all category NULL until the backfill.
    - **The first paste failed and applied nothing** (owner, 2026-10-13): the self-test's `TRUNCATE entity_link_proposals` raised 0A000, because TRUNCATE checks foreign keys before any TRUNCATE trigger. Read back via db_inspect: nothing from the file existed. The fix truncates the closed set (proposals, links, ends) and counts only the guard's P0001 message. **Rule for future self-tests: a TRUNCATE assertion on a table that other tables reference must name every referencing table in the same statement.**
  - **`db/043_feed_rules_cross_cap.sql` applied 2026-10-13 21:53:59 UTC** (`migration_log`; the 349-line file, self-test NOTICE shown once). Two url_regex rules (feed_rules 162 author pages, Axios Local and Complex; 163 PGA Tour player pages) and `feed_for_you`'s 6th argument `p_cross_cap` (NULL = 5; 0 = no tier-1 sports cross-in at any position, /news; Home's short list passes 2; floored cross-ins count first). **Verified via db_inspect the same session:** both rules active, 52 active rules; the 6-argument function exists and the 5-argument one is gone; readonly executes it, anon does not; on real data the cross-ins number 0 at cap 0 (all positions), 2 in Home's top 10 at cap 2, 5 on page one by default.
  - **`db/044_sports_business_entities.sql` applied 2026-10-13 22:27:30 UTC** (`migration_log`; the 62-line file, rows and a CHECK, no self-test). `entities.kind` gains `broadcaster`; 7 broadcasters (ESPN, NBC Sports, CBS Sports, FOX Sports, Amazon Prime Video, Netflix, TNT Sports) and 4 leagues (MLB, NWSL, WNBA, NCAA), sport NULL, for the business-of-sports gate only. **Verified via db_inspect the same session:** the CHECK lists broadcaster; the 11 rows with their aliases, none test, none followed; follows still 30 (7 tier 1, 23 tier 3).
  - **`db/045_tech_hub.sql` applied 2026-10-14 02:32:51 UTC** (`migration_log`). Chat 4 Phase 1, the Tech Hub pillar: `tech` exclusive on sources and stories; source kinds `page` and `hn`, `sources.official` and `config`; `content_items.backfill` and `published_precision`; `feed_pillar_guard` (no tech cross-in at write time); `feed_for_you` with `category` and `official` (27 columns, tech window 168 h, unscored tech stories listed); `tech_credit_pushes` (append-only; 3 real a day, never twice, by trigger); `ctx_tech` and `ctx_tech_brief` (sort 50 both; only `tech_brief` is a brief section); tech seeds; `_e2e-tech` and `add_test_tech_story`. **Verified via db_inspect the same session:** (a) 12 constraints present (the file's comment said 10: a miscount, 8 named + 4 pillar CHECKs; corrected); (b) `tech_credit_pushes` RLS on, 1 policy, anon none, authenticated read-only, 0 rows; (c) `feed_for_you` 27 output columns ending `category, official`, readonly executes it and `ctx_tech`, `ctx_tech_brief`, `tech_credit_push_room`, anon none; (d) `tech` 50 not a brief section, `tech_brief` 50 a brief section; (e) tech seeds love 8 / not interested 4, home 10 / 7 and sports 8 / 2 unchanged; (f) `_e2e-tech` inactive, is_test, official, `{tech}`; (g) push room 3, `ctx_tech_brief()` NULL. The self-test NOTICE was not seen here (the owner's paste); the log row exists only if it passed. `tests_live -v` on the branch after: 331 OK, 0 skipped.
    - **The first paste failed and applied nothing** (2026-10-14): the test source slug `_e2e_tech` broke db/032's `sources_slug_check` (`^_?[a-z0-9][a-z0-9-]*$`: one leading underscore only). Read back via db_inspect: no db/045 column, table or log row. Renamed `_e2e-tech`.
  - **`db/046_tech_official_decay.sql` applied 2026-10-14 02:57:59 UTC** (`migration_log`). Two owner decisions (2026-10-13/14): an OFFICIAL tech story decays at a 72 h half-life in `feed_for_you` (community, Home, Sports and boosts keep 18 h); and push eligibility, `tech_push_eligible()` (lead item not a first-fetch backfill AND published within 24 h of first seen), checked by `tech_credit_push_guard` and `tech_credit_push_allowed`. **Verified via db_inspect the same session:** (a) `feed_for_you` has the slow half-life, 27 columns, readonly executes it, anon not; (b) the log row; (c) the guard and the check both call `tech_push_eligible`, readonly executes it, anon not, story 8359 (notification 323, the Vercel backfill item pushed before the rule) is not eligible, and its `tech_credit_pushes` row 20 stays as history. `tests_live -v` after: 333 OK, 0 skipped.
  - **`db/047_feed_hide_late_items.sql` applied 2026-10-14 04:45:18 UTC** (`migration_log`). A data change (owner, 2026-10-14): `feed_hide_late_stories()` filtered as `stale:first_seen_after_72h` every real, kept story made only of LATE items (not seeded, not backfill, stored > 72 h after publication, 96 h date-only), never one with a reaction, a free-credits push or a move proposal; nothing deleted. The cause, a first fetch skipping old items unrecorded so the next fetch stored them, is fixed in `feed/ingest.py` (PR #32). **Verified via db_inspect the same session:** (a) 579 hidden, 318 Home/Sports and 261 tech, 0 reacted, as counted at ~04:40 UTC; (b) 20 late stories still kept, the mixed ones; (c) 0 late-only stories on Tech For you; (d) the log row. 614 of the 667 late items sit in hidden stories, the other 53 in the 20 mixed ones. Hidden stories reach the existing 30-day filtered prune (retention unchanged).
  - **`db/048_feed_dates.sql` applied 2026-10-14 06:04:04 UTC** (`migration_log`; the 398-line file). Three owner decisions (2026-10-14): `content_items.published_precision` gains `none` (the worker stores an undated item so; it still shows on Home and Sports, never pushes, and is never a latency sample), `tech_push_eligible()` requires `published_precision <> 'none'`; `v_feed_stories` gains `lead_published_precision` and `lead_backfill`; `feed_for_you` (dropped and re-created, same 6 arguments) gains `published_precision` (28 columns) and counts a first-fetch backfill lead's window from `published_at` (a date-only one from the end of its day), every other story from first seen. **Verified via db_inspect the same session (06:04:48 UTC):** (a) the CHECK lists time, date, none; `push_needs_a_date` true, 28 output columns ending `published_precision`, readonly executes it, anon not; (b) both view columns present; (c) Tech For you 75 cards, 59 backfill-led, 0 backfill past the window, and none of the 25 counted to retire still listed; (d) the log row. The self-test NOTICE was the owner's paste, not seen here; the log row exists only if it passed.
  - **`db/049_repos.sql` applied 2026-10-14 21:07:08 UTC** (`migration_log`; owner's paste, the NOTICE was not seen here; the log row exists only if the self-test passed). Chat 4 Phase 2, GitHub repos: source kind `github` (tech only, never official) and the `_e2e-repos` test source; `repos` (app table); `repo_star_snapshots` and `repo_surfacings`, append-only with TRUNCATE guards, their days set by trigger (`owner_today()`); `repo_surface_room(day)` (8 a day) and `repo_may_surface(repo, stars_7d, day)` enforced by the surfacing trigger; `add_test_repo`; `ctx_tech` gains `repos_this_week` and `loved_repos`, `ctx_tech_brief` gains `top_repo` only after a repo Love in 14 days. **Verified via db_inspect the same session:** (a) `sources_kind_check` lists github, `sources_kind_url` and `sources_github_tech` present; (b) the three tables RLS on, 1 policy each, anon no select, authenticated select only, readonly select, 0 rows each; (c) 6 triggers (guard, immutable, no_truncate on both append-only tables); (d) readonly executes `repo_may_surface`, `repo_surface_room`, `ctx_tech`, `ctx_tech_brief` and not `add_test_repo`, anon none, `repo_surface_room(owner_today())` = 8; (e) `_e2e-repos` inactive, is_test, github, not official; (f) `repos_this_week` and `loved_repos` both `[]`; (g) the log row; the pasted `repo_may_surface` is the file's final text.
  - **`db/050_repo_measured_baseline.sql` applied 2026-10-14 21:12:56 UTC** (`migration_log`; owner's paste, the NOTICE was not seen here). Owner's change: `repo_may_surface`'s baseline is the first surfacing's stars_7d when MEASURED, else the repo's first measured stars_7d from its snapshots (snapshots on D and D − 7); none yet, no resurfacing yet; doubling and the 14-day floor unchanged; a measured 0 counts as 1. **Verified via db_inspect the same session:** (a) the body reads `first_measured`, readonly executes it, anon not; (b) `repo_surfacings_guard` and `repo_star_snapshots_guard` both enabled (`O`) after the self-test; (c) the log row. `tests_live.test_repos_sql.MeasuredBaseline` by name: 4 ran, 4 OK, 0 skipped. Full `tests_live -v` on the branch: 358 OK, 0 skipped (661.6 s).
  - **`db/051_screening.sql` applied 2026-10-15 01:35:19 UTC** (`migration_log`; owner's paste, the NOTICE was not seen here; the log row exists only if the self-test passed). Chat 4 Phase 3, the Screening Room: pillar `screening` exclusive like tech (sources, stories, feed_rules, feed_reactions, interest_profiles, story_scores CHECKs; `stories_category_screening`); `feed_pillar_guard` with two exclusive worlds; `titles`, `title_trailers`, append-only `watch_status` (finished_at by trigger) and `v_watch_current`; `_e2e-screening`, `add_test_title`, `add_test_trailer`; `ctx_screening` and a silent `ctx_screening_brief`. **Verified via db_inspect the same session:** (a) 7 CHECKs name screening; (b) titles RLS on, 3 policies, authenticated S/I/U no D; title_trailers 1 policy, S only; watch_status 2 policies, S/I only; anon nothing; readonly reads all three and the view; 0 rows each; (c) the 3 watch_status triggers; (d) `screening` (not a brief section) and `screening_brief` (a brief section), both enabled, sort 55; `ctx_screening_brief()` NULL; `ctx_screening()` keys about, finished_recent, new_trailers, upcoming_30d, want, watching; (e) `_e2e-screening` inactive, is_test, `{screening}`; (f) the log row; the guard's body names screening. **One finding:** `authenticated` held INSERT/UPDATE/DELETE on `v_watch_current` (Supabase's default grants on a new object, not revoked), the only public view that did; not exploitable (not insertable or updatable, security_invoker, the base table refuses), fixed by `db/052`.
  - **`db/052_watch_current_grants.sql` applied 2026-10-15 01:49:29 UTC** (`migration_log`). `v_watch_current`: REVOKE ALL from PUBLIC, anon, authenticated; SELECT to authenticated and valemont_readonly. **Verified via db_inspect the same session:** all 10 public views are select-only for authenticated (no INSERT, UPDATE or DELETE), readonly reads every one; `tests_live.test_screening_sql.Objects` passes in its after-052 form.
  - **`db/053_screening_recs.sql` applied 2026-10-15 19:36:32 UTC** (`migration_log`; the third paste). Chat 4 Phase 4, Screening Room recommendations: titles gain the recommendation inputs (keywords, votes, `tonight`, the embedding); `screening_seeds` (15) with seen-and-loved seed reactions and the taste notes; `title_reactions` (append-only); `v_title_state`; the pool, `title_scores` and `screening_rec_runs` (append-only); `screening_seen_pass`; `screening_for_you` (live exclusions, the 6.8 / 200 floor, 4 of each kind) and `screening_trailers_for_you`; the debounced `screening_rerank` trigger; release alerts (`title_provider_arrivals`, `release_pushes` one real a day, `release_alerts` never twice; all append-only); entity kind `title` and `title_news`; `content_items.youtube_key` (UNIQUE, one trailer across both sources) and source kind `tmdb`; `ctx_screening` and a speaking `ctx_screening_brief`; `add_test_rec`, `add_test_seen_title`. **Verified via db_inspect the same session:** (a) 6 tv, 6 film, 3 romcom seeds, 15 seed reactions, 3 notes; (b) the 9 tables RLS on, 1 policy each (`title_reactions` 2), anon nothing, only `title_reactions` takes INSERT, readonly reads all and `v_title_state`; (c) triggers present (`watch_status` 5); (d) readonly executes the four readers, anon none, the test helpers closed to both; (e) `youtube_key` generated with its unique index, the three source rules; (f) entity kind `title`, both Want/Watching titles have one; (g) `ctx_screening`'s new keys, the log row.
    - **The first two pastes failed in the self-test and applied nothing** (read back via db_inspect each time: no log row, no `titles.keywords`, no `title_reactions`): (1) the self-test inserted an explicit NULL into `stories.entities` (NOT NULL, default `'{}'`); (2) its trailer story never set `lead_item_id`, which every read path joins through. Both were the self-test's own fixtures; the schema was right. Before the paste, every column, constraint and helper it assumed was read back from production, and 0 duplicate YouTube keys; that check covered NOT NULL columns without a default only, which is how (1) slipped through: a fixture must also never write NULL into a NOT NULL column that has one.
  - **`db/054_arsenal.sql` applied 2026-10-16 04:36:56 UTC** (`migration_log`; the second paste; owner's paste, NOTICE "054 self-test passed (rolled back)" shown). Chat 5 Phase 1, the Arsenal: `arsenal_pool_runs`, `arsenal_drops`, `arsenal_reactions` (append-only, TRUNCATE-guarded), `arsenal_refs` and `arsenal_words` (lifecycle guards: content changes only while pending, verification settles once, a dropped/seed/rejected row never changes status, no DELETE or TRUNCATE, a real row enters pending); the drop guard (no repeat, no seed, nothing unverified, 3 categories, no real/test mix; one real drop a day by unique index); `arsenal_near_dup`, `arsenal_weights`, `arsenal_pick_drop`; the 27 seeds; `add_test_arsenal_ref/_word/_drop`; `ctx_arsenal` and `ctx_arsenal_brief` (sort 60). Design: `docs/arsenal.md`. **Verified via db_inspect the same session:** (a) the log row; (b) 27 refs, all seed/pending, none test; (c) the 5 tables RLS on, 1 policy each (`arsenal_reactions` 2), anon nothing, authenticated inserts only reactions and never updates/deletes/truncates, readonly reads all; (d) providers `arsenal` (not a brief section) and `arsenal_brief` (a brief section), sort 60, enabled; (e) triggers 3/2/2/4/3 (pool_runs, refs, words, drops, reactions); (f) `arsenal_pick_drop` executable by neither anon, authenticated nor readonly; readers and ctx functions by authenticated and readonly; test helpers by authenticated only; (g) words, drops, reactions, runs all 0; `ctx_arsenal_brief()` NULL; `arsenal_weights()` 11 rows; the Cortés seed says scuttled and the Godfather quote is exact.
    - **The first paste failed and applied nothing** (42601, "INSERT has more target columns than expressions"): every seed row carried 12 values for 14 target columns (`status` and `origin_kind` were missing). Read back via db_inspect: no arsenal object, function, provider or log row. Fixed by selecting the 12 fields from a `VALUES` list and adding `'seed', 'seed'` once. **Rule:** before a migration is handed over, every `INSERT … VALUES` tuple is checked mechanically against its column list (the check found all 27 on the failed file, 0 on the fixed one).
  - **`db/055_arsenal_rooms_word_seeds.sql` applied 2026-10-16 20:31:30 UTC** (`migration_log`; the second paste; owner's paste, the self-test NOTICE shown). The Arsenal's gate fixes:
    - `arsenal_words.status` gains `seed`; inflection, provenance and fiduciary became seeds (asymmetric had already dropped).
    - The picker rotates rooms (the most distinct rooms; the Power Word off the references' majority) and skips holds.
    - ONE re-verification of an unverified pool row, through the worker-only `arsenal_reverify_ref` / `_word`, recorded in append-only `arsenal_reverifications`.
    - `arsenal_holds`: append-only, TRUNCATE-guarded.
    - Item 11's two writes: ref 206 corrected, ref 216 verified.

    **Verified via db_inspect the same session (20:32 UTC):**
    - (a) The log row.
    - (b) The status CHECK lists seed; asymmetric dropped, the other three seed.
    - (c) 206 corrected (the Moneyball book's URL) and 216 verified (the Enchiridion's), each with one `arsenal_reverifications` row (old unverified, `by_whom` "owner, 2026-10-16: item 11 …", role postgres). 190, 192, 201, 209, 212 and 215 stay unverified.
    - (d) Both new tables: RLS on, 1 policy, anon nothing, authenticated select only, readonly select; triggers stamp, immutable, no_truncate.
    - (e) The five functions: executable by none of anon, authenticated or readonly. Both guards read the record table; the picker reads holds.
    - (f) 0 holds before the outcome check.
    - **The first paste failed and applied nothing** (42P01, `relation "public.arsenal_reverifications" does not exist` in `arsenal_words_guard`). Section 2's UPDATE fired the replaced words guard, which read a table the file created only in section 3. Read back via db_inspect: no log row, no new object, the CHECK and the words unchanged, all three guards still db/054's. Fixed by creating the table first. **Rule:** a trigger function that a later statement of the same file fires must find every table it reads already created; and a guard reads an optional table only inside the branch that needs it.
  - **`db/056_arsenal_hold_release.sql` applied 2026-10-16 21:07:36 UTC** (`migration_log`; the first paste; owner's paste, the self-test NOTICE shown).
    - `arsenal_hold_releases`: append-only, TRUNCATE-guarded, `hold_id` UNIQUE (a hold is released at most once); reason, `released_by` (role), `created_at`. The hold stays as history.
    - `v_arsenal_active_holds` (security_invoker): a hold with no release.
    - The worker-only `arsenal_release_hold`.
    - `arsenal_pick_drop`: db/055's, skipping active holds only.

    **Verified via db_inspect the same session:**
    - (a) The log row.
    - (b) The releases table: RLS on, 1 policy, triggers stamp, immutable, no_truncate.
    - (c) The table and the view: anon nothing, authenticated select only, readonly select; the view `security_invoker=true`.
    - (d) `arsenal_release_hold` and `arsenal_pick_drop`: executable by none of anon, authenticated or readonly; the picker reads the view.
    - (e) Before the releases: 0 releases, and active holds 9 (ref 195), 10 (197), 11 (218).

    **Holds 9, 10 and 11 released** with `scripts.arsenal_release_hold` (releases 3, 4, 5; role postgres; reason "false positive: intermediate outcome prompt; final prompt clears (dry run 2026-10-16)"). Read back: `v_arsenal_active_holds` empty; refs 195, 197 and 218 ready; 21 ready references across all 7 categories.
  - **`db/057_chat5_fixes.sql` applied 2026-10-20 20:23:21 UTC** (`migration_log`; the first paste; owner's paste, the self-test NOTICE was not seen here; the log row exists only if it and the backfill assertion passed). Branch `chat5-fixes`, not merged (Railway builds paused).
    - `content_items_frozen` leaves the generated `youtube_key` out of its comparison: in a BEFORE trigger it reads NULL, so every trailer item looked changed and `feed_prune` failed Oct 18 and 19 ("content_items 16900 is frozen"; 67 trailer items blocked the clear of 5,572 rows).
    - `arsenal_outcome_checks` (append-only, TRUNCATE-guarded; a guard on votes vs the conflict flag, every control passed, test/real); `arsenal_pick_drop` takes a reference only with a check on record (owner, Oct 20). Backfill: the 26 refs then in the pool (14 from run 14, round B dry run Oct 16; 12 from run 38, `--write` Oct 20, controls 3/3), all `nnn`.
    - `egress_samples` (append-only, TRUNCATE-guarded) and `v_egress_days` (security_invoker): the egress job's day total every run, and a final row for the day just closed.

    **Verified via db_inspect the same session:**
    - (a) The log row.
    - (b) The freeze function names `youtube_key`; the picker reads `arsenal_outcome_checks`.
    - (c) 26 backfill checks, all `nnn`, no conflict, none test; 26 checks in all; 0 real ready refs without a check.
    - (d) Both tables RLS on, 1 policy each; triggers guard/immutable/no_truncate and stamp/immutable/no_truncate.
    - (e) Both tables and the view: anon nothing, authenticated select only, readonly select.
    - (f) `egress_samples` 0 rows (the worker that writes it is not deployed).
    - **Merge gate on `chat5-fixes` after the paste:** `tests/` 1060 OK; full `tests_live -v`, every connection metered: **430 OK, 0 skipped**, ~2.69 MB read (2,751 KB, 977 s). The trailer freeze test was red on production before the paste (the same "is frozen" error) and is green after it.
  - **`db/058_feed_review.sql` applied 2026-10-20 21:37:26 UTC** (`migration_log`; owner's paste, the self-test NOTICE shown). Branch `feed-review-fixes` (PR #60).
    - `summary_rejections` (append-only, TRUNCATE-guarded, no foreign key so the prune is unaffected): every refused or missing summary, its attempt and reason (feed review item 4).
    - `feed_similarity_log.kind` gains `would_join` (item 3a, shadow only).
    - **A paste reported at ~21:25 UTC did not reach production** (no log row, no table, read via db_inspect at 21:25 and 21:28 UTC); the 21:37 paste did. Before trusting a paste, read its `migration_log` row.

    **Verified via db_inspect the same session:** (a) the log row; (b) `summary_rejections` RLS on, 1 policy, triggers stamp/immutable/no_truncate, no foreign key, 0 rows; (c) anon nothing, authenticated select only, readonly select; (d) the kind CHECK lists `would_join`.
    - **Merge gate on `feed-review-fixes` after the paste:** `tests/` 1067 OK; full `tests_live -v`, every connection metered: **434 OK, 0 skipped**, ~4.71 MB read (4,819 KB, 782 s; the db/057 gate read ~2.69 MB, both under the ~10 MB line).
  - **`db/059_arsenal_phase2.sql` applied 2026-10-21 19:52:26 UTC** (`migration_log`; the second paste; owner's paste, the self-test NOTICE was not reported here; the log row exists only if the self-test passed). Chat 5 Phase 2, branch `chat5-phase2` (PR #62): Arm Me, My Arsenal saves, the Monday quiz (`docs/arsenal.md` §9).
    - `arsenal_refs` gains status `arm` and origin `arm_me` (CHECK: exactly together); the refs guard lets a card enter `arm`, and an arm card never changes status, so it never drops.
    - `arm_requests` (append-only, TRUNCATE-guarded), written only by `arsenal_arm_record`; `arsenal_arm_card` (SECURITY DEFINER, owner only) upserts a card by `canonical_key`; `arsenal_arm_requests_since` (the rate limit).
    - `arsenal_saves` (append-only; is_test from the target; a redundant event refused) and `v_arsenal_saved`; a save of a pending arm card queues ONE `arsenal_arm_verify` job, ever.
    - `arsenal_quizzes`, `arsenal_quiz_reviews` (append-only; the guard recomputes `correct` and the rating; one answer per question) and `v_arsenal_quiz_attempts`.
    - `arsenal_weights` counts a save as two Loves (+0.30), same signature; `arsenal_quiz_line`; `ctx_arsenal` (saved_count, recent_saves, last_quiz) and `ctx_arsenal_brief` (the Monday quiz); `add_test_arm_ref`, `add_test_save`.
    - **The first paste failed and applied nothing** (42601, "syntax error at end of input", in `arsenal_quiz_reviews_guard`): plpgsql ends an `IF` condition at the first `THEN`, and a bare `CASE … THEN … END` inside one cut it short. Read back via db_inspect: no log row, no new table, the status CHECK and the refs guard still db/055's. Fixed by parenthesizing the `CASE`. **Rule: a `CASE` inside an `IF`/`ELSIF` condition in plpgsql is always parenthesized.**

    **Verified via db_inspect the same session (~19:55 UTC):** (a) the log row; (b) the three `arsenal_refs` CHECKs (`status` with `arm`, `origin_kind` with `arm_me`, `arsenal_refs_arm`); (c) the four tables RLS on, policies 1/2/2/2 (arm_requests read only; saves, quizzes, reviews read and insert), anon no select, authenticated no update/delete/truncate, readonly select; both views `security_invoker=true`, select-only for authenticated, readonly select; (d) triggers guard/immutable/no_truncate on each table, plus `arsenal_saves_queue_verify`; (e) the functions: none for anon; `arsenal_arm_card`, `arsenal_arm_record` and the test helpers authenticated only; `arsenal_arm_requests_since`, `arsenal_quiz_line`, the weights and both ctx functions authenticated and readonly; the two save triggers' functions nobody; (f) the review guard holds the parenthesized `CASE`, the refs guard admits `arm`; (g) 0 rows in all four tables, 0 `arsenal_arm_verify` jobs; `ctx_arsenal_brief()` answers (today's drop, no quiz on a Tuesday).
    - **Merge gate on `chat5-phase2` after the paste:** `tests/` 1083 OK; full `tests_live -v`, every connection metered: **453 OK, 0 skipped**, ~3.31 MB read (3,390 KB, 828 s; `test_arsenal_phase2_sql` 5 classes, ~16 KB). The first run had 1 error, `test_arsenal_sql.Guards.test_truncate_is_refused_with_every_referencing_table_named`: its hand-written list missed db/059's referencing tables, so Postgres refused on the foreign key before the guard. It now derives the set from `pg_constraint` (11 tables).
  - **`db/060_job_interval_changed.sql` applied 2026-11-02 22:42:06 UTC** (`migration_log`; the first paste). The owner's paste returned "Success. No rows returned"; no NOTICE was checked, because the file has no self-test (it creates no function or trigger).
    - `job_health.interval_changed_at` (timestamptz, NULL until an interval really changes). The worker's `job_register` stamps it on a changed interval only, and staleness counts from the later of the last good run and that change, in the worker (`ledger.job_stale`) and on `/settings/health`.
    - Why: PR #64's weekly → 6 h change sent a false "arsenal_pool needs attention" at 21:18 UTC Nov 2.
    - PR #67 (`job-interval-staleness`).

    **Verified via db_inspect the same session (~22:45 UTC):** (a) the log row; (b) the column, timestamptz, nullable, with its comment; (c) `authenticated` and `valemont_readonly` can SELECT it (table-level grants), `anon` cannot; (d) 0 of 31 jobs stamped (no interval has changed since the paste).
  - **`db/061_prune_runs_pool_quotas.sql` applied 2026-11-03 19:28:35 UTC** (`migration_log`; the first paste; owner's paste, the self-test NOTICE was not seen here; the log row exists only if the self-test passed). PR #68 (`worker-credit-prune-quotas`).
    - `feed_prune_runs` (append-only, TRUNCATE-guarded), written in the prune's own transaction: `deleted` (table → rows, cascades included), `steps`, `held` (each story rule's protected stories kept, by protection), `embeddings_cleared`.
    - `arsenal_pool_runs.quotas`: a submitted run's target references per category; a CHECK refuses it on any other row and refuses a negative or non-number quota.
    - Why: Nov 3's first 21-day prune and first automatic refill could not be read from the database (the prune's counts lived only in a Railway log line; the refill recorded what it inserted, not what it asked for).

    **Verified via db_inspect the same session (~19:30 UTC):** (a) the log row; (b) `feed_prune_runs` RLS on, 1 policy, anon nothing, authenticated select only, readonly select, triggers immutable/no_truncate/stamp, 0 rows; (c) `arsenal_pool_runs_quotas` CHECK as written; `quotas` jsonb, readonly can select it, 0 rows set.
    - **Merge gate on `worker-credit-prune-quotas` after the paste:** `tests/` 1124 OK; full `tests_live -v`, every connection metered: **463 OK, 0 skipped**, ~3.18 MB read (3,254 KB, 1,003 s). `test_sports_sql.Categories` measured alone: 1,901 KB before the fix, 43 KB after.
  - **`db/062_arsenal_title_hook.sql` applied 2026-11-03 20:48:02 UTC** (`migration_log`; the second paste; owner's paste, reported "success"; the log row exists only if the self-test passed). Chat 5 Phase 3, branch `chat5-phase3`: the Screening Room hook (`docs/arsenal.md` §10).
    - `arsenal_refs.origin_kind` gains `title_hook`; CHECK `arsenal_refs_title_hook` (title_hook exactly when `source_title_id` is set); the refs guard admits a title_hook card only as pool, with its title's is_test, while the title's hook is still to draft.
    - `arsenal_title_hooks`: one row per title ever (UNIQUE `title_id`); identity frozen, queued settles once to drafted or failed, drafted never changes, no DELETE or TRUNCATE. `arsenal_title_hook_reruns` (append-only, TRUNCATE-guarded): the only way a failed hook moves again.
    - `watch_status_title_hook` (AFTER INSERT, SECURITY DEFINER): the first finish writes the hook and, for a real title only, one `job_queue` row `arsenal_title_hook`. `add_test_title_hook_cards` (the e2e marker). `ctx_arsenal.recent_title_cards`; `ctx_screening` finished titles' `arsenal_cards`.
    - **The first paste failed in its own self-test and applied nothing** ("SELFTEST FAILED: add_test_title_hook_cards"): one IF both called the writing helper and read the hook back, and a statement reads one snapshot, taken before the helper's writes. Read back via db_inspect: no log row, neither table, no new function, the origin CHECK and refs guard still db/059's. **Rule: in a self-test, never read back in the same statement as a function that writes.**

    **Verified via db_inspect the same session (~20:50 UTC):** (a) the log row; (b) the origin CHECK lists `title_hook`, and `arsenal_refs_title_hook` is as written; (c) both tables RLS on, 1 policy each, anon no select, authenticated select only, readonly select; (d) the 6 triggers enabled (hooks guard and no_truncate; reruns stamp, immutable, no_truncate; `watch_status_title_hook`); (e) 0 hooks, 0 re-runs, 0 `arsenal_title_hook` jobs; (f) authenticated executes `add_test_title_hook_cards`, anon does not, nobody but the trigger runs `watch_status_title_hook`; the refs guard, `ctx_arsenal` and `ctx_screening` are db/062's.
  - **`db/063_reverify_seeds_arm.sql` applied 2026-11-04 06:21:01 UTC** (`migration_log`; the first paste; owner's paste; the log row exists only if the self-test passed). Branch `verifier-yield`, the verifier yield fix (`docs/arsenal.md` §11).
    - `arsenal_reverify_ref` and the reference guard's re-verify branch accept an unverified **pool, seed or arm** reference (was: pool only). Once per card ever, from unverified only, recorded in `arsenal_reverifications` first; status never changes, so a seed stays a seed and never drops. The guard was generated from db/062's text with that one condition changed.

    **Verified via db_inspect the same session (~06:25 UTC):** (a) the log row; (b) the function and the guard both name pool, seed and arm, and the guard keeps db/062's title_hook rules; (c) neither authenticated nor anon executes `arsenal_reverify_ref`; (d) 0 re-verifications written in the hour after the paste (nothing spent).
  - **`db/064_pool_refusals.sql` applied 2026-11-04 08:04:57 UTC** (`migration_log`; the first paste; owner's paste, the self-test NOTICE seen). Branch `pool-refusals`.
    - `arsenal_pool_runs.refusals` (jsonb): a collected run's refusals by category ("words", "unknown") and reason (`duplicate_key`, `near_dup`, `invalid_field`, `other`); a CHECK refuses it on any other row and any other shape. No function or trigger; the CHECK's JSON paths were tested read-only before the paste.
    - Why: Nov 4's fresh batch refused 20 of 36 references as "invalid" and the reasons were only logged (`docs/arsenal.md` §11.8).

    **Verified via db_inspect the same session (~08:07 UTC):** (a) the log row; (b) the CHECK names `status = 'collected'`; (c) the column is jsonb and `valemont_readonly` can select it; (d) 0 rows carry refusals (no collect since).
  - **`db/065_sports_events.sql` applied 2026-11-04 19:30:12 UTC** (`migration_log`; the first paste; owner's paste, reported "db/065 pasted"; **the self-test NOTICE was not seen (owner saw only "Success"); the log row is present**, and it exists only if the self-test passed). Branch `chat7-phase1`, Chat 7 Phase 1 (`docs/sports_data.md`).
    - `sports_leagues`: the registry (14 leagues; the owner's five cfb, nfl, nba, f1, pga first; `fetch_mode` per_day for team sports, whose date ranges ESPN refuses).
    - `sports_events`: UNIQUE (league, espn_id); (league, kind) must match the registry; a test row is exactly an `e2e-` id; indexes for today by league and live now. The worker upserts in place, never touching `kalshi_event_ticker` (Phase 2).
    - `add_test_sports_event` (verified e2e marker); `ctx_sports_today`, provider `sports_today` (sort 46), not a brief section.

    **Verified via db_inspect the same session (19:34 UTC):** (a) the log row; (b) the 14 leagues, kinds, fetch modes, owner five and CFB/NCAAB params as written; (c) both tables RLS on, 1 policy each, anon no select, authenticated select only (no insert/update/delete/truncate), readonly select; (d) the 4 indexes; (e) `sports_today` enabled, not a brief section, sort 46; (f) 0 events; `ctx_sports_today()` counts `{}`; anon executes neither function, readonly executes the ctx only, authenticated `add_test_sports_event`.
    - **Merge gate on `chat7-phase1` after the paste:** `tests/` 1235 OK; full `tests_live -v`, every connection metered: **483 OK, 0 skipped**, ~2.73 MB read (2,798 KB, 1,047 s; `test_sports_events_sql` 3 classes, ~2.7 KB).
  - **`db/066_kalshi_mapping.sql` applied 2026-11-05 04:10:29 UTC** (`migration_log`; the first paste; owner's paste, reported "db/066 pasted"; the self-test NOTICE was not reported here; the log row exists only if the self-test passed). Branch `chat7-phase2`, Chat 7 Phase 2 (`docs/kalshi_mapping.md`).
    - `sports_leagues`: `kalshi_series`, `kalshi_map` (team_pair, player_pair, by_date, off) and `days_ahead` (14 for NFL and CFB, 7 for the rest; MLB off).
    - `sports_team_aliases`: ESPN → Kalshi code exceptions only, 91 rows (owner kept all 12 FOR REVIEW).
    - `sports_events`: `neutral_site` (nullable) and the link columns, with NULL-safe CHECKs; one Kalshi event per game, match or bout (golf rounds and F1 sessions share); `sports_events_link_guard`, the no-relink rule.
    - The hand repair (owner, Nov 4): append-only `sports_event_relinks`, and the worker-only `sports_event_relink`, run only by `scripts.sports_event_relink`. The guard allows a linked row's change only with its log row from the same transaction; an unlink is for good (out_of_scope).
    - Backward-compatible with the deployed Phase 1 worker (owner's condition for the paste).
    - Before handover, the file was run on a throwaway local PG 17 as one transaction: the self-test passed and 8 mutations each failed it.

    **Verified via db_inspect the same session (~04:11 UTC):**
    - (a) The log row.
    - (b) The 14 registry rows as written.
    - (c) Aliases: cfb 14, epl 5, mcbb 61, mls 3, nba 6, nfl 2.
    - (d) `sports_team_aliases` and (h) `sports_event_relinks`: RLS on, 1 policy each, anon no select, authenticated select only, readonly select.
    - (e) The 9 new columns, all nullable.
    - (f) Triggers `sports_events_link_guard` and `_link_insert`, and the log's stamp, immutable and no_truncate, all `O`.
    - (g) The unique index on `kind IN ('game', 'match', 'bout')`.
    - (h) `sports_event_relink` and the guard: executable by none of anon, authenticated or readonly.
    - (i) 0 relinks, 0 link states, 0 tickers, 0 neutral flags, 965 events.
    - The three pasted function bodies hash identically (md5) to the file's.
    - The deployed Phase 1 worker kept writing after the paste: 9 rows by 04:11:28 UTC; `sports_live` ok, 0 failures.
    - **Merge gate on `chat7-phase2`** (rebased onto `90866df`) **after the paste:**
      - `tests/` 1314 OK.
      - Full `tests_live -v`, every connection metered: **493 OK, 0 skipped**, ~2.83 MB read (2,968,418 bytes, 1,140 s).
      - The first run had 1 error. A Phase 1 test wrote a bare `kalshi_event_ticker`, which db/066's `sports_events_linked_has_ticker` refuses by design. The test now writes a whole link.
  - **`db/067_cfb_alias_indiana.sql` applied 2026-11-05 20:22:22 UTC** (`migration_log`; the first paste; the owner reported "db 67 success". The self-test NOTICE was not reported here; the log row exists only if the self-test passed). Rows only: one `sports_team_aliases` row, cfb `IU → IND`, the Nov 5 alias sweep's one finding (Chat 7 Phase 2). Evidence: ESPN row 10190, USC at Indiana, Nov 14, not_linked; Kalshi `KXNCAAFGAME-26NOV14USCIND`, unclaimed, same Eastern date. The sweep covered all 56 not_linked team_pair rows (cfb 1, mcbb 55; the 55 have no unclaimed Kalshi event). The tuple was checked mechanically before handover (4 columns, 4 values; target, SELECT and alias lists equal).

    **Verified via db_inspect the same session (20:22 UTC):** (a) the log row; (b) cfb `IU → IND` (note "Indiana Hoosiers (USCIND; mcbb has the same alias)"), and mcbb `IU → IND` unchanged; (c) counts cfb 15, epl 5, mcbb 61, mls 3, nba 6, nfl 2 (92). **Pending:** row 10190 linked to `KXNCAAFGAME-26NOV14USCIND` by `team_pair` at the next `kalshi_map` run (22:00 UTC Nov 5); at 20:22 UTC it was still not_linked, because the last run (20:15:55 UTC) came before the paste.
  - **`db/068_gameday.sql` applied 2026-11-05 21:25:58 UTC** (`migration_log`; the first paste; the owner reported "Success on db 68". The self-test NOTICE was not reported here; the log row exists only if the self-test passed). Chat 7 Phase 3, branch `chat7-phase3` (PR #88; `docs/gameday.md`). Read functions only (no table, column or trigger): `gameday_slate`, `gameday_live`, `gameday_event`, `gameday_brief`, the helpers (`gameday_day`, `sports_tv_tier`, …); `ctx_sports_today` extended (same keys, plus `other_tonight`, `this_week`, `alabama_next`); provider `sports_brief` ("Game Day", sort 47, a brief section); `add_test_gameday_event` and `set_test_sports_live` (SECURITY DEFINER, the marker, test rows only). The final text includes the owner's tier additions (TBS, FS2, NHL Net/Network, ESPNEWS).

    **Verified via db_inspect the same session (21:26 UTC):** (a) the log row; (b) `sports_today` 46 not a brief section, `sports_brief` 47 a brief section, both enabled; (c) all 15 functions: 13 readers invoker, anon no, authenticated and `valemont_readonly` yes; the 2 test helpers definer, authenticated only; (d) `sports_tv_tier` answers 2 for NHL Net and ESPNEWS (the final file); (e) `ctx_sports_brief()` for Thu Nov 5: 5 lines (two CFB on ESPN/ESPN2, NBA on NBA TV, NFL on Prime Video, golf R1). **Measured on production:** the page 142 rows / 29,214 bytes (the five, Thu), 240 / 49,845 (All sports); Sat Nov 7 183 / 37,737 and 290 / 59,723; a live poll of 20 games 760 bytes; the detail (Alabama at LSU) 908; `ctx_sports_today()` 6,285.
  - **`db/069_nfl_first_marker_closed.sql` applied 2026-11-05 22:05:16 UTC** (`migration_log`; the first paste; the owner reported "Success". The self-test NOTICE was not reported here; the log row exists only if the self-test passed). Two owner decisions (Nov 5), PR #89: `e2e_marker_ok` fails closed (`coalesce(…, false)`: false, never NULL, on an empty `e2e_markers`); `gameday_brief` leads with the NFL on any night it plays (was Sundays only), Alabama still first. The web's day order matches (`slate.ts` `DAY_ORDER`).

    **Verified via db_inspect the same session (~22:10 UTC):** (a) the log row; (b) both bodies byte-identical to the file (`e2e_marker_ok` md5 `3009ae3e71ca…`, `gameday_brief` `2075af7939ea…`); (c) grants unchanged: `e2e_marker_ok` SECURITY DEFINER, search_path '', anon / authenticated / `valemont_readonly` no, service_role yes; `gameday_brief` invoker, authenticated and readonly yes, anon no; (d) the self-test's rollback restored the markers table (1 row) and its append-only trigger (`O`); (e) `ctx_sports_brief()` for Thu Nov 5 now leads with "NFL: Jacksonville Jaguars at Baltimore Ravens, 7:15 PM on Prime Video."
    - **Merge gate (#89):** `scripts.tests_live_metered` (`24e9511`, its first real run; db/068's gate lost its KB to the scratch copy's bug): **506 OK, 0 failures, 0 errors, 0 skipped (none to name); 3,059,943 bytes (~2.92 MB, 2,988 KB); 1,121 s.** db/069's 4 new live tests all ok: `test_true_for_the_owner_with_the_marker_false_otherwise_never_null`, `test_an_empty_markers_table_answers_false_and_the_helpers_refuse`, `test_grants_unchanged`, `test_thursday_monday_and_a_night_without_the_nfl`. A first attempt from the db/069 worktree ran 0 tests (153 classes skipped: the worktree has no `.env`) and was discarded; the run that counts loaded the main checkout's `.env` into the process.
    - **Merged and deployed:** PR #89 merge `7a16c8e`, 23:11:58 UTC Nov 5 (merge commit, owner's instruction). GitHub deployment statuses for `7a16c8e`: Vercel Production success 23:12:30 UTC; Railway success 23:12:35 UTC; the worker's boot runs 23:12:38 UTC (`capital_snapshot`, `db_size`, `morning_brief`), `sports_schedule` and `kalshi_map` 23:13, `job_health` ok, 0 failures. **The RSS 6 h curve restarts at that boot** (read after ~05:15 UTC Nov 6).
  - **`db/070_brief_slate_order.sql` applied 2026-11-06 17:44:00 UTC** (`migration_log`; the first paste; owner-approved and pasted by the owner. The self-test NOTICE was not reported here; the log row exists only if the self-test passed). PR #92. `gameday_brief()` only, db/069's text outside the games block (owner, Nov 6): the night's best national NBA game always gets a line; the lines read NFL first, then the TV tier, then the start time (rank picks who gets in, never the order). Before handover: a scratch PG 17 with production's schema, `psql -1` passes, 5 mutations each red.

    **Verified via db_inspect the same session (~17:50 UTC):** (a) the log row; (b) the body is the file's byte for byte once the paste's carriage returns are removed (md5 `c2a613d6861901f6638135e2e48b83f2` both; the raw body holds CRs from a CRLF checkout); (c) grants unchanged: invoker, authenticated and `valemont_readonly` execute, anon not; (d) `gameday_brief('2026-11-06')`: Fox 7:00; VT at SMU 6:00; TCU at No. 22 Arizona 9:15; PHI at CLE 6:30 (Prime Video); the golf line.
    - **Merge gate (#92, with the word-cap trim commit `080ab56`):** `scripts.tests_live_metered`: **508 tests, 507 OK, 1 error, 0 skipped; 3,123,548 bytes (~2.98 MB); 1,129 s.** The error, `test_push_ledger.PushLedger.test_gone_deactivates_and_drops_it_from_the_active_set`, was the pooler closing the connection ("server closed the connection unexpectedly"), not an assertion; `tests_live.test_push_ledger` rerun by name: 5/5 OK. `BriefOrder070.test_a_friday_like_nov_6` ok. **The dropped run left its fixture: `push_subscriptions` 798 (`tests_live`, created 17:56:43 UTC), inactive, so not a push target; not deleted.**
    - **Merged:** PR #92 merge `fbf1e44`, 18:06:25 UTC Nov 6 (merge commit).
  - **`db/071_kalshi_board.sql` applied 2026-11-06 20:37:04 UTC** (`migration_log`; the first paste; the owner reported "success". The self-test NOTICE was not reported here; the log row exists only if the self-test passed). Chat 8 Phase 1, branch `chat8-phase1` (`docs/kalshi_board.md`): the Kalshi board, every open Kalshi sports market for the next 36 h, current APP tables upserted in place and pruned (never the `kalshi_markets` archive).
    - `kalshi_series_board` (fee type and multiplier, sport tag, league or NULL, combo eligibility), `kalshi_board_events` (game, milestone start, `starts_at` from the linked `sports_events` row, combo flags, **`combo_size_max`** (the per-event leg limit; NULL = absent, never coerced) and **`combo_quoters`** (a count only); owner, Nov 6), a narrow `kalshi_board` (YES bid/ask, fee at the ask, kind, variant; fillfactor 80); `v_kalshi_board` (NO = 1 − YES; 0 mismatches over 35,521 live markets before the paste). Test rows: an `E2E` series ticker, carried by composite foreign keys.
    - The worker's writers (changed-only upserts, touch, sweep, link, the prune at `coalesce(starts_at, close_time)` + 6 h), executable by no API role; the page's readers; `ctx_kalshi_board` (provider `kalshi_board`, sort 48, not a brief section); `add_test_kalshi_board`.
    - Before handover: a scratch PG 17 with production's schema, `psql -1` passes; **22 mutations each red**; `tests_live/test_kalshi_board_sql.py` 9/9 there.

    **Verified via db_inspect the same session (~20:40 UTC):** (a) the log row; (b) the three tables RLS on, 1 policy each, anon no select, authenticated select only, readonly select, `kalshi_board` `fillfactor=80`; `v_kalshi_board` `security_invoker=true`, select-only for authenticated, readonly select, anon none; (c) the 7 writers executable by none of anon, authenticated or readonly; the 3 readers and `ctx_kalshi_board` by authenticated and readonly, not anon; `add_test_kalshi_board` SECURITY DEFINER, authenticated only; (d) `kalshi_board` provider enabled, sort 48, not a brief section; (e) `combo_size_max` and `combo_quoters` smallint (the final file); 0 rows in all three tables; the database 252,420,623 bytes.
  - **`db/072_kalshi_board_payout.sql` applied 2026-11-07 21:39:56 UTC** (`migration_log`; the first paste; the owner reported "success". The self-test NOTICE was not reported here; the log row exists only if the self-test passed). Chat 8 Phase 1 fixes, PR #100 (`docs/kalshi_board.md` §3).
    - `kalshi_board.payout` text NOT NULL DEFAULT `'binary'`, CHECK binary | scalar; `variant` NULL | protected | ladder | escalator; `kalshi_board_scalar_shape`: scalar exactly when the variant is ladder or escalator, and no `line`.
    - `kalshi_board_markets_upsert` writes payout (absent = binary: backward-compatible with the deployed worker, checked by running `main`'s board `tests_live` against a scratch copy with db/072); `v_kalshi_board` gains `payout` (last); the readers count scalar apart and keep it out of every combo figure and filter; `kalshi_board_game` marks a scalar market and takes an event's combo flags from its binary markets; `ctx_kalshi_board` gains `scalar`, binary-only combo and `top_tonight`; `add_test_kalshi_board` adds a ladder event.
    - Before handover: a scratch PG 17 with production's `public` schema (`pg_dump` as `valemont_readonly`, roles and `auth` stubbed, `app_settings` and `sports_leagues` rows): `psql -1` passes; **17 mutations each red**, each refusal asserted by its constraint name; production's 54,309 rows read against the new CHECKs first (0 would fail).

    **Verified via db_inspect the same session (~21:45 UTC):** (a) the log row; (b) the three CHECKs as written, 14 CHECKs on `kalshi_board`; (c) `payout` text, NOT NULL, default `'binary'`; (d) 54,313 rows, all binary (before #100 deployed); (e) the six function bodies hash identically to the scratch copy's from the same file (CRs removed); grants unchanged (anon none on the view; authenticated and readonly read it; the writer closed to authenticated); `payout` is the view's last column. After #100's first pull: 414 scalar rows, 0 with a line.
  - **`db/073_consensus.sql` applied 2026-11-09 22:47:33 UTC** (`migration_log`; the owner reported "saw success". The self-test NOTICE was not reported here; the log row exists only if the self-test passed). Chat 8 Phase 2, branch `chat8-phase2` (`docs/consensus.md`): the consensus engine.
    - `kalshi_board_events.mutually_exclusive` and the events upsert writing it (absent keeps the stored value: backward-compatible); `odds_api_calls` (append-only, TRUNCATE-guarded) and `odds_api_usage()`; `book_lines` (an APP table with a collapse prune, not append-only: owner, Nov 9); `leg_consensus` (outside-line rows only, cascades with the board) with its changed-only upsert and per-game sweep; `kalshi_taker_fee`, `consensus_leg`, `leg_consensus_for(games)` (the one pricing implementation; `edge_claimed` never on the kalshi tier; normalization band 0.95–1.05) and `v_leg_consensus`; `kalshi_board_game` carries `q`; `ctx_consensus` (provider `consensus`, sort 49, not a brief section); `add_test_kalshi_board` with an exclusive ML event and a books row.
    - Before handover: a scratch PG 17 with production's `public` schema (`pg_dump` as `valemont_readonly`, db/071's grants replayed): `psql -1` passes; **60 mutations each red**; 17 INSERT tuples checked against their column lists. Two owner rounds before the paste (the first file was amended for `edge_claimed`, the band and `book_lines`' collapse).

    **Verified via db_inspect the same session (22:47–22:50 UTC):** (a) the log row; (b) RLS on `odds_api_calls`, `book_lines`, `leg_consensus`; (c) 2,016 events, 0 with the flag (until the worker sends it); (d) provider `consensus` sort 49, not a brief section, enabled; (e) `v_leg_consensus` 14,662 binary markets, all kalshi, 0 claimed; (f) `odds_api_usage()` spent 0, remaining NULL, allowance 500; (g) the 12 function bodies identical (md5, CRs removed) to the scratch copy's; grants: authenticated cannot run the writers or `book_lines_prune`, anon neither `ctx_consensus` nor `v_leg_consensus`, `valemont_readonly` runs `leg_consensus_for`, authenticated `odds_api_usage`. **Measured:** `kalshi_board_game` for DEN at HOU (957 markets) 195 KB, `q` 102 KB of it (86 KB without).
    - Next new file: `db/074`.
  - **Rule (owner, 2026-10-12): the migration record updates in the same session a migration is verified, never deferred to a merge.** Record `applied_at` from `migration_log` and the verification, and move "Next new file" here and in MASTER_PLAN §"Migration numbering", in their own commit.
- **Known intermittent `tests_live` skip (identified 2026-10-12):** `test_system_jobs_sql.SystemJobsSql.test_claim_retry_and_finish` skips with "another poller claimed the test job first" when the production worker's 10 s `job_queue` poller claims the test's `noop` job first (observed 2 of 60 isolated runs). By design (skip, not fail); not a broken invariant. Two sibling tests in the same file skip the same way ("another poller claimed/failed the test job first"). Since 2026-10-13 `tests_live` runs with `-v`, so a skip is named, not assumed.
  - **2026-10-13, cleared (owner):** a quiet Phase 3 run reported "314 tests, OK (skipped=1)" with no name. The full verbose rerun had **0 skips** (314 OK, 593 s). The owner's judgment: the plausible cause is this documented poller race, since every migration-gated skip cannot fire (all applied). One alternative was not ruled out: a class whose own `setUpClass` hits a transient "database unreachable" is also reported as one skip.
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
- **Tests (2026-10-10, after Chat 2 Phase 4):** `tests/` **496** OK; `tests_live/` **229** OK, 0 skipped (19 in `test_wags_sql.py`). Web: `pnpm test:unit` 72; `pnpm test:e2e` logged out 55 pass, 77 skip; owner runs (phone, mock model) `wags.spec` 10, `screens-wags` 2.
- **Tests (2026-10-10, after Chat 2 Phase 3):** `tests/` **493** OK; `tests_live/` **210** OK, 0 skipped. Web: `pnpm test:unit` 42; `pnpm test:e2e` logged out 51 pass, 55 skip; owner runs (phone) `capital.spec` 9, `screens-capital` 2, `goals.spec` 6, all pass.
- **Tests (2026-10-09, after Chat 2 Phase 2):** `tests/` **472** OK; `tests_live/` **170** OK, 0 skipped, **0 failing** (Phase 2 added `test_ventures_sql.py` 17 and 2 retired-job tests). Web: `pnpm test:unit` 30; `pnpm test:e2e` logged out 49 pass, 33 skip; owner run `ventures goals owner` 20 pass, 8 skip. Run both from `workers/` with **`..\venv\Scripts\python.exe`**.
  - The 3 `test_attempt_budgets.py` failures that predated Chat 2 Phase 1 are fixed by PR #10 (§8, sweep starvation). PR #10 added 9 to `tests/` (`test_first_look.py`) and 1 to `tests_live/` (`test_sweep_order.py`, about 90 s: it waits out 70 fixtures' deadlines and runs real sweeps).
  - **`tests_live` fixtures:** a parked fixture (`due=False`) is parked for both sweeps; only `due=True` fixtures enter a due set, and they are sealed. Never give a parked fixture a near close.
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
- **Railway (verified 2026-10-08):** service `valemont-agents`, project `accomplished-heart`, environment `production`. **Trial plan: $5 credit, ends ~2026-10-18; when it lapses the worker stops and needs a manual redeploy.** No payment method on file.
  - **Decision CLOSED 2026-10-09 (owner): stay on Railway's Free plan.** The worker runs at about 40 MB steady, with near-zero CPU, with the canary gone. Oracle Cloud is not needed.
  - ~~**Superseded 2026-10-13 (owner, 13:28 Chicago): Railway moves to Hobby**~~ **WRONG, corrected 2026-10-20 (owner): the account stayed on the trial.** The Oct 13 note recorded a switch that had not happened.
  - **The trial ended and Railway removed the worker at ~20:06 UTC Mon 2026-10-19** (owner, 2026-10-20). Seen in the database: the last heartbeat 20:06:42 UTC, nothing ran after it, and the watchdog pushed "Worker down" every ~35 min (notifications 529–570). Seen in the Railway CLI: the running deployment `4e3c4eae` (merge `ed5ffcd`, Oct 16 21:24 UTC) is `REMOVED`, no deployment is active, and the service shows `Failed` only because the newest remaining record is `c5aa6e17`, a failed deployment from 2026-10-08 05:32 UTC. No commit or deployment was made Oct 17–19. The CLI keeps no removal time or reason; the cause is the owner's reading.
  - **The owner switched the account to Free on 2026-10-20.** Railway builds were paused that day (Railway's outage from 17:03 UTC), so the worker was not yet redeployed when this was written.
  - **Decided (owner, 2026-10-20): stay on Free until the owner funds Hobby ($5/month, $5 of usage), which happens before the credit runs out.** The owner checks Railway's usage page **weekly** and upgrades when **less than $0.25** of credit is left. Why the check matters: Free's $1 credit is less than the worker's ~$1.3–1.4/month of memory at ~141 MB at rest, so left alone on Free the worker lapses mid-month (it stops, the watchdog pushes "Worker down", and it needs a hand redeploy). Oracle's free VM was considered and not chosen.
  - **Railway Hobby funded 2026-11-05 (owner): the plan page reads Hobby** ($5/month including $5 of usage). The Free-plan weekly credit check and its $0.25 trigger are retired. The worker booted 20:01:14 UTC, before PR #83's merge; this note does not say that the boot came from the plan change. Chat 7 Phase 2 merged on it, and Chat 8's prerequisite is met.
  - **Worker RAM after the Phase 1 hotfix** (`job_health` RSS notes, read via `db_inspect` 2026-10-13 18:27 UTC; boot 02:54:57 UTC): 74 MB at boot; 129 MB at +2 h 10 m (`capital_snapshot`, nearest to +3 h); 133 MB at +5 h 05 m (`db_size`, nearest to +6 h); 130 MB at rest at +15.5 h (`heartbeat`); ingest peak 155 MB, back to 129 MB after. Slope +2 h → latest: ~+0.07 MB/h, flat within rounding. `job_health` keeps only the latest note, so readings at exact checkpoints did not exist; **from 2026-10-13 every job's INFO log line carries the same RSS note** (`core/jobs.py` `run_tracked`), and Railway's logs keep the history.
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
- **Deploy: owner GO (2026-10-01); `prod-roster` merged to `main`.** Go-live (out of scope until the owner says so):
  1. paste `db/013`;
  2. record an `nfl_ml` fit (production has **no** `model_versions` row for it, so it stands down every window until one exists);
  3. set `ROSTER=production` and delete `CANARY` in the same change.
- **🔁 RECURRING, weekly, by hand:**
  - **Archive, weeks 6–18: Supabase Storage (from 2026-11-04).** Every Tuesday after Monday night's game, from `workers/`, for the week just played:

    ```
    ..\venv\Scripts\python.exe -m jobs.archive_nfl_props --week N --to-storage
    ```

    then commit `docs/props_archive/manifest.jsonl` (the four lines it appended). Private bucket **`nfl-props-archive`**, objects `2026/week-NN/rR/{markets,settlements,candles,prints}.jsonl.gz`; design `docs/props_archive_storage.md`. The S3 keys (`PROPS_ARCHIVE_*`, `.env.example`) live in the **root `.env` only**, never Railway or Vercel; the archive runs from the owner's machine, never on the worker. It writes nothing to Postgres and never reads an object back (no GET in `core/s3.py`). A matching rerun is a no-op; a mismatch refuses (exit 2). **If a week logs unsettled > 0, rerun it a week later as `--revision 2`** (allowed only then). Schedule: week 9 Tue 2026-11-10, then each Tuesday, week 17 Tue 2027-01-05, **week 18 Tue 2027-01-12** (nflverse: week 18 is 2027-01-10).
    - **Weeks 6–8, 2026-11-04** (owner's runs; manifest read, nothing F1 or F2 evaluates): week 6: 2,673 markets, all settled, 108,000 candles, 61,596 prints; week 7: 2,825, all settled, 139,186, 54,971; week 8: 3,071, all settled, 121,568, 52,744. 0 games pending, 0 prints without an id, 0 unsettled each week (no rerun due). 12 objects, 7,934,238 bytes in all (2.77, 2.64, 2.52 MB a week); every HEAD size matched and every ETag equalled the body's md5 (`etag_is_md5` true on all 12). Postgres archive tables unchanged by the work (13,710 / 13,710 / 110,963 / 64,306 rows, same bytes, db_inspect 23:10 UTC).
  - **Archive, weeks 1–5: Postgres (history).** `..\venv\Scripts\python.exe -m jobs.archive_nfl_props --week N` (no flag) wrote the db/011 and db/014 tables; week 5 and weeks 1–4's settlements stay there. Never use it for a new week. Store-only; never query the archive before January (§8.1). The original estimate (~150–250 MB for weeks 5–18) was too low, below.
    - **First runs, 2026-10-13** (job summaries; only row counts and table sizes read since, nothing F1 or F2 evaluates). The job had never loaded `.env` (fixed, `tests/test_jobs_env.py`); the first attempt failed at its first write and stored nothing. Week 5: 2,751 markets, all settled, 110,963 candles, 64,306 prints, 0 games pending (13:33–14:05 Chicago). Weeks 1–4, settlements only: 2,527, 2,747, 2,718, 2,967 markets, all settled. **0 unsettled in every week, so no rerun is due.** Totals: 13,710 markets and settlements.
    - **⚠ The size estimate above is too low.** The database went from 46.7 MB to 87.6 MB (MB = 2^20) over the run; the four archive tables hold ~40 MB, of which week 5's candles and prints are ~34 MB with indexes (candles 18.4 MB, prints 15.6 MB). At that rate weeks 5–18 are ~470 MB, about the whole 500 MB tier, before the feed's growth. **Resolved 2026-11-04: weeks 6–18 go to Supabase Storage** (above).
  - **`nfl_ml` refit** (`jobs.fit_nfl_ml --lambda 10`). Preregistration §3.1 requires it and nothing schedules it.
- **Forward-only F1 (vacated usage, as amended by F1a: Questionable + game-day `INA`) and F2 (longshots under P2)** are pre-registered in `docs/preregistration_nfl.md` §8 and evaluated on 2026 weeks 5–18. **No interim look.**
- **⏰ JANUARY REMINDER — on or after 2027-01-20, AND only once week 18's archive shows 0 unsettled** (owner, 2026-11-04): write the F1/F2 runner exactly to §8, commit it, then execute it **once**, reading the archive: Postgres for weeks 1–5, Storage for weeks 6–18 at each week's highest revision in the manifest, sha256-checked before parsing (`docs/props_archive_storage.md` §8). It must refuse before 2027-01-20 and refuse if output exists. **Why the second condition:** nflverse puts week 18 on 2027-01-10, not the Jan 3 frozen §8.1 assumes, and props settle up to ~15 days after the game, so week 18 may not be fully settled until ~Jan 25. If week 18's r1 (Jan 12) logs unsettled markets, rerun it as `--revision 2` a week later, and the runner waits for a revision with 0. The frozen §8.1 text is unchanged. If data are missing from both the archive and Kalshi, record the hypothesis as unevaluable, which is a fail.
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
    - pushes "Worker down" when the heartbeat is more than 5 min old (again at most every 4 h while the same outage lasts; it was 30 min until 2026-10-20, when one ~23 h outage sent 41), and "Worker back" on the first good call;
    - answers 200 ok/down, 503 when it can't read the heartbeat, 401 for a bad token;
    - a failed push never fails the request (PR #7): it's recorded in `job_health.last_error` and retried;
    - pings **healthchecks.io** (`WATCHDOG_PING_URL`, period 5 min, grace 5 min) on every run, and `/fail` when it can't read;
    - `SUPABASE_SECRET_KEY` is read only in `route.ts` (`check:secret-key`).
  - **Known Supabase issue:** PostgREST intermittently rejects the gateway's per-request token as **"JWT issued at future"** (PGRST303), because the gateway's clock runs ahead of PostgREST's.
    - Seen in production at 03:00, 03:05 and 03:30 UTC on 2026-10-09 (500s on the old route), and in local calls.
    - The route now retries once (rescued the 04:35 drill call). Persistent skew is a 503 plus healthchecks `/fail`.
    - The worker connects to Postgres directly and is unaffected. Worth reporting to Supabase if it continues.
    - **2026-10-10: it reached web server reads** (Home's `ensureRollover`: "Couldn't check goal rollover: JWT issued at future"). The web's server-side client now retries any PGRST303 request **once** after 250 ms, writes included (the skew rejects before PostgREST does anything), with one log line naming method and path (`src/lib/supabase/skew-retry.ts`; `e2e/supabase-unit.spec.ts`).
  - **AI budget guard** `core/ai.py` (§6). `ANTHROPIC_API_KEY` is set on Railway, Vercel (Production) and in the root `.env` since 2026-10-10.
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
- **Ventures HQ (Chat 2 Phase 2, 2026-10-09):**
  - **db/022:**
    - `ventures`, `venture_workstreams` and `venture_dates` are app tables.
    - `venture_log` is **append-only**: select and insert policies only; a trigger refuses UPDATE and DELETE; TRUNCATE is refused.
    - The one delete allowed on the log is the cascade from deleting the venture. The trigger lets a log row go only when its venture no longer exists.
    - **Auto-log:** a database trigger writes an `auto` entry ("Next action: old → new") when a venture's stage, next_action or blockers changes, or a workstream's state or next_action changes. Names and notes don't log.
    - `goals.venture_id` (on delete set null); the carry functions copy it.
    - `v_venture_today` (security_invoker) lists open dates due today or earlier, in Chicago time, excluding archived ventures.
    - Seed: 7 ventures (NCLEXCompass and Senior Care Systems dropped, per the owner).
  - **db/023:** `job_health.retired_at`. `health_drill` is retired: kept as history, off `/settings/health` and out of the health monitor. `job_register` un-retires a job that is scheduled again. This was the phase's only worker change (`ledger.job_register`, `ledger.job_health_rows`).
  - **Web:**
    - `/ventures` ledger: set-up ventures first (two lines), name-only ventures one line ("not set up"); Archived fold; "+ Venture".
    - `/ventures/[slug]`: hero, NEXT, blockers, workstreams with a Parked fold, dates with a Done fold, this week's linked goals, notes, and the log with a kind toggle; archive and restore in the ⋯ menu.
    - Home TODAY above This Week.
    - Venture chips on business goals.
    - Nav: Ventures is built; the dock is Home · Goals · Ventures · More.
    - Chips are 32 px outlined pills inside 44 px targets (`components/ui/chip.tsx`).
  - **Owner e2e helper lessons:**
    - Set the theme cookie at path `/` (a nested-page `url:` cookie lost to the `/` one).
    - Sign in once per worker; per-call sign-ins tripped Supabase Auth's rate limit.
- **Capital Tracker (Chat 2 Phase 3, 2026-10-10), paper only:**
  - **db/026** (bankroll_entries, crypto_holdings, capital_snapshots; all append-only, all carry `mode`), **db/027** (`is_test` entries, written only by `add_test_bankroll_entry` with a verified e2e marker, excluded from every total and snapshot) and **db/028** (valemont_readonly can read every view) are applied. A mistake is corrected with an adjustment, never an edit.
  - **Worker:** `capital_snapshot`, 00:05 America/Chicago and at boot, writes each ended day's closing value per mode and source. By hand: `-m scripts.capital_snapshot --backfill` (prints rows per mode; 0 = already complete).
  - **Web `/capital`:** the paper total is the hero, tagged PAPER. LIVE appears only when live rows exist and needs LIVE typed before an entry. The share bar shows only with 2+ sources. Entries: the latest 10, then "All entries · N". Each is two lines: the note (or, with no note, its kind) and the amount in a fixed 12ch column, then mono meta with an outlined neutral `test` tag inline.
  - **Live 2026-10-10:** Railway deployment `579b757b` (boot 15:43:33 UTC; `job_health.capital_snapshot` ok 15:43:34, no error). Production `--backfill` printed `paper 1` (bankroll, 2026-10-09, 1000.00, written 15:43:10 UTC); live has no data. Vercel production `dpl_3AtKLEbpSLm1tMtKDTq83TrPcdiB`; logged out, `/capital` redirects to `/login?next=%2Fcapital`.
  - **Colour rule: negative amounts and a falling daily change are primary text with a minus sign, never danger red,** because on paper a withdrawal is not a loss. **Once Chat 9/10 sources (bets, bots) feed capital, a negative daily change returns to the loss colour.** Red is reserved for real losses.
- **Wags and the context layer (Chat 2 Phase 4, 2026-10-10):**
  - **db/029:** `context_providers` (the registry; only migrations write it) naming one SECURITY INVOKER STABLE function per pillar (`ctx_goals`, `ctx_ventures`, `ctx_capital`; paper and live never summed). `context_snapshot()` runs every enabled provider; one that raises is reported as `{"error"}` under its key. `context_snapshot_record()` stores it in `context_snapshots`, deduped by a hash of the data without the clock. `wags_threads`; `wags_messages` append-only (role user/assistant/tool; a tool row is a proposal's outcome; the only delete is a thread delete's cascade). `ai_budget_state()` and `wags_user_messages_since()`. The read-only role executes the readers only.
  - **The context layer is SQL** so the Morning Brief (Phase 5, worker) reads the same implementation as Wags. A new pillar adds a provider function and a registry row in its own migration.
  - **Web AI twin** (`src/lib/ai/`): `pricing.ts` mirrors `core/ai.py` PRICING (`tests/test_ai.py` WebTwinPricing fails on any difference); `budget.ts` reads `ai_budget_state()` and shares the worker's 80%/100% alert state; every call writes `ai_usage`; `claude.ts` is the only importer of the Anthropic provider (`pnpm check:ai-import`).
  - **Pricing verified 2026-10-10** against platform.claude.com/docs/en/about-claude/pricing: the 2026-10-06 table had Sonnet 5.5 cache reads at $0.20; it is **$0.10** (0.05x). Fixed in both files. No row had been costed at the old rate.
  - **`/api/wags`:** owner only; 12 user messages per rolling minute (database clock); 4,000 characters; budget (Sonnet, Haiku from 80%, refused at 100%); history rebuilt from the database, never from the client; system = persona + context, cache breakpoint on the last system block; the clock and the page note ride on the newest turn only. `maxDuration` 300 (Hobby with Fluid compute). A stopped stream keeps what arrived, marked `incomplete`, with input tokens from Anthropic and output estimated at ~4 characters a token.
  - **Mock model** only when `VERCEL` is unset AND `WAGS_MOCK=1` (`playwright.config.ts` sets it for every e2e run). Mock turns write no `ai_usage` and no `context_snapshots` row. Local e2e may also send `x-wags-e2e-rate-limit` and `x-wags-e2e-budget`, honoured in mock mode only.
  - **Persona** is versioned (`src/lib/wags/persona.ts`, `PERSONA_VERSION`, stored on each answer). Wags is read-only: it proposes (`propose_goal`, `propose_venture_log`, `propose_next_action`); Confirm runs the existing server actions.
  - **UI:** the dock's centre is the WAGS mark (opens a 92% sheet, drag to dismiss); desktop has "Ask Wags" on the rail (right panel); `/wags` lists threads (a toggle on phones, a column on desktop), rename and archive. The Wags bundle loads on first open only.
  - **Live 2026-10-10:** Vercel `dpl_8AbJCcZpQMzN92KNrqqnGF98Nwig`, Railway `97a33d2b`. `ai_smoke` (local): Haiku, 18 in / 4 out, $0.000004, `ai_usage` id 64.
  - **The owner's first real thread** (18:49–18:52 UTC, from Ventures · Sail Beach Club, read via `db_inspect`): `ai_usage` 69 (title, Haiku, $0.000014), 70 (Sonnet, 430 in, 520 out, 2,999 cache written, $0.013558), 71 (987 in, 544 out, **2,999 cache read**, $0.007714), 72 ($0.012596); each matches the pricing arithmetic to the micro-dollar. Snapshots 19 and 21 (all three pillars, no error, ~1.6 KB); turn 2 reused 19 (dedupe), turn 3 got 21 after a confirmed next action changed the data. One proposal dismissed, one next action and one goal confirmed, each a tool row. Thread auto-titled "Weekly Deadlines Overview".
  - **Known: a proposal can arrive with no sentence.** Sonnet's chat mode (`thinking: between_tools`) may still think right before a tool call; message 188 was a reasoning block plus a proposal, no text, against the persona's "say in one line what the proposal does". Its 544 output tokens were mostly thinking. Not a fault in the record. **Scheduled as Chat 2 Phase 5, Step 0 item 4** (owner, 2026-10-10), below.
- **The Morning Brief (Chat 2 Phase 5, 2026-10-10):**
  - **Storage:** `briefs` (db/001, empty until now) extended in db/030 rather than a new table: `kind`, `brief_date`, `body` jsonb, `model`, `ai_usage_id`, `fallback`, `voice_version`, `word_count`, `is_test`. Append-only; a regenerated brief is a NEW row and the latest per date wins (`v_briefs_latest`). `brief_deliveries`: append-only, one per kind and date, only for a real brief.
  - **Test briefs** (`is_test`, from `add_test_brief()` with the verified e2e marker) are invisible to every real selector: `ctx_brief`, `v_briefs_latest` (Home, /brief, /brief/[date], history) and the 09:45 job (`ledger.LATEST_BRIEF_SQL`). `tests_live/test_brief_sql.py` drives the real push job in a rolled-back transaction: a newer test brief never beats the real one; with only a test brief the job makes a real one and never pushes the test one. Only a marked e2e request shows them.
  - **Sections ARE the registry:** `context_providers` with `brief_section`, in sort order (goals, ventures, capital). A later chat flips its provider's `brief_section` and its section appears with no generator change (contract in `core/brief.py`'s header). `ctx_brief` is a provider with `brief_section = false`.
  - **Generator** (`core/brief.py`): Sonnet via `core/ai.complete()`, critical, the shared voice, `between_tools`, structured JSON, validated (registered keys, headline, one_move, at most 260 words). Refusal or two invalid answers fall back at once; API failures retry every 15 min and fall back from 09:30.
  - **Live 2026-10-10 (PR #20, merge `6d3c73c`):** Vercel `dpl_7wfXPwn1mDBDuJCBPVNdwAA4FoNx`, Railway `3c1be7d0`. The worker booted at 17:47 Chicago and, by the boot rule, generated Saturday's brief and sent a REAL "Late ·" push (delivery 29, notification 132). It also exposed a race: the generate and push jobs both found no brief and both called Claude (briefs 124 and 125, $0.0106 each); only 125 was pushed (one per date held). Fixed in `brief-boot-race`: generation runs under `ledger.brief_generation_lock` with a re-check inside (`core/brief.ensure_brief`). Production `--generate`: brief 126, 182 words, `claude-sonnet-5-5`, $0.007354 (`ai_usage` 83: 1,145 in, 492 out, 1,442 cache read). `--force-test-push`: notification 133, not a delivery.
  - **Schedule (Chicago, database clock):** `morning_brief` 06:15 to 09:30 every 15 min and at boot; `morning_brief_push` 09:45 and at boot, once per date (advisory lock + check + push + record in one transaction), "Late ·" after 09:50, skipped after 18:00. Both in `job_health`. By hand: `-m scripts.morning_brief --generate | --push | --force-test-push | --fallback`.
  - **One voice:** `prompts/wags_voice.md` is the Wags voice for both Wags and the brief. The web generates `src/generated/wags-voice.ts` from it at build; the worker reads it via `core/paths.VOICE_FILE`. Railway builds from `workers/` only, so `workers/prompts/wags_voice.md` is a byte-identical copy in the image (`tests/test_voice.py` fails on drift). Edit the root file, copy it over, bump `VOICE_VERSION`.
  - **Silent proposals fixed:** Sonnet cannot turn thinking off (AI SDK provider docs); Wags runs `between_tools` at effort `low`, the persona demands a sentence before any proposal, and the route inserts a generated "Proposing: …" line (marked `providerMetadata.wags.generated`) when a turn ends with a proposal and no text. Proposal rows name the venture, not its slug.
  - **Web:** Home's brief card above TODAY; `/brief`, `/brief/[date]` with a Past briefs fold; Ask Wags about this opens the sheet with Context · Brief; nav (More, rail).
  - **First scheduled brief, Sun 2026-10-11 (read via `db_inspect`):** brief 173 (the only row for the date, real), created 11:15:07 UTC (06:15 Chicago), `claude-sonnet-5-5`, 185 words, not fallback, voice `2026-10-10.2`; `ai_usage` 84 (1,167 in, 485 out, 1,442 cache written, $0.010789, critical). Delivery 45 → notification 134 `sent`, no error, 14:45:02 UTC (09:45 Chicago), `late = false`. `job_health`: `morning_brief` last ok 14:30:00, `morning_brief_push` last ok 14:45:02, both 0 failures, `alert_state` ok. The owner confirmed the push arrived.
- **Server actions never throw an expected failure (2026-10-10).** Every action in `apps/web` returns `ActionResult` (`{ ok: true, … } | { ok: false, error }`) via `settle()` in `src/lib/action-result.ts`. Inside an action, say no with `throw new Refusal("…")`. Why: in a production build Next redacts a thrown message, so the page showed "Minified React error" (number 441) instead of the reason. Pages show `error` in their alert and revert; only a network failure can still throw on the client. `e2e/goals.spec.ts` forces a real server-side refusal and checks the readable message.
    - After sign-in, wait out "JWT issued at future" with a harmless read before any write.
- **Tech Hub (Chat 4 Phase 1): GREEN 2026-10-14 (owner)**; watch items 1 and 2 closed, 3 pending (below). Design and rules: `docs/tech_hub.md`; migrations db/045–db/048 (above).
  - **Owner checks, 2026-10-14:**
    - `e2e/tech.spec.ts`, phone: **7/7 pass**.
    - Screenshot gate, phone and desktop, both themes: **approved**.
    - Forced test push (`-m scripts.tech_test_push`): **arrived with the phone locked and opened the story**. Read via `db_inspect`: notification **353** (`tech_free_credits`, "Free credits (test): Update to OpenAI API Rate Limits", `/tech/story/8941`), `sent` 06:07:03 UTC, no error; `tech_credit_pushes` row 76 with `is_test = true`, so the day's real push room still read 3.
  - **Watch items (two closed by the owner Oct 14; one pending):**
    1. **CLOSED GREEN (owner, Oct 14), with a caveat.** Page-watcher latency on the first anthropic.com/news, claude.com/blog or openai.com/news post first seen after deploy (`-m scripts.tech_latency --since 2026-10-14T06:22:00Z`, 20:25 UTC Oct 14). The feed path passed earlier: 3 items at 12.7–24.4 min, 0 over the limit (06:10 UTC Oct 14). anthropic.com/news (2 posts) and claude.com/blog (1) were first seen on their stated day (date-only sources). openai.com/news: 1 date-only pass, 3 over 30 min (533, 323, 383 min), each with an on-the-hour published time (07:00, 12:00, 12:00 UTC); the source fetched every 30 min with 0 failures, so the lag is attributed to the source's scheduled timestamps, not the worker. **That is an inference, not a measurement:** what each earlier fetch contained was not recorded. Phase 2 adds the newest 3 item GUIDs to every fetch's INFO log line so a future late item can be checked.
    2. **CLOSED (owner, Oct 14). New baseline for Phase 2: 138 MB at rest.** Worker memory at rest against the ~130 MB baseline, read from the Railway log RSS lines; due after 08:50 UTC Oct 14. **Read 20:25 UTC Oct 14** (deployment `4fce67ec`, boot 06:22:38 UTC, every RSS line since boot): boot 75 MB; at rest 126–129 MB to 14Z, then steps at the 14:45 brief push (+5) and the 15:25 ingest (+4) to 136–138 MB, 134 MB at the last reading; ingest peak up to **168 MB** (15:55), returned to 134–139 MB after each run; slope −0.19 MB/h since 15:30Z (flat), +1.06 MB/h from 07Z (the two steps). The owner's reading: flat, returns after ingest, the +5/+4 MB steps are one-time loads. Detail: MASTER_PLAN §6, Chat 4 Phase 1.
    3. **PENDING:** the why-it-matters Haiku cost for one full Chicago day (`ai_usage`): Oct 14 Chicago ends 05:00 UTC Oct 15; not read yet.
- **Next:**
  - **Zero-attempt database invariant** (db/024, db/025, both applied): merged from branch `db-zero-attempt-invariant`. `tests/` 479 OK; `tests_live/` 181 OK, 0 skipped (2026-10-09).
  - **Chat 2 COMPLETE (2026-10-11).** Phase 5 closed on the first scheduled brief and push (above).
  - **Chat 3:** Learning engine, Home feed, Sports News (MASTER_PLAN §6). Phase 1 GREEN 2026-10-13; Phase 2 merging; Phase 3 on `chat3-phase3-sports`.
  - **Carried out of Chat 2: DONE, Mon 2026-10-12** (the last item; read via `db_inspect`, owner-confirmed):
    - `goals_rollover` started 05:01:00 UTC (00:01 Chicago), ok 05:01:01, 0 failures. It carried the 3 weekly goals still open in week 2026-10-05 into week 2026-10-12 (`goals` 319639a7…, 8d8fd0c3…, c50a7981…, carry_count 1, created 05:01:00.562 UTC).
    - `goals_monday_push` started 12:00:00 UTC (07:00 Chicago), ok 12:00:02. Notification 206 "Set your week" `sent` 12:00:01 UTC, no error. The owner confirmed it arrived.
  - **Silent proposals, verified after the fix (2026-10-11, phone app, read via `db_inspect`):**

    | | Before: message 188 (2026-10-10) | After: message 260 (2026-10-11 22:12:53 UTC) |
    |---|---|---|
    | Tool | `propose_next_action` | `propose_goal` (confirmed, tool row 261) |
    | Parts | step-start, **reasoning**, tool call; **no text** | step-start, **text**, tool call; **no reasoning part** |
    | Text before proposal | none | model-written, 2 sentences ("Proposing it as a weekly goal tied to Sail Beach Club…"); not the server's generated fallback (no `providerMetadata.wags.generated`) |
    | Output tokens | 544 | **254** (−53%) |
    | Persona | `2026-10-10.1` | `2026-10-10.2+web.2` |
    | `ai_usage` | 71: $0.007714 (2,999 cache read) | 86: $0.010932 (131 in, 3,252 cache written, first turn of a new thread) |

    - The turns aren't identical: different tool, different thread. The cost is higher only because a new thread writes the cache; output tokens are the like-for-like figure.
    - The generated "Proposing: …" fallback has not fired on a real turn yet.
  - **Railway: the trial ended ~20:06 UTC Oct 19 and removed the worker** (the Oct 13 Hobby note was wrong). On Free from Oct 20; **Hobby funded 2026-11-05 (owner)** (Current State, "Railway").
  - Still pending from before: owner review of the crypto track, owner go-live, and the weekly archive.
  - Any new idea needs a new pre-registration and forward-only validation.
