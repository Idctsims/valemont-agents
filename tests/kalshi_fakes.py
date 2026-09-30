"""Offline stand-ins for Kalshi and nflverse, shared by the Kalshi test modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from typing import Any

from sports.nfl.schedule import Game, NflSchedule
from venues.kalshi.client import Candle, KalshiNotFound, Quote
from venues.kalshi.fees import FeeRegime, FeeSchedule

UTC = timezone.utc
T0 = datetime(2026, 10, 3, 17, 0, tzinfo=UTC)          # a Saturday; "now" in most tests


def regime(fee_type: str = "quadratic_with_maker_fees", mult: str = "1",
           effective_from: datetime | None = None) -> FeeRegime:
    return FeeRegime(series="KXNFLGAME", fee_type=fee_type, multiplier=D(mult),
                     effective_from=effective_from, fetched_at=T0)


def quote(ticker: str, *, bid: str = "0.49", ask: str = "0.50",
          status: str = "active", settlement: str | None = None,
          size: str = "5000", fetched_at: datetime = T0,
          expected_expiration: datetime | None = None,
          settlement_ts: datetime | None = None) -> Quote:
    yb, ya = D(bid), D(ask)
    return Quote(
        ticker=ticker, event_ticker=ticker.rsplit("-", 1)[0], status=status,
        yes_bid=yb, yes_ask=ya, no_bid=1 - ya, no_ask=1 - yb,
        yes_ask_size=D(size), no_ask_size=D(size),
        result="" if settlement is None else "settled",
        settlement_value=None if settlement is None else D(settlement),
        settlement_ts=settlement_ts, expected_expiration=expected_expiration,
        close_time=None, fetched_at=fetched_at,
    )


def candle(end: datetime, bid: str, ask: str, *, volume: str = "1000",
           low: str | None = None, high: str | None = None) -> Candle:
    b, a = D(bid), D(ask)
    return Candle(end=end, yes_bid_close=b, yes_ask_close=a,
                  trade_low=D(low) if low else b, trade_high=D(high) if high else a,
                  trade_close=(b + a) / 2, volume=D(volume), open_interest=D(0))


def flat_candles(start: datetime, end: datetime, step: timedelta, bid: str, ask: str,
                 volume: str = "1000") -> list[Candle]:
    out, t = [], start
    while t <= end:
        out.append(candle(t, bid, ask, volume=volume))
        t += step
    return out


def game(kickoff: datetime, *, game_id: str = "2026_05_BAL_DAL", away: str = "BAL",
         home: str = "DAL", season: int = 2026, week: int = 5,
         away_rest: int | None = 7, home_rest: int | None = 7) -> Game:
    return Game(game_id=game_id, season=season, week=week, game_type="REG",
                away=away, home=home, kickoff=kickoff,
                away_rest=away_rest, home_rest=home_rest)


def schedule(*games: Game, fetched_at: datetime = T0) -> NflSchedule:
    return NflSchedule(games, fetched_at)


@dataclass
class FakeKalshi:
    """Duck-types `KalshiClient` for the calls the agents make."""

    quotes: dict[str, Quote] = field(default_factory=dict)
    candle_map: dict[tuple[str, int], list[Candle]] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def market(self, ticker: str) -> Quote:
        self.calls.append(("market", ticker))
        if ticker not in self.quotes:
            raise KalshiNotFound(ticker)
        return self.quotes[ticker]

    def markets(self, *, series_ticker: str | None = None, event_ticker: str | None = None,
                status: str | None = None) -> list[Quote]:
        self.calls.append(("markets", series_ticker))
        return [q for q in self.quotes.values() if status is None or q.status in ("active", status)]

    def candles(self, series: str, ticker: str, start: datetime, end: datetime,
                period_minutes: int, *, settled_at: datetime | None = None) -> list[Candle]:
        self.calls.append(("candles", (ticker, start, end, period_minutes)))
        return [c for c in self.candle_map.get((ticker, period_minutes), [])
                if start <= c.end <= end]


def fee_schedule(r: FeeRegime | None = None) -> FeeSchedule:
    r = r or regime()
    return FeeSchedule(series=r.series, regimes=(r,))
