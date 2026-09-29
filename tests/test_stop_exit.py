"""CLAUDE.md §9.1 — the declared stop is a live exit, and -1.0 is the floor."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from typing import ClassVar, Literal

from adapters.crypto import Candle, CryptoAgent, FeedError
from core.agent import directional_return
from core.ledger import Leg
from tests.support import TEST_AGENTS, LedgerTestCase, pending


class TestCrypto(CryptoAgent):
    slug: ClassVar[str] = "_test_crypto"


class ScriptedFeed:
    """Spot at the horizon, plus hourly candles over the holding window."""

    def __init__(self, spot: D, candles: list[Candle], *, spot_age: timedelta = timedelta(0),
                 ticker_error: bool = False, candle_error: bool = False) -> None:
        self.spot, self._candles, self.spot_age = spot, candles, spot_age
        self.ticker_error, self.candle_error = ticker_error, candle_error

    def ticker(self, symbol: str) -> tuple[D, datetime]:
        if self.ticker_error:
            raise FeedError("ticker down")
        return self.spot, datetime.now(timezone.utc) - self.spot_age

    def candles(self, symbol: str, granularity: int = 3600) -> list[Candle]:
        if self.candle_error:
            raise FeedError("candles down")
        return list(self._candles)


def candle(start: datetime, low: D, high: D) -> Candle:
    mid = (low + high) / 2
    return Candle(start=start, low=low, high=high, open=mid, close=mid, volume=D(1))


COMMITTED = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)


def window(*extremes: tuple[D, D]) -> list[Candle]:
    """Hourly candles starting at commit time, one per (low, high)."""
    return [candle(COMMITTED + timedelta(hours=i), lo, hi) for i, (lo, hi) in enumerate(extremes)]


def position(direction: Literal["long", "short"], entry: D, stop: D,
             size: D = D(10), target: D | None = None):
    payload = {"invalidation": str(stop), "entry": str(entry)}
    if target is not None:
        payload["target"] = str(target)
    return pending(
        501, agent_id=TEST_AGENTS["_test_crypto"], slug="_test_crypto",
        kind="paper_position", committed_at=COMMITTED, payload=payload,
        legs=(Leg("BTC-USD", f"spot_{direction}", entry, direction, size),),
    )


class StopAsLiveExit(LedgerTestCase):

    def resolve(self, feed: ScriptedFeed, commitment):
        return TestCrypto(feed=feed, universe=("BTC-USD",)).resolve(commitment)

    def test_long_blown_through_stop_records_exactly_minus_one(self) -> None:
        feed = ScriptedFeed(D(85), window((D(99), D(101)), (D(90), D(99))))
        verdict = self.resolve(feed, position("long", D(100), D(97)))

        self.assertEqual(verdict.pnl, D(-1))
        self.assertEqual(verdict.outcome, "miss")
        self.assertEqual(verdict.detail["exit"], "97")
        self.assertIs(verdict.detail["stop_hit"], True)
        self.assertEqual(verdict.detail["fill"], "assumed_at_stop")
        self.assertEqual(verdict.leg_outcomes[0].actual, D(97))
        # Marking to the horizon price instead would have broken the floor.
        self.assertEqual(directional_return(D(100), D(97), D(85), "long"), D(-5))

    def test_short_blown_through_stop_records_exactly_minus_one(self) -> None:
        feed = ScriptedFeed(D(115), window((D(99), D(101)), (D(101), D(110))))
        verdict = self.resolve(feed, position("short", D(100), D(103)))

        self.assertEqual(verdict.pnl, D(-1))
        self.assertIs(verdict.detail["stop_hit"], True)
        self.assertEqual(verdict.detail["exit"], "103")
        self.assertEqual(directional_return(D(100), D(103), D(115), "short"), D(-5))

    def test_touching_the_stop_exactly_counts_as_hit(self) -> None:
        feed = ScriptedFeed(D(104), window((D(97), D(101))))
        verdict = self.resolve(feed, position("long", D(100), D(97)))
        self.assertEqual(verdict.pnl, D(-1))
        self.assertIs(verdict.detail["stop_hit"], True)

    def test_untouched_stop_marks_to_spot(self) -> None:
        feed = ScriptedFeed(D(103), window((D(98), D(101)), (D(99), D(104))))
        verdict = self.resolve(feed, position("long", D(100), D(97), target=D(102)))

        self.assertEqual(verdict.pnl, D(1))
        self.assertEqual(verdict.outcome, "hit")
        self.assertIs(verdict.detail["stop_hit"], False)
        self.assertEqual(verdict.detail["fill"], "spot")
        self.assertIs(verdict.detail["target_reached"], True)

    def test_untouched_short_stop_marks_to_spot(self) -> None:
        feed = ScriptedFeed(D(97), window((D(96), D(102))))
        verdict = self.resolve(feed, position("short", D(100), D(103)))
        self.assertEqual(verdict.pnl, D(1))

    def test_stop_touched_before_commit_is_ignored(self) -> None:
        before = candle(COMMITTED - timedelta(hours=3), D(80), D(100))
        feed = ScriptedFeed(D(103), [before, *window((D(99), D(101)))])
        verdict = self.resolve(feed, position("long", D(100), D(97)))
        self.assertIs(verdict.detail["stop_hit"], False)
        self.assertEqual(verdict.pnl, D(1))

    def test_capital_at_risk_is_the_declared_distance(self) -> None:
        feed = ScriptedFeed(D(103), window((D(99), D(101))))
        verdict = self.resolve(feed, position("long", D(100), D(97), size=D(10)))
        self.assertEqual(verdict.detail["capital_at_risk"], "30")


class DefersRatherThanGuesses(LedgerTestCase):

    def resolve(self, feed: ScriptedFeed, commitment):
        return TestCrypto(feed=feed, universe=("BTC-USD",)).resolve(commitment)

    def test_no_declared_invalidation_is_unscoreable(self) -> None:
        commitment = position("long", D(100), D(97))
        commitment.payload.pop("invalidation")
        self.assertIsNone(self.resolve(ScriptedFeed(D(103), window((D(99), D(101)))), commitment))

    def test_feed_down_defers(self) -> None:
        feed = ScriptedFeed(D(103), [], ticker_error=True)
        self.assertIsNone(self.resolve(feed, position("long", D(100), D(97))))

    def test_stale_spot_defers(self) -> None:
        feed = ScriptedFeed(D(103), window((D(99), D(101))), spot_age=timedelta(minutes=10))
        self.assertIsNone(self.resolve(feed, position("long", D(100), D(97))))

    def test_cannot_check_the_stop_defers(self) -> None:
        feed = ScriptedFeed(D(103), [], candle_error=True)
        self.assertIsNone(self.resolve(feed, position("long", D(100), D(97))))

    def test_stop_outside_the_band_is_unscoreable(self) -> None:
        feed = ScriptedFeed(D(103), window((D(99), D(101))))
        self.assertIsNone(self.resolve(feed, position("long", D(100), D("99.9"))))


if __name__ == "__main__":
    unittest.main()
