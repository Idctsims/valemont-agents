"""The pre-registered CFB totals holdout, run once.

    python -m jobs.holdout_cfb_totals --execute

docs/preregistration_cfb_totals.md. Every `KXNCAAFTOTAL` event dated
2026-07-01 through 2026-09-27, priced at t = ESPN start − 60 min, forecast
`0.375·mid + 0.625·b`, taker gate net of half-spread, Kalshi fee and a 3¢
margin, one per game. Pass iff the taker R's game-clustered 95% CI is above 0
with at least 20 trades in 10 games.

**Run once.** Refuses without `--execute` and if any output exists. Runs its
own tests and the scan's before scoring. Results go to `docs/backtests/`,
never the ledger. API answers are cached under `.cache/cfb_holdout/`, so a
crash before output (no result seen) resumes rather than re-reading.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import statistics
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Final, Sequence

from jobs import scan_market_efficiency as scan
from jobs.dev_props_maker import fill
from venues.kalshi.client import Candle, KalshiClient, Quote
from venues.kalshi.fees import fetch_schedule
from core.preregistration import PreregistrationMissing, require_registered
from core.paths import REPO_ROOT

log = logging.getLogger("valemont.holdout_cfb_totals")

#: The frozen text this runner executes; it refuses unless DB-stamped first (db/017).
PREREG_DOC: Final = "docs/preregistration_cfb_totals.md"
PREREG_SECTION: Final = "BEFORE ## 9. Execution log"

# --- frozen by preregistration_cfb_totals.md -----------------------------------
SERIES: Final = "KXNCAAFTOTAL"
HOLDOUT_START: Final = date(2026, 7, 1)
HOLDOUT_END: Final = date(2026, 9, 27)
POOL_END: Final = date(2026, 6, 30)
W_BLEND: Final = 0.625
WINDOW: Final = 3.0
MARGIN: Final = 0.03
MAX_SPREAD: Final = 0.06
LEAD: Final = timedelta(minutes=60)
VOLUME_WINDOW: Final = timedelta(hours=24)
PARTICIPATION: Final = 0.10
MIN_TRADES: Final = 20
MIN_GAMES: Final = 10
DAILY_CAPACITY_FLOOR: Final = 100.0
SEED: Final = 20261002
DRAWS: Final = 2000
CELL: Final = scan.Cell("CFB", "total", SERIES, "total", WINDOW)

CACHE: Final = REPO_ROOT / ".cache/cfb_holdout"
OUTPUT_DIR: Final = REPO_ROOT / "docs/backtests"
OUTPUT_STEM: Final = "cfb_totals-holdout"
TESTS: Final = ("tests.test_cfb_totals_holdout", "tests.test_market_scan")
EPS: Final = 1e-9


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------

class HoldoutScope(RuntimeError):
    """An event outside the holdout reached a holdout request."""


def in_holdout(event_ticker: str) -> bool:
    if not event_ticker.startswith(SERIES + "-"):
        return False
    return HOLDOUT_START <= scan.parse_game_code(event_ticker).day <= HOLDOUT_END


def assert_holdout(event_ticker: str) -> scan.GameCode:
    if not in_holdout(event_ticker):
        raise HoldoutScope(f"{event_ticker} is not a holdout event")
    return scan.parse_game_code(event_ticker)


def forecast(mid: float, base: float, w: float = W_BLEND) -> float:
    return (1 - w) * mid + w * base


def _passes(r: dict, edge: float) -> bool:
    return edge >= MARGIN - EPS and r["ask"] - r["bid"] <= MAX_SPREAD + EPS


def select_taker(rows: Sequence[dict], w: float = W_BLEND) -> list[dict]:
    """One trade per game: the largest edge over every rung and side with
    edge ≥ 3¢ and spread ≤ 6¢. Entry is the ask of the side; cost adds the
    taker fee. Rows carry fees precomputed at their own t."""
    best: dict[str, dict] = {}
    for r in rows:
        p = forecast(r["mid"], r["base"], w)
        for side, p_side, entry, fee in (("yes", p, r["ask"], r["taker_fee_yes"]),
                                          ("no", 1 - p, 1 - r["bid"], r["taker_fee_no"])):
            cost = entry + fee
            edge = p_side - cost
            if _passes(r, edge) and (r["event"] not in best or edge > best[r["event"]]["edge"]):
                best[r["event"]] = {"row": r, "side": side, "entry": entry, "cost": cost, "edge": edge}
    return list(best.values())


def select_maker(rows: Sequence[dict], w: float = W_BLEND) -> list[dict]:
    """One resting order per game at the side's bid; cost includes the maker fee."""
    best: dict[str, dict] = {}
    for r in rows:
        p = forecast(r["mid"], r["base"], w)
        for side, p_side, limit, fee in (("yes", p, r["bid"], r["maker_fee_yes"]),
                                          ("no", 1 - p, 1 - r["ask"], r["maker_fee_no"])):
            cost = limit + fee
            edge = p_side - cost
            if limit > 0 and _passes(r, edge) and (r["event"] not in best or edge > best[r["event"]]["edge"]):
                best[r["event"]] = {"row": r, "side": side, "entry": limit, "cost": cost, "edge": edge}
    return list(best.values())


def r_of(settle: float | None, side: str, cost: float) -> float | None:
    if settle is None or cost <= 0:
        return None
    value = settle if side == "yes" else 1 - settle
    return (value - cost) / cost


def game_ci(values: dict[str, list[float]]) -> list[float] | None:
    games = list(values.values())
    if len(games) < 2:
        return None
    rng = random.Random(SEED)
    means = []
    for _ in range(DRAWS):
        sample = [x for _ in games for x in rng.choice(games)]
        means.append(sum(sample) / len(sample))
    means.sort()
    return [means[int(0.025 * DRAWS)], means[int(0.975 * DRAWS) - 1]]


def verdict(n_trades: int, n_games: int, ci: list[float] | None) -> bool:
    return n_trades >= MIN_TRADES and n_games >= MIN_GAMES and ci is not None and ci[0] > EPS


def capacity(trade: dict) -> tuple[float, float]:
    """(contracts, dollars): 10% of the rung's 24 h volume before t, at cost."""
    contracts = PARTICIPATION * trade["row"]["volume_24h"]
    return contracts, contracts * trade["cost"]


def refuse_reasons(out_dir: Path) -> list[str]:
    existing = sorted(out_dir.glob(f"{OUTPUT_STEM}*")) if out_dir.exists() else []
    return [f"output already exists ({existing[0]}); the holdout runs once"] if existing else []


# ---------------------------------------------------------------------------
# Network, cached
# ---------------------------------------------------------------------------

def holdout_events(client: KalshiClient) -> list[str]:
    def fetch() -> list[str]:
        out, cursor = [], None
        while True:
            body = client.get("/events", series_ticker=SERIES, limit=200, cursor=cursor)
            page = body.get("events", [])
            out += [e["event_ticker"] for e in page]
            cursor = body.get("cursor")
            if not cursor or not page:
                return out
    keep = []
    for ev in scan._cached(CACHE / "events.json", fetch):
        try:
            if in_holdout(ev):
                keep.append(ev)
        except ValueError:
            continue
    return sorted(keep)


def holdout_markets(client: KalshiClient, event: str) -> list[dict]:
    assert_holdout(event)
    def fetch() -> list[dict]:
        markets = client.get("/markets", event_ticker=event, limit=1000).get("markets", [])
        return markets or client.get("/historical/markets", event_ticker=event, limit=1000).get("markets", [])
    return scan._cached(CACHE / "markets" / f"{event}.json", fetch)


def holdout_candles(client: KalshiClient, event: str, q: Quote, t: datetime, start: datetime) -> list[Candle]:
    assert_holdout(event)
    def fetch() -> list[dict]:
        cs = client.candles(SERIES, q.ticker, t - VOLUME_WINDOW, start, 1, settled_at=q.settlement_ts)
        return [{"end": c.end.isoformat(), "bid": None if c.yes_bid_close is None else str(c.yes_bid_close),
                 "ask": None if c.yes_ask_close is None else str(c.yes_ask_close),
                 "volume": str(c.volume), "oi": None if c.open_interest is None else str(c.open_interest)}
                for c in cs]
    raw = scan._cached(CACHE / "candles" / f"{q.ticker}.json", fetch)
    return [Candle(end=datetime.fromisoformat(c["end"]),
                   yes_bid_close=None if c["bid"] is None else Decimal(c["bid"]),
                   yes_ask_close=None if c["ask"] is None else Decimal(c["ask"]),
                   trade_low=None, trade_high=None, trade_close=None, volume=Decimal(c["volume"]),
                   open_interest=None if c["oi"] is None else Decimal(c["oi"])) for c in raw]


def latest_mid(candles: Sequence[Candle], at: datetime) -> float | None:
    quoted = [c for c in candles if c.end <= at and c.yes_bid_close is not None and c.yes_ask_close is not None]
    if not quoted:
        return None
    last = max(quoted, key=lambda c: c.end)
    return float(last.yes_bid_close + last.yes_ask_close) / 2


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--execute", action="store_true")
    if not ap.parse_args(argv).execute:
        ap.error("refusing without --execute: the CFB totals holdout runs once")
    reasons = refuse_reasons(OUTPUT_DIR)
    if reasons:
        for r in reasons:
            log.error("refusing: %s", r)
        return 2
    try:
        prereg = require_registered(PREREG_DOC, PREREG_SECTION)
    except PreregistrationMissing as exc:
        log.error("refusing: %s", exc)
        return 2
    suite = unittest.defaultTestLoader.loadTestsFromNames(list(TESTS))
    if not unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite).wasSuccessful():
        log.error("tests failed before scoring")
        return 1

    client = KalshiClient(min_interval=0.3)
    fees = fetch_schedule(client, SERIES)
    f = lambda fn, price: float(fn(Decimal(str(round(price, 4)))))

    # Pool: every rung of every event dated on or before 2026-06-30.
    pool: list[scan.PoolRung] = []
    pool_events = scan.event_tickers(client, SERIES)
    for ev in pool_events:
        for m in scan.event_markets(client, SERIES, ev):
            settle, at = scan._settle(m)
            if settle in (0.0, 1.0) and at is not None:
                pool.append(scan.PoolRung(None, scan._floor(m), settle, at))
    log.info("pool: %d events, %d binary rungs", len(pool_events), len(pool))

    events = holdout_events(client)
    skipped: dict[str, int] = {}
    bump = lambda k: skipped.__setitem__(k, skipped.get(k, 0) + 1)
    schedules: dict = {}
    rows: list[dict] = []
    matched = 0
    for ev in events:
        code = assert_holdout(ev)
        markets = holdout_markets(client, ev)
        if not markets:
            bump("no markets"); continue
        start = scan.start_of(CELL, ev, code, schedules)
        if start is None:
            bump("start unmatched"); continue
        matched += 1
        t = start - LEAD
        regime = fees.regime_at(t)
        for m in sorted(markets, key=lambda m: m["ticker"]):
            q = Quote.from_api(m, datetime.now(timezone.utc))
            candles = holdout_candles(client, ev, q, t, start)
            ba = scan.quote_at(candles, t)
            if ba is None:
                bump("no fresh two-sided quote at t"); continue
            settle, settled_at = scan._settle(m)
            if settle is None:
                bump("unsettled"); continue
            bid, ask = ba
            close = latest_mid(candles, start)
            earlier = scan.quote_at(candles, t - scan.INPLAY_LOOKBACK)
            rows.append({
                "event": ev, "ticker": m["ticker"], "start": start.isoformat(), "t": t.isoformat(),
                "floor": scan._floor(m), "bid": bid, "ask": ask, "mid": (bid + ask) / 2, "settle": settle,
                "settled_at": None if settled_at is None else settled_at.isoformat(),
                "close_mid": close,
                "mid_t_minus_2h": None if earlier is None else sum(earlier) / 2,
                "volume_24h": float(sum(c.volume for c in candles if c.end <= t)),
                "taker_fee_yes": f(regime.taker_fee, ask), "taker_fee_no": f(regime.taker_fee, 1 - bid),
                "maker_fee_yes": f(regime.maker_fee, bid), "maker_fee_no": f(regime.maker_fee, 1 - ask),
                "fee_type": regime.fee_type,
            })

    # Holdout rungs join the pool only once their settlement is public before t.
    pool += [scan.PoolRung(None, r["floor"], r["settle"], datetime.fromisoformat(r["settled_at"]))
             for r in rows if r["settle"] in (0.0, 1.0) and r["settled_at"]]
    for r in rows:
        r["base"], r["base_fallback"] = scan.base_rate(pool, kind="total", flag=None, floor=r["floor"],
                                                       window=WINDOW, t=datetime.fromisoformat(r["t"]))
        r["p"] = forecast(r["mid"], r["base"])

    sanity = select_taker(rows, w=0.0)
    taker = select_taker(rows)
    maker = select_maker(rows)

    def summarize(trades: list[dict], cost_key: str = "cost") -> dict:
        by_game: dict[str, list[float]] = {}
        for tr in trades:
            x = r_of(tr["row"]["settle"], tr["side"], tr[cost_key])
            if x is not None:
                by_game.setdefault(tr["row"]["event"], []).append(x)
        flat = [x for v in by_game.values() for x in v]
        return {"n": len(flat), "games": len(by_game), "mean_R": statistics.mean(flat) if flat else None,
                "ci95": game_ci(by_game), "win_rate": sum(x > 0 for x in flat) / len(flat) if flat else None}

    taker_result = summarize(taker)
    clv: dict[str, list[float]] = {}
    for tr in taker:
        close = tr["row"]["close_mid"]
        if close is not None:
            side_close = close if tr["side"] == "yes" else 1 - close
            clv.setdefault(tr["row"]["event"], []).append(side_close - tr["entry"])
    flat_clv = [x for v in clv.values() for x in v]

    fills = []
    for tr in maker:
        r = tr["row"]
        t, start = datetime.fromisoformat(r["t"]), datetime.fromisoformat(r["start"])
        settled_at = None if r["settled_at"] is None else datetime.fromisoformat(r["settled_at"])
        prints = [(float(p.yes_price), float(p.count)) for p in client.trades(r["ticker"], t, start,
                                                                             settled_at=settled_at)]
        if fill(tr["side"], tr["entry"], prints):
            fills.append(tr)
    maker_result = summarize(fills)

    caps = [capacity(tr) for tr in taker]
    by_day: dict[str, float] = {}
    for tr, (_, dollars) in zip(taker, caps):
        day = datetime.fromisoformat(tr["row"]["start"]).astimezone(scan.NEW_YORK).date().isoformat()
        by_day[day] = by_day.get(day, 0.0) + dollars
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    median_daily = statistics.median(by_day.values()) if by_day else None

    binary = [r for r in rows if r["settle"] in (0.0, 1.0)]
    brier = lambda k: statistics.mean((r[k] - r["settle"]) ** 2 for r in binary) if binary else None
    moved = [abs(r["mid"] - r["mid_t_minus_2h"]) > scan.INPLAY_MOVE for r in rows if r["mid_t_minus_2h"] is not None]
    passed = verdict(taker_result["n"], taker_result["games"], taker_result["ci95"])
    report = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration": prereg,
        "frozen": {"w": W_BLEND, "margin": MARGIN, "max_spread": MAX_SPREAD, "window": WINDOW,
                   "lead_min": 60, "seed": SEED, "holdout": [HOLDOUT_START.isoformat(), HOLDOUT_END.isoformat()]},
        "coverage": {"holdout_events": len(events), "start_matched": matched,
                     "events_priced": len({r["event"] for r in rows}), "rungs_priced": len(rows),
                     "binary_rungs": len(binary), "fair_value_rungs": len(rows) - len(binary),
                     "skipped": skipped, "pool_rungs_before_holdout": len(pool) - len(binary),
                     "base_fallback_share": sum(r["base_fallback"] for r in rows) / len(rows) if rows else None,
                     "inplay_share_moved_gt_25c": sum(moved) / len(moved) if moved else None},
        "sanity_w0_commitments": len(sanity),
        "taker": {**taker_result, "yes": sum(tr["side"] == "yes" for tr in taker),
                  "no": sum(tr["side"] == "no" for tr in taker),
                  "mean_clv": statistics.mean(flat_clv) if flat_clv else None, "clv_ci95": game_ci(clv)},
        "maker": {"orders": len(maker), "filled": len(fills),
                  "fill_rate": len(fills) / len(maker) if maker else None, **maker_result},
        "brier": {"mid": brier("mid"), "base": brier("base"), "p": brier("p")},
        "replicated_weight": scan.weight_with_ci(rows, "base"),
        "capacity": {
            "per_trade_contracts_median": q([c for c, _ in caps], 0.5),
            "per_trade_contracts_p90": q([c for c, _ in caps], 0.9),
            "per_trade_dollars_median": q([d for _, d in caps], 0.5),
            "per_trade_dollars_p90": q([d for _, d in caps], 0.9),
            "daily_dollars_median": median_daily,
            "daily_dollars_max": max(by_day.values()) if by_day else None,
            "share_trades_rung_volume_lt_100": sum(tr["row"]["volume_24h"] < 100 for tr in taker) / len(taker) if taker else None,
            "research_only": median_daily is None or median_daily < DAILY_CAPACITY_FLOOR,
        },
        "void": len(sanity) > 0,
        "pass": passed and not sanity,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (OUTPUT_DIR / f"{OUTPUT_STEM}-{stamp}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    with (OUTPUT_DIR / f"{OUTPUT_STEM}-{stamp}.csv").open("w", newline="", encoding="utf-8") as fh:
        if rows:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
            wr.writeheader()
            wr.writerows(rows)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
