"""The Kalshi board: every open sports market in the next 36 h (Chat 8 Phase 1;
db/071; docs/kalshi_board.md). Fetch and parse only: no SQL, no orders, no
key. sports/kalshi_board.py runs the jobs and writes through core/ledger.py.

The fetch path (owner, Nov 6), never the raw open-markets list (MASTER_PLAN
§7, the MVE flood):

1. ``GET /milestones?category=Sports&minimum_start_date=<now>``, every page:
   each sports fixture's start and the event tickers it names (primary and
   related). The window is the milestones starting in [now, now + 36 h).
2. ``GET /series/{t}`` for each series that owns a window event, once per
   Chicago day (the 09:30 pull, or the first after a restart; later pulls
   that day read only series new to the window): title, tags, fee_type and
   fee_multiplier. Not the bulk ``/series?category=Sports`` list, whose one
   6.5 MB response took the process to +41 MB (measured Nov 6).
3. ``GET /multivariate_event_collections?status=open``: which events a combo
   may use, and whether only YES.
4. ``GET /markets?…&status=open``, filtered so no combo market can arrive:
   ``event_ticker=E`` (ONE ticker; a comma-separated list answers 200 with
   no markets, verified Nov 6) for a series with at most PER_EVENT_MAX window
   events, else ``series_ticker=S``, its out-of-window events dropped as
   parsed. Processed one page at a time; the raw JSON is dropped before the
   next call. A pull whose window has events but returns no market fails
   loud (the filter changed shape).

Every field read is shape-checked (venues/kalshi/events.py req/opt): a
missing or mistyped one raises KalshiShapeError naming the series and the
path, so every run is also the contract test of these four shapes. A fee type
or multiplier FeeRegime does not model fails the run with the series named;
no series is ever skipped silently.

Parsing a market (owner-approved kinds, Nov 6):

* ``prop``: the market names a player (``primary_participant_key`` or a
  ``custom_strike`` key ending ``_player``).
* ``game_line``: otherwise, a series whose ticker ends GAME, MATCH, FIGHT,
  SPREAD or TOTAL (team totals included), or a period (1Q–4Q, 1H, 2H,
  1P–9P), with or without a SPREAD/TOTAL after it. A half or period winner is
  a game line.
* ``other``: the rest (correct score, overtime, win margin, points leader,
  race-to, double result, outrights).

subject, stat and line come from the fields Kalshi sends (``floor_strike`` is
the line; the title "Naz Reid: 10+ rebounds" gives subject and stat). A
market that cannot be parsed stays on the board with nulls and counts as a
parse miss for its series.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Iterator, Sequence

from venues.kalshi.client import USER_AGENT, KalshiClient, KalshiError, KalshiUnreachable, dec, ts
from venues.kalshi.events import KalshiShapeError, opt, req
from venues.kalshi.fees import FeeRegime, UnsupportedFeeType

WINDOW = timedelta(hours=36)
#: The light refresh re-prices events starting within this.
REFRESH_WITHIN = timedelta(hours=6)
#: A series with at most this many wanted events is read event by event;
#: a larger one in one series query (market_batches).
PER_EVENT_MAX = 2
PAGE = 200
#: Markets per page: ~2.6 KB each with their rules text, so ~0.26 MB a page
#: (200 measured +21.2 MB peak over the pull's start; the budget is +20).
MARKETS_PAGE = 100
MAX_PAGES = 60
#: Variant suffixes: a series ending so is the same stat on another ladder.
VARIANTS = (("PROT", "protected"),)
_LINE_TAIL = re.compile(r"(GAME|MATCH|FIGHT|SPREAD|TOTAL|[1-4]Q|[12]H|[1-9]P)$")
_PERIOD = re.compile(r"^(Full Game|[^:]{0,30}(Half|Quarter|Period|Set|Inning|Map)[^:]{0,20}): ")
_NPLUS = re.compile(r"^(?P<subject>.+?): (?P<n>\d+(?:\.\d+)?)\+ (?P<stat>.+?)\s*$")
_WINS_BY = re.compile(r"^(?P<subject>.+?) wins by (?:over|more than) (?P<n>\d+(?:\.\d+)?) ")
_OVER = re.compile(r"^Over (?P<n>\d+(?:\.\d+)?) ")
#: "Virginia Tech over 37.5 points scored" (a team total).
_TEAM_OVER = re.compile(r"^(?P<subject>.+?) over (?P<n>\d+(?:\.\d+)?) ")
SUBJECT_MAX, STAT_MAX, LABEL_MAX, TITLE_MAX = 120, 60, 160, 200


# ------------------------------------------------------------- transport

class Counting:
    """The client's transport, counting calls, 429s and raw bytes (Kalshi's
    side; not Supabase egress). The raw body is parsed and dropped here."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.calls = 0
        self.limited = 0
        self.bytes = 0
        self.timeout = timeout

    def __call__(self, url: str) -> tuple[int, Any]:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        self.calls += 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                self.limited += 1
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KalshiUnreachable(f"{url.split('?')[0]} unreachable: {exc}") from exc
        self.bytes += len(raw)
        try:
            return status, json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise KalshiError(f"{url.split('?')[0]} returned non-JSON") from exc


# ------------------------------------------------------------- shapes

@dataclass(frozen=True)
class Game:
    """One milestone: a fixture and every event ticker it names."""

    game_ticker: str
    title: str
    starts_at: datetime
    label: str | None
    kind: str | None
    events: tuple[str, ...]


@dataclass(frozen=True)
class Series:
    ticker: str
    title: str
    sport: str
    tags: tuple[str, ...]
    category: str
    regime: FeeRegime


@dataclass(frozen=True)
class Combo:
    collections: tuple[str, ...]
    yes_only: bool
    #: associated_events[].size_max: markets of this event one combo may use.
    #: None = absent on the event (no stated per-event cap); never 0 or 1.
    size_max: int | None = None
    #: len(associated_events[].active_quoters); None when the field is absent.
    quoters: int | None = None


def _min_cap(a: int | None, b: int | None) -> int | None:
    """Two collections' caps for one event: the tighter stated one; absent
    only when neither states one."""
    if a is None:
        return b
    return a if b is None else min(a, b)


def _max_count(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    return a if b is None else max(a, b)


def merge_combo(have: Combo | None, new: Combo) -> Combo:
    if have is None:
        return new
    return Combo(have.collections + new.collections, have.yes_only or new.yes_only,
                 _min_cap(have.size_max, new.size_max), _max_count(have.quoters, new.quoters))


@dataclass
class Collections:
    """Open multivariate collections, reduced to what the board needs."""

    by_event: dict[str, Combo] = field(default_factory=dict)
    #: collection ticker -> its flags and leg limits (report and ctx).
    meta: dict[str, dict[str, Any]] = field(default_factory=dict)


def series_of(event_ticker: str) -> str:
    return event_ticker.split("-", 1)[0]


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_milestones(body: Any, since: datetime, until: datetime) -> tuple[list[Game], str | None]:
    """One /milestones page: the games starting in [since, until)."""
    src = "milestones"
    items = req(body, "milestones", list, src, "$")
    cursor = opt(body, "cursor", str, src, "$") or None
    games: list[Game] = []
    for i, m in enumerate(items):
        p = f"$.milestones[{i}]"
        start_raw = req(m, "start_date", str, src, p)
        try:
            start = ts(start_raw)
        except KalshiError as exc:
            raise KalshiShapeError(src, f"{p}.start_date", str(exc)) from None
        if start is None or not (since <= start < until):
            continue
        details = opt(m, "details", dict, src, p) or {}
        primary = opt(m, "primary_event_tickers", list, src, p) or []
        related = opt(m, "related_event_tickers", list, src, p) or []
        tickers: list[str] = []
        for j, t in enumerate([*primary, *related]):
            if not isinstance(t, str) or not t:
                raise KalshiShapeError(src, f"{p}.event_tickers[{j}]", f"is {type(t).__name__}")
            if t not in tickers:
                tickers.append(t)
        if not tickers:
            continue
        main = opt(details, "main_game_event_ticker", str, src, f"{p}.details")
        games.append(Game(
            game_ticker=main or tickers[0],
            title=(opt(m, "title", str, src, p) or main or tickers[0]).strip()[:TITLE_MAX],
            starts_at=start,
            label=(details.get("league") or details.get("tour") or details.get("competition")
                   if isinstance(details.get("league") or details.get("tour") or details.get("competition"), str)
                   else None),
            kind=opt(m, "type", str, src, p),
            events=tuple(tickers),
        ))
    return games, cursor


def milestones(client: KalshiClient, since: datetime, until: datetime) -> list[Game]:
    """Every sports game starting in [since, until), every page."""
    out: list[Game] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        body = client.get("/milestones", category="Sports", minimum_start_date=_iso(since), limit=PAGE,
                          cursor=cursor)
        games, cursor = parse_milestones(body, since, until)
        del body
        out.extend(games)
        if not cursor:
            return out
    raise KalshiShapeError("milestones", "$.cursor", f"still paging after {MAX_PAGES} pages")


def window_events(games: Iterable[Game]) -> dict[str, Game]:
    """event ticker -> its game. An event named by two milestones keeps the
    one that names it first as primary or main, else the earliest."""
    out: dict[str, Game] = {}
    for g in sorted(games, key=lambda g: g.starts_at):
        for t in g.events:
            have = out.get(t)
            if have is None or (have.game_ticker != t and g.game_ticker == t):
                out[t] = g
    return out


def sport_of(tags: Sequence[str], fallback: str | None) -> str:
    """Kalshi's first tag ('Soccer', 'Basketball'); else the milestone type's
    sport ('basketball_game' -> 'Basketball'); else 'Other'."""
    for t in tags:
        if t and t not in ("Olympics",):
            return t[:40]
    if tags:
        return tags[0][:40]
    if fallback:
        return fallback.split("_", 1)[0].replace("-", " ").title()[:40]
    return "Other"


def parse_series(raw: Any, fetched_at: datetime, src: str = "series") -> tuple[str, dict[str, Any]]:
    """One series object -> (ticker, its fields). Fee type and multiplier are
    checked by FeeRegime here: a type or multiplier it does not model raises
    UnsupportedFeeType naming the series (the run fails; owner, Nov 6)."""
    ticker = req(raw, "ticker", str, src, "$")
    title = req(raw, "title", str, ticker, "$")
    fee_type = req(raw, "fee_type", str, ticker, "$")
    mult_raw = req(raw, "fee_multiplier", (int, float, str), ticker, "$")
    try:
        mult = Decimal(str(mult_raw))
    except Exception:  # noqa: BLE001 - any non-number is a shape error
        raise KalshiShapeError(ticker, "$.fee_multiplier", f"not a number: {mult_raw!r}") from None
    if not (Decimal(0) < mult <= Decimal(10)):
        raise UnsupportedFeeType(f"{ticker}: fee_multiplier {mult} is outside (0, 10]. Refusing to price it.")
    regime = FeeRegime(series=ticker, fee_type=fee_type, multiplier=mult, effective_from=None,
                       fetched_at=fetched_at)
    tags = opt(raw, "tags", list, ticker, "$") or []
    return ticker, {"title": title, "tags": tuple(str(t) for t in tags if isinstance(t, str)),
                    "category": opt(raw, "category", str, ticker, "$") or "", "regime": regime}


def series_info(client: KalshiClient, wanted: set[str], fallback_sport: dict[str, str | None],
                fetched_at: datetime, known: dict[str, Series] | None = None) -> dict[str, Series]:
    """The `wanted` series: those in `known` (read earlier the same Chicago
    day, sports/kalshi_board.py) as they are, each other one from
    ``GET /series/{t}`` (~2 KB). Never the bulk ``/series?category=Sports``
    list: one 6.5 MB response of 4,156 series took the process to +41 MB
    while it was held (measured Nov 6; the pull's budget is +20 MB). One
    Kalshi does not know fails the run, named."""
    out: dict[str, Series] = {t: s for t, s in (known or {}).items() if t in wanted}
    for t in sorted(wanted - set(out)):
        one = client.get(f"/series/{t}")
        ticker, f = parse_series(req(one, "series", dict, t, "$"), fetched_at, t)
        if ticker != t:
            raise KalshiShapeError(t, "$.series.ticker", f"is {ticker[:40]!r}")
        out[t] = Series(t, f["title"].strip()[:TITLE_MAX], sport_of(f["tags"], fallback_sport.get(t)),
                        f["tags"], f["category"], f["regime"])
    return out


def parse_collections(body: Any, keep: set[str] | None = None) -> tuple[Collections, str | None]:
    """One /multivariate_event_collections page. `keep` limits by_event to
    the window's events (the rest are dropped as read)."""
    src = "collections"
    items = req(body, "multivariate_contracts", list, src, "$")
    cursor = opt(body, "cursor", str, src, "$") or None
    out = Collections()
    for i, c in enumerate(items):
        p = f"$.multivariate_contracts[{i}]"
        ticker = req(c, "collection_ticker", str, src, p)
        assoc = req(c, "associated_event_tickers", list, src, p)
        per_event: dict[str, tuple[bool, int | None, int | None]] = {}
        for j, a in enumerate(opt(c, "associated_events", list, src, p) or []):
            q = f"{p}.associated_events[{j}]"
            t = req(a, "ticker", str, src, q)
            cap = opt(a, "size_max", int, src, q)
            if cap is not None and cap < 0:
                raise KalshiShapeError(src, f"{q}.size_max", f"is negative ({cap})")
            quoters = opt(a, "active_quoters", list, src, q)
            per_event[t] = (bool(opt(a, "is_yes_only", bool, src, q)), cap,
                            None if quoters is None else len(quoters))
        out.meta[ticker] = {
            "series_ticker": opt(c, "series_ticker", str, src, p),
            "size_min": opt(c, "size_min", int, src, p), "size_max": opt(c, "size_max", int, src, p),
            "is_single_market_per_event": opt(c, "is_single_market_per_event", bool, src, p),
            "is_all_yes": opt(c, "is_all_yes", bool, src, p), "is_ordered": opt(c, "is_ordered", bool, src, p),
            "open_date": opt(c, "open_date", str, src, p), "close_date": opt(c, "close_date", str, src, p),
            "events": len(assoc),
        }
        for j, t in enumerate(assoc):
            if not isinstance(t, str):
                raise KalshiShapeError(src, f"{p}.associated_event_tickers[{j}]", f"is {type(t).__name__}")
            if keep is not None and t not in keep:
                continue
            yes_only, cap, quoters = per_event.get(t, (False, None, None))
            out.by_event[t] = merge_combo(out.by_event.get(t), Combo((ticker,), yes_only, cap, quoters))
    return out, cursor


def collections(client: KalshiClient, keep: set[str] | None = None) -> Collections:
    out = Collections()
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        body = client.get("/multivariate_event_collections", status="open", limit=PAGE, cursor=cursor)
        page, cursor = parse_collections(body, keep)
        del body
        out.meta.update(page.meta)
        for t, c in page.by_event.items():
            out.by_event[t] = merge_combo(out.by_event.get(t), c)
        if not cursor:
            return out
    raise KalshiShapeError("collections", "$.cursor", f"still paging after {MAX_PAGES} pages")


# ------------------------------------------------------------- markets

def variant_of(series: str) -> tuple[str, str | None]:
    """(the series without its variant suffix, the variant or None)."""
    for suffix, name in VARIANTS:
        if series.endswith(suffix) and len(series) > len(suffix) + 2:
            return series[: -len(suffix)], name
    return series, None


def is_player(raw: dict[str, Any]) -> bool:
    key = raw.get("primary_participant_key")
    if isinstance(key, str) and key.endswith("_player"):
        return True
    strike = raw.get("custom_strike")
    return isinstance(strike, dict) and any(isinstance(k, str) and k.endswith("_player") for k in strike)


def market_kind(series: str, raw: dict[str, Any]) -> str:
    if is_player(raw):
        return "prop"
    base, _ = variant_of(series)
    return "game_line" if _LINE_TAIL.search(base) else "other"


@dataclass(frozen=True)
class Parsed:
    subject: str | None
    stat: str | None
    miss: bool


def _clip(s: str | None, n: int) -> str | None:
    s = (s or "").strip()
    return s[:n] if s else None


def parse_subject(kind: str, title: str, sub_title: str | None, strike_type: str | None,
                  line: Decimal | None) -> Parsed:
    """subject and stat from the title and sub-title. A miss: a prop without
    a subject or a stat, or a game line without a stat."""
    text = _PERIOD.sub("", title.strip(), count=1)
    if kind == "game_line":
        # The sub-title is Kalshi's canonical form for every period ("X wins
        # by over 7.5 points", "Over 66.5 points", "X over 6.5 points"); the
        # title varies ("X wins 1H by over", "1st Quarter: Over ...").
        for t in (s for s in ((sub_title or "").strip(), text) if s):
            if m := _WINS_BY.match(t):
                return Parsed(_clip(m["subject"], SUBJECT_MAX), "spread", False)
            if m := _TEAM_OVER.match(t):
                return Parsed(_clip(m["subject"], SUBJECT_MAX), "team_total", False)
            if m := _NPLUS.match(t):
                return Parsed(_clip(m["subject"], SUBJECT_MAX), "team_total", False)
            if _OVER.match(t):
                return Parsed(None, "total", False)
        if strike_type == "structured" or line is None:
            return Parsed(_clip(sub_title, SUBJECT_MAX), "winner", False)
        return Parsed(_clip(sub_title, SUBJECT_MAX), None, True)
    if kind == "prop":
        if m := _NPLUS.match(text):
            return Parsed(_clip(m["subject"], SUBJECT_MAX), _clip(m["stat"].lower(), STAT_MAX), False)
        if ": " in text:
            subject, rest = text.split(": ", 1)
            return Parsed(_clip(subject, SUBJECT_MAX), _clip(rest.lower(), STAT_MAX), False)
        subject = _clip((sub_title or "").split(":", 1)[0], SUBJECT_MAX)
        return Parsed(subject, None, True)
    return Parsed(_clip(sub_title, SUBJECT_MAX), None, False)


def _price(raw: dict[str, Any], key: str, ticker: str, path: str) -> Decimal | None:
    v = opt(raw, key, str, ticker, path)
    try:
        return dec(v)
    except KalshiError as exc:
        raise KalshiShapeError(ticker, f"{path}.{key}", str(exc)) from None


@dataclass
class Batch:
    """One /markets fetch, parsed: market rows by event, and the counters."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    #: event ticker -> (min close_time, min expected_expiration_time)
    times: dict[str, tuple[datetime | None, datetime | None]] = field(default_factory=dict)
    misses: Counter = field(default_factory=Counter)
    no_mismatch: int = 0
    checked: int = 0


def parse_market(raw: Any, series: Series, path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """One market -> (its board row, its extras: close/expiry, the NO
    complement check, the parse miss). Shape-checked."""
    s = series.ticker
    ticker = req(raw, "ticker", str, s, path)
    event = req(raw, "event_ticker", str, s, path)
    if not ticker.startswith(event + "-"):
        raise KalshiShapeError(s, f"{path}.ticker", f"{ticker[:60]!r} is not under {event[:60]!r}")
    title = req(raw, "title", str, s, path)
    sub = opt(raw, "yes_sub_title", str, s, path)
    yes_bid, yes_ask = _price(raw, "yes_bid_dollars", s, path), _price(raw, "yes_ask_dollars", s, path)
    no_bid, no_ask = _price(raw, "no_bid_dollars", s, path), _price(raw, "no_ask_dollars", s, path)
    volume = _price(raw, "volume_fp", s, path)
    oi = _price(raw, "open_interest_fp", s, path)
    line = raw.get("floor_strike")
    if line is not None and not isinstance(line, (int, float, str)) or isinstance(line, bool):
        raise KalshiShapeError(s, f"{path}.floor_strike", f"is {type(line).__name__}")
    line_d = dec(line) if line is not None else None
    strike = opt(raw, "custom_strike", dict, s, path)
    del strike  # read for the kind only (is_player), never stored: Kalshi's ids
    strike_type = opt(raw, "strike_type", str, s, path)
    close = req(raw, "close_time", str, s, path)
    expiry = opt(raw, "expected_expiration_time", str, s, path)
    try:
        close_t, expiry_t = ts(close), ts(expiry)
    except KalshiError as exc:
        raise KalshiShapeError(s, f"{path}.close_time", str(exc)) from None
    kind = market_kind(s, raw)
    _, variant = variant_of(s)
    parsed = parse_subject(kind, title, sub, strike_type, line_d)
    fee = series.regime.taker_fee(yes_ask) if yes_ask is not None and Decimal(0) < yes_ask < Decimal(1) else None
    row = {
        "market_ticker": ticker, "event_ticker": event, "market_kind": kind, "variant": variant,
        "subject": parsed.subject, "stat": parsed.stat, "line": None if line_d is None else str(line_d),
        "label": _clip(sub or title, LABEL_MAX),
        "yes_bid": None if yes_bid is None else str(yes_bid), "yes_ask": None if yes_ask is None else str(yes_ask),
        "volume": None if volume is None else str(volume), "open_interest": None if oi is None else str(oi),
        "taker_fee_at_ask": None if fee is None else str(fee),
    }
    complement = None
    if None not in (yes_bid, yes_ask, no_bid, no_ask):
        complement = no_ask == 1 - yes_bid and no_bid == 1 - yes_ask
    extras = {"close_time": close_t, "expiry": expiry_t, "complement": complement, "miss": parsed.miss}
    return row, extras


def parse_markets_page(body: Any, series: dict[str, Series], batch: Batch, label: str) -> str | None:
    """One /markets page into `batch`. Returns the cursor."""
    items = req(body, "markets", list, label, "$")
    cursor = opt(body, "cursor", str, label, "$") or None
    for i, raw in enumerate(items):
        p = f"$.markets[{i}]"
        event = req(raw, "event_ticker", str, label, p)
        s = series.get(series_of(event))
        if s is None:
            raise KalshiShapeError(label, f"{p}.event_ticker", f"{event[:60]!r}: series not on the board")
        if req(raw, "status", str, s.ticker, p) not in ("active", "open"):
            continue
        row, ex = parse_market(raw, s, p)
        batch.rows.append(row)
        if ex["complement"] is not None:
            batch.checked += 1
            batch.no_mismatch += 0 if ex["complement"] else 1
        if ex["miss"]:
            batch.misses[s.ticker] += 1
        c0, e0 = batch.times.get(event, (None, None))
        c1, e1 = ex["close_time"], ex["expiry"]
        batch.times[event] = (min(x for x in (c0, c1) if x) if (c0 or c1) else None,
                              min(x for x in (e0, e1) if x) if (e0 or e1) else None)
    return cursor


def _fetch(client: KalshiClient, series: dict[str, Series], keep: set[str], label: str,
           **params: Any) -> Batch:
    """Every page of one filtered /markets query into a Batch, keeping only
    markets of `keep` (a series query also returns its events outside the
    window: dropped as parsed)."""
    batch = Batch()
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        body = client.get("/markets", status="open", limit=MARKETS_PAGE, cursor=cursor, **params)
        items = req(body, "markets", list, label, "$")
        cursor = opt(body, "cursor", str, label, "$") or None
        kept = {"markets": [m for m in items if isinstance(m, dict) and m.get("event_ticker") in keep]}
        del body, items
        parse_markets_page(kept, series, batch, label)
        if not cursor:
            return batch
    raise KalshiShapeError(label, "$.cursor", f"still paging after {MAX_PAGES} pages")


def market_batches(client: KalshiClient, event_tickers: Sequence[str], series: dict[str, Series],
                   per_event_max: int = PER_EVENT_MAX) -> Iterator[tuple[list[str], Batch]]:
    """Every open market of `event_tickers`, one parsed Batch per series.

    /markets takes ONE event_ticker: a comma-separated list answers 200 with
    no markets (verified Nov 6), so it is never sent. A series with at most
    `per_event_max` of these events is read event by event
    (``event_ticker=E``); a larger one in one series query
    (``series_ticker=S``, 200 a page), its other events dropped as parsed.
    Both are filtered: no combo market can arrive."""
    by_series: dict[str, list[str]] = {}
    for t in event_tickers:
        by_series.setdefault(series_of(t), []).append(t)
    for s in sorted(by_series):
        group = by_series[s]
        if len(group) <= per_event_max:
            batch = Batch()
            for t in group:
                one = _fetch(client, series, {t}, f"markets[{t}]", event_ticker=t)
                batch.rows.extend(one.rows)
                batch.times.update(one.times)
                batch.misses.update(one.misses)
                batch.checked += one.checked
                batch.no_mismatch += one.no_mismatch
        else:
            batch = _fetch(client, series, set(group), f"markets[{s}]", series_ticker=s)
        yield group, batch


def order_events(events: dict[str, Game]) -> list[str]:
    """Window tickers grouped by series (similar sizes share a call), then
    by start."""
    return sorted(events, key=lambda t: (series_of(t), events[t].starts_at, t))


def make_client(counter: Counting | None = None, *, min_interval: float = 0.5,
                sleep: Callable[[float], None] = time.sleep) -> tuple[KalshiClient, Counting]:
    c = counter or Counting()
    return KalshiClient(transport=c, min_interval=min_interval, sleep=sleep), c
