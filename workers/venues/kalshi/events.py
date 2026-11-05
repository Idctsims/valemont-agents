"""Kalshi sports events, read series -> events -> markets (Chat 7 Phase 2;
docs/kalshi_mapping.md). Never the keyword search, never the raw open-markets
list (MASTER_PLAN §7: that list is flooded with combo markets).

Shape-checked like the ESPN adapter: every field read here is checked for
presence and type, and a missing or mistyped one raises KalshiShapeError
naming the series and the path. Every page the mapping job reads goes through
here, so every run is also the contract test of these shapes; the daily
series check (`check_series`) covers /series/{ticker}.

What an event is reduced to (docs/kalshi_mapping_probe.md §3):

  * ticker, and its date part when it has one: `<SERIES>-<YY><MON><DD>[HHMM]
    <REST>`. The date is the US Eastern date the event was listed for (it can
    be a day early); F1 and golf tickers carry none.
  * start: its milestone's start_date (the milestone whose
    details.main_game_event_ticker, else primary_event_tickers, names it).
  * sides: every market but TIE, as (code, name): the code is the market
    ticker's LAST segment (the event ticker runs the codes together), the
    name its yes_sub_title.
  * order: (away, home) when the ticker's rest is exactly the two codes run
    together; US leagues list AWAY then HOME.

No SQL here, no orders: read-only, like the rest of venues/kalshi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterator

from venues.kalshi.client import KalshiClient, KalshiError, ts

TICKER_RE = re.compile(
    r"^(?P<series>[A-Z0-9]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hhmm>\d{4})?(?P<rest>[A-Z0-9]+)$")
MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}
#: Market codes that are not a side: soccer's draw.
NOT_A_SIDE = frozenset({"TIE"})
#: Events per page. 200 is the API's page; 100 halves the transient RAM of
#: one parse (a 200-event NCAAF page with nested markets is ~1.2 MB of JSON).
PAGE_LIMIT = 100
#: A series with more pages than this is a runaway cursor, not a listing.
MAX_PAGES = 20


class KalshiShapeError(KalshiError):
    """A field the mapping reads is missing or has the wrong type."""

    def __init__(self, series: str, path: str, detail: str) -> None:
        super().__init__(f"{series}: shape at {path}: {detail}")
        self.series = series
        self.path = path


@dataclass(frozen=True)
class Side:
    code: str
    name: str


@dataclass(frozen=True)
class KalshiEvent:
    ticker: str
    series: str
    #: The ticker's date part (US Eastern), or None (F1, golf).
    day: date | None
    #: The milestone's start_date, or None when no milestone names the event.
    start: datetime | None
    sides: tuple[Side, ...]

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(s.code for s in self.sides)

    @property
    def order(self) -> tuple[str, str] | None:
        """(away, home) from the ticker: its rest is exactly the two codes
        run together, away first. None when it cannot be read so."""
        m = TICKER_RE.match(self.ticker)
        if not m or len(self.sides) != 2:
            return None
        a, b = self.sides[0].code, self.sides[1].code
        rest = m.group("rest")
        if rest == a + b:
            return (a, b)
        if rest == b + a:
            return (b, a)
        return None


def ticker_day(ticker: str) -> date | None:
    """The date part of an event ticker, or None when it has none (or it is
    not a real date)."""
    m = TICKER_RE.match(ticker)
    if not m or m.group("mon") not in MONTHS:
        return None
    try:
        return date(2000 + int(m.group("yy")), MONTHS[m.group("mon")], int(m.group("dd")))
    except ValueError:
        return None


# ------------------------------------------------------------- validation

_MISSING = object()


def _get(obj: Any, key: str, types: type | tuple[type, ...], series: str, path: str, *, required: bool) -> Any:
    if not isinstance(obj, dict):
        raise KalshiShapeError(series, path, f"expected an object, got {type(obj).__name__}")
    value = obj.get(key, _MISSING)
    if value is _MISSING or value is None:
        if required:
            raise KalshiShapeError(series, f"{path}.{key}", "missing")
        return None
    if not isinstance(value, types) or isinstance(value, bool) and bool not in (
            types if isinstance(types, tuple) else (types,)):
        raise KalshiShapeError(series, f"{path}.{key}", f"is {type(value).__name__}")
    return value


def req(obj: Any, key: str, types: type | tuple[type, ...], series: str, path: str) -> Any:
    return _get(obj, key, types, series, path, required=True)


def opt(obj: Any, key: str, types: type | tuple[type, ...], series: str, path: str) -> Any:
    return _get(obj, key, types, series, path, required=False)


def _time(value: str, series: str, path: str) -> datetime:
    try:
        t = ts(value)
    except KalshiError as exc:
        raise KalshiShapeError(series, path, str(exc)) from None
    if t is None:
        raise KalshiShapeError(series, path, "empty timestamp")
    return t


def check_series(series: str, body: Any) -> None:
    """GET /series/{series}: the series exists and says what it is."""
    s = req(body, "series", dict, series, "$")
    ticker = req(s, "ticker", str, series, "$.series")
    if ticker != series:
        raise KalshiShapeError(series, "$.series.ticker", f"is {ticker[:40]!r}")
    req(s, "title", str, series, "$.series")


def milestone_starts(series: str, milestones: list[Any]) -> dict[str, datetime]:
    """event ticker -> start, from a page's milestones. A milestone names its
    main event in details.main_game_event_ticker (games, matches, fights) or
    lists it in primary_event_tickers (F1, golf); the main ticker wins."""
    main: dict[str, datetime] = {}
    primary: dict[str, datetime] = {}
    for i, m in enumerate(milestones):
        p = f"$.milestones[{i}]"
        start = _time(req(m, "start_date", str, series, p), series, f"{p}.start_date")
        details = opt(m, "details", dict, series, p) or {}
        mt = opt(details, "main_game_event_ticker", str, series, f"{p}.details")
        if mt:
            main.setdefault(mt, start)
        for j, t in enumerate(opt(m, "primary_event_tickers", list, series, p) or []):
            if not isinstance(t, str):
                raise KalshiShapeError(series, f"{p}.primary_event_tickers[{j}]", f"is {type(t).__name__}")
            primary.setdefault(t, start)
    return {**primary, **main}


@dataclass
class Page:
    events: list[KalshiEvent]
    cursor: str | None
    #: Events skipped for having no market but TIE (nothing to match on).
    no_sides: int = 0
    #: Events no milestone names (no start: never matched by date-only rules).
    no_milestone: int = 0


def parse_events_page(series: str, body: Any) -> Page:
    """One /events page (status=open, with_nested_markets, with_milestones)."""
    events = req(body, "events", list, series, "$")
    milestones = req(body, "milestones", list, series, "$")
    cursor = opt(body, "cursor", str, series, "$") or None
    starts = milestone_starts(series, milestones)
    page = Page([], cursor)
    for i, e in enumerate(events):
        p = f"$.events[{i}]"
        ticker = req(e, "event_ticker", str, series, p)
        got = req(e, "series_ticker", str, series, p)
        if got != series:
            raise KalshiShapeError(series, f"{p}.series_ticker", f"is {got[:40]!r}")
        if not ticker.startswith(series + "-"):
            raise KalshiShapeError(series, f"{p}.event_ticker", f"{ticker[:60]!r} is not under {series}")
        sides: list[Side] = []
        for j, mk in enumerate(req(e, "markets", list, series, p)):
            q = f"{p}.markets[{j}]"
            mt = req(mk, "ticker", str, series, q)
            if not mt.startswith(ticker + "-"):
                raise KalshiShapeError(series, f"{q}.ticker", f"{mt[:60]!r} is not under {ticker}")
            code = mt[len(ticker) + 1:]
            if not re.fullmatch(r"[A-Z0-9]{1,12}", code):
                raise KalshiShapeError(series, f"{q}.ticker", f"unexpected code {code[:20]!r}")
            name = req(mk, "yes_sub_title", str, series, q)
            if code not in NOT_A_SIDE:
                sides.append(Side(code, name.strip()[:120]))
        if not sides:
            page.no_sides += 1
            continue
        start = starts.get(ticker)
        if start is None:
            page.no_milestone += 1
        page.events.append(KalshiEvent(ticker, series, ticker_day(ticker), start, tuple(sides)))
    return page


def open_pages(client: KalshiClient, series: str, *, limit: int = PAGE_LIMIT) -> Iterator[Page]:
    """Every page of a series' open events, parsed and checked one at a time
    (the raw page is dropped before the next is fetched)."""
    cursor: str | None = None
    for n in range(MAX_PAGES):
        body = client.get("/events", series_ticker=series, status="open", limit=limit, cursor=cursor,
                          with_nested_markets="true", with_milestones="true")
        page = parse_events_page(series, body)
        del body
        yield page
        if not page.cursor or not page.events and not page.no_sides:
            return
        cursor = page.cursor
    raise KalshiShapeError(series, "$.cursor", f"still paging after {MAX_PAGES} pages")
