"""Step 1, M1 (Chat 8 Phase 3.5): what a combo filled at, and its legs' quotes in that minute.

Runs on the owner's machine only. Public, keyless Kalshi endpoints. Nothing is
scheduled, nothing touches Supabase, everything is written under
`research_data/kalshi_history/m1/fills/` (gitignored).

    ..\\venv\\Scripts\\python.exe -m scripts.research.kalshi_fills sample
    ..\\venv\\Scripts\\python.exe -m scripts.research.kalshi_fills trades  --max-minutes 240
    ..\\venv\\Scripts\\python.exe -m scripts.research.kalshi_fills candles --max-minutes 480
    ..\\venv\\Scripts\\python.exe -m scripts.research.kalshi_fills status

Three stages, each resumable (stop with Ctrl+C, run the same command again):

1. `sample`: from the M1 listing (`kalshi_history list --study m1`), up to
   `--per-cell` combos in each cell (same-game or cross-game, by 2-5, 6-10,
   11+ legs), chosen by a hash of the ticker so a rerun picks the same ones.
   A combo whose legs are not all games (a tournament or an award) is left
   out and counted.
2. `trades`: `/historical/trades?ticker=` for every sampled combo.
3. `candles`: for every leg of every traded combo, the 1-minute candles
   around each fill (`/historical/markets/{leg}/candlesticks`), one call per
   leg per window of fills.

THE RESERVE WALL. Every ticker here comes from the listing, which already
dropped anything that closed on or after 2026-07-01. A leg of such a combo
closed before it did. As a second guard, a trade stamped on or after the wall
is dropped and counted, and no candle window reaches past it.

2025 NFL rows are fitting-only (owner, J3): nothing here is cited as
validation of anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from scripts.research.kalshi_history import DATA_DIR, WALL, Fetch, Pacer, TooMany429s, http_fetch
from venues.kalshi.client import BASE_URL

WALL_TS = int(dt.datetime.fromisoformat(WALL).replace(tzinfo=dt.timezone.utc).timestamp())
BANDS = ("2-5", "6-10", "11+")
KINDS = ("same-game", "cross-game")
PER_CELL = 3500
#: Candles kept around a fill: the five minutes before its minute, and the minute after.
BEFORE_MIN, AFTER_MIN = 5, 1
#: One candle call spans at most 5,000 one-minute periods (Kalshi's limit).
MAX_SPAN_MIN = 5000
#: Two fills of one leg closer than this share a call.
JOIN_GAP_MIN = 60

TRADE_FIELDS = ("trade_id", "ticker", "count_fp", "yes_price_dollars", "no_price_dollars", "taker_outcome_side",
                "taker_book_side", "created_time", "is_block_trade")
_GAME = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})(?:\d{4})?([A-Z0-9]+)$")
_MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


# --------------------------------------------------------------------------- cells
def game_of(event_ticker: str) -> tuple[str, dt.date] | None:
    """(teams, date) for KXNBAPTS-26JAN15LALBOS; None when the event is not a game."""
    parts = event_ticker.split("-")
    m = _GAME.match(parts[1]) if len(parts) > 1 else None
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return m.group(4), dt.date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)))
    except ValueError:
        return None


def n_games(legs: Iterable[dict[str, Any]]) -> int | None:
    """Distinct games among the legs; None if any leg is not a game. One game's
    series can carry dates a day apart (KXNFLGAME-26JAN18SFSEA beside
    KXNFLPASSYDS-26JAN17SFSEA), so the same teams within one day are one game."""
    keys = [game_of(leg.get("event_ticker") or "") for leg in legs]
    if not keys or any(k is None for k in keys):
        return None
    games: list[tuple[str, dt.date]] = []
    for teams, day in sorted(set(keys)):  # type: ignore[type-var]
        if not any(t == teams and abs((day - d).days) <= 1 for t, d in games):
            games.append((teams, day))
    return len(games)


def band(n_legs: int) -> str:
    return "2-5" if n_legs <= 5 else "6-10" if n_legs <= 10 else "11+"


def cell_of(row: dict[str, Any]) -> tuple[str, str] | None:
    legs = row.get("mve_selected_legs") or []
    g = n_games(legs) if len(legs) >= 2 else None
    return None if g is None else ("same-game" if g == 1 else "cross-game", band(len(legs)))


def build_sample(listing_dir: Path, per_cell: int = PER_CELL) -> tuple[list[dict[str, Any]], Counter]:
    """Up to `per_cell` combos per cell, lowest ticker hash first (a rerun, or
    a longer listing, keeps every combo already chosen ahead of new ones only
    if they hash lower: the choice depends on the tickers, never on order)."""
    cells: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    counts: Counter = Counter()
    for path in sorted(listing_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                cell = cell_of(row)
                if cell is None:
                    counts["not a game combo"] += row["ticker"] not in counts.setdefault("_seen", set())  # type: ignore[arg-type]
                    counts["_seen"].add(row["ticker"])  # type: ignore[union-attr]
                    continue
                cells[cell].setdefault(row["ticker"], {
                    "ticker": row["ticker"], "series": path.stem, "kind": cell[0], "legs_band": cell[1],
                    "legs": [{"event": leg.get("event_ticker"), "market": leg.get("market_ticker"), "side": leg.get("side")}
                             for leg in row["mve_selected_legs"]],
                    "open_time": row.get("open_time"), "close_time": row["close_time"]})
    counts.pop("_seen", None)
    sample: list[dict[str, Any]] = []
    for kind in KINDS:
        for b in BANDS:
            rows = sorted(cells[(kind, b)].values(), key=lambda r: (zlib.crc32(r["ticker"].encode()), r["ticker"]))
            counts[f"{kind} {b} listed"] = len(rows)
            counts[f"{kind} {b} sampled"] = min(len(rows), per_cell)
            sample.extend(rows[:per_cell])
    return sample, counts


# --------------------------------------------------------------------------- files
def _read(path: Path) -> Iterator[dict[str, Any]]:
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def _done(path: Path) -> set[str]:
    return set(path.read_text(encoding="utf-8").split()) if path.exists() else set()


def _append(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _mark(path: Path, key: str) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(key + "\n")


def ts_of(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


# --------------------------------------------------------------------------- trades
def crawl_trades(out: Path, pacer: Pacer, fetch: Fetch, *, deadline: float | None = None,
                 log: Callable[[str], None] = print) -> Counter:
    """Every trade of every sampled combo. A combo's rows are appended, then it
    is marked done: a stop between the two re-reads that one combo on resume
    (trade_id de-duplicates in the analysis)."""
    done = _done(out / "trades.done")
    counts: Counter = Counter()
    todo = [r["ticker"] for r in _read(out / "sample.jsonl") if r["ticker"] not in done]
    for i, ticker in enumerate(todo):
        if deadline is not None and time.time() >= deadline:
            break
        cursor, rows = "", []
        try:
            while True:
                query = {"ticker": ticker, "limit": 1000} | ({"cursor": cursor} if cursor else {})
                body = json.loads(pacer.get(fetch, f"{BASE_URL}/historical/trades?{urllib.parse.urlencode(query)}"))
                for t in body.get("trades") or []:
                    if not t.get("created_time") or t["created_time"][:10] >= WALL:
                        counts["past the wall, dropped"] += 1
                        continue
                    rows.append({k: t.get(k) for k in TRADE_FIELDS})
                cursor = body.get("cursor") or ""
                if not cursor or not body.get("trades"):
                    break
        except TooMany429s:
            raise
        except RuntimeError as exc:  # a 4xx for this ticker: recorded, not retried
            _append(out / "trades.miss.jsonl", [{"ticker": ticker, "error": str(exc)[-120:]}])
            counts["missed"] += 1
        _append(out / "trades.jsonl", rows)
        _mark(out / "trades.done", ticker)
        counts["combos"] += 1
        counts["trades"] += len(rows)
        counts["combos with no trade"] += not rows
        if (i + 1) % 250 == 0:
            log(f"trades: {i + 1} of {len(todo)} combos this run, {counts['trades']} trades; pace {pacer.interval:.2f}s, "
                f"429s {pacer.r429}/{pacer.calls}")
    counts["left"] = len(todo) - counts["combos"]
    return counts


# --------------------------------------------------------------------------- candles
def windows(minutes: Iterable[int]) -> list[tuple[int, int]]:
    """Inclusive (first, last) minute windows covering every fill minute with
    its lead-in and the minute after; fills closer than JOIN_GAP_MIN share a
    window, and no window spans more than Kalshi's 5,000 periods."""
    out: list[tuple[int, int]] = []
    for m in sorted(set(minutes)):
        lo, hi = m - BEFORE_MIN, m + AFTER_MIN
        if out and lo - out[-1][1] <= JOIN_GAP_MIN and hi - out[-1][0] < MAX_SPAN_MIN:
            out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return out


def leg_minutes(out: Path) -> dict[str, set[int]]:
    """{leg market: the minutes in which one of its combos filled}."""
    legs = {r["ticker"]: [leg["market"] for leg in r["legs"] if leg.get("market")] for r in _read(out / "sample.jsonl")}
    need: dict[str, set[int]] = defaultdict(set)
    seen: set[str] = set()
    for t in _read(out / "trades.jsonl"):
        if t["trade_id"] in seen:
            continue
        seen.add(t["trade_id"])
        minute = ts_of(t["created_time"]) // 60
        for market in legs.get(t["ticker"], ()):
            need[market].add(minute)
    return need


def crawl_candles(out: Path, pacer: Pacer, fetch: Fetch, *, deadline: float | None = None,
                  log: Callable[[str], None] = print) -> Counter:
    """Each leg's 1-minute quotes around each fill. Only the minutes near a fill
    are kept (a window's quiet middle is dropped)."""
    need = leg_minutes(out)
    done = _done(out / "candles.done")
    counts: Counter = Counter()
    todo = sorted(m for m in need if m not in done)
    for i, market in enumerate(todo):
        if deadline is not None and time.time() >= deadline:
            break
        wanted = {m + d for m in need[market] for d in range(-BEFORE_MIN, AFTER_MIN + 1)}
        rows: list[dict[str, Any]] = []
        try:
            for lo, hi in windows(need[market]):
                end_ts = min((hi + 1) * 60, WALL_TS - 1)
                if lo * 60 >= end_ts:
                    counts["windows past the wall, skipped"] += 1
                    continue
                query = {"start_ts": lo * 60, "end_ts": end_ts, "period_interval": 1}
                body = json.loads(pacer.get(fetch, f"{BASE_URL}/historical/markets/{urllib.parse.quote(market)}/candlesticks?"
                                                   f"{urllib.parse.urlencode(query)}"))
                counts["calls"] += 1
                for c in body.get("candlesticks") or []:
                    end = int(c["end_period_ts"])
                    if (end - 1) // 60 in wanted:  # the candle that ENDS at 12:01:00 is minute 12:00
                        bid, ask, price = c.get("yes_bid") or {}, c.get("yes_ask") or {}, c.get("price") or {}
                        rows.append({"market": market, "end": end, "bid_open": bid.get("open"), "bid_close": bid.get("close"),
                                     "ask_open": ask.get("open"), "ask_close": ask.get("close"),
                                     "last": price.get("close"), "volume": c.get("volume")})
        except TooMany429s:
            raise
        except RuntimeError as exc:
            _append(out / "candles.miss.jsonl", [{"market": market, "error": str(exc)[-120:]}])
            counts["missed"] += 1
        _append(out / "candles.jsonl", rows)
        _mark(out / "candles.done", market)
        counts["legs"] += 1
        counts["candles"] += len(rows)
        counts["legs with no candle"] += not rows
        if (i + 1) % 250 == 0:
            log(f"candles: {i + 1} of {len(todo)} legs this run, {counts['calls']} calls, {counts['candles']} candles; "
                f"pace {pacer.interval:.2f}s, 429s {pacer.r429}/{pacer.calls}")
    counts["left"] = len(todo) - counts["legs"]
    return counts


# --------------------------------------------------------------------------- cli
def status(out: Path, log: Callable[[str], None] = print) -> None:
    sample = list(_read(out / "sample.jsonl"))
    by_cell = Counter((r["kind"], r["legs_band"]) for r in sample)
    log(f"sample: {len(sample)} combos; " + ", ".join(f"{k} {b}: {by_cell[(k, b)]}" for k in KINDS for b in BANDS))
    log(f"trades: {len(_done(out / 'trades.done'))} of {len(sample)} combos asked; "
        f"{sum(1 for _ in _read(out / 'trades.jsonl'))} trade rows; {sum(1 for _ in _read(out / 'trades.miss.jsonl'))} missed")
    need = leg_minutes(out)
    log(f"candles: {len(_done(out / 'candles.done'))} of {len(need)} legs asked "
        f"({sum(len(windows(v)) for v in need.values())} calls in all for the trades on disk); "
        f"{sum(1 for _ in _read(out / 'candles.jsonl'))} candle rows; {sum(1 for _ in _read(out / 'candles.miss.jsonl'))} missed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("sample", "trades", "candles", "status"))
    ap.add_argument("--per-cell", type=int, default=PER_CELL, help=f"combos per cell (default {PER_CELL})")
    ap.add_argument("--max-minutes", type=float, help="stop the run after this long")
    args = ap.parse_args(argv)
    listing, out = DATA_DIR / "m1", DATA_DIR / "m1" / "fills"
    out.mkdir(parents=True, exist_ok=True)
    if args.cmd == "status":
        status(out)
        return 0
    if args.cmd == "sample":
        if (out / "trades.done").exists():
            print("A sample with trades already exists; delete m1/fills to draw a new one.")
            return 2
        sample, counts = build_sample(listing, args.per_cell)
        (out / "sample.jsonl").unlink(missing_ok=True)
        _append(out / "sample.jsonl", sample)
        for k in sorted(counts):
            print(f"{k}: {counts[k]}")
        print(f"sample: {len(sample)} combos written to {out / 'sample.jsonl'}")
        return 0
    pacer = Pacer()
    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    try:
        counts = (crawl_trades if args.cmd == "trades" else crawl_candles)(out, pacer, http_fetch, deadline=deadline)
    except TooMany429s as exc:
        print(f"STOPPED: {exc}. Progress is saved. Report this before running again.")
        return 3
    except KeyboardInterrupt:
        print("Stopped by hand. Progress is saved; run the same command to continue.")
        return 130
    print(f"{args.cmd} this run: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"this run: {pacer.calls} calls, {pacer.r429} answered 429")
    status(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
