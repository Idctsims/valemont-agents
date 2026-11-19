# M5 Step 0: where the board snapshots live (for the owner's pick)

Written 2026-11-19. Nothing is built. Plan: `docs/betting_science_audit.md` §6 M5
(frozen): a lean snapshot of eligible, not-started legs at 10:30 and 16:30
Chicago for 14 days (28 snapshots), each also logging eligibility by hours to
start (D13). The board is pruned, so **a missed snapshot is lost for good**.
The decision rule needs at least 5 distinct tickets on **12 of the 14 days**.

## Measured today (db_inspect, 18:35 UTC Thu Nov 19)

- One snapshot: **12,082 legs** (binary, combo-eligible event, not started) in
  769 events; **1.35 MB** of narrow column data (ticker, event, kind, stat,
  line, bid, ask, fee, team, volume, start, combo cap, YES-only, tier,
  p_books). The plan's "about 1.5 MB" holds. The whole row as JSON is 5.4 MB.
  A weekend slate will be larger; unmeasured.
- Database: **306,629,779 bytes** (~292 MiB) against 500 MB; growth since
  Nov 9 ~1.6 MB a day. The parked retention work triggers at ~350 MB.
- 10:30 and 16:30 sit 15 minutes after the consensus runs (10:15, 16:15) and an
  hour after the full pulls (09:30, 15:30), so each snapshot carries fresh
  consensus. The refresh covers only events within 3 h of start (N2), so most
  asks in a snapshot are hours old; the snapshot records each price's age.

## Options

| | A. Script on your PC | B. Worker job, copy inside Postgres | C. Worker job to Storage | D. GitHub Actions cron |
|---|---|---|---|---|
| Runs with PC off | **No** | **Yes** | Yes | Yes (best-effort; cron can start minutes late) |
| What it does | Task Scheduler runs a readonly script; gzip JSONL to `research_data/` | `INSERT INTO research_board_snapshots SELECT ... FROM kalshi_board ...` on the worker's schedule | Worker reads the board, uploads a file | Runner reads via the readonly URL, keeps an artifact |
| Needs | Nothing new | **db/084** (one lean append-only table, test rows, self-test) + a worker job + a merge | S3 keys on Railway | `DATABASE_URL_READONLY` as a GitHub secret |
| Storage | Local disk, ~0.4 MB per snapshot gzipped (est.) | Postgres: est. **25–35 MB** for 28 with a lean shape (ticker suffix, prices as integers), 55–70 MB as wide rows; dropped after export | Storage bucket | Artifacts (90-day retention) |
| Egress during the 14 days | ~1.4 MB per snapshot, **~40 MB** | **~0** (the copy never leaves the database) | ~40 MB | ~40 MB |
| Egress afterwards | 0 | One export to your PC, ~40 MB, then the table is dropped (another migration) | 0 | Download only |
| Risk | Sleep, reboot, Wi-Fi, the Nov 18 `getaddrinfo` failures: each loses a snapshot for good | Pushes the database toward the 350 MB trigger for 2–3 weeks | **Ruled out:** Supabase S3 keys are project-wide (Supabase's S3 docs; not re-read today) (they would open `nfl-props-archive`), and that bucket's keys never go on Railway | A database credential in a third place; Actions schedules are not guaranteed |

Not considered: `pg_cron` (CLAUDE.md §5: no exotic extensions, not superuser).

## Recommendation: B

It is the only option that runs whether or not your PC is on and puts nothing
new on Railway except a job. The copy never leaves Postgres, so collection
costs no egress. The 12-of-14-days rule makes reliability the point: two
missed days on your PC would sink the measurement, and this machine failed
DNS repeatedly on Nov 18. Its cost is a table of about 30 MB for 2–3 weeks,
then an export and a drop. It needs your approval for db/084, a worker job
and a merge; it is not started.

If you would rather change nothing on the worker or schema: **A**, with Task
Scheduler set to wake the PC and to run a late start (a late snapshot is
labelled with its real time; a lost one stays lost).

Pending your pick. Nothing is written or scheduled.

## Decision and build (owner, 2026-11-19: B)

Built on `chat8-step1-measure`, not merged: `db/084_board_snapshots.sql`
(not pasted), the worker job `board_snapshot` (`consensus/board_snapshot.py`,
10:30 and 16:30 Chicago, never at boot, misfire grace 30 min), the ledger call
`research_board_snapshot`. The 14-day window starts the day it deploys and
ends by itself after 28 real snapshots. No backfill: earlier boards are gone,
and July 1 onward is the reserve.

Measured on a full board copy (production's Nov 19 board, 12:39 Chicago,
local PG 17, JIT off): one snapshot = 769 events, 12,082 legs, 44 eligibility
rows; **1.14 MB on disk per snapshot** (2 snapshots 2,285,568 bytes after
compaction; legs ~80 B a row), so **~32 MB (30.5 MiB) for 28** at this board's
size. A weekend board is larger: unmeasured. One take 2.03–2.07 s on the copy
(the consensus reader over the 288 eligible games is ~1.9 s of it); production
has run these readers ~2.7x slower, so ~5–6 s, read-only, twice a day.

Trimmed to fit: snapshot ids are integers; the Kalshi price age is per event
(constant within every event, Nov 19); a prop's player is the tail before its
last `-` (1:1 with the board's subject on all 2,093 player-events), so the
name is not stored. Before the trims: 1.42 MB a snapshot (~40 MB for 28).
