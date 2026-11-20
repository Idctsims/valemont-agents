"""M1b (owner-approved 2026-11-20, docs/research/m1b_draft.md): E1's window at 100%.

Runs on the owner's machine only. Public, keyless Kalshi endpoints. Nothing is
scheduled, nothing touches Supabase; everything is written under
`research_data/kalshi_history/m1b/` (gitignored).

    ..\\venv\\Scripts\\python.exe -m scripts.research.m1b list
    ..\\venv\\Scripts\\python.exe -m scripts.research.m1b sample
    ..\\venv\\Scripts\\python.exe -m scripts.research.m1b trades
    ..\\venv\\Scripts\\python.exe -m scripts.research.m1b candles
    ..\\venv\\Scripts\\python.exe -m scripts.research.m1b status

Every stage is resumable (run the same command again) and is M1's own code:
the listing page reader and its reserve wall (`kalshi_history.read_page`,
traded only, sample 100%), the sample's row shape (`kalshi_fills.build_sample`),
and the trades and candles stages (`kalshi_fills.crawl_trades`,
`crawl_candles`), pointed at `m1b/`. The pacer stops the run if 429s pass 2%.

What is new here, and only this:
- `list` walks KXMVENBASINGLEGAME from its first page (latest close first) and
  stops after the first page on which every row closes before 2026-04-03, or
  at 200 pages (then it stops and says so; the draft's cap).
- `sample` keeps every traded combo whose close_time falls 2026-04-03 to
  2026-06-01 (UTC), with no per-cell cap.
The 76 combos M1 already crawled are fetched again here, not copied (a leg's
candle windows depend on every combo that holds it).
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.parse
from pathlib import Path

from scripts.research import kalshi_fills as kf
from scripts.research import kalshi_history as kh
from venues.kalshi.client import BASE_URL

SERIES = "KXMVENBASINGLEGAME"
WINDOW = ("2026-04-03", "2026-06-01")
MAX_PAGES = 200
BASE = kh.DATA_DIR / "m1b"
FILLS = BASE / "fills"


def page_all_before(text: str, start: str = WINDOW[0]) -> bool | None:
    """True when every row on the page closes before `start` (by the reserve
    wall's own admission, so a reserve row is never decoded); None for an
    empty page."""
    scratch = kh.Counts()
    closes = []
    for raw in kh.elements(kh.top_level(text).get("markets", "[]")):
        row = kh.admit(raw, scratch)
        if row is not None:
            closes.append(row["close_time"][:10])
    if not closes:
        return None
    return all(c < start for c in closes)


def crawl_list(pacer: kh.Pacer, fetch: kh.Fetch, *, deadline: float | None = None,
               log=print) -> kh.SeriesState:
    BASE.mkdir(parents=True, exist_ok=True)
    state_path, rows_path = BASE / f"{SERIES}.state.json", BASE / f"{SERIES}.jsonl"
    state = kh.SeriesState.load(state_path)
    while not state.done:
        if state.pages >= MAX_PAGES:
            log(f"STOPPED at the {MAX_PAGES}-page cap before the window's start: report this.")
            break
        if deadline is not None and time.time() >= deadline:
            break
        query = {"series_ticker": SERIES, "limit": 1000} | ({"cursor": state.cursor} if state.cursor else {})
        text = pacer.get(fetch, f"{BASE_URL}/historical/markets?{urllib.parse.urlencode(query)}")
        page = kh.Counts()
        rows, cursor = kh.read_page(text, page, traded_only=True, sample_pct=100)
        if rows:
            kf._append(rows_path, rows)
        before = page_all_before(text)
        state.counts.add(page)
        state.pages += 1
        state.cursor = cursor
        state.done = (not cursor) or page.seen == 0 or before is True
        state.save(state_path)
        if state.pages % 10 == 0 or state.done:
            c = state.counts
            log(f"list: page {state.pages}, seen {c.seen}, kept {c.kept}, untraded {c.untraded}, reserve {c.reserve}; "
                f"pace {pacer.interval:.2f}s, 429s {pacer.r429}/{pacer.calls}" + (" DONE" if state.done else ""))
    return state


def build_sample() -> tuple[list[dict], dict]:
    rows, counts = kf.build_sample(BASE, per_cell=10 ** 9)
    keep = [r for r in rows if WINDOW[0] <= r["close_time"][:10] <= WINDOW[1]]
    counts = dict(counts)
    counts["in E1's window"] = len(keep)
    counts["outside the window (dropped)"] = len(rows) - len(keep)
    return keep, counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("list", "sample", "trades", "candles", "status"))
    ap.add_argument("--max-minutes", type=float)
    args = ap.parse_args(argv)
    FILLS.mkdir(parents=True, exist_ok=True)
    if args.cmd == "status":
        st = kh.SeriesState.load(BASE / f"{SERIES}.state.json")
        print(f"list: {st.pages} pages, done={st.done}, kept {st.counts.kept}, seen {st.counts.seen}")
        kf.status(FILLS)
        return 0
    if args.cmd == "sample":
        if (FILLS / "trades.done").exists():
            print("A sample with trades already exists; delete m1b/fills to draw a new one.")
            return 2
        if not kh.SeriesState.load(BASE / f"{SERIES}.state.json").done:
            print("The listing is not done; run `list` first.")
            return 2
        sample, counts = build_sample()
        (FILLS / "sample.jsonl").unlink(missing_ok=True)
        kf._append(FILLS / "sample.jsonl", sample)
        for k in sorted(counts):
            print(f"{k}: {counts[k]}")
        print(f"sample: {len(sample)} combos written to {FILLS / 'sample.jsonl'}")
        return 0
    pacer = kh.Pacer()
    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    try:
        if args.cmd == "list":
            st = crawl_list(pacer, kh.http_fetch, deadline=deadline)
            print(f"list: {st.pages} pages, done={st.done}")
        else:
            fn = kf.crawl_trades if args.cmd == "trades" else kf.crawl_candles
            counts = fn(FILLS, pacer, kh.http_fetch, deadline=deadline)
            print(f"{args.cmd} this run: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    except kh.TooMany429s as exc:
        print(f"STOPPED: {exc}. Progress is saved. Report this before running again.")
        return 3
    except KeyboardInterrupt:
        print("Stopped by hand. Progress is saved; run the same command to continue.")
        return 130
    print(f"this run: {pacer.calls} calls, {pacer.r429} answered 429")
    return 0


if __name__ == "__main__":
    sys.exit(main())
