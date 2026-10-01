"""Market-efficiency scan: base-rate blend weight against the Kalshi mid.

    python -m jobs.scan_market_efficiency [--cells KXMLBGAME,KXNHLGAME]

docs/dev/market_efficiency_scan.md, specified before any run. Descriptive
only: no model, no trade, nothing written to the ledger. Per sport × market
type, does a player/team-agnostic base rate earn positive blend weight against
the mid at t = scheduled start − 60 min, and how liquid is the market?

**Two guards, asserted before every market request:** no `KXNFL*` series (no
untouched NFL season exists), and no event dated after 2026-06-30 (the
reserve, left unread as the next build's holdout).

Every API answer is cached under `.cache/scan/`, so an interrupted run resumes
without re-reading. Results go to `docs/dev/`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import random
import re
import statistics
import sys
import unicodedata
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Final, Sequence
from zoneinfo import ZoneInfo

from jobs.dev_props_blend import w_star
from venues.kalshi.client import Candle, KalshiClient, KalshiNotFound, Quote

log = logging.getLogger("valemont.scan_market_efficiency")

# --- fixed by the spec ---------------------------------------------------------
WINDOW_END: Final = date(2026, 6, 30)
LEAD: Final = timedelta(minutes=60)
STALE_AFTER: Final = timedelta(minutes=60)
VOLUME_WINDOW: Final = timedelta(hours=24)
INPLAY_LOOKBACK: Final = timedelta(hours=2)
INPLAY_MOVE: Final = 0.25
POOL_EVENTS: Final = 300
PRICED_EVENTS: Final = 150
RUNGS_PER_EVENT: Final = 5
BASE_MIN_N: Final = 20
SEED: Final = 20261001
DRAWS: Final = 2000
NEW_YORK: Final = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class Cell:
    sport: str
    market: str
    series: str
    kind: str          # winner | margin | total | prop
    window: float = 0.0


CELLS: Final = (
    Cell("CFB", "game", "KXNCAAFGAME", "winner"),
    Cell("CFB", "spread", "KXNCAAFSPREAD", "margin", 3.0),
    Cell("CFB", "total", "KXNCAAFTOTAL", "total", 3.0),
    Cell("NHL", "game", "KXNHLGAME", "winner"),
    Cell("NHL", "spread", "KXNHLSPREAD", "margin"),
    Cell("NHL", "total", "KXNHLTOTAL", "total"),
    Cell("NHL", "player goals", "KXNHLGOAL", "prop"),
    Cell("NHL", "player points", "KXNHLPTS", "prop"),
    Cell("NHL", "player assists", "KXNHLAST", "prop"),
    Cell("MLB", "game", "KXMLBGAME", "winner"),
    Cell("MLB", "spread", "KXMLBSPREAD", "margin"),
    Cell("MLB", "total", "KXMLBTOTAL", "total"),
    Cell("MLB", "hits", "KXMLBHIT", "prop"),
    Cell("MLB", "strikeouts", "KXMLBKS", "prop"),
    Cell("MLB", "total bases", "KXMLBTB", "prop"),
    Cell("MLB", "home runs", "KXMLBHR", "prop"),
    Cell("MLB", "hits+runs+RBIs", "KXMLBHRR", "prop"),
    Cell("Tennis", "ATP match", "KXATPMATCH", "winner"),
    Cell("Tennis", "WTA match", "KXWTAMATCH", "winner"),
    Cell("Tennis", "ATP total games", "KXATPGTOTAL", "total"),
    Cell("Tennis", "WTA total games", "KXWTAGTOTAL", "total"),
    Cell("Tennis", "ATP game spread", "KXATPGSPREAD", "margin"),
)

CACHE: Final = Path(".cache/scan")
OUTPUT_DIR: Final = Path("docs/dev")
OUTPUT_STEM: Final = "market-scan"


# ---------------------------------------------------------------------------
# Guards and ticker parsing
# ---------------------------------------------------------------------------

class ScopeViolation(RuntimeError):
    """A request would read NFL data or the reserve. Never caught."""


_GAME_CODE = re.compile(r"^(\d\d)([A-Z]{3})(\d\d)(\d{4})?(.+)$")


@dataclass(frozen=True, slots=True)
class GameCode:
    day: date
    hhmm: str | None      # scheduled start, ET, when the ticker carries it
    teams: str            # participant letters, first-listed then second


def parse_game_code(event_ticker: str) -> GameCode:
    """`KXMLBGAME-26AUG011507STLTOR` → (2026-08-01, '1507', 'STLTOR')."""
    parts = event_ticker.split("-", 1)
    m = _GAME_CODE.match(parts[1]) if len(parts) == 2 else None
    if m is None:
        raise ValueError(f"unparseable event ticker {event_ticker!r}")
    yy, mon, dd, hhmm, teams = m.groups()
    day = datetime.strptime(f"{yy}{mon}{dd}", "%y%b%d").date()
    return GameCode(day=day, hhmm=hhmm, teams=teams)


def assert_in_scope(series: str, event_ticker: str) -> GameCode:
    if series.upper().startswith("KXNFL") or event_ticker.upper().startswith("KXNFL"):
        raise ScopeViolation(f"NFL is out of scope: {event_ticker}")
    code = parse_game_code(event_ticker)
    if code.day > WINDOW_END:
        raise ScopeViolation(f"{event_ticker} is in the reserve (after {WINDOW_END})")
    return code


def yes_participant(market_ticker: str, event_ticker: str) -> str:
    """The participant code a winner or margin market's YES is about:
    `...-VGK` → 'VGK', `...-VGK2` → 'VGK'."""
    suffix = market_ticker[len(event_ticker) + 1:] if market_ticker.startswith(event_ticker + "-") else ""
    return suffix.rstrip("0123456789")


def second_listed(teams: str, participant: str) -> bool | None:
    """True if `participant` is the second code in `teams`, False if the
    first, None if it cannot be told (absent, or both ends match)."""
    if not participant:
        return None
    end, start = teams.endswith(participant), teams.startswith(participant)
    if end == start:
        return None
    return end


# ---------------------------------------------------------------------------
# Schedule matching (pure: the fetched schedules are passed in)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Scheduled:
    start: datetime       # UTC
    first: str            # away / first-listed code or surname
    second: str


ALIASES: Final = {
    "LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA", "WAS": "WSH",
    "OAK": "ATH", "ARI": "AZ", "CHW": "CWS", "KCR": "KC", "SDP": "SD", "SFG": "SF",
    "TBR": "TB", "WSN": "WSH", "LV": "VGK",
}


def _canon(code: str) -> str:
    c = re.sub(r"[^A-Z0-9]", "", code.upper())
    return ALIASES.get(c, c)


def codes_compatible(kalshi: str, official: str) -> bool:
    a, b = _canon(kalshi), _canon(official)
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 2 and (a.startswith(b) or b.startswith(a)))


def match_team_game(code: GameCode, games: Sequence[Scheduled]) -> Scheduled | None:
    """The one scheduled game whose two codes split `code.teams` (either
    order). Same ET date first; ±1 day only if the same day has none.
    `hhmm`, when present, must agree within 15 minutes. Ambiguous → None."""
    def fits(g: Scheduled) -> bool:
        for i in range(2, len(code.teams) - 1):
            a, b = code.teams[:i], code.teams[i:]
            if (codes_compatible(a, g.first) and codes_compatible(b, g.second)) or \
               (codes_compatible(a, g.second) and codes_compatible(b, g.first)):
                break
        else:
            return False
        if code.hhmm:
            et = g.start.astimezone(NEW_YORK)
            listed = et.replace(hour=int(code.hhmm[:2]), minute=int(code.hhmm[2:]))
            return abs((et - listed).total_seconds()) <= 15 * 60
        return True

    candidates = [g for g in games if fits(g)]
    same_day = [g for g in candidates if g.start.astimezone(NEW_YORK).date() == code.day]
    if len(same_day) == 1:
        return same_day[0]
    if same_day:
        return None
    near = [g for g in candidates if abs((g.start.astimezone(NEW_YORK).date() - code.day).days) <= 1]
    return near[0] if len(near) == 1 else None


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z]", "", text.upper())


def surname_forms(display_name: str) -> set[str]:
    """Every surname reading of the name, folded: 'Alex de Minaur' →
    {'MINAUR', 'DEMINAUR'}. The given name is never one, so 'Stefanos'
    cannot satisfy a code meant for a surname starting 'STE'."""
    tokens = display_name.split()
    starts = range(1, len(tokens)) if len(tokens) > 1 else range(1)
    return {_fold("".join(tokens[i:])) for i in starts} - {""}


def match_tennis(code: GameCode, matches: Sequence[Scheduled]) -> Scheduled | None:
    """Tennis codes are two three-letter surname prefixes (`TSIRIC`). The
    unique singles match within ±2 days whose players carry them, either
    order; ambiguous → None."""
    if len(code.teams) != 6:
        return None
    a, b = code.teams[:3], code.teams[3:]
    has = lambda name, p: any(f.startswith(p) for f in surname_forms(name))
    hits = [m for m in matches
            if abs((m.start.date() - code.day).days) <= 2
            and ((has(m.first, a) and has(m.second, b)) or (has(m.first, b) and has(m.second, a)))]
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------------------
# Prices, base rate, weight
# ---------------------------------------------------------------------------

def quote_at(candles: Sequence[Candle], t: datetime) -> tuple[float, float] | None:
    """(bid, ask) of the latest candle ending at or before t, at most 60 min
    old, two-sided and strictly inside (0, 1); else None."""
    quoted = [c for c in candles if c.end <= t and c.yes_bid_close is not None and c.yes_ask_close is not None]
    if not quoted:
        return None
    last = max(quoted, key=lambda c: c.end)
    if t - last.end > STALE_AFTER:
        return None
    bid, ask = float(last.yes_bid_close), float(last.yes_ask_close)
    return (bid, ask) if 0 < bid < ask < 1 else None


@dataclass(frozen=True, slots=True)
class PoolRung:
    flag: bool | None
    floor: float | None
    settle: float
    settled_at: datetime


def base_rate(pool: Sequence[PoolRung], *, kind: str, flag: bool | None, floor: float | None,
              window: float, t: datetime) -> tuple[float, bool]:
    """(b, fell_back). Mean settlement of pool rungs settled before t that
    share the key; 0.5 when fewer than 20 do, or the key is unknown."""
    if kind in ("winner", "margin") and flag is None:
        return 0.5, True
    if kind != "winner" and floor is None:
        return 0.5, True
    hits = []
    for r in pool:
        if r.settled_at >= t:
            continue
        if kind in ("winner", "margin") and r.flag != flag:
            continue
        if kind != "winner" and (r.floor is None or abs(r.floor - floor) > window + 1e-9):
            continue
        hits.append(r.settle)
    if len(hits) < BASE_MIN_N:
        return 0.5, True
    return sum(hits) / len(hits), False


def weight_with_ci(rows: Sequence[dict], key: str) -> dict:
    """Closed-form Brier-optimal blend weight of `key` against the mid over
    binary rungs, clipped to [0, 1], with a 95% bootstrap over whole events."""
    per: dict[str, list[float]] = {}
    for r in rows:
        if r["settle"] not in (0.0, 1.0):
            continue
        e, d = r["mid"] - r["settle"], r[key] - r["mid"]
        a = per.setdefault(r["event"], [0.0, 0.0, 0.0])
        a[0] += e * e; a[1] += e * d; a[2] += d * d
    events = list(per.values())
    if len(events) < 2:
        return {"w": None, "ci95": None, "n_events": len(events), "positive": False}
    tot = [sum(g[i] for g in events) for i in range(3)]
    rng = random.Random(SEED)
    draws = []
    for _ in range(DRAWS):
        s = [0.0, 0.0, 0.0]
        for _ in events:
            g = rng.choice(events)
            s[0] += g[0]; s[1] += g[1]; s[2] += g[2]
        draws.append(w_star(*s))
    draws.sort()
    ci = [draws[int(0.025 * DRAWS)], draws[int(0.975 * DRAWS) - 1]]
    return {"w": w_star(*tot), "ci95": ci, "n_events": len(events), "positive": ci[0] > 1e-9}


def liquidity(attempted: int, rows: Sequence[dict]) -> dict:
    med = lambda xs: statistics.median(xs) if xs else None
    spreads = [r["ask"] - r["bid"] for r in rows]
    return {
        "rungs_attempted": attempted,
        "quoted_share": len(rows) / attempted if attempted else None,
        "median_spread": med(spreads),
        "share_spread_le_3c": sum(s <= 0.03 + 1e-9 for s in spreads) / len(spreads) if spreads else None,
        "share_spread_le_8c": sum(s <= 0.08 + 1e-9 for s in spreads) / len(spreads) if spreads else None,
        "median_volume_24h": med([r["volume_24h"] for r in rows]),
        "median_open_interest": med([r["open_interest"] for r in rows if r["open_interest"] is not None]),
    }


# ---------------------------------------------------------------------------
# Network, cached
# ---------------------------------------------------------------------------

def _cached(path: Path, fetch: Callable[[], Any]) -> Any:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    value = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


def _http_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "valemont-agents research (read-only)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def event_tickers(client: KalshiClient, series: str) -> list[str]:
    """Every event ticker in the series dated inside the window. Event
    objects carry names and dates only; reserve tickers are dropped here."""
    def fetch() -> list[str]:
        out, cursor = [], None
        while True:
            body = client.get("/events", series_ticker=series, limit=200, cursor=cursor)
            page = body.get("events", [])
            out += [e["event_ticker"] for e in page]
            cursor = body.get("cursor")
            if not cursor or not page:
                return out
    keep = []
    for ev in _cached(CACHE / series / "events.json", fetch):
        try:
            if parse_game_code(ev).day <= WINDOW_END:
                keep.append(ev)
        except ValueError:
            continue
    return sorted(keep)


def event_markets(client: KalshiClient, series: str, event: str) -> list[dict]:
    assert_in_scope(series, event)
    def fetch() -> list[dict]:
        body = client.get("/historical/markets", event_ticker=event, limit=1000)
        markets = body.get("markets", [])
        if not markets:
            markets = client.get("/markets", event_ticker=event, limit=1000).get("markets", [])
        return markets
    return _cached(CACHE / series / "markets" / f"{event}.json", fetch)


def market_candles(client: KalshiClient, series: str, event: str, q: Quote, t: datetime) -> list[Candle]:
    assert_in_scope(series, event)
    def fetch() -> list[dict]:
        cs = client.candles(series, q.ticker, t - VOLUME_WINDOW, t, 1, settled_at=q.settlement_ts)
        return [{"end": c.end.isoformat(), "bid": None if c.yes_bid_close is None else str(c.yes_bid_close),
                 "ask": None if c.yes_ask_close is None else str(c.yes_ask_close),
                 "volume": str(c.volume), "oi": None if c.open_interest is None else str(c.open_interest)}
                for c in cs]
    raw = _cached(CACHE / series / "candles" / f"{q.ticker}.json", fetch)
    return [Candle(end=datetime.fromisoformat(c["end"]),
                   yes_bid_close=None if c["bid"] is None else Decimal(c["bid"]),
                   yes_ask_close=None if c["ask"] is None else Decimal(c["ask"]),
                   trade_low=None, trade_high=None, trade_close=None,
                   volume=Decimal(c["volume"]), open_interest=None if c["oi"] is None else Decimal(c["oi"]))
            for c in raw]


def schedule_for(sport: str, day: date) -> list[Scheduled]:
    """Official start times on `day` (UTC dates for tennis, ET otherwise)."""
    path = CACHE / "schedule" / f"{sport}-{day.isoformat()}.json"
    if sport == "MLB":
        raw = _cached(path, lambda: _http_json(
            f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={day}&hydrate=team"))
        out = []
        for d in raw.get("dates", []):
            for g in d.get("games", []):
                if g.get("status", {}).get("detailedState") in ("Postponed", "Cancelled"):
                    continue
                out.append(Scheduled(datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00")),
                                     g["teams"]["away"]["team"].get("abbreviation", ""),
                                     g["teams"]["home"]["team"].get("abbreviation", "")))
        return out
    if sport == "NHL":
        raw = _cached(path, lambda: _http_json(f"https://api-web.nhle.com/v1/schedule/{day}"))
        return [Scheduled(datetime.fromisoformat(g["startTimeUTC"].replace("Z", "+00:00")),
                          g["awayTeam"]["abbrev"], g["homeTeam"]["abbrev"])
                for w in raw.get("gameWeek", []) if w.get("date") == day.isoformat()
                for g in w.get("games", [])]
    if sport == "CFB":
        raw = _cached(path, lambda: _http_json(
            "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
            f"?dates={day:%Y%m%d}&groups=80&limit=500"))
        out = []
        for ev in raw.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            teams = {c.get("homeAway"): (c.get("team") or {}).get("abbreviation", "") for c in comp.get("competitors", [])}
            if "home" in teams and "away" in teams:
                out.append(Scheduled(datetime.fromisoformat(ev["date"].replace("Z", "+00:00")),
                                     teams["away"], teams["home"]))
        return out
    raise ValueError(sport)


def tennis_schedule(tour: str, day: date) -> list[Scheduled]:
    path = CACHE / "schedule" / f"{tour}-{day.isoformat()}.json"
    raw = _cached(path, lambda: _http_json(
        f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={day:%Y%m%d}"))
    out = []
    for ev in raw.get("events", []):
        for grouping in ev.get("groupings", []):
            for comp in grouping.get("competitions", []):
                players = [(c.get("athlete") or {}).get("displayName") for c in comp.get("competitors", [])]
                if len(players) != 2 or not all(players) or not comp.get("timeValid"):
                    continue
                out.append(Scheduled(datetime.fromisoformat(comp["date"].replace("Z", "+00:00")),
                                     players[0], players[1]))
    return out


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def _floor(m: dict) -> float | None:
    v = m.get("floor_strike")
    return None if v is None else float(v)


def _settle(m: dict) -> tuple[float | None, datetime | None]:
    v = m.get("settlement_value_dollars")
    at = m.get("settlement_ts")
    return (None if v is None else float(v),
            None if not at else datetime.fromisoformat(at.replace("Z", "+00:00")))


def start_of(cell: Cell, event: str, code: GameCode, cache: dict) -> datetime | None:
    if cell.sport == "Tennis":
        tour = "atp" if cell.series.startswith("KXATP") else "wta"
        games: list[Scheduled] = []
        for k in range(-2, 3):
            d = code.day + timedelta(days=k)
            games += cache.setdefault((tour, d), tennis_schedule(tour, d))
        hit = match_tennis(code, games)
    else:
        games = []
        for k in (-1, 0, 1):
            d = code.day + timedelta(days=k)
            games += cache.setdefault((cell.sport, d), schedule_for(cell.sport, d))
        hit = match_team_game(code, games)
    return None if hit is None else hit.start


def scan_cell(client: KalshiClient, cell: Cell, schedules: dict) -> tuple[dict, list[dict]]:
    rng = random.Random(f"{SEED}:{cell.series}")
    events = event_tickers(client, cell.series)
    pool_events = rng.sample(events, min(POOL_EVENTS, len(events)))
    priced_events = pool_events[:PRICED_EVENTS]
    counts: dict[str, int] = {}
    bump = lambda k: counts.__setitem__(k, counts.get(k, 0) + 1)

    pool: list[PoolRung] = []
    listed: dict[str, list[dict]] = {}
    for ev in pool_events:
        code = assert_in_scope(cell.series, ev)
        markets = event_markets(client, cell.series, ev)
        listed[ev] = markets
        for m in markets:
            settle, at = _settle(m)
            if settle not in (0.0, 1.0) or at is None:
                continue
            flag = second_listed(code.teams, yes_participant(m["ticker"], ev))
            pool.append(PoolRung(flag, _floor(m), settle, at))

    rows: list[dict] = []
    attempted = 0
    for ev in priced_events:
        code = assert_in_scope(cell.series, ev)
        markets = sorted(listed[ev], key=lambda m: m["ticker"])
        if not markets:
            bump("no markets"); continue
        start = start_of(cell, ev, code, schedules)
        if start is None:
            bump("start unmatched"); continue
        t = start - LEAD
        chosen = [rng.choice(markets)] if cell.kind == "winner" else \
            rng.sample(markets, min(RUNGS_PER_EVENT, len(markets)))
        for m in chosen:
            attempted += 1
            q = Quote.from_api(m, datetime.now(timezone.utc))
            candles = market_candles(client, cell.series, ev, q, t)
            ba = quote_at(candles, t)
            if ba is None:
                bump("no fresh two-sided quote at t"); continue
            bid, ask = ba
            mid = (bid + ask) / 2
            settle, _ = _settle(m)
            if settle is None:
                bump("unsettled"); continue
            flag = second_listed(code.teams, yes_participant(m["ticker"], ev))
            b, fell_back = base_rate(pool, kind=cell.kind, flag=flag, floor=_floor(m),
                                     window=cell.window, t=t)
            earlier = quote_at(candles, t - INPLAY_LOOKBACK)
            before = [c for c in candles if c.end <= t]
            rows.append({
                "series": cell.series, "event": ev, "ticker": m["ticker"], "start": start.isoformat(),
                "t": t.isoformat(), "floor": _floor(m), "flag": flag, "bid": bid, "ask": ask, "mid": mid,
                "settle": settle, "base": b, "base_fallback": fell_back, "half": 0.5,
                "mid_t_minus_2h": None if earlier is None else (earlier[0] + earlier[1]) / 2,
                "volume_24h": float(sum(c.volume for c in before)),
                "open_interest": None if not before or before[-1].open_interest is None
                else float(before[-1].open_interest),
            })

    binary = [r for r in rows if r["settle"] in (0.0, 1.0)]
    brier = lambda k: statistics.mean((r[k] - r["settle"]) ** 2 for r in binary) if binary else None
    moved = [abs(r["mid"] - r["mid_t_minus_2h"]) > INPLAY_MOVE for r in rows if r["mid_t_minus_2h"] is not None]
    result = {
        "sport": cell.sport, "market": cell.market, "series": cell.series, "kind": cell.kind,
        "window_events": len(events), "pool_events": len(pool_events), "pool_rungs": len(pool),
        "priced_events": len(priced_events), "rungs": len(rows), "binary_rungs": len(binary),
        "fair_value_rungs": len(rows) - len(binary),
        "base_fallback_share": sum(r["base_fallback"] for r in rows) / len(rows) if rows else None,
        "skipped": counts,
        "brier": {"mid": brier("mid"), "base": brier("base"), "half": brier("half")},
        "weight_base": weight_with_ci(rows, "base"),
        "weight_half": weight_with_ci(rows, "half"),
        "liquidity": liquidity(attempted, rows),
        "inplay_check_share_moved_gt_25c": sum(moved) / len(moved) if moved else None,
    }
    return result, rows


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cells", default="", help="comma-separated series; default all")
    args = ap.parse_args(argv)
    wanted = {s for s in args.cells.split(",") if s}
    cells = [c for c in CELLS if not wanted or c.series in wanted]

    client = KalshiClient(min_interval=0.3)
    schedules: dict = {}
    results, all_rows = [], []
    for cell in cells:
        log.info("cell %s %s (%s)", cell.sport, cell.market, cell.series)
        fee_type = client.series(cell.series).get("fee_type")
        result, rows = scan_cell(client, cell, schedules)
        result["fee_type"] = fee_type
        results.append(result)
        all_rows += rows
        w = result["weight_base"]
        log.info("  %s rungs; w_base %s CI %s; liquidity %s", result["rungs"], w["w"], w["ci95"],
                 result["liquidity"])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows_path = OUTPUT_DIR / f"{OUTPUT_STEM}-{stamp}.csv"
    with rows_path.open("w", newline="", encoding="utf-8") as fh:
        if all_rows:
            w = csv.DictWriter(fh, fieldnames=list(all_rows[0]))
            w.writeheader()
            w.writerows(all_rows)
    report = {"run_utc": datetime.now(timezone.utc).isoformat(), "window_end": WINDOW_END.isoformat(),
              "lead_minutes": LEAD.total_seconds() / 60, "seed": SEED,
              "rows_sha256": hashlib.sha256(rows_path.read_bytes()).hexdigest(), "cells": results}
    (OUTPUT_DIR / f"{OUTPUT_STEM}-{stamp}.json").write_text(json.dumps(report, indent=2, default=str),
                                                             encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
