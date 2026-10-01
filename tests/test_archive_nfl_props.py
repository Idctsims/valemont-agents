"""The F1/F2 archive job's pure pieces. Never reaches Kalshi or the database."""

from __future__ import annotations

import random
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from jobs import archive_nfl_props as a
from venues.kalshi.client import Candle, Trade

K = datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc)


def candle(end: datetime) -> Candle:
    return Candle(end=end, yes_bid_close=Decimal("0.4"), yes_ask_close=Decimal("0.45"),
                  trade_low=None, trade_high=None, trade_close=None, volume=Decimal(1), open_interest=None)


def latest_at_or_before(cs, x):
    eligible = [c.end for c in cs if c.end <= x]
    return max(eligible) if eligible else None


class KeepCandles(unittest.TestCase):
    def test_latest_candle_at_or_before_any_x_in_the_window_is_preserved(self) -> None:
        rng = random.Random(7)
        for _ in range(200):
            cs = [candle(K - timedelta(minutes=rng.randint(0, 24 * 60))) for _ in range(rng.randint(0, 60))]
            kept = a.keep_candles(cs, K)
            for minutes in (0, 1, 30, 75, 100, 135, 179, 180):
                x = K - timedelta(minutes=minutes)
                self.assertEqual(latest_at_or_before(kept, x), latest_at_or_before(cs, x))

    def test_nothing_after_kickoff_is_kept(self) -> None:
        kept = a.keep_candles([candle(K + timedelta(minutes=1)), candle(K)], K)
        self.assertEqual([c.end for c in kept], [K])

    def test_only_one_candle_survives_from_before_the_window(self) -> None:
        cs = [candle(K - timedelta(hours=h)) for h in (5, 4, 3.5)]
        self.assertEqual([c.end for c in a.keep_candles(cs, K)], [K - timedelta(hours=3.5)])


class Scope(unittest.TestCase):
    def test_prices_only_for_the_evaluation_weeks(self) -> None:
        self.assertEqual([w for w in range(1, 19) if a.stores_prices(w)], list(range(5, 19)))

    def test_weeks_outside_the_season_are_refused(self) -> None:
        for week in (0, 19):
            with self.assertRaises(SystemExit):
                a.week_games(None, week)  # type: ignore[arg-type]

    def test_a_game_is_archived_only_after_the_grace(self) -> None:
        class G:
            kickoff = K
        self.assertFalse(a.ready(G, K + timedelta(hours=4)))
        self.assertTrue(a.ready(G, K + timedelta(hours=5)))


class TradeIds(unittest.TestCase):
    def test_trade_keeps_kalshis_id_and_taker_side(self) -> None:
        t = Trade.from_api({"created_time": "2025-11-30T20:50:52Z", "yes_price_dollars": "0.01",
                            "count_fp": "935.00", "trade_id": "abc", "taker_side": "yes"})
        self.assertEqual((t.trade_id, t.taker_side), ("abc", "yes"))


if __name__ == "__main__":
    unittest.main()
