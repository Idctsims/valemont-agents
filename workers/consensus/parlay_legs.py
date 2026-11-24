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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

from venues.kalshi import board as kb
from venues.kalshi.client import KalshiClient, dec, ts
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
#: The re-quote's page (owner, Nov 24): Kalshi's documented maximum for /markets.
REQUOTE_PAGE = 1000
#: A series with at most this many of the slot's events is read event by event;
#: a larger one in one series read (owner, Nov 24: the board's own rule).
REQUOTE_PER_EVENT_MAX = kb.PER_EVENT_MAX


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
    #: The outside inputs of the engine's blend (leg_consensus_for), so the slot
    #: recomputes p from its fresh quote (owner, Nov 20). stale_outside is the
    #: engine's 12 h flag: it blocks a claim, never the blend (as the engine does).
    p_books: Decimal | None = None
    p_espn: Decimal | None = None
    stale_outside: bool | None = None
    #: The game's teams in words (kalshi_board_events.game_title).
    game_title: str | None = None


@dataclass(frozen=True)
class Quote:
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    at: datetime
    #: Kalshi's expected_expiration_time for the market (Phase 4.4: a ticket's
    #: resolves_after is its latest leg's, from the API, pre-registration §5).
    expires: datetime | None = None


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
                      "yes_ask": d(r["yes_ask"]), "p_books": d(r.get("p_books")), "p_espn": d(r.get("p_espn"))})
            for r in rows]


# --------------------------------------------------------------------------- p from the fresh quote
#: The consensus engine's blend and normalization (db/073-db/081:
#: consensus_leg and leg_consensus_priced), applied to the slot's fresh quotes.
#: The engine is SQL; this is its Python twin, held equal to it on the real
#: board by tests_live/test_parlay_candidates_sql.FreshTwin.
OUTSIDE_W, KALSHI_W = Decimal("0.7"), Decimal("0.3")
NORM_LO, NORM_HI = Decimal("0.95"), Decimal("1.05")
P_PLACES = Decimal("0.000001")


def _round6(x: Decimal) -> Decimal:
    return x.quantize(P_PLACES, rounding=ROUND_HALF_UP)  # Postgres round(numeric, 6): half away from zero


def p_outside(m: Market) -> Decimal | None:
    """The tier's outside price, as consensus_leg takes it: books -> p_books, espn -> p_espn."""
    return {"books": m.p_books, "espn": m.p_espn}.get(m.tier)


def _events(markets: Sequence[Market], quotes: Mapping[str, Quote]):
    """Per event: (its markets, {market: fresh mid or None}, the mids' sum,
    normalized?) by the engine's rule (leg_consensus_priced: mutually
    exclusive, at least 2 markets, every one quoted, sum within 0.95-1.05)."""
    def mid(t: str) -> Decimal | None:
        q = quotes.get(t)
        if q is None or q.yes_bid is None or q.yes_ask is None:
            return None
        return (q.yes_bid + q.yes_ask) / 2

    by_event: dict[str, list[Market]] = defaultdict(list)
    for m in markets:
        by_event[m.event_ticker].append(m)
    for group in by_event.values():
        mids = {m.market_ticker: mid(m.market_ticker) for m in group}
        quoted = [v for v in mids.values() if v is not None]
        total = sum(quoted, Decimal(0))
        norm = bool(group[0].mutually_exclusive and len(group) >= 2 and len(quoted) == len(group)
                    and NORM_LO <= total <= NORM_HI)
        yield group, mids, total, norm


def normalized_events(markets: Sequence[Market], quotes: Mapping[str, Quote]) -> set[str]:
    """The events whose fresh mids the engine's rule normalizes (for the report)."""
    return {group[0].event_ticker for group, _, _, norm in _events(markets, quotes) if norm}


def fresh_p(markets: Sequence[Market], quotes: Mapping[str, Quote]) -> dict[str, Decimal | None]:
    """{market: consensus p of YES} from the slot's quotes, by the engine's rules:
    the Kalshi mid, normalized across a mutually exclusive event when every
    market of it is quoted and the mids sum to 0.95-1.05 (anything else, and
    ladder rungs, plain); then 0.7 x outside + 0.3 x that mid where the tier
    has an outside price, else the mid. A market with no two-sided fresh quote
    gets None: its stored p is never used."""
    out: dict[str, Decimal | None] = {}
    for group, mids, total, norm in _events(markets, quotes):
        for m in group:
            k = mids[m.market_ticker]
            if k is None:
                out[m.market_ticker] = None
                continue
            pk = _round6(k / total if norm else k)
            o = p_outside(m)
            out[m.market_ticker] = _round6(OUTSIDE_W * o + KALSHI_W * pk) if o is not None else pk
    return out


def with_fresh_p(markets: Sequence[Market], quotes: Mapping[str, Quote]) -> list[Market]:
    """The markets with p_yes replaced by the slot's recomputed p (None when not
    re-read). The stored p never reaches ordering or pricing (owner, Nov 20: the
    Nov 9 decision, re-quote and recompute before selecting)."""
    fresh = fresh_p(markets, quotes)
    return [replace(m, p_yes=fresh[m.market_ticker]) for m in markets]


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
    #: Owner, Nov 24: reads by series (page 1,000) where a series has more than
    #: REQUOTE_PER_EVENT_MAX of the slot's events, else by event.
    series_reads: int = 0
    event_reads: int = 0
    #: Every market the reads returned, the slot's or not (a series read also
    #: returns its other events): the over-read is markets_read / quotes kept.
    markets_read: int = 0
    #: Each call's answer time in seconds (the pacer's, never its wait).
    answer_s: list[float] = field(default_factory=list)

    @property
    def share_429(self) -> float:
        return self.limited / self.calls if self.calls else 0.0

    def answer_ms(self) -> dict[str, float | int]:
        """The per-call answer times: count, median, p90, p99, max and total, in ms."""
        xs = sorted(self.answer_s)
        if not xs:
            return {"n": 0}

        def pct(p: float) -> float:
            return round(xs[min(len(xs) - 1, int(p * len(xs)))] * 1000, 1)

        return {"n": len(xs), "p50": pct(0.5), "p90": pct(0.9), "p99": pct(0.99),
                "max": round(xs[-1] * 1000, 1), "total_s": round(sum(xs), 1)}


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
            expires = ts(opt(raw, "expected_expiration_time", str, p, path))
        except Exception as exc:  # dec / ts raise KalshiError on a malformed value
            raise KalshiShapeError(p, path, str(exc)) from None
        out[ticker] = Quote(bid, ask, at, expires)
    return out, (opt(body, "cursor", str, p, "$") or None)


def parse_series_quotes(body: Any, series: str, keep: set[str], at: datetime) -> tuple[dict[str, Quote], int, str | None]:
    """One /markets page of a series read -> ({market ticker: Quote} for the
    slot's events only, every market on the page, the next cursor). A market of
    another of the series' events is dropped; one outside the series fails loudly."""
    p = f"requote[{series}]"
    items = req(body, "markets", list, p, "$")
    out: dict[str, Quote] = {}
    for i, raw in enumerate(items):
        path = f"$.markets[{i}]"
        event = req(raw, "event_ticker", str, p, path)
        if kb.series_of(event) != series:
            raise KalshiShapeError(p, path, f"{event[:60]!r} is not in series {series[:60]!r}")
        if event not in keep:
            continue
        got, _ = parse_quotes({"markets": [raw]}, event, at)
        out.update(got)
    return out, len(items), (opt(body, "cursor", str, p, "$") or None)


def requote_plan(events: Sequence[str], per_event_max: int = REQUOTE_PER_EVENT_MAX) -> list[tuple[str, tuple[str, ...]]]:
    """The slot's reads (owner, Nov 24): ("event", (E,)) for each event of a series
    with at most `per_event_max` of the slot's events, else ("series", its events)
    for one series read. `events` come latest start first; a read goes in the order
    of its earliest game, so the earliest games are still read last."""
    pos = {e: i for i, e in enumerate(events)}
    by_series: dict[str, list[str]] = {}
    for e in events:
        by_series.setdefault(kb.series_of(e), []).append(e)
    reads: list[tuple[str, tuple[str, ...]]] = []
    for group in by_series.values():
        if len(group) <= per_event_max:
            reads += [("event", (e,)) for e in group]
        else:
            reads.append(("series", tuple(group)))
    return sorted(reads, key=lambda r: (max(pos[e] for e in r[1]), r[1][0]))


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
    """Every open market of the slot's events (owner, Nov 24): per `requote_plan`,
    one series read (`series_ticker=S`, page 1,000, other events dropped) for a
    series with more than REQUOTE_PER_EVENT_MAX of them, else one read per event
    (`event_ticker=E`; a comma list answers 200 with no markets). Paced by
    kb.Paced with the re-quote's own floor; every call's answer time is kept. A
    spent budget stops the run and counts the events not read; their legs then
    fail `fresh`."""
    t0 = clock()
    paced = kb.Paced(interval=pace, max_calls=max_calls, deadline=None if max_seconds is None else t0 + max_seconds,
                     http=http, clock=clock, sleep=sleep, timeout=CALL_TIMEOUT_S, floor=floor)
    client = KalshiClient(transport=paced, min_interval=0, sleep=paced.sleep)
    run = Requote()
    reads = requote_plan(events)
    try:
        for i, (kind, group) in enumerate(reads):
            try:
                cursor: str | None = None
                label = group[0] if kind == "event" else kb.series_of(group[0])
                keep = set(group)
                for _ in range(kb.MAX_PAGES):
                    if kind == "event":
                        body = client.get("/markets", event_ticker=group[0], status="open", limit=REQUOTE_PAGE,
                                          cursor=cursor)
                        got, cursor = parse_quotes(body, group[0], now())
                        run.markets_read += len(got)
                    else:
                        body = client.get("/markets", series_ticker=label, status="open", limit=REQUOTE_PAGE,
                                          cursor=cursor)
                        got, n, cursor = parse_series_quotes(body, label, keep, now())
                        run.markets_read += n
                    del body
                    run.quotes.update(got)
                    if not cursor:
                        break
                else:
                    raise kb.PageCapExceeded(label, "$.cursor", f"still paging after {kb.MAX_PAGES} pages")
            except kb.BudgetSpent as cap:
                run.cap = cap.reason
                run.events_skipped = sum(len(g) for _, g in reads[i:])
                break
            run.events_asked += len(group)
            if kind == "event":
                run.event_reads += 1
            else:
                run.series_reads += 1
    finally:
        run.calls, run.limited, run.bytes = paced.calls, paced.limited, paced.bytes
        run.pace, run.seconds = paced.interval, clock() - t0
        run.answer_s = list(paced.answer_s)
    return run
