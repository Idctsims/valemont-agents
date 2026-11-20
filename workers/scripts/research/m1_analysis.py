"""Step 1, M1 analysis (Chat 8 Phase 3.5): E1's reproduction first, then the fill premium by cell.

    ..\\venv\\Scripts\\python.exe -m scripts.research.m1_analysis --out <repo>\\docs\\research

Reads only research_data/kalshi_history/m1 (gitignored; VALEMONT_RESEARCH_DIR
points a worktree at the main checkout's): the listing (settlement), the
sample, its trades and its legs' candles. No network, no database.

THE RULE (docs/betting_science_audit.md §6 M1, frozen; J2: no before/after
2026-08-20 cell): reproduce E1's headline on its window first. If our
equal-weight maker edge on E1's window is within +-1.0c of +3.3c, E1 is
reproduced and the fill premium (fill minus the product of the legs' mids)
is reported per cell with its interval; a cell under 300 combos gets no number
and borrows the next shorter leg-count cell, labelled borrowed. If it is
outside +-1.0c the cell table is NOT computed: stop and report.

DEFINITIONS, fixed 2026-11-19 before any fill was analyzed (E1's code is not
public; where its README is silent the choice is ours and stated):
- A trade is one row of /historical/trades, de-duplicated by trade_id; block
  trades are kept (and counted).
- Maker edge of a trade, in cents per contract: what the taker paid for the
  side it took minus what that side settles to. Taker YES: yes_price -
  settlement; taker NO: no_price - (1 - settlement). Settlement is the
  combo's settlement_value_dollars (fractional when a leg settled at a fair
  price). Before fees (E1: trade prices are before fees).
- Equal-weight: the mean over trades. Volume-weight: weighted by count_fp.
- E1's window: combos whose close_time falls 2026-04-03 to 2026-06-01 (UTC).
- A leg's mid at a fill: the last 1-minute candle that closed at or before
  the trade's time (within the 5 minutes the crawl kept), (bid_close +
  ask_close) / 2, for the leg's side (NO: 1 - the YES mid). Only a two-sided
  quote counts (0 < bid <= ask < 1). A fill with any leg lacking one is left
  out of the premium and counted.
- Fill premium of a trade: yes_price - the product of its legs' side mids,
  in cents.
- Cells: same-game / cross-game (a combo whose legs are not all one game is
  cross-game; tournaments and awards were never sampled), legs 2, 3-5, 6-10,
  11-20 (21+ reported apart), combo price band (the fill's YES price: under
  10c, 10-25, 25-50, 50-75, 75c+), sport (the legs' league prefix).
- Interval: 95% bootstrap over combos (a combo's trades move together), 500
  resamples, seed fixed.
- 2025 NFL rows are fitting-only (J3): nothing here is validation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from scripts.research.kalshi_fills import n_games, ts_of
from scripts.research.kalshi_history import DATA_DIR, WALL

E1_WINDOW = ("2026-04-03", "2026-06-01")
E1_EDGE_C, TOLERANCE_C = 3.3, 1.0
MIN_COMBOS = 300
LOOKBACK_S = 5 * 60
BOOT, SEED = 500, 20261119
LEG_BANDS = (("2", 2, 2), ("3-5", 3, 5), ("6-10", 6, 10), ("11-20", 11, 20), ("21+", 21, 10_000))
PRICE_BANDS = (("<10c", 0.0, 0.10), ("10-25c", 0.10, 0.25), ("25-50c", 0.25, 0.50), ("50-75c", 0.50, 0.75),
               ("75c+", 0.75, 1.01))
#: The next shorter leg-count cell a thin cell borrows from.
BORROW = {"3-5": "2", "6-10": "3-5", "11-20": "6-10", "21+": "11-20"}


def _read(path: Path) -> Iterator[dict[str, Any]]:
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def leg_band(n: int) -> str:
    return next(name for name, lo, hi in LEG_BANDS if lo <= n <= hi)


def price_band(p: float) -> str:
    return next(name for name, lo, hi in PRICE_BANDS if lo <= p < hi)


def sport_of(legs: Sequence[dict[str, Any]]) -> str:
    prefixes = {(leg.get("event") or leg.get("event_ticker") or "").split("-")[0] for leg in legs}
    leagues = {next((lg for lg in ("NBA", "NFL", "NHL", "NCAAF", "NCAAMB", "MLB") if p.startswith("KX" + lg)), "other")
               for p in prefixes}
    return leagues.pop() if len(leagues) == 1 else "mixed"


def maker_edge_c(trade: dict[str, Any], settlement: float) -> float:
    if trade["taker_outcome_side"] == "yes":
        return (float(trade["yes_price_dollars"]) - settlement) * 100
    return (float(trade["no_price_dollars"]) - (1 - settlement)) * 100


def side_mid(candles: Sequence[dict[str, Any]], at_ts: int, side: str) -> float | None:
    """The leg's side mid from the last candle closed at or before at_ts (within LOOKBACK_S), two-sided only."""
    best = None
    for c in candles:
        if at_ts - LOOKBACK_S <= c["end"] <= at_ts and (best is None or c["end"] > best["end"]):
            best = c
    if best is None or best.get("bid_close") is None or best.get("ask_close") is None:
        return None
    bid, ask = float(best["bid_close"]), float(best["ask_close"])
    if not (0 < bid <= ask < 1):
        return None
    mid = (bid + ask) / 2
    return mid if side == "yes" else 1 - mid


def mean_ci(groups: dict[str, list[tuple[float, float]]], *, weighted: bool = False,
            seed: int = SEED) -> dict[str, Any]:
    """Mean of (value, weight) pairs, and a 95% bootstrap over the groups (combos)."""
    def mean(rows: Iterable[tuple[float, float]]) -> float | None:
        rows = list(rows)
        if not rows:
            return None
        if weighted:
            w = sum(x[1] for x in rows)
            return sum(v * x for v, x in rows) / w if w else None
        return statistics.fmean(v for v, _ in rows)

    keys = sorted(groups)
    point = mean(r for k in keys for r in groups[k])
    rng = random.Random(seed)
    boots = []
    for _ in range(BOOT if keys else 0):
        pick = [keys[rng.randrange(len(keys))] for _ in keys]
        m = mean(r for k in pick for r in groups[k])
        if m is not None:
            boots.append(m)
    boots.sort()
    ci = [round(boots[int(0.025 * len(boots))], 3), round(boots[min(len(boots) - 1, int(0.975 * len(boots)))], 3)] if boots else None
    return {"mean_c": None if point is None else round(point, 3), "ci95_c": ci, "combos": len(keys),
            "trades": sum(len(v) for v in groups.values())}


def load(base: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, float], Counter]:
    counts: Counter = Counter()
    sample = {r["ticker"]: r for r in _read(base / "fills" / "sample.jsonl")}
    settle: dict[str, float] = {}
    for path in sorted(base.glob("*.jsonl")):
        for r in _read(path):
            if r.get("ticker") in sample and r.get("settlement_value_dollars") is not None:
                if r["close_time"][:10] >= WALL:  # the listing never kept one; a second guard
                    counts["reserve row in the listing (dropped)"] += 1
                    continue
                settle[r["ticker"]] = float(r["settlement_value_dollars"])
    trades, seen = [], set()
    for t in _read(base / "fills" / "trades.jsonl"):
        if t["trade_id"] in seen:
            counts["duplicate trade rows"] += 1
            continue
        seen.add(t["trade_id"])
        trades.append(t)
    candles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in _read(base / "fills" / "candles.jsonl"):
        candles[c["market"]].append(c)
    return sample, trades, candles, settle, counts


def analyze(sample: dict[str, dict[str, Any]], trades: list[dict[str, Any]], candles: dict[str, list[dict[str, Any]]],
            settle: dict[str, float], counts: Counter) -> dict[str, Any]:
    edge_eq: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for t in trades:
        combo = sample.get(t["ticker"])
        if combo is None or t["ticker"] not in settle:
            counts["trades without a settled sampled combo"] += 1
            continue
        counts["blocks"] += bool(t.get("is_block_trade"))
        if E1_WINDOW[0] <= combo["close_time"][:10] <= E1_WINDOW[1]:
            edge_eq[t["ticker"]].append((maker_edge_c(t, settle[t["ticker"]]), float(t["count_fp"])))
    repro_eq, repro_vw = mean_ci(edge_eq), mean_ci(edge_eq, weighted=True)
    reproduced = repro_eq["mean_c"] is not None and abs(repro_eq["mean_c"] - E1_EDGE_C) <= TOLERANCE_C
    window_mix = Counter((sample[k]["series"], sample[k]["kind"]) for k in edge_eq)
    out: dict[str, Any] = {
        "reproduction": {"window": E1_WINDOW, "e1_equal_weight_c": E1_EDGE_C, "tolerance_c": TOLERANCE_C,
                         "equal_weight": repro_eq, "volume_weight": repro_vw, "e1_volume_weight_c": 2.1,
                         "reproduced": reproduced,
                         "window_combos_by_series_kind": {f"{s} {k}": n for (s, k), n in sorted(window_mix.items())}},
    }
    if not reproduced:
        out["cells"] = "NOT COMPUTED: E1 not reproduced within +-1.0c (the frozen rule: stop and report)"
        return out
    by_cell: dict[tuple[str, str], dict[str, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    by_side: dict[tuple[str, str], dict[str, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    for t in trades:
        combo = sample.get(t["ticker"])
        if combo is None:
            continue
        ts = ts_of(t["created_time"])
        mids = [side_mid(candles.get(leg["market"], []), ts, leg["side"]) for leg in combo["legs"]]
        if any(m is None for m in mids):
            counts["fills with a one-sided or missing leg quote (left out)"] += 1
            continue
        prod = 1.0
        for m in mids:
            prod *= m  # type: ignore[operator]
        prem = (float(t["yes_price_dollars"]) - prod) * 100
        g = n_games([{"event_ticker": leg["event"]} for leg in combo["legs"]])
        kind = "same-game" if g == 1 else "cross-game"
        lb = leg_band(len(combo["legs"]))
        row = (prem, float(t["count_fp"]))
        by_cell[(kind, lb)][t["ticker"]].append(row)
        by_side[(kind, f"{sport_of(combo['legs'])} {price_band(float(t['yes_price_dollars']))}")][t["ticker"]].append(row)
        counts["fills priced"] += 1
    cells: dict[str, Any] = {}
    for kind in ("same-game", "cross-game"):
        for name, _, _ in LEG_BANDS:
            m = mean_ci(by_cell.get((kind, name), {}))
            cells[f"{kind} {name}"] = m | {"number": m["combos"] >= MIN_COMBOS}
        for name, _, _ in LEG_BANDS:  # a thin cell borrows the next shorter cell's number, labelled
            c = cells[f"{kind} {name}"]
            if not c["number"]:
                src = BORROW.get(name)
                while src and not cells[f"{kind} {src}"]["number"]:
                    src = BORROW.get(src)
                c["borrowed_from"] = f"{kind} {src}" if src else None
    out["cells"] = cells
    out["by_sport_price_band"] = {f"{k} {b}": mean_ci(v) for (k, b), v in sorted(by_side.items())}
    return out


def git_hash() -> str:
    here = Path(__file__).resolve().parent
    h = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", str(Path(__file__).resolve())], cwd=here,
                           capture_output=True, text=True).stdout.strip()
    return h + ("-dirty" if dirty else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    # M1b (owner, Nov 20) reads its own crawl; every definition above is unchanged.
    ap.add_argument("--study", choices=("m1", "m1b"), default="m1")
    args = ap.parse_args(argv)
    base = DATA_DIR / args.study
    done = len(set((base / "fills" / "candles.done").read_text(encoding="utf-8").split())) if (base / "fills" / "candles.done").exists() else 0
    sample, trades, candles, settle, counts = load(base)
    result = {"study": args.study.upper(), "script": "workers/scripts/research/m1_analysis.py", "git": git_hash(),
              "run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "input": {"sampled_combos": len(sample), "settled": len(settle), "trades": len(trades),
                        "legs_with_candles_asked": done, "candle_markets": len(candles)}}
    result |= analyze(sample, trades, candles, settle, counts)
    result["counts"] = dict(counts)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{args.study}_fills_{result['run_at'][:10]}.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    r = result["reproduction"]
    print(f"written {path}: E1 window equal-weight maker edge {r['equal_weight']['mean_c']}c "
          f"(CI {r['equal_weight']['ci95_c']}), reproduced={r['reproduced']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
