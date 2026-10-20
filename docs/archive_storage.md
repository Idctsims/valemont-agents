# NFL props archive → Supabase Storage (Step 0: design)

Status: **design only, no code** (owner, 2026-10-20). Nothing here changes
`jobs/archive_nfl_props.py`, the db/011 and db/014 tables, or
`docs/preregistration_nfl.md` §8. Owner decisions are marked **[decide]**.

## 1. Why

`jobs.archive_nfl_props` copies what F1 and F2 need (preregistration §8) into
four append-only Postgres tables. Week 5, the first full price week, measured:

| Table | Rows (job summary) | Size with indexes |
|---|---|---|
| `kalshi_candles` | 110,963 (week 5) | 18.4 MB |
| `kalshi_trades` | 64,306 (week 5) | 15.6 MB |
| `kalshi_markets` | 13,710 (weeks 1–5) | 4.5 MB |
| `kalshi_settlements` | 13,710 (weeks 1–5) | 1.6 MB |

Sizes were read with `db_inspect` on 2026-10-20 (`pg_total_relation_size`). The
database was 146 MB that day.

At ~34 MB of candles and prints a week, weeks 6–18 would add **~440 MB**, which
fills the Free tier's 500 MB on top of the feed. Candles and prints are bulk,
write-once and read once in January, so they suit object storage.

## 2. What moves, what stays

| Data | Where | Why |
|---|---|---|
| Week 5 candles and prints | **Stay in Postgres** | They cannot leave: the tables refuse DELETE (`*_immutable`) and TRUNCATE (`*_no_truncate`), by design (CLAUDE.md §2). |
| Markets and settlements, every week | **Stay in Postgres** | They are small (~0.5 MB a week) and relational. F2's base-rate pool reads every 2026 settlement, weeks 1–4 included, and the files key on `kalshi_markets.id`. |
| Weeks 6–18 candles and prints | **Storage** | The bulk: ~95% of each week's bytes. |
| The file ledger (`archive_objects`, new) | **Postgres**, append-only | Each file's path, SHA-256, byte size, row counts, the week, its part, and a database-stamped `created_at`. This is what makes a Storage file tamper-evident (§5). |

The job keeps its shape: fetch, then `archive_market` and `archive_settlement`
into Postgres as today. Only the two bulk inserts change: they become one file
per kind per run.

## 3. File format and partitioning

**Format: gzip-compressed CSV**, UTF-8, with a header. No new dependency:
`csv` and `gzip` are stdlib. Parquet would be ~2× smaller but needs `pyarrow`
(~100 MB in the image), against CLAUDE.md §6 ("keep dependencies boring").
At these sizes compression is not the constraint.

- **Columns:** exactly the Postgres tables' columns, in the same order, minus
  `id`. `market_id` is the `kalshi_markets.id` the job just wrote, plus a
  `ticker` column, so a file is readable without the database.
  - Candles: `ticker, market_id, period_minutes, end_period_ts,
    yes_bid_{open,high,low,close}, yes_ask_{open,high,low,close},
    trade_{open,high,low,close}, volume, open_interest, source, captured_at`.
  - Prints: `ticker, market_id, trade_id, created_time, yes_price, count,
    taker_side, source, captured_at`.
- **Values:** timestamps in ISO 8601 UTC (`2026-10-18T17:03:00+00:00`);
  numbers as their exact decimal text (no float round-trip); NULL as an empty
  field.
- **Deterministic:** rows sorted by `(ticker, end_period_ts)` for candles and
  `(ticker, created_time, trade_id)` for prints, and gzip `mtime=0`. The same
  input gives the same bytes, so a rerun can tell "same data" from "new data"
  by hash.

**Partitioning: one file per kind per week per run.** A week is ~16 games, and
one file each is ~3–6 MB compressed (§6):

```
bucket: nfl-props-archive          (private)
2026/week-06/part-001/candles.csv.gz
2026/week-06/part-001/trades.csv.gz
2026/week-06/part-001/manifest.json
2026/week-06/part-002/...          (a rerun that found new rows only)
```

- **`manifest.json`:** the week, the part, every file's SHA-256, byte size and
  row count, the fetch window (`kickoff − 3 h` to kickoff, as now), the
  games, the job's git commit, and Kalshi's `historical_cutoff` at fetch
  time.
- **Reruns:** Postgres's `ON CONFLICT DO NOTHING` becomes "a new part holds
  only rows no earlier part has". The job reads its own earlier manifests
  (keys and counts, never prices) to know what it already holds. A rerun with
  nothing new writes nothing.
- **Why not one file per game?** ~16× the objects for no benefit: the
  January runner reads every game anyway, and per-week parts keep reruns
  simple.

## 4. Who can touch it

- **The bucket is private, with no Storage RLS policies.** Supabase denies
  `anon` and `authenticated` by default, so the web app and the browser can
  never read or list it. That also respects "stay in the public schema"
  (CLAUDE.md §5): adding policies to `storage.objects` would be a change in
  Supabase's reserved schema.
- **Only the archive job writes,** and it runs by hand from the owner's
  machine (RECURRING, weekly). The key stays in the root `.env`, never on
  Railway or Vercel.
- **[decide] Which credential:**
  - **(a, recommended) Storage S3 access keys:** Storage-only and revocable on
    their own. Needs `boto3`, kept out of the Railway image in a separate
    `requirements-dev.txt`, because the job runs locally only.
  - (b) The project's secret key over Storage's REST API with `httpx` (already
    a dependency). No new package, but the key bypasses RLS on every table.
    That is far wider than the job needs.
- Every upload sets **`x-upsert: false`**: a path that exists is refused,
  never overwritten.

## 5. Immutability and the TRUNCATE-guard story

Postgres enforces §2 for the archive with triggers: no UPDATE, no DELETE, no
TRUNCATE, no candle or print after kickoff. Storage has no triggers, and the
service credential could delete or replace an object. So the guarantee moves
from "cannot change" to **"cannot change undetected"**, anchored in Postgres:

1. **`archive_objects` (new, db/0xx):** append-only, TRUNCATE-guarded, the
   usual trigger set. One row per file, written **after** the upload
   succeeds, with the SHA-256 computed locally before upload. `created_at` is
   the database's.
   - The row is the commitment: "this file, with this hash, existed at this
     time". A file replaced later no longer matches its hash. A deleted file
     is missing against its row.
2. **No overwrite at the API** (`x-upsert: false`, §4).
3. **The pre-kickoff rule, kept twice:**
   - the job asserts `end_period_ts ≤ kickoff` and `created_time < kickoff`
     for every row before writing a file (db/011's `*_pregame` triggers do it
     in Postgres today);
   - the January runner checks it again on read.
4. **A second copy:** the job also keeps each file under
   `.cache/nfl-props-archive/` (gitignored, under `core/paths.REPO_ROOT`).
   Storage is the primary; the local copy is only for recovery, and it must
   hash to its `archive_objects` row to be used.
5. **What counts as loss:** a file with no `archive_objects` row is not
   evidence. A row whose file is missing in both places, or matches neither
   hash, makes that week's data **unavailable**. §8.1 already says data
   that is gone makes a hypothesis unevaluable, which is a fail.

The `archive_objects` TRUNCATE assertion in its self-test must name every
table that references it. db/042's lesson: no table does today, so none.

## 6. Size against the 1 GB Storage tier

Estimate, to be replaced by week 6's measured bytes (the manifest records
them):

| | Per week | Weeks 6–18 (13) |
|---|---|---|
| Candles, CSV (~110k rows × ~150 B) | ~16 MB raw, ~4–5 MB gz | ~55–65 MB |
| Prints, CSV (~65k rows × ~90 B) | ~6 MB raw, ~1.5–2 MB gz | ~20–26 MB |
| **Total** | **~6–7 MB gz** | **~75–90 MB (~8–9% of 1 GB)** |

- **Margin:** even uncompressed (~22 MB a week, ~290 MB in all) it fits.
- **Per-file limit:** Supabase Free's standard upload limit (50 MB; **to
  confirm** on the current pricing page) is ~8× a week's largest file.
- **Postgres after the move:** markets and settlements grow ~0.5 MB a week
  (~7 MB by week 18), plus `archive_objects` (a few rows a week). The ~440 MB
  of §1 is avoided.

## 7. Egress

- **The write:** an upload is ingress, not egress. The job's reads from
  Kalshi are not Supabase egress. Its Postgres traffic is small: `RETURNING id`
  per market, and the manifests (a few KB) on a rerun. **~0 MB a week**, as
  now.
- **The January read:** the runner downloads every file once, **~75–90 MB**,
  plus week 5 and the markets and settlements from Postgres (~40 MB by then,
  read once). **~120–130 MB, one time.**
- **Quota:** on Free, Storage downloads count toward the same monthly egress
  quota as the database (**to confirm** on the pricing page). That is ~2.5%
  of 5 GB. The runner reads each file once into memory, never in a loop. It
  checks hashes on the downloaded bytes, so no second download is needed.

## 8. How F1 and F2 read it in January

The runner is still written in January to §8, committed, and executed once,
not before 2027-01-20. This design only fixes where its inputs come from:

1. **Refuse first,** as §8.1 requires: before 2027-01-20, or if any F1/F2
   output exists. Nothing is downloaded before these checks pass. The
   download is the look.
2. **Read the ledger:** every `archive_objects` row for 2026 weeks 6–18, and
   `kalshi_markets` and `kalshi_settlements` for weeks 1–18.
3. **Download each file and check it:** its SHA-256 against the row, and its
   row count against the manifest. Any mismatch tries the local copy (§5.4).
   If both fail, that week is recorded as unavailable and the runner
   continues: a missing week does not stop the run, it is reported.
4. **Union the data:** week 5 (Postgres) and weeks 6–18 (files) into one
   in-memory candle table and one print table, keyed by `ticker`. Duplicates
   across parts are an error, because the parts are disjoint by construction.
5. **Evaluate exactly as §8 says.** The latest 1-minute candle at or before
   t = kickoff − 75 min is exact for every week, because the job keeps the
   window `[kickoff − 3 h, kickoff]` plus the latest earlier candle. That is
   unchanged.

Before 2027-01-20, nothing reads a file's contents: not the job (§3 reads only
its own manifests), not `db_inspect` (it cannot reach Storage), and not a test.
The `tests/` coverage for the new job uses synthetic rows only.

## 9. Order of work (after this design is approved)

1. **db/0xx `archive_objects`**, with its self-test, pasted and verified.
2. **The job:**
   - write files instead of the two bulk inserts, with manifest, hash, upload,
     ledger row and local copy;
   - stub tests for determinism, the pregame assertion, no-overwrite, and
     rerun parts;
   - a `tests_live` test of the ledger table only (no real upload in tests).
3. **The credential** (§4 [decide]) in the root `.env`, never printed.
4. **Week 6** archived through the new path and its manifest read back.
   - **[decide]** If the new path is not ready by Tue 2026-10-27 (week 7's
     slot), archive week 6 into Postgres as before. That is +~34 MB, still
     well under 500 MB. Kalshi currently serves its historical tier back to
     January 2025, so a few days' delay loses nothing; how long it keeps the
     data is undocumented (§8.1), which is why the archive exists.
5. **RECURRING and the reminders** updated: week N to Storage every Tuesday.

## 10. Open for the owner

- [decide] The credential: S3 keys with `boto3` as a dev-only dependency
  (recommended), or the secret key over REST.
- [decide] The week 6 fallback date (§9.4).
- To confirm on the Supabase pricing page:
  - the Free plan's Storage per-file upload limit;
  - whether Storage downloads count toward the database's 5 GB egress.
