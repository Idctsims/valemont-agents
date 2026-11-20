"""Phase 4.2: the parlay leg universe, the slot's re-quote, and P1's leg rules 1-4.

docs/preregistration_parlays.md §3 (stamped, db/085). No SQL here: the
candidates come from `ledger.parlay_candidates`, and Kalshi is read with the
existing client and the board refresh's adaptive pacer (`kb.Paced`).

A *leg* is one side (YES or NO) of one market. Rules 1-4 are pure functions
of a leg (and the slot's clock), named, so an empty slot can say which rule
bound:

    universe   binary, the game not started, a combo-eligible event; a YES-only
               event gives YES legs only
    fresh      re-read from Kalshi in this slot, at most 20 minutes old, with
               the quote the side needs
    ask_band   the side's ask between 0.75 and 0.95, inclusive
    spread     the market's YES ask minus its YES bid, 3c or less

The side's ask: YES -> yes_ask; NO -> 1 - yes_bid (Kalshi's NO ask is the
complement of the YES bid; the board checks the complement on every pull).
There is no commit path here, and nothing is written.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

from venues.kalshi import board as kb
from venues.kalshi.client import KalshiClient, dec
from venues.kalshi.events import KalshiShapeError, opt, req

ASK_LO, ASK_HI = Decimal("0.75"), Decimal("0.95")
SPREAD_MAX = Decimal("0.03")
FRESH = timedelta(minutes=20)
RULES = ("universe", "fresh", "ask_band", "spread")
SIDES = ("yes", "no")
#: One call's timeout, as the board refresh (sports/kalshi_board.REFRESH_CALL_TIMEOUT_S).
CALL_TIMEOUT_S = 15.0
#: The re-quote's pace floor (owner, Nov 20: faster, not smaller). The board
#: refresh keeps kb.PACE_FLOOR; the AIMD steps are unchanged.
REQUOTE_FLOOR = 0.10


@dataclass(frozen=True)
class Market:
    """One board market as the candidate reader returns it."""

    market_ticker: str
    event_ticker: str
    game_ticker: str
    series_ticker: str
    sport: str
    league: str | None
    starts_at: datetime
    market_kind: str
    stat: str | None
    subject: str | None
    team: str | None
    line: Decimal | None
    tier: str
    p_yes: Decimal | None
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    payout: str
    combo_eligible: bool
    combo_yes_only: bool
    combo_size_max: int | None
    mutually_exclusive: bool | None
    #: The Kalshi market title (owner, Nov 20); the ticker stays the key.
    label: str | None = None


@dataclass(frozen=True)
class Quote:
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    at: datetime


@dataclass(frozen=True)
class Leg:
    market: Market
    side: str
    quote: Quote | None

    @property
    def ask(self) -> Decimal | None:
        q = self.quote
        if q is None:
            return None
        if self.side == "yes":
            return q.yes_ask
        return None if q.yes_bid is None else Decimal(1) - q.yes_bid

    @property
    def spread(self) -> Decimal | None:
        q = self.quote
        return None if q is None or q.yes_bid is None or q.yes_ask is None else q.yes_ask - q.yes_bid


def markets_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[Market]:
    """`ledger.parlay_candidates` rows -> Markets (Decimals stay Decimals)."""
    def d(v: Any) -> Decimal | None:
        return None if v is None else Decimal(str(v))

    return [Market(**{**r, "line": d(r["line"]), "p_yes": d(r["p_yes"]), "yes_bid": d(r["yes_bid"]),
                      "yes_ask": d(r["yes_ask"])}) for r in rows]


def legs_of(markets: Iterable[Market], quotes: Mapping[str, Quote]) -> list[Leg]:
    """Both sides of every market, each with the slot's quote (None: not re-read)."""
    return [Leg(m, s, quotes.get(m.market_ticker)) for m in markets for s in SIDES]


# --------------------------------------------------------------------------- rules 1-4
def universe(leg: Leg, now: datetime) -> bool:
    m = leg.market
    return (m.payout == "binary" and m.starts_at > now and m.combo_eligible
            and not (m.combo_yes_only and leg.side != "yes"))


def fresh(leg: Leg, now: datetime) -> bool:
    q = leg.quote
    return q is not None and timedelta(0) <= now - q.at <= FRESH and leg.ask is not None


def ask_band(leg: Leg, now: datetime | None = None) -> bool:
    a = leg.ask
    return a is not None and ASK_LO <= a <= ASK_HI


def spread(leg: Leg, now: datetime | None = None) -> bool:
    s = leg.spread
    return s is not None and s <= SPREAD_MAX


RULE_FNS: tuple[tuple[str, Callable[[Leg, datetime], bool]], ...] = (
    ("universe", universe), ("fresh", fresh), ("ask_band", ask_band), ("spread", spread))


def first_failed(leg: Leg, now: datetime) -> str | None:
    """The first of rules 1-4 the leg breaks, in order; None if it passes them all."""
    return next((name for name, fn in RULE_FNS if not fn(leg, now)), None)


@dataclass
class Funnel:
    """Per sport: legs before rule 1 and after each rule; the slot's binding rule counts."""

    by_sport: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: Counter()))
    passed: list[Leg] = field(default_factory=list)

    def add_cut(self, sport: str, markets: int) -> None:
        """Markets the reader left out before rule 1 (not combo-eligible): two legs each,
        counted in, failed at universe."""
        self.by_sport[sport]["before"] += 2 * markets

    def table(self) -> dict[str, dict[str, int]]:
        return {s: {k: c[k] for k in ("before", *RULES)} for s, c in sorted(self.by_sport.items())}


def funnel(legs: Iterable[Leg], now: datetime, *, cut: Mapping[str, int] | None = None) -> Funnel:
    f = Funnel()
    for sport, n in (cut or {}).items():
        f.add_cut(sport, n)
    for leg in legs:
        c = f.by_sport[leg.market.sport]
        c["before"] += 1
        failed = first_failed(leg, now)
        for name in RULES:
            if failed == name:
                break
            c[name] += 1
        if failed is None:
            f.passed.append(leg)
    return f


# --------------------------------------------------------------------------- the re-quote
@dataclass
class Requote:
    quotes: dict[str, Quote] = field(default_factory=dict)
    events_asked: int = 0
    events_skipped: int = 0
    cap: str | None = None
    calls: int = 0
    limited: int = 0
    bytes: int = 0
    seconds: float = 0.0
    pace: float = 0.0

    @property
    def share_429(self) -> float:
        return self.limited / self.calls if self.calls else 0.0


def parse_quotes(body: Any, event: str, at: datetime) -> tuple[dict[str, Quote], str | None]:
    """One /markets page for one event -> {market ticker: Quote}, and the next cursor.
    A market outside the event, or a price that is not a decimal, fails loudly."""
    p = f"requote[{event}]"
    out: dict[str, Quote] = {}
    for i, raw in enumerate(req(body, "markets", list, p, "$")):
        path = f"$.markets[{i}]"
        ticker = req(raw, "ticker", str, p, path)
        if req(raw, "event_ticker", str, p, path) != event:
            raise KalshiShapeError(p, path, f"{ticker[:60]!r} is not in {event[:60]!r}")
        try:
            bid, ask = dec(opt(raw, "yes_bid_dollars", str, p, path)), dec(opt(raw, "yes_ask_dollars", str, p, path))
        except Exception as exc:  # dec raises KalshiError on a non-decimal
            raise KalshiShapeError(p, path, str(exc)) from None
        out[ticker] = Quote(bid, ask, at)
    return out, (opt(body, "cursor", str, p, "$") or None)


def requote_order(markets: Iterable[Market]) -> list[str]:
    """The slot's events, latest start first, so the earliest games are re-read
    last, closest to the commit (owner, Nov 20). Ties by event ticker."""
    starts: dict[str, datetime] = {}
    for m in markets:
        starts[m.event_ticker] = m.starts_at
    return [e for e, _ in sorted(starts.items(), key=lambda kv: (-kv[1].timestamp(), kv[0]))]


def oldest_age(quotes: Mapping[str, Quote], at: datetime) -> timedelta | None:
    """How old the oldest re-read quote is at `at` (the end of the run)."""
    return max((at - q.at for q in quotes.values()), default=None)


def requote(events: Sequence[str], *, http: kb.Http | None = None, max_calls: int | None = None,
            max_seconds: float | None = None, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
            clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
            pace: float = kb.PACE_START, floor: float = REQUOTE_FLOOR) -> Requote:
    """Every open market of each event, one event at a time (`event_ticker=E`,
    the refresh's only shape), in the order given, paced by kb.Paced with the
    re-quote's own floor. A spent budget stops the run and counts the events
    not read; their legs then fail `fresh`."""
    t0 = clock()
    paced = kb.Paced(interval=pace, max_calls=max_calls, deadline=None if max_seconds is None else t0 + max_seconds,
                     http=http, clock=clock, sleep=sleep, timeout=CALL_TIMEOUT_S, floor=floor)
    client = KalshiClient(transport=paced, min_interval=0, sleep=paced.sleep)
    run = Requote()
    try:
        for i, event in enumerate(events):
            try:
                cursor: str | None = None
                for _ in range(kb.MAX_PAGES):
                    body = client.get("/markets", event_ticker=event, status="open", limit=kb.MARKETS_PAGE, cursor=cursor)
                    got, cursor = parse_quotes(body, event, now())
                    run.quotes.update(got)
                    if not cursor:
                        break
                else:
                    raise kb.PageCapExceeded(event, "$.cursor", f"still paging after {kb.MAX_PAGES} pages")
            except kb.BudgetSpent as cap:
                run.cap, run.events_skipped = cap.reason, len(events) - i
                break
            run.events_asked += 1
    finally:
        run.calls, run.limited, run.bytes = paced.calls, paced.limited, paced.bytes
        run.pace, run.seconds = paced.interval, clock() - t0
    return run
