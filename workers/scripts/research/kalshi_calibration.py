"""Step 1, M2 (Chat 8 Phase 3.5): the leg calibration map's data. WRITTEN, NOT RUN.

Owner, 2026-11-19: M2 waits until M1's candles finish (one Kalshi pacing).
Every network stage refuses without `--live`; without it a stage prints what
it would do and makes no call.

    ..\\venv\\Scripts\\python.exe -m scripts.research.kalshi_calibration sample
    ..\\venv\\Scripts\\python.exe -m scripts.research.kalshi_calibration starts  [--live]
    ..\\venv\\Scripts\\python.exe -m scripts.research.kalshi_calibration candles [--live] [--max-minutes 480]
    ..\\venv\\Scripts\\python.exe -m scripts.research.kalshi_calibration status

What it gathers (docs/betting_science_audit.md §6 M2, frozen): for settled
NBA and NHL singles closed before 2026-07-01 (NFL singles are not in J3), by
family (winner, spread rung, total rung, player prop rung), the pre-game mid:
the last 1-minute candle at or before the scheduled start. About 30,000
markets, one candle call each.

1. `sample` (no network): reads the M2 listing (`kalshi_history list
   --study m2`) line by line THROUGH THE RESERVE GATE (`kalshi_history.admit`:
   a row's `close_time` is found in the raw text and a reserve-dated or
   undated row is dropped before any other field is decoded). Keeps traded
   markets of the full-game series in `FAMILIES`, up to `PER_CELL` per league
   and family by the lowest crc32 of the ticker, and marks each `fit` or
   `test`: per league, the first 70% of game dates (every admitted row's date,
   not only the sample's) are `fit`, the rest `test`. The sample file carries
   no result, settlement or price field: the crawl never sees an outcome.
2. `starts` (network, `--live`): each league's schedule (NBA: stats.nba.com
   `scheduleleaguev2`, one call a season; NHL: api-web.nhle.com
   `/v1/schedule/{date}`, one call a week) to `starts.jsonl`. The response
   shapes are UNVERIFIED until the first live call; an unknown shape fails
   loudly, never quietly empty.
3. `candles` (network, `--live`): one call per sampled market whose game was
   matched to a start (same Eastern date within a day, the ticker's team
   string = away + home), the window [start - 60 min, start], never past the
   wall. Keeps the last candle at or before the start: bid and ask close.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.parse
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from scripts.research.kalshi_fills import game_of
from scripts.research.kalshi_history import DATA_DIR, WALL, Counts, Fetch, Pacer, TooMany429s, admit, http_fetch
from venues.kalshi.client import BASE_URL

WALL_TS = int(dt.datetime.fromisoformat(WALL).replace(tzinfo=dt.timezone.utc).timestamp())
#: Full-game series only, by league and family.
FAMILIES: dict[str, tuple[str, str]] = {
    "KXNBAGAME": ("nba", "winner"), "KXNBASPREAD": ("nba", "spread"), "KXNBATOTAL": ("nba", "total"),
    "KXNBAPTS": ("nba", "prop"), "KXNBAREB": ("nba", "prop"), "KXNBAAST": ("nba", "prop"),
    "KXNBA3PT": ("nba", "prop"), "KXNBABLK": ("nba", "prop"), "KXNBASTL": ("nba", "prop"),
    "KXNHLGAME": ("nhl", "winner"), "KXNHLSPREAD": ("nhl", "spread"), "KXNHLTOTAL": ("nhl", "total"),
    "KXNHLGOAL": ("nhl", "prop"), "KXNHLPTS": ("nhl", "prop"), "KXNHLAST": ("nhl", "prop"),
}
PER_CELL = 3750  # 2 leagues x 4 families x 3,750 = 30,000
FIT_SHARE = 0.70
WINDOW_MIN = 60
SAMPLE_FIELDS = ("ticker", "event", "series", "league", "family", "date", "teams", "split", "floor_strike")


# --------------------------------------------------------------------------- the listing, through the gate
def gated_rows(paths: Iterable[Path], counts: Counts) -> Iterator[tuple[str, dict[str, Any]]]:
    """(series, row) for every listing line that passes `admit`: the reserve
    check runs on the raw line before it is decoded."""
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = admit(line.strip(), counts)
                    if row is not None:
                        yield path.stem, row


def split_dates(dates: Iterable[dt.date], share: float = FIT_SHARE) -> dt.date:
    """The first `test` date: the first `share` of distinct dates are `fit`."""
    d = sorted(set(dates))
    if not d:
        raise ValueError("no dates to split")
    return d[min(len(d) - 1, int(len(d) * share))]


def build_sample(listing_dir: Path, per_cell: int = PER_CELL) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    counts = Counts()
    paths = [listing_dir / f"{s}.jsonl" for s in FAMILIES if (listing_dir / f"{s}.jsonl").exists()]
    cells: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    dates: dict[str, set[dt.date]] = defaultdict(set)
    skipped: Counter = Counter()
    for series, row in gated_rows(paths, counts):
        league, fam = FAMILIES[series]
        g = game_of(row.get("event_ticker") or "")
        if g is None:
            skipped["not a game"] += 1
            continue
        dates[league].add(g[1])
        if float(row.get("volume_fp") or 0) <= 0:
            skipped["untraded"] += 1
            continue
        cells[(league, fam)].setdefault(row["ticker"], {
            "ticker": row["ticker"], "event": row["event_ticker"], "series": series, "league": league, "family": fam,
            "date": g[1].isoformat(), "teams": g[0], "floor_strike": row.get("floor_strike")})
    cut = {lg: split_dates(d) for lg, d in dates.items()}
    sample: list[dict[str, Any]] = []
    info: dict[str, Any] = {"cells": {}}
    for (league, fam), rows in sorted(cells.items()):
        chosen = sorted(rows.values(), key=lambda r: (zlib.crc32(r["ticker"].encode()), r["ticker"]))[:per_cell]
        for r in chosen:
            r["split"] = "fit" if dt.date.fromisoformat(r["date"]) < cut[league] else "test"
            sample.append({k: r[k] for k in SAMPLE_FIELDS})
        info["cells"][f"{league} {fam}"] = {"listed": len(rows), "sampled": len(chosen),
                                           "fit": sum(r["split"] == "fit" for r in chosen)}
    info |= {"first_test_date": {lg: d.isoformat() for lg, d in cut.items()}, "gate": vars(counts) | {"census": None},
             "skipped": dict(skipped)}
    return sample, info


# --------------------------------------------------------------------------- starts
def parse_nba_schedule(body: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    try:
        for day in body["leagueSchedule"]["gameDates"]:
            for g in day["games"]:
                out.append({"league": "nba", "start": g["gameDateTimeUTC"], "away": g["awayTeam"]["teamTricode"],
                            "home": g["homeTeam"]["teamTricode"]})
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unknown NBA schedule shape: {exc!r}") from None
    return out


def parse_nhl_week(body: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    try:
        for day in body["gameWeek"]:
            for g in day["games"]:
                out.append({"league": "nhl", "start": g["startTimeUTC"], "away": g["awayTeam"]["abbrev"],
                            "home": g["homeTeam"]["abbrev"]})
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unknown NHL schedule shape: {exc!r}") from None
    return out


def match_start(league: str, teams: str, date: dt.date, starts: dict[str, list[dict[str, Any]]]) -> int | None:
    """Epoch seconds of the game's start, or None. Kalshi's team string is away
    + home; the Eastern date may differ from the UTC date by one day."""
    for s in starts.get(league, []):
        if s["away"] + s["home"] != teams:
            continue
        ts = dt.datetime.fromisoformat(s["start"].replace("Z", "+00:00"))
        eastern = (ts - dt.timedelta(hours=5)).date()
        if abs((eastern - date).days) <= 1:
            return int(ts.timestamp())
    return None


# --------------------------------------------------------------------------- candles
def candle_query(start_ts: int) -> dict[str, int] | None:
    """The window ending at the start, never past the wall; None if the start is past it."""
    end = min(start_ts, WALL_TS - 1)
    if start_ts >= WALL_TS:
        return None
    return {"start_ts": end - WINDOW_MIN * 60, "end_ts": end, "period_interval": 1}


def last_before(candles: Iterable[dict[str, Any]], start_ts: int) -> dict[str, Any] | None:
    best = None
    for c in candles:
        end = int(c["end_period_ts"])
        if end <= start_ts and (best is None or end > int(best["end_period_ts"])):
            best = c
    if best is None:
        return None
    bid, ask = best.get("yes_bid") or {}, best.get("yes_ask") or {}
    return {"end": int(best["end_period_ts"]), "bid_close": bid.get("close"), "ask_close": ask.get("close")}


def _read(path: Path) -> Iterator[dict[str, Any]]:
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def _append(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def plan(out: Path) -> dict[str, int]:
    starts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in _read(out / "starts.jsonl"):
        starts[s["league"]].append(s)
    done = set((out / "candles.done").read_text(encoding="utf-8").split()) if (out / "candles.done").exists() else set()
    c: Counter = Counter()
    for r in _read(out / "sample.jsonl"):
        c["sampled"] += 1
        ts = match_start(r["league"], r["teams"], dt.date.fromisoformat(r["date"]), starts)
        if ts is None:
            c["no start matched"] += 1
        elif candle_query(ts) is None:
            c["start past the wall"] += 1
        elif r["ticker"] in done:
            c["done"] += 1
        else:
            c["calls to make"] += 1
    return dict(c)


def crawl_candles(out: Path, pacer: Pacer, fetch: Fetch, *, deadline: float | None = None,
                  log: Callable[[str], None] = print) -> Counter:
    starts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in _read(out / "starts.jsonl"):
        starts[s["league"]].append(s)
    done = set((out / "candles.done").read_text(encoding="utf-8").split()) if (out / "candles.done").exists() else set()
    counts: Counter = Counter()
    for r in _read(out / "sample.jsonl"):
        if r["ticker"] in done:
            continue
        if deadline is not None and time.time() >= deadline:
            break
        ts = match_start(r["league"], r["teams"], dt.date.fromisoformat(r["date"]), starts)
        q = None if ts is None else candle_query(ts)
        if q is None:
            counts["no start or past the wall"] += 1
            continue
        try:
            body = json.loads(pacer.get(fetch, f"{BASE_URL}/historical/markets/{urllib.parse.quote(r['ticker'])}/candlesticks?"
                                               f"{urllib.parse.urlencode(q)}"))
            got = last_before(body.get("candlesticks") or [], ts)
            _append(out / "candles.jsonl", [{"ticker": r["ticker"], "start": ts} | (got or {"end": None})])
            counts["no candle in the window"] += got is None
        except TooMany429s:
            raise
        except RuntimeError as exc:
            _append(out / "candles.miss.jsonl", [{"ticker": r["ticker"], "error": str(exc)[-120:]}])
            counts["missed"] += 1
        with (out / "candles.done").open("a", encoding="utf-8", newline="\n") as f:
            f.write(r["ticker"] + "\n")
        counts["asked"] += 1
        if counts["asked"] % 250 == 0:
            log(f"candles: {counts['asked']} asked; pace {pacer.interval:.2f}s, 429s {pacer.r429}/{pacer.calls}")
    return counts


# --------------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("sample", "starts", "candles", "status"))
    ap.add_argument("--live", action="store_true", help="make network calls (otherwise: say what would be done)")
    ap.add_argument("--max-minutes", type=float)
    args = ap.parse_args(argv)
    listing, out = DATA_DIR / "m2", DATA_DIR / "m2" / "calibration"
    out.mkdir(parents=True, exist_ok=True)
    if args.cmd == "sample":
        if (out / "candles.done").exists():
            print("Candles exist for the current sample; delete m2/calibration to draw a new one.")
            return 2
        sample, info = build_sample(listing)
        (out / "sample.jsonl").unlink(missing_ok=True)
        _append(out / "sample.jsonl", sample)
        (out / "sample.info.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
        print(json.dumps(info, indent=1))
        return 0
    if args.cmd == "status" or not args.live:
        print(json.dumps(plan(out), indent=1))
        if args.cmd != "status":
            print(f"{args.cmd}: dry run, no call made. M2 waits for M1's candles (owner, 2026-11-19); --live runs it.")
        return 0
    pacer = Pacer()
    if args.cmd == "starts":
        print("starts --live: the NBA and NHL schedule calls are not wired to a fetch yet; "
              "add them after the owner approves the start-time source.")
        return 2
    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    try:
        counts = crawl_candles(out, pacer, http_fetch, deadline=deadline)
    except TooMany429s as exc:
        print(f"STOPPED: {exc}. Progress is saved. Report this before running again.")
        return 3
    print("candles this run: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"this run: {pacer.calls} calls, {pacer.r429} answered 429")
    return 0


if __name__ == "__main__":
    sys.exit(main())
