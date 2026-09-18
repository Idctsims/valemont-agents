# Valemont Agents — Architecture Contract

Read this at the start of every session. It is the reason the codebase is shaped
the way it is. If a request in a session conflicts with something here, say so
before writing code.

---

## 1. What this is

A multi-agent paper-trading and forecast-tracking system with a live dashboard.

Three worker agents, one supervisor:

| Agent | Domain | What it commits to | Money |
|---|---|---|---|
| `crypto` | Crypto markets | Simulated positions | None. Paper only. |
| `equities` | US stocks | Simulated positions | None. Paper only. |
| `prizepicks` | Player props | Proposed slips, handed to the operator | None. Never places bets. |
| `chief_of_staff` | Supervision | Nothing. Writes briefs. | None. |

**No component of this system moves real money, ever.** There is no broker
credential with trade permission, no exchange key with withdrawal permission,
and no bookmaker automation. The `prizepicks` agent produces slips for a human
to enter manually and then tracks whether they would have hit. If a session
proposes adding live execution, stop and flag it.

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

## 3. Structure: one core loop, three thin adapters

```
core/          The shared machinery. Agents do not own logic.
  ledger.py       All database writes. Nothing else touches SQL.
  agent.py        BaseAgent — the observe/thesis/commit/resolve loop.
  orchestrator.py Scheduler, agent registry, event emission.
adapters/      Domain specifics ONLY. Thin.
  crypto.py
  equities.py
  prizepicks.py
db/            Numbered SQL migrations. Committed to git.
dashboard/     Next.js. Reads from the API, never from the DB directly.
scripts/       One-off utilities, health checks.
reference/     Cloned third-party repos for reading. GITIGNORED.
```

**The smell test:** if an adapter starts growing its own version of something
that exists in `core/`, the abstraction is wrong. Fix `core/`, don't duplicate.

An adapter is responsible for exactly four things:
1. `observe()` — fetch domain data
2. `form_thesis(observation)` — reason about it
3. `build_commitment(thesis)` — produce a domain payload
4. `resolve(commitment)` — determine what actually happened

Everything else — scheduling, persistence, scoring, event emission, error
handling, retries — belongs to `core/` and is written once.

Adding a fourth agent later should be one new file in `adapters/` and one row
in the agent registry. If it takes more than that, `core/` isn't doing its job.

---

## 4. The chief of staff

Runs once or twice a day. Reads the ledger, writes a synthesis brief:
what each agent did, where a thesis was wrong and why, current running score.

**It does not supervise.** It cannot cancel, override, or instruct another
agent. It has read access to the ledger and write access only to `briefs`.
It is a reporter, not a manager. Do not give it decision authority — at three
agents that adds failure modes without adding capability.

---

## 5. Database rules

Supabase Postgres, on a separate account not linked to Cursor. There is no
Supabase MCP and no CLI access — **the agent writes numbered `.sql` files into
`db/` and I paste them into the Supabase SQL Editor myself.** Never attempt to
apply a migration programmatically.

- Connection **only** from `DATABASE_URL` in `.env`. Never a hardcoded host.
- Use the **Session pooler** connection string (port 5432), not the direct
  `db.<ref>` host, which is IPv6-only.
- RLS is enabled on every table with **no policies** — that denies anon and
  authenticated by default. The worker connects via the Postgres string and
  bypasses RLS. Do not add policies to let the frontend read Supabase directly.
- Schema changes **only** via numbered files in `db/`. Never via a GUI.
- `timestamptz` everywhere, store UTC. Three agents span crypto (24/7),
  US market hours, and game slates. Naive timestamps will burn us.
- Stay in the `public` schema. Supabase reserves `auth`, `storage`,
  `realtime`, `extensions`.
- Assume **not superuser**. No `COPY FROM` a server path, no exotic extensions.
- Migrations are paste-by-hand, so each file must run top-to-bottom in one go
  with no `BEGIN`/`COMMIT` (the SQL Editor supplies its own transaction) and
  no `psql` meta-commands like `\i` or `\copy`.
- The dashboard talks to the Python API, not to the database. Do not couple
  the frontend to Supabase's client library.

---

## 6. Conventions

- Python 3.12, `psycopg` v3, `apscheduler`. Keep dependencies boring.
- All DB access goes through `core/ledger.py`. If SQL appears in an adapter,
  that's a bug.
- Every agent action emits an event row. The dashboard is a consumer of that
  stream — build the stream first, the visuals last.
- Secrets in `.env`, which is gitignored. `.env.example` documents the keys.
- Fail loudly. A silent exception in a worker that runs at 3am is the single
  most likely way this project quietly dies.

---

## 7. Build order

Do not skip ahead. Each step is cheap to change; the ones before it are not.

1. Ledger schema + connection proven ← **you are here**
2. `core/` loop with a fake agent writing real rows
3. `crypto` adapter, live data, paper positions
4. Deploy to Railway — prove the 24/7 path with ONE agent running
5. `equities` adapter
6. `prizepicks` adapter
7. `chief_of_staff`
8. Dashboard + pixel visualization layer

Reason for deploying at step 4 and not at the end: "works locally, dies
silently at 3am in production" is the classic failure here. Hit it while
there's one agent to debug, not four.

---

## 8. Known open problems

- **PrizePicks has no public API.** The board comes from an internal endpoint
  or scraping, and both break. v1 may need a projection source plus manual
  board entry. Don't build deep on a fragile fetch.
- **Scoring methodology is unsolved.** Hit rate alone is a bad metric. Needs
  a thought-through approach (calibration, EV vs. line, not just W/L).
  Flag it when we get there rather than guessing.
