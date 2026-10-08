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
  web/           Next.js (Phase 2). Reads Supabase server-side (§5). Never
                 writes ledger tables.
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

**Status 2026-10-08: none of these policies exist yet.** Every table still has
RLS on with NO policies, so anon and authenticated see nothing. The owner
policies arrive in db/019+ before apps/web reads any table.

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
- Every agent action emits an event row. The dashboard is a consumer of that
  stream — build the stream first, the visuals last.
- Secrets in `.env`, which is gitignored. `.env.example` documents the keys.
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
  APScheduler firing on a real clock, and the SIGTERM drain.

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
  from `workers/`) ← **you are here**

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

## Current State (2026-10-08)

- **Migrations:** **`db/001`–`db/012` and `db/014`–`db/018` applied.** 007–012 verified live by `workers/scripts/verify_migrations_007_012.sql` (20/20). 014–018 were pasted 2026-10-08 and verified by read-only SELECTs. `migration_log` (UTC): 015 04:41:03, 016 04:41:16, 017 04:41:30, 018 04:50:35. 014 predates the log; its tables and triggers are present.
  - **`db/013` (enable the production roster) stays unpasted.** It is go-live and out of scope until the owner says so.
  - Real dependencies: 016, 017 and 018 each need only 015 (`migration_log`). 014 needs only 011.
  - Next new file: `db/019`, the shared tables (MASTER_PLAN §3).
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
- **Tests:** `tests/` **360**, and `tests_live/` 88 run, 0 skipped. Run both from `workers/` with **`..\venv\Scripts\python.exe`**.
  - The earlier "361" was a typo made in `cf18f9d`. That commit took the suite from 356 to 360 (4 tests in `test_nfl_ml.py`), and nothing has changed `tests/` since.
  - Kalshi jobs run one at a time. The archive's SQL path has no live test, because a test row would be permanent in the real archive; its first real run is the test.
- **Quote provenance and clock (2026-10-02):**
  - Every tick stores its worker→DB clock offset on the run row (`db/016`).
  - Every commitment carries `payload.quote_provenance`: `fetched_at_worker`, plus a separately named `fetched_at_db_estimate`. Worker time is never labelled DB time.
  - Local machine: the DB is ~373 ms behind it (RTT ~55 ms).
  - **Railway's offset is unmeasured: no canary run has been written since 2026-09-29 23:35 UTC** (see Deploy below for why).
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
  - The loop continues until Chat 1 Phase 1, Step 3 restores `CANARY` on the new `workers/` build path. `ROSTER` stays unset and `db/013` unpasted.
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
- **Next:** finish Chat 1 Phase 1 (Railway redeploy from `workers/`, canary restored, 24 h usage check against the free plan). Then MASTER_PLAN §6. Still pending from before: owner review of the crypto track, owner go-live, the weekly archive. Any new idea needs a new pre-registration and forward-only validation.
