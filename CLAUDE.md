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

It reports **per-agent, with no blended headline number** — see §9.1. That is
a constraint on the brief, not a stylistic preference.

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
- **Void rate is a health metric, not a log line.** It is the earliest signal
  that an adapter's data source has broken, and it moves *before* hit rate
  does: a dead settlement feed stops producing resolutions long before it
  produces wrong ones, and hit rate can't move on commitments that never
  resolve. A quiet adapter looks identical to a careful one on every other
  chart. The dashboard (step 8) surfaces per-agent void rate over time as a
  first-class number, not buried in the event stream — and `resolution_attempts`
  is the table that feeds it. Treat a rising line as an outage, not as data.
- **Scoring methodology is unsolved.** Hit rate alone is a bad metric. Needs
  a thought-through approach (calibration, EV vs. line, not just W/L).
  Flag it when we get there rather than guessing. What *is* settled is the
  unit the raw number is stored in — see §9. Solving the metric is still open;
  the unit is not up for renegotiation.

---

## 9. The pnl contract

Adapters decide the value of `resolutions.pnl`. That is correct — only the
adapter knows that a prop pays a multiplier and a contract settles at par.
But without a shared unit, a chief-of-staff brief that averages crypto dollars
against a prop multiplier is averaging nonsense, confidently.

**`resolutions.pnl` is return on risk: a dimensionless decimal.**

```
pnl = (proceeds - capital_at_risk) / capital_at_risk
```

where `capital_at_risk` is what would be lost in full in the worst modeled
case, measured at commit time. Never dollars. Never a payout multiplier.
Never a percentage — `0.05`, not `5`.

Read points:

- `-1.0` means everything at risk was lost. See the floor rule below — it is a
  real floor, but only because `capital_at_risk` is defined to make it one.
- `+0.05` is a 5% gain on capital at risk, whatever the domain.
- `0` is a push, or a void that returned the stake. Write `0`, not `NULL`.
- `NULL` means **not scored**: the return is not meaningful or never became
  computable. An abandoned commitment (§9.1) is `NULL`. Never a break-even.

### Defining `capital_at_risk`

It is the loss under the **worst case the instrument allows**, measured at
commit time — not a margin requirement, not a broker's number.

| Domain | worst case | capital_at_risk | proceeds | reduces to |
|---|---|---|---|---|
| paper long | price → 0 | `entry × size` | `exit × size` | `(exit − entry) / entry` |
| **paper short** | **the declared invalidation** | **`\|entry − stop\| × size`** | **`(entry − exit) × size + risk`** | **`(entry − exit) / \|entry − stop\|`** |
| prop slip | slip misses | `stake` | `stake × multiplier` if it hits, else `0` | `multiplier − 1`, or `−1` |
| event contract YES | settles 0 | `price × contracts` | `settlement × contracts` | `(settlement − price) / price` |
| event contract NO | settles 1 | `(1 − price) × contracts` | `(1 − settlement) × contracts` | `(price − settlement) / (1 − price)` |

That a YES contract and a long position reduce to the same expression is not a
coincidence — it is the same trade with a settlement price that only ever
lands on 0 or 1. If a fifth domain doesn't reduce this cleanly, say so before
writing the adapter.

### The floor rule, and the one instrument that breaks it

`-1.0` is a hard floor for a long, a prop and an event contract because each
has a worst case the instrument itself supplies: price zero, slip missed,
settlement against you. **A short has no such worst case.** Loss is unbounded,
so `entry × size` is not what is at risk and using it would put `-2.0` on the
board — a documented invariant violated by one domain, which is worse than no
invariant.

So: **a paper short must declare an invalidation level at commit time**, in
the leg's payload, and its `capital_at_risk` is the distance to that level.
`-1.0` then means "the stop was hit, the thesis was fully wrong" — which is
the same sentence as "the stake is gone", so the floor holds across all four.

This is not a special case bolted on. Every other shape's worst case is
implied by the instrument; a short's has to be stated. Requiring the agent to
name what would prove it wrong, before the outcome is known, is the same
principle as §2 applied to risk rather than to direction.

**A short with no declared invalidation has undefined risk. Report `pnl=NULL`
rather than a number that is not comparable to anything.** Do not substitute
notional and hope. Do not clamp a loss to `-1.0` — clamping would turn a
modeling gap into a fake data point, which is the one thing this project
exists not to do.

**The floor is an assumption, not a property.** `|entry − stop| × size` models
the short as filling *at* the declared invalidation level. A real gap through
the stop — an overnight halt, a weekend move, a squeeze — fills worse than
that, and the true loss exceeds `-1.0` again. We are choosing to record the
modeled fill, not the gapped one. Write that down in `resolutions.detail`
(`{"fill": "assumed_at_stop"}`) so a resolution that relied on the assumption
is distinguishable later from one that didn't need it. This is the honest
statement of the limit: for shorts, `-1.0` is a floor on what we *model*, and
the first time a real gap violates it, that is the assumption surfacing, not
a new bug.

### 9.1 Aggregation: not yet, and not across agents

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

### 9.2 Abandoned commitments

A commitment whose outcome never becomes knowable is closed by core, not by
the adapter: `outcome='void'`, `pnl=NULL`, with the reason in `detail`. See
§9.1 on `NULL` — it is not a zero, and it must not be counted as one.
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
