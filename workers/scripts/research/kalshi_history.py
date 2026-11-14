"""Step 1 crawl (Chat 8 Phase 3.5): Kalshi's settled markets, public and keyless.

Runs on the owner's machine only. Nothing here is scheduled, nothing touches
Supabase, and nothing is written outside `research_data/` (gitignored).

    ..\\venv\\Scripts\\python.exe -m scripts.research.kalshi_history list --study m1
    ..\\venv\\Scripts\\python.exe -m scripts.research.kalshi_history list --study m2
    ..\\venv\\Scripts\\python.exe -m scripts.research.kalshi_history status

`list` walks `/historical/markets` one series at a time (owner, J5) and is
resumable: stop it with Ctrl+C, run the same command again, and it continues
from the last saved page of each series.

THE RESERVE WALL (CLAUDE.md, "The reserve"; owner, J1). A row that closed on
or after 2026-07-01 UTC is never read. The page arrives as text. `top_level`
finds each row's `close_time` WITHOUT decoding any other value, and a
reserve-dated or undated row is dropped there: it is never passed to
`json.loads`, never reaches a parser, and is never written. Only its count is
kept. `tests/test_research_history.py` proves it with a reserve row whose
other fields are not even valid JSON.

Pacing is adaptive (faster while Kalshi answers, half speed on a 429). The run
stops and says so if 429s pass 2% of its calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from core.paths import REPO_ROOT
from venues.kalshi.client import BASE_URL, USER_AGENT

#: First UTC date of the reserve. A row dated on or after it is never read.
WALL = "2026-07-01"
#: Raw rows live here: gitignored, local, never Supabase. VALEMONT_RESEARCH_DIR
#: points a worktree's run at the main checkout's folder so one crawl has one home.
DATA_DIR = Path(os.environ.get("VALEMONT_RESEARCH_DIR") or REPO_ROOT / "research_data") / "kalshi_history"

#: M2 reads settled single markets of these leagues' series. NFL singles are
#: left out until the owner says otherwise (J3 covers 2025 NFL combos and box
#: scores, not singles); college football is a spent holdout.
M2_PREFIXES = ("KXNBA", "KXNHL")
#: The fields a study uses. Everything else on a row is left behind (a full
#: row is ~1.8 KB; these are ~0.5 KB).
KEEP_FIELDS = frozenset({
    "ticker", "event_ticker", "market_type", "mve_collection_ticker", "mve_selected_legs", "open_time",
    "created_time", "close_time", "expected_expiration_time", "expiration_time", "settlement_ts", "result",
    "settlement_value_dollars", "last_price_dollars", "previous_price_dollars", "volume_fp", "open_interest_fp",
    "price_level_structure", "yes_sub_title", "strike_type", "floor_strike", "cap_strike", "custom_strike"})
#: M1 keeps this share of traded combos (chosen by a hash of the ticker, so a
#: re-run keeps the same ones) and counts every one in the census.
M1_SAMPLE_PCT = 10

MAX_429_SHARE = 0.02
MIN_CALLS_FOR_STOP = 200

#: Text in, (HTTP status, body text) out.
Fetch = Callable[[str], tuple[int, str]]


# --------------------------------------------------------------------------- the gate
def _ws(t: str, i: int) -> int:
    while t[i] in " \t\r\n":
        i += 1
    return i


def _string_end(t: str, i: int) -> int:
    """Index just past the string that opens at t[i]."""
    j = i + 1
    while t[j] != '"':
        j += 2 if t[j] == "\\" else 1
    return j + 1


def _value_end(t: str, i: int) -> int:
    """Index just past the JSON value that starts at t[i]. Nothing is decoded."""
    c = t[i]
    if c == '"':
        return _string_end(t, i)
    if c in "{[":
        depth, j = 0, i
        while True:
            c = t[j]
            if c == '"':
                j = _string_end(t, j)
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
    j = i
    while t[j] not in ",}] \t\r\n":
        j += 1
    return j


def top_level(text: str) -> dict[str, str]:
    """{key: the RAW text of its value} for one JSON object. Values are located
    by matching quotes and brackets and are not decoded."""
    out: dict[str, str] = {}
    i = _ws(text, 0)
    if text[i] != "{":
        raise ValueError("not a JSON object")
    i += 1
    while True:
        i = _ws(text, i)
        if text[i] == "}":
            return out
        if text[i] == ",":
            i += 1
            continue
        end = _string_end(text, i)
        key = json.loads(text[i:end])
        i = _ws(text, end)
        if text[i] != ":":
            raise ValueError(f"no value for {key!r}")
        i = _ws(text, i + 1)
        end = _value_end(text, i)
        out[key] = text[i:end]
        i = end


def elements(array_text: str) -> Iterator[str]:
    """The RAW text of each element of a JSON array."""
    i = _ws(array_text, 0)
    if array_text[i] != "[":
        raise ValueError("not a JSON array")
    i += 1
    while True:
        i = _ws(array_text, i)
        if array_text[i] == "]":
            return
        if array_text[i] == ",":
            i += 1
            continue
        end = _value_end(array_text, i)
        yield array_text[i:end]
        i = end


@dataclass
class Counts:
    seen: int = 0
    kept: int = 0
    reserve: int = 0
    undated: int = 0
    untraded: int = 0
    sampled_out: int = 0
    #: Admitted, traded rows by "YYYY-MM|legs", before sampling.
    census: dict[str, int] = field(default_factory=dict)

    def add(self, other: "Counts") -> None:
        for k in ("seen", "kept", "reserve", "undated", "untraded", "sampled_out"):
            setattr(self, k, getattr(self, k) + getattr(other, k))
        for k, v in other.census.items():
            self.census[k] = self.census.get(k, 0) + v


def admit(raw_row: str, counts: Counts) -> dict[str, Any] | None:
    """The decoded row if it closed before the wall, else None. A reserve-dated
    or undated row is counted and dropped before any other field is decoded."""
    counts.seen += 1
    raw_close = top_level(raw_row).get("close_time")
    close = json.loads(raw_close) if raw_close and raw_close.startswith('"') else None
    if not close:
        counts.undated += 1
        return None
    if close[:10] >= WALL:
        counts.reserve += 1
        return None
    return json.loads(raw_row)


def lean(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k in KEEP_FIELDS}


def read_page(text: str, counts: Counts, *, traded_only: bool, sample_pct: int = 100,
              parse: Callable[[dict[str, Any]], dict[str, Any]] = lean) -> tuple[list[dict[str, Any]], str]:
    """(rows to keep, next cursor) from one `/historical/markets` page."""
    page = top_level(text)
    cursor = json.loads(page.get("cursor", '""')) or ""
    rows: list[dict[str, Any]] = []
    for raw in elements(page.get("markets", "[]")):
        row = admit(raw, counts)
        if row is None:
            continue
        if traded_only and float(row.get("volume_fp") or 0) <= 0:
            counts.untraded += 1
            continue
        key = f"{row['close_time'][:7]}|{len(row.get('mve_selected_legs') or [])}"
        counts.census[key] = counts.census.get(key, 0) + 1
        if zlib.crc32(str(row.get("ticker")).encode()) % 100 >= sample_pct:
            counts.sampled_out += 1
            continue
        rows.append(parse(row))
        counts.kept += 1
    return rows, cursor


# --------------------------------------------------------------------------- pacing
class TooMany429s(RuntimeError):
    """429s passed 2% of this run's calls. The state is saved; report it."""


@dataclass
class Pacer:
    interval: float = 0.6
    floor: float = 0.25
    ceiling: float = 10.0
    calls: int = 0
    r429: int = 0
    sleep: Callable[[float], None] = time.sleep
    backoff: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0, 40.0, 60.0)

    def ok(self) -> None:
        self.interval = max(self.floor, self.interval * 0.97)

    def limited(self) -> None:
        self.r429 += 1
        self.interval = min(self.ceiling, self.interval * 2.0)
        if self.calls >= MIN_CALLS_FOR_STOP and self.r429 / self.calls > MAX_429_SHARE:
            raise TooMany429s(f"{self.r429} of {self.calls} calls answered 429 (over {MAX_429_SHARE:.0%})")

    def get(self, fetch: Fetch, url: str) -> str:
        for wait in (*self.backoff, None):
            self.sleep(self.interval)
            self.calls += 1
            try:
                status, body = fetch(url)
            except OSError as exc:  # no HTTP answer: wait and ask again
                status, body = 0, str(exc)
            if status == 200:
                self.ok()
                return body
            if status == 429:
                self.limited()
            elif 400 <= status < 500:
                raise RuntimeError(f"{url} answered HTTP {status}")
            if wait is None:
                break
            self.sleep(wait)
        raise RuntimeError(f"{url} kept failing (last status {status})")


def http_fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30.0) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


# --------------------------------------------------------------------------- the crawl
@dataclass
class SeriesState:
    cursor: str = ""
    done: bool = False
    pages: int = 0
    counts: Counts = field(default_factory=Counts)

    @classmethod
    def load(cls, path: Path) -> "SeriesState":
        if not path.exists():
            return cls()
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(d["cursor"], d["done"], d["pages"], Counts(**d["counts"]))

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"cursor": self.cursor, "done": self.done, "pages": self.pages,
                                   "counts": vars(self.counts)}), encoding="utf-8")
        tmp.replace(path)


def crawl_series(series: str, out_dir: Path, pacer: Pacer, fetch: Fetch, *, traded_only: bool,
                 sample_pct: int = 100, max_pages: int | None = None, deadline: float | None = None,
                 log: Callable[[str], None] = print) -> SeriesState:
    """Walk one series to its end, or to a page or time limit. Each page's rows
    are appended, then the cursor is saved: a stop between the two re-reads one
    page on resume (rows repeat; the analysis de-duplicates by ticker)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path, rows_path = out_dir / f"{series}.state.json", out_dir / f"{series}.jsonl"
    state = SeriesState.load(state_path)
    pages_now = 0
    while not state.done:
        if (max_pages is not None and pages_now >= max_pages) or (deadline is not None and time.time() >= deadline):
            break
        query = {"series_ticker": series, "limit": 1000} | ({"cursor": state.cursor} if state.cursor else {})
        text = pacer.get(fetch, f"{BASE_URL}/historical/markets?{urllib.parse.urlencode(query)}")
        page = Counts()
        rows, cursor = read_page(text, page, traded_only=traded_only, sample_pct=sample_pct)
        if rows:
            with rows_path.open("a", encoding="utf-8", newline="\n") as f:
                for row in rows:
                    f.write(json.dumps(row, separators=(",", ":")) + "\n")
        state.counts.add(page)
        state.pages += 1
        pages_now += 1
        state.cursor, state.done = cursor, not cursor or page.seen == 0
        state.save(state_path)
        if state.pages % 25 == 0 or state.done:
            c = state.counts
            log(f"{series}: page {state.pages}, kept {c.kept}, reserve discarded {c.reserve}, undated {c.undated}, "
                f"untraded {c.untraded}, sampled out {c.sampled_out}; pace {pacer.interval:.2f}s, 429s {pacer.r429}/{pacer.calls}"
                + (" DONE" if state.done else ""))
    return state


def discover(study: str, pacer: Pacer, fetch: Fetch) -> list[str]:
    """Series to walk. `/series` rows are metadata (no prices, no results)."""
    body = json.loads(pacer.get(fetch, f"{BASE_URL}/series?category=Sports"))
    tickers = sorted({s["ticker"] for s in body.get("series", []) if s.get("ticker")})
    if study == "m1":
        extra = json.loads(pacer.get(fetch, f"{BASE_URL}/series?category=Exotics"))
        tickers = sorted(set(tickers) | {s["ticker"] for s in extra.get("series", []) if s.get("ticker")})
        return [t for t in tickers if t.startswith("KXMVE")]
    return [t for t in tickers if t.startswith(M2_PREFIXES) and "MVE" not in t and not t.endswith("PROT")]


def status(log: Callable[[str], None] = print) -> None:
    for study in ("m1", "m2"):
        d = DATA_DIR / study
        states = sorted(d.glob("*.state.json")) if d.exists() else []
        total, done = Counts(), 0
        for p in states:
            s = SeriesState.load(p)
            total.add(s.counts)
            done += s.done
        log(f"{study}: {done} of {len(states)} series finished; kept {total.kept}, reserve discarded {total.reserve}, "
            f"undated {total.undated}, untraded {total.untraded}, sampled out {total.sampled_out}, "
            f"rows seen {total.seen}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="walk /historical/markets by series (resumable)")
    ls.add_argument("--study", choices=("m1", "m2"), required=True)
    ls.add_argument("--series", help="comma-separated series tickers (default: discovered)")
    ls.add_argument("--max-pages", type=int, help="stop each series after this many pages this run")
    ls.add_argument("--sample-pct", type=int, help=f"share of rows to keep (m1 default {M1_SAMPLE_PCT}, m2 100)")
    ls.add_argument("--max-minutes", type=float, help="stop the run after this long")
    sub.add_parser("status", help="what is on disk")
    args = ap.parse_args(argv)
    if args.cmd == "status":
        status()
        return 0
    pacer = Pacer()
    out_dir = DATA_DIR / args.study
    out_dir.mkdir(parents=True, exist_ok=True)
    series_file = out_dir / "series.json"
    if args.series:
        series = [s.strip() for s in args.series.split(",") if s.strip()]
    elif series_file.exists():
        series = json.loads(series_file.read_text(encoding="utf-8"))
    else:
        series = discover(args.study, pacer, http_fetch)
        series_file.write_text(json.dumps(series), encoding="utf-8")
    print(f"{args.study}: {len(series)} series; writing under {out_dir}")
    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    try:
        for s in series:
            if deadline is not None and time.time() >= deadline:
                break
            crawl_series(s, out_dir, pacer, http_fetch, traded_only=args.study == "m1",
                         sample_pct=args.sample_pct or (M1_SAMPLE_PCT if args.study == "m1" else 100), max_pages=args.max_pages, deadline=deadline)
    except TooMany429s as exc:
        print(f"STOPPED: {exc}. Progress is saved. Report this before running again.")
        return 3
    except KeyboardInterrupt:
        print("Stopped by hand. Progress is saved; run the same command to continue.")
        return 130
    status()
    print(f"this run: {pacer.calls} calls, {pacer.r429} answered 429")
    return 0


if __name__ == "__main__":
    sys.exit(main())
