"""Phase 4.2 dry run (owner's machine): one slot's parlay candidates, the re-quote, and P1 rules 1-4.

    ..\\venv\\Scripts\\python.exe -m scripts.parlay_dryrun --env-file <repo>\\.env [--max-calls N] [--max-seconds S]

Writes nothing to the database: the candidates are read through
`valemont_readonly` (the same guard as scripts/db_inspect.py), every
transaction rolled back. Kalshi is read keyless with the board refresh's
adaptive pacer. The report (per sport, legs before rule 1 and after each rule;
Kalshi calls, 429 share, seconds, bytes; the 300 s / 300 call check) is
printed and saved under research_data/parlays/ (gitignored).

Do not run while another Kalshi crawler runs from this machine (owner, Nov 19:
M1's candles first); --force-alongside overrides the check.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
from dotenv import dotenv_values

from consensus import parlay_legs as pl
from core import ledger
from core import memory
from scripts.research.kalshi_history import DATA_DIR

SLOT_CALLS, SLOT_SECONDS = 300, 300


def readonly(env_file: Path) -> str:
    url = dotenv_values(env_file).get("DATABASE_URL_READONLY") if env_file.is_file() else None
    if not url:
        sys.exit(f"DATABASE_URL_READONLY is not set in {env_file}.")
    user = unquote(urlsplit(url).username or "")
    if user != "valemont_readonly" and not user.startswith("valemont_readonly."):
        sys.exit("DATABASE_URL_READONLY is not the valemont_readonly role. Refusing.")
    return url


def crawler_running() -> bool:
    """A Kalshi research crawler alive on this machine: scripts.research.kalshi_*
    or scripts.research.m1b (Nov 20: the M1b crawler was not caught by the first
    pattern). A `status` call is not a crawler."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                          "Select-Object -ExpandProperty CommandLine"], capture_output=True, text=True).stdout
    return any(("scripts.research.kalshi_" in line or "scripts.research.m1b" in line) and "status" not in line
               for line in out.splitlines())


#: Saturday Nov 21 10:33 Chicago, the biggest slot so far (the worker's log): its
#: candidate markets and its candidate read. The owner's bar (Nov 24): at 1.5x
#: this board, the re-quote ends with at least 4 minutes before the :45 commit.
SATURDAY_MARKETS, SATURDAY_READ_S = 33_597, 20.6
START_MIN, COMMIT_MIN, SPARE_MIN = 33, 45, 4


def projection(rq: pl.Requote, markets: int, read_s: float) -> dict:
    """1.5x Saturday from this run, linear in candidate markets: calls, the
    re-quote's seconds and the candidate read all scale by the same factor.
    Conservative for the series reads (their over-read is smaller on a board whose
    games are mostly in the window), and it assumes this run's answer times."""
    if not markets:
        return {}
    k = 1.5 * SATURDAY_MARKETS / markets
    secs = rq.seconds * k + SATURDAY_READ_S * 1.5
    spare = (COMMIT_MIN - START_MIN) * 60 - secs
    return {"scale": round(k, 2), "calls": round(rq.calls * k), "seconds": round(secs), "ends_at_min": round(
        START_MIN + secs / 60, 1), "spare_min": round(spare / 60, 1), "meets_4_min": spare >= SPARE_MIN * 60,
        "if_start_28": {"spare_min": round(spare / 60 + 5, 1)}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env-file", type=Path, required=True)
    ap.add_argument("--max-calls", type=int)
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--force-alongside", action="store_true")
    ap.add_argument("--floor", type=float, default=pl.REQUOTE_FLOOR, help="the re-quote's pace floor, seconds")
    args = ap.parse_args(argv)
    if not args.force_alongside and crawler_running():
        print("A Kalshi research crawler is running on this machine; the dry run waits for it (owner, Nov 19).")
        return 2
    t0 = time.monotonic()
    with psycopg.connect(readonly(args.env_file)) as conn:
        try:
            conn.execute("SET statement_timeout = 60000")
            rows, cut = ledger.parlay_candidates(conn=conn)
        finally:
            conn.rollback()
    read_s = time.monotonic() - t0
    markets = pl.markets_from_rows(rows)
    events = pl.requote_order(markets)
    plan = pl.requote_plan(events)
    start_mb = memory.rss_mb()
    with memory.PeakSampler(interval_s=0.5) as rss:
        rq = pl.requote(events, max_calls=args.max_calls, max_seconds=args.max_seconds, floor=args.floor)
        fresh = pl.with_fresh_p(markets, rq.quotes)  # the slot keeps these too: part of the peak
    now = dt.datetime.now(dt.timezone.utc)
    f = pl.funnel(pl.legs_of(markets, rq.quotes), now, cut=cut)
    passed = {leg.market.market_ticker for leg in f.passed}
    oldest = pl.oldest_age(rq.quotes, now)
    oldest_passed = pl.oldest_age({t: q for t, q in rq.quotes.items() if t in passed}, now)
    first_start = min((m.starts_at for m in markets), default=None)
    table = f.table()
    totals = {k: sum(t[k] for t in table.values()) for k in ("before", *pl.RULES)}
    report = {
        "run_at": now.isoformat(timespec="seconds"), "git": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True, text=True).stdout.strip(),
        "candidate_markets": len(markets), "cut_markets_not_eligible": sum(cut.values()), "events": len(events),
        "read_seconds": round(read_s, 1),
        "requote": {"events_asked": rq.events_asked, "events_skipped": rq.events_skipped, "cap": rq.cap,
                    "calls": rq.calls, "limited_429": rq.limited, "share_429": round(rq.share_429, 4),
                    "seconds": round(rq.seconds, 1), "bytes": rq.bytes, "pace_end": round(rq.pace, 2),
                    "markets_quoted": len(rq.quotes), "floor": args.floor, "order": "latest start first",
                    "oldest_quote_age_s_at_end": None if oldest is None else round(oldest.total_seconds(), 1),
                    "oldest_passing_leg_age_s_at_end": None if oldest_passed is None else round(oldest_passed.total_seconds(), 1),
                    "earliest_start": first_start,
                    "slot_check": {"calls_under_300": rq.calls < SLOT_CALLS, "seconds_under_300": rq.seconds < SLOT_SECONDS},
                    # Owner, Nov 24: per series at page 1,000, per event for <= 2 window events.
                    "plan": {"series_reads": sum(1 for k, _ in plan if k == "series"),
                             "event_reads": sum(1 for k, _ in plan if k == "event")},
                    "series_reads": rq.series_reads, "event_reads": rq.event_reads,
                    "markets_read": rq.markets_read,
                    "over_read": round(rq.markets_read / len(rq.quotes), 2) if rq.quotes else None,
                    "answer_ms": rq.answer_ms()},
        "rss_mb": {"start": start_mb, "peak": rss.peak_mb, "end": rss.end_mb,
                   "peak_over_start": None if start_mb is None or rss.peak_mb is None else round(rss.peak_mb - start_mb, 1),
                   "note": "this machine's working set (Windows) or RSS (Linux); the worker is Linux",
                   "fresh_markets_kept": len(fresh)},
        "projection_1_5x_saturday": projection(rq, len(markets), read_s),
        "funnel_legs_by_sport": table, "funnel_legs_total": totals,
    }
    out = DATA_DIR.parent / "parlays"  # research_data/ (VALEMONT_RESEARCH_DIR for a worktree)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"dryrun_{now:%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps(report, indent=1, default=str))
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
