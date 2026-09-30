"""The Kalshi client: two tiers, two spellings, chunking, backoff. No network."""

from __future__ import annotations

import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from typing import Any

from venues.kalshi.client import Candle, KalshiClient, KalshiError, KalshiNotFound

UTC = timezone.utc
T = datetime(2026, 9, 27, 20, 0, tzinfo=UTC)

LIVE_CANDLE = {
    "end_period_ts": int(T.timestamp()),
    "yes_bid": {"close_dollars": "0.6200"}, "yes_ask": {"close_dollars": "0.6300"},
    "price": {"low_dollars": "0.6100", "high_dollars": "0.6400", "close_dollars": "0.6300"},
    "volume_fp": "57138.57", "open_interest_fp": "100.00",
}
HIST_CANDLE = {
    "end_period_ts": int(T.timestamp()),
    "yes_bid": {"close": "0.6200"}, "yes_ask": {"close": "0.6300"},
    "price": {"low": "0.6100", "high": "0.6400", "close": "0.6300"},
    "volume": "57138.57", "open_interest": "100.00",
}


class Transport:
    """Scripted responses keyed by path; records every URL."""

    def __init__(self, routes: dict[str, list[tuple[int, Any]]]) -> None:
        self.routes = {k: list(v) for k, v in routes.items()}
        self.urls: list[str] = []

    def __call__(self, url: str) -> tuple[int, Any]:
        self.urls.append(url)
        path = urllib.parse.urlparse(url).path.split("/trade-api/v2", 1)[1]
        queue = self.routes.get(path)
        if not queue:
            return 404, None
        return queue.pop(0) if len(queue) > 1 else queue[0]


def client(routes: dict[str, list[tuple[int, Any]]]) -> tuple[KalshiClient, Transport, list[float]]:
    transport, sleeps = Transport(routes), []
    return (KalshiClient(transport=transport, min_interval=0, sleep=sleeps.append),
            transport, sleeps)


class CandleSpellings(unittest.TestCase):
    def test_live_and_historical_spellings_normalize_identically(self) -> None:
        self.assertEqual(Candle.from_api(LIVE_CANDLE), Candle.from_api(HIST_CANDLE))

    def test_prices_are_decimal_never_float(self) -> None:
        c = Candle.from_api(LIVE_CANDLE)
        self.assertIsInstance(c.yes_ask_close, D)
        self.assertEqual(c.mid, D("0.625"))
        self.assertEqual(c.end, T)


class TierRouting(unittest.TestCase):
    CUTOFF = {"/historical/cutoff": [(200, {"market_settled_ts": "2026-08-01T00:00:00Z"})]}

    def test_settled_before_cutoff_reads_the_historical_tier(self) -> None:
        c, t, _ = client({**self.CUTOFF,
                          "/historical/markets/M/candlesticks": [(200, {"candlesticks": [HIST_CANDLE]})]})
        out = c.candles("S", "M", T - timedelta(hours=1), T, 1,
                        settled_at=datetime(2025, 12, 1, tzinfo=UTC))
        self.assertEqual(len(out), 1)
        self.assertIn("/historical/markets/M/candlesticks", t.urls[-1])

    def test_recent_or_unsettled_reads_the_live_tier(self) -> None:
        c, t, _ = client({**self.CUTOFF,
                          "/series/S/markets/M/candlesticks": [(200, {"candlesticks": [LIVE_CANDLE]})]})
        c.candles("S", "M", T - timedelta(hours=1), T, 1, settled_at=None)
        self.assertIn("/series/S/markets/M/candlesticks", t.urls[-1])

    def test_market_falls_back_to_historical_on_404(self) -> None:
        c, t, _ = client({"/historical/markets/M": [(200, {"market": {"ticker": "M", "status": "finalized"}})]})
        self.assertEqual(c.market("M").status, "finalized")


class Chunking(unittest.TestCase):
    def test_a_long_1_minute_window_is_split_under_5000(self) -> None:
        c, t, _ = client({"/series/S/markets/M/candlesticks": [(200, {"candlesticks": []})]})
        c.candles("S", "M", T - timedelta(days=5), T, 1)
        self.assertEqual(len(t.urls), 2)       # 7200 minutes → two requests

    def test_results_outside_the_window_and_duplicates_are_dropped(self) -> None:
        early = dict(LIVE_CANDLE, end_period_ts=int((T - timedelta(days=3)).timestamp()))
        c, _, _ = client({"/series/S/markets/M/candlesticks":
                          [(200, {"candlesticks": [LIVE_CANDLE, LIVE_CANDLE, early]})]})
        out = c.candles("S", "M", T - timedelta(hours=1), T, 1)
        self.assertEqual([x.end for x in out], [T])

    def test_naive_bounds_refused(self) -> None:
        c, _, _ = client({})
        with self.assertRaises(ValueError):
            c.candles("S", "M", datetime(2026, 1, 1), datetime(2026, 1, 2), 60)


class Errors(unittest.TestCase):
    def test_429_backs_off_then_succeeds(self) -> None:
        c, _, sleeps = client({"/series/S": [(429, None), (429, None), (200, {"series": {"x": 1}})]})
        self.assertEqual(c.series("S"), {"x": 1})
        self.assertEqual(sleeps, [2.0, 5.0])

    def test_persistent_429_raises(self) -> None:
        c, _, _ = client({"/series/S": [(429, None)]})
        with self.assertRaises(KalshiError):
            c.series("S")

    def test_404_is_its_own_error(self) -> None:
        c, _, _ = client({})
        with self.assertRaises(KalshiNotFound):
            c.series("NOPE")

    def test_500_raises_loudly(self) -> None:
        c, _, _ = client({"/series/S": [(500, None)]})
        with self.assertRaises(KalshiError):
            c.series("S")


if __name__ == "__main__":
    unittest.main()
