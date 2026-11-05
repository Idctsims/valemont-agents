# NFL props archive: weeks 6–18 in Supabase Storage

Status: design, committed 2026-11-04 before any code (branch
`props-archive-storage`). Governs `jobs.archive_nfl_props --to-storage`.

## 1. Why

`jobs.archive_nfl_props` copies what F1 and F2 need (preregistration_nfl.md
§8) into Postgres: market rows, settlements, 1-minute candles and trade
prints. Week 5 cost ~34 MB with indexes (candles 18.4 MB, prints 15.6 MB).
At that rate weeks 5–18 are ~470 MB, about the whole 500 MB Free tier, and
the database was already 245.6 MB on 2026-11-04. Weeks 6–18 go to Supabase
Storage (1 GB on Free) instead, compressed, and Postgres stops growing from
this archive.

**The store-only rule is unchanged (§8.1).** The job fetches, serializes,
hashes, uploads and counts. It never reads an object back for content, and it
logs only what the Postgres job already logs (markets, settled, unsettled,
candle rows, print rows, games pending), plus byte sizes and hashes.

## 2. Step 0: what Kalshi still serves (probe, 2026-11-04 ~21:00 UTC)

Read-only, no keys, counts only. For the first and last game of each week,
4 markets per series (12 a game): did `[kickoff − 3 h, kickoff]` return at
least one candle and at least one print?

| Week | Games | Markets | First game | Last game | Verdict |
|---|---|---|---|---|---|
| 6 | 14 | 2,673 | SEA–DEN: 9/12 candles, 9/12 prints | WAS–SF: 11/12, 11/12 | available |
| 7 | 14 | 2,825 | NE–CHI: 11/12, 11/12 | DAL–PHI: 12/12, 12/12 | available |
| 8 | 14 | 3,071 | CAR–GB: 11/12, 11/12 | CHI–SEA: 12/12, 11/12 | available |
| 9 | 15 | 1,514 so far | JAX–BAL kicks off 2026-11-06 01:15 UTC | BUF–MIN 2026-11-10 01:15 UTC | **not played yet** |

Every sampled market with no candle in the window (6 of them) **opened after
kickoff** (in-play rungs, opened 8 min to 2 h after kickoff), and each has
candles after kickoff. There was never a pre-kickoff window to keep, so
this is not loss. All sampled markets are still in Kalshi's live tier (the
historical cutoff is 2026-09-05).

**Week 9 is not missing: it is this week.** It is archived on Tue Nov 10,
after Monday night's BUF–MIN. Week 10 follows on Tue Nov 17.

## 3. Bucket

- Name **`nfl-props-archive`**, **private** (never public). The owner
  creates it in the dashboard. The job never creates or reconfigures it.
- Reached only through the Storage **S3 protocol** with an access key pair
  held in the root `.env` only: not Railway, not Vercel. The archive runs
  from the owner's machine, never on Railway.
- Disclosed: an S3 access key has full access to **every** bucket of the
  project and bypasses Storage RLS. It is a Storage key, not a database key:
  it cannot reach Postgres.

## 4. Objects

One object per week per kind, under a revision folder:

```
2026/week-06/r1/markets.jsonl.gz
2026/week-06/r1/settlements.jsonl.gz
2026/week-06/r1/candles.jsonl.gz
2026/week-06/r1/prints.jsonl.gz
```

**Format: gzip JSONL, stdlib only.** Parquet would mean pyarrow, tens of MB
of dependency for a file read once in January. `gzip` and `json` are already
there. Compression is close enough for this volume: one week is ~175k rows,
about 20 MB raw and an estimated 3–5 MB gzipped. 13 weeks fit well inside the
1 GB tier.

**Deterministic bytes**, so a rerun of the same data hashes the same:

- one JSON object per line, `sort_keys`, compact separators, `\n` endings;
- decimals as strings, exactly as Kalshi sent them (no float); timestamps
  ISO-8601 UTC with `Z`;
- rows sorted: markets and settlements by ticker; candles by (ticker, end);
  prints by (ticker, created_time, trade_id);
- gzip level 9, `mtime=0`, no file name in the header.

**Row shapes** mirror db/011 and db/014, so the January runner reads one shape
from either store:

| Kind | Fields |
|---|---|
| markets | ticker, event_ticker, series_ticker, sport, game_id, kickoff, kickoff_source, floor_strike, title |
| settlements | ticker, result, settlement_value, settlement_ts |
| candles | ticker, period_minutes (1), end_period_ts, yes_bid_close, yes_ask_close, trade_close, volume, open_interest |
| prints | ticker, trade_id, created_time, yes_price, count, taker_side |

Two deliberate differences from Postgres:

- **No `source` column** (live/historical tier) in the rows. The tier depends
  on when the fetch ran relative to Kalshi's moving cutoff, so it would make
  a rerun of identical data hash differently. The manifest line records the
  tier counts instead.
- **Prints without a `trade_id` are kept** (Postgres drops them: its key is
  `trade_id`). The manifest counts them. **The runner drops them**, for parity
  with week 5.

What is fetched is exactly the Postgres job's selection: the same three series
(`KXNFLPASSYDS`, `KXNFLRSHYDS`, `KXNFLRECYDS`), regular-season games of the
week, candles fetched over [kickoff − 24 h, kickoff] and kept by
`keep_candles` (the 3 h window plus the one latest before it), prints in
[kickoff − 3 h, kickoff).

## 5. Manifest: `docs/props_archive/manifest.jsonl`, committed to git

One line per object, appended by the job only after that week's four objects
are uploaded and verified (§7):

```
{"season":2026,"week":6,"revision":1,"kind":"candles",
 "path":"2026/week-06/r1/candles.jsonl.gz","bucket":"nfl-props-archive",
 "rows":118000,"bytes":2400000,"sha256":"…","md5":"…",
 "etag":"…","etag_is_md5":true,"head_bytes":2400000,
 "fetched_at":"2026-11-04T22:10:00Z",
 "window":{"candles_fetch":"kickoff-24h..kickoff","candles_keep":"latest<kickoff-3h + kickoff-3h..kickoff","prints":"kickoff-3h..kickoff"},
 "kickoff_range":["…","…"],
 "week_counts":{"markets":…,"settled":…,"unsettled":…,"candles":…,"prints":…,"prints_without_id":…,"games":14,"games_pending":0},
 "tiers":{"live":…,"historical":…},"job":"jobs.archive_nfl_props --to-storage"}
```

**Git history is the tamper record.** A line, once committed, is never edited.
No migration is needed: nothing about this lives in Postgres.

## 6. Rules the job enforces

1. **A week is uploaded only when every one of its games is past kickoff +
   5 h** (`SETTLE_GRACE`). A week with a game pending is refused whole, not
   uploaded in part: a partial week would be indistinguishable from a
   complete one (the slate rule, CLAUDE.md §3).
2. **An object key is written once.** The S3 layer has no conditional PUT, so
   the job HEADs every key first and never PUTs over an existing one.
3. **Rerun of a revision already in the manifest:** the job rebuilds the four
   objects and compares sha256 with the manifest. All four match, and HEAD
   finds each object at its recorded size: **no-op**, exit 0. Any mismatch:
   **refuses loudly** (exit 2) and uploads nothing.
4. **A new revision** (`--revision N`, N ≥ 2) is allowed only when revision
   N − 1's manifest records `unsettled > 0`: the case of settlements arriving
   late. The new revision is a full copy under `r{N}/`. The old one stays,
   untouched, as history. The runner reads the highest revision of each week.
5. **Object present, no manifest line** (a crash between upload and manifest
   write): the job continues only if HEAD's size equals the local bytes and
   HEAD's ETag equals the local MD5. It then writes the missing line marked
   `"recovered": true`. Anything else refuses.
6. Week range 6–18 only. Week 5 stays in Postgres. Weeks 1–4 are settlement-only
   and already in Postgres.
7. No Postgres write in this mode.

## 7. Verification without egress

Never download an object to check it. Per object:

- the PUT is signed with the body's **sha256 as `x-amz-content-sha256`** (SigV4
  signed payload, not `UNSIGNED-PAYLOAD`). An S3 server that checks it rejects
  a body that differs. Whether Supabase checks it is not documented, so this
  is not claimed as verification;
- **HEAD after PUT**: `Content-Length` must equal the local byte count, or
  the job refuses and writes no manifest line;
- the ETag is recorded. If it equals the local MD5 (S3's single-part rule),
  `etag_is_md5` is true and the check is a content check. If it doesn't,
  `etag_is_md5: false` and the check is **size only**, reported as such.
  Supabase does not document its ETag (Content-MD5 is listed unsupported);
  the first real upload tells us which case we are in.

Each HEAD is a few hundred bytes. The upload is ingress.

## 8. How the January 2027 runner reads both

- **Weeks 1–4 settlements, week 5 everything:** Postgres, as now (db/011,
  db/014 tables), through the ledger's read path.
- **Weeks 6–18:** for each week, the highest revision in the committed
  manifest. Download each of its four objects **once** (estimated 40–65 MB of
  Storage egress for 13 weeks, a one-off). Compute sha256 and refuse to read a
  byte of content if it differs from the manifest. Then parse the JSONL into
  the same records as the Postgres rows. Prints without a `trade_id` are
  dropped.
- **A week missing from both stores is unevaluable**, which is a fail
  (§8.1), not a deferral. The runner says which weeks.
- The runner remains as §8.1 says: committed before it runs, refuses before
  2027-01-20, refuses if output exists, runs once. **And (owner,
  2026-11-04): it runs only once week 18's archive shows 0 unsettled**
  (§10, the week-18 note). It reads each week's highest revision; if that
  revision records unsettled > 0 for week 18, it refuses.

## 9. Keys (`.env` names, documented in `.env.example`)

```
PROPS_ARCHIVE_S3_ENDPOINT        https://<project_ref>.storage.supabase.co/storage/v1/s3
PROPS_ARCHIVE_S3_REGION          the region shown on the S3 configuration page
PROPS_ARCHIVE_S3_ACCESS_KEY_ID
PROPS_ARCHIVE_S3_SECRET_ACCESS_KEY
PROPS_ARCHIVE_BUCKET             nfl-props-archive (default)
```

The job never prints a value. It logs the bucket name and object paths only.

## 10. Weekly operation

From `workers/`, the Tuesday after Monday night's game:

```
..\venv\Scripts\python.exe -m jobs.archive_nfl_props --week N --to-storage
```

Then commit the new `docs/props_archive/manifest.jsonl` lines. Unsettled > 0:
rerun a week later with `--revision 2`.

| Week | Run on |
|---|---|
| 6, 7, 8 | done 2026-11-04 (r1, 0 unsettled each) |
| 9 | Tue 2026-11-10 |
| 10–17 | each following Tuesday, through Tue 2027-01-05 (week 17) |
| 18 | Tue 2027-01-12 (nflverse, read 2026-11-04: every week-18 game at 2027-01-10 18:00 UTC, a placeholder until the league sets times) |

**Week 18 is later than §8.1 assumed. Decided (owner, 2026-11-04): the
frozen text stays untouched; the January runner waits for week 18's archive
to show 0 unsettled, as well as for 2027-01-20 (§8).**
Preregistration §8.1 says week 18 ends 2027-01-03 and sets the runner's
earliest date at 2027-01-20, on the basis that stat props settle within
15 days (§2.7). nflverse shows week 18 on 2027-01-10. On that rule, its
settlements could run to ~2027-01-25, after the runner's earliest date. The
frozen text is not edited here. The week-18 archive run, plus a revision
2 if any market is still unsettled, is what shows whether this matters.
