"""Read-only Kalshi market data. No auth, no orders, ever.

Every endpoint used here declares `security: []` in Kalshi's OpenAPI spec and
answers unauthenticated (verified 2026-09-30). There is no order endpoint in
this module and there must never be one: no component of this system places
an order (CLAUDE.md §1).

Four facts this client exists to encode, each of which cost a discovery cycle:

*   **Two tiers.** Markets settled before `GET /historical/cutoff` →
    `market_settled_ts` are served only from `/historical/...`. The client
    routes on that, per market, so callers never think about it.
*   **Two spellings.** The live tier says `close_dollars`, `volume_fp`,
    `open_interest_fp`; the historical tier says `close`, `volume`,
    `open_interest`. `Candle` normalizes both.
*   **5,000 candles per request** (undocumented; the API answers 400 above it).
    `candles()` chunks.
*   **Prices are strings** (`"0.6300"`). Parsed to `Decimal`, never float.

The transport is injectable. Tests pass a fake and never reach the network,
which `tests/support.py` enforces by making `urllib` raise.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Final

__all__ = [
    "BASE_URL",
    "KalshiClient",
    "KalshiError",
    "KalshiNotFound",
    "KalshiUnreachable",
    "Candle",
    "Quote",
    "Trade",
    "Transport",
]

log = logging.getLogger("valemont.kalshi")

BASE_URL: Final = "https://external-api.kalshi.com/trade-api/v2"
USER_AGENT: Final = "valemont-agents/0.1 (paper research; read-only)"

#: The API refuses more than this many candles in one request.
MAX_CANDLES_PER_REQUEST: Final = 5000

#: Backoff schedule for 429s. Unauthenticated limits are undocumented, so the
#: client paces itself and backs off rather than assuming a budget.
_BACKOFF_SECONDS: Final = (2.0, 5.0, 10.0, 20.0, 40.0, 60.0, 120.0)

#: Transport signature: URL in, (HTTP status, parsed JSON or None) out.
Transport = Callable[[str], tuple[int, Any]]


class KalshiError(RuntimeError):
    """Kalshi failed, or answered with something unusable. Transient: retry."""


class KalshiNotFound(KalshiError):
    """HTTP 404. Normal for a market not listed; the caller decides."""


class KalshiUnreachable(KalshiError):
    """The request never got an HTTP answer (DNS, TLS, timeout). Retried with
    the same backoff as a 429 before it is raised."""


def _http_transport(url: str, timeout: float = 20.0) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except urllib.error.URLError as exc:
        raise KalshiUnreachable(f"{url} unreachable: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise KalshiUnreachable(f"{url} failed: {exc}") from exc
    try:
        return status, json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise KalshiError(f"{url} returned non-JSON ({raw[:80]!r})") from exc


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def dec(value: Any) -> Decimal | None:
    """Kalshi's string prices and counts to Decimal. None stays None."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise KalshiError(f"not a number: {value!r}") from exc


def ts(value: str | None) -> datetime | None:
    """ISO-8601 with a Z to an aware UTC datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KalshiError(f"not a timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise KalshiError(f"naive timestamp from Kalshi: {value!r}")
    return parsed.astimezone(timezone.utc)


def _field(block: dict[str, Any] | None, name: str) -> Decimal | None:
    """Read `name` from a candle sub-object under either tier's spelling."""
    if not block:
        return None
    return dec(block.get(f"{name}_dollars", block.get(name)))


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Candle:
    """One period of one market, normalized across both tiers.

    `end` is the exchange's own `end_period_ts`: the candle covers the period
    ending at `end`. Fencing on `end <= t` therefore never admits a period that
    straddles t. All prices are the YES side, in dollars (= probability).
    """

    end: datetime
    yes_bid_close: Decimal | None
    yes_ask_close: Decimal | None
    trade_low: Decimal | None
    trade_high: Decimal | None
    trade_close: Decimal | None
    volume: Decimal
    open_interest: Decimal | None

    @property
    def mid(self) -> Decimal | None:
        if self.yes_bid_close is None or self.yes_ask_close is None:
            return None
        return (self.yes_bid_close + self.yes_ask_close) / 2

    @property
    def spread(self) -> Decimal | None:
        if self.yes_bid_close is None or self.yes_ask_close is None:
            return None
        return self.yes_ask_close - self.yes_bid_close

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Candle":
        end = raw.get("end_period_ts")
        if not isinstance(end, int):
            raise KalshiError(f"candle without integer end_period_ts: {raw!r}")
        price = raw.get("price") or {}
        return cls(
            end=datetime.fromtimestamp(end, timezone.utc),
            yes_bid_close=_field(raw.get("yes_bid"), "close"),
            yes_ask_close=_field(raw.get("yes_ask"), "close"),
            trade_low=_field(price, "low"),
            trade_high=_field(price, "high"),
            trade_close=_field(price, "close"),
            volume=dec(raw.get("volume_fp", raw.get("volume"))) or Decimal(0),
            open_interest=dec(raw.get("open_interest_fp", raw.get("open_interest"))),
        )


@dataclass(frozen=True, slots=True)
class Trade:
    """One print. `yes_price` is the YES side's price, whatever the taker did."""

    at: datetime
    yes_price: Decimal
    count: Decimal
    #: Kalshi's own id and the taker's side, kept for the archive (db/014).
    trade_id: str | None = None
    taker_side: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Trade":
        at = ts(raw.get("created_time"))
        price = dec(raw.get("yes_price_dollars", raw.get("yes_price")))
        count = dec(raw.get("count_fp", raw.get("count")))
        if at is None or price is None or count is None:
            raise KalshiError(f"unusable trade print: {raw!r}")
        return cls(at=at, yes_price=price, count=count,
                   trade_id=raw.get("trade_id"), taker_side=raw.get("taker_side"))


@dataclass(frozen=True, slots=True)
class Quote:
    """A market as the live snapshot shows it at `fetched_at`.

    This is what a commitment prices against: the price available at commit
    time, with the displayed size (CLAUDE.md §2 — no price chosen later).
    """

    ticker: str
    event_ticker: str
    status: str
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    no_bid: Decimal | None
    no_ask: Decimal | None
    yes_ask_size: Decimal | None
    no_ask_size: Decimal | None
    result: str
    settlement_value: Decimal | None
    settlement_ts: datetime | None
    expected_expiration: datetime | None
    close_time: datetime | None
    fetched_at: datetime
    #: The rung of a ladder market ("wins by over 3.5" → 3.5); None otherwise.
    floor_strike: Decimal | None = None
    title: str = ""

    @property
    def finalized(self) -> bool:
        return self.status == "finalized"

    @classmethod
    def from_api(cls, market: dict[str, Any], fetched_at: datetime) -> "Quote":
        ticker = market.get("ticker")
        if not ticker:
            raise KalshiError(f"market without a ticker: {market!r}")
        return cls(
            ticker=ticker,
            event_ticker=market.get("event_ticker", ""),
            status=market.get("status", ""),
            yes_bid=dec(market.get("yes_bid_dollars")),
            yes_ask=dec(market.get("yes_ask_dollars")),
            no_bid=dec(market.get("no_bid_dollars")),
            no_ask=dec(market.get("no_ask_dollars")),
            yes_ask_size=dec(market.get("yes_ask_size_fp")),
            no_ask_size=dec(market.get("no_ask_size_fp")),
            result=market.get("result") or "",
            settlement_value=dec(market.get("settlement_value_dollars")),
            settlement_ts=ts(market.get("settlement_ts")),
            expected_expiration=ts(market.get("expected_expiration_time")),
            close_time=ts(market.get("close_time")),
            fetched_at=fetched_at,
            floor_strike=dec(market.get("floor_strike")),
            title=market.get("title") or "",
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class KalshiClient:
    """Paced, read-only access to the endpoints the Kalshi agents need."""

    def __init__(
        self,
        base: str = BASE_URL,
        transport: Transport | None = None,
        *,
        min_interval: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.base = base.rstrip("/")
        self._transport = transport or _http_transport
        self._min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last_request = 0.0
        self._cutoff: datetime | None = None

    # -- plumbing -----------------------------------------------------------

    def get(self, path: str, **params: Any) -> Any:
        """GET one endpoint, paced, with 429 backoff. Raises on anything off."""
        query = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        last_error: KalshiUnreachable | None = None
        for attempt, wait in enumerate((0.0, *_BACKOFF_SECONDS)):
            if wait:
                log.warning("kalshi %s on %s — backing off %.0fs",
                            "unreachable" if last_error else "429", path, wait)
                self._sleep(wait)
            gap = time.monotonic() - self._last_request
            if gap < self._min_interval:
                self._sleep(self._min_interval - gap)
            self._last_request = time.monotonic()
            try:
                status, body = self._transport(url)
            except KalshiUnreachable as exc:
                last_error = exc
                continue
            last_error = None
            if status == 200:
                if body is None:
                    raise KalshiError(f"{path} returned 200 with no body")
                return body
            if status == 404:
                raise KalshiNotFound(f"{path} returned 404")
            if status != 429:
                raise KalshiError(f"{path} returned HTTP {status}")
        if last_error is not None:
            raise last_error
        raise KalshiError(f"{path} still rate-limited after {len(_BACKOFF_SECONDS)} backoffs")

    # -- metadata -----------------------------------------------------------

    def series(self, series_ticker: str) -> dict[str, Any]:
        body = self.get(f"/series/{series_ticker}")
        series = body.get("series") if isinstance(body, dict) else None
        if not isinstance(series, dict):
            raise KalshiError(f"/series/{series_ticker} had no series object")
        return series

    def series_fee_changes(self, series_ticker: str) -> list[dict[str, Any]]:
        body = self.get(
            "/series/fee_changes", series_ticker=series_ticker, show_historical="true"
        )
        changes = body.get("series_fee_change_arr") if isinstance(body, dict) else None
        if not isinstance(changes, list):
            raise KalshiError("/series/fee_changes had no series_fee_change_arr")
        return changes

    def historical_cutoff(self) -> datetime:
        """`market_settled_ts`: markets settled before it live only in /historical."""
        if self._cutoff is None:
            body = self.get("/historical/cutoff")
            cutoff = ts(body.get("market_settled_ts") if isinstance(body, dict) else None)
            if cutoff is None:
                raise KalshiError("/historical/cutoff had no market_settled_ts")
            self._cutoff = cutoff
        return self._cutoff

    # -- markets ------------------------------------------------------------

    def market(self, ticker: str) -> Quote:
        """The market snapshot, from whichever tier holds it."""
        fetched_at = self._clock()
        try:
            body = self.get(f"/markets/{ticker}")
        except KalshiNotFound:
            body = self.get(f"/historical/markets/{ticker}")
        market = body.get("market") if isinstance(body, dict) else None
        if not isinstance(market, dict):
            raise KalshiError(f"/markets/{ticker} had no market object")
        return Quote.from_api(market, fetched_at)

    def markets(
        self,
        *,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        status: str | None = None,
    ) -> list[Quote]:
        """Every live-tier market matching the filter, all pages."""
        fetched_at = self._clock()
        out: list[Quote] = []
        cursor: str | None = None
        while True:
            body = self.get(
                "/markets", series_ticker=series_ticker, event_ticker=event_ticker,
                status=status, limit=1000, cursor=cursor,
            )
            page = body.get("markets", []) if isinstance(body, dict) else []
            out.extend(Quote.from_api(m, fetched_at) for m in page)
            cursor = body.get("cursor") if isinstance(body, dict) else None
            if not cursor or not page:
                return out

    def historical_markets(self, *, series_ticker: str) -> list[Quote]:
        """Every archived market in a series (settled before the cutoff)."""
        fetched_at = self._clock()
        out: list[Quote] = []
        cursor: str | None = None
        while True:
            body = self.get("/historical/markets", series_ticker=series_ticker,
                            limit=1000, cursor=cursor)
            page = body.get("markets", []) if isinstance(body, dict) else []
            out.extend(Quote.from_api(m, fetched_at) for m in page)
            cursor = body.get("cursor") if isinstance(body, dict) else None
            if not cursor or not page:
                return out

    def trades(
        self, ticker: str, start: datetime, end: datetime, *, settled_at: datetime | None = None,
    ) -> list[Trade]:
        """Every print on `ticker` with start <= created_time < end, oldest
        first, from whichever tier holds the market."""
        historical = settled_at is not None and settled_at < self.historical_cutoff()
        path = "/historical/trades" if historical else "/markets/trades"
        out: list[Trade] = []
        cursor: str | None = None
        while True:
            body = self.get(path, ticker=ticker, min_ts=int(start.timestamp()),
                            max_ts=int(end.timestamp()), limit=1000, cursor=cursor)
            page = body.get("trades", []) if isinstance(body, dict) else []
            out.extend(Trade.from_api(t) for t in page)
            cursor = body.get("cursor") if isinstance(body, dict) else None
            if not cursor or not page:
                break
        return sorted((t for t in out if start <= t.at < end), key=lambda t: t.at)

    # -- history ------------------------------------------------------------

    def candles(
        self,
        series_ticker: str,
        ticker: str,
        start: datetime,
        end: datetime,
        period_minutes: int,
        *,
        settled_at: datetime | None = None,
    ) -> list[Candle]:
        """Candles with `start <= end_period_ts <= end`, oldest first.

        `settled_at`, when known, routes to the historical tier if the market
        settled before the cutoff. Chunked to stay under 5,000 per request.
        """
        if period_minutes not in (1, 60, 1440):
            raise ValueError(f"period_minutes must be 1, 60 or 1440, not {period_minutes}")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("candle bounds must be timezone-aware")
        historical = settled_at is not None and settled_at < self.historical_cutoff()
        path = (
            f"/historical/markets/{ticker}/candlesticks" if historical
            else f"/series/{series_ticker}/markets/{ticker}/candlesticks"
        )
        span = timedelta(minutes=period_minutes * (MAX_CANDLES_PER_REQUEST - 1))
        out: list[Candle] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(end, cursor + span)
            body = self.get(
                path, start_ts=int(cursor.timestamp()), end_ts=int(chunk_end.timestamp()),
                period_interval=period_minutes,
            )
            raw = body.get("candlesticks", []) if isinstance(body, dict) else []
            out.extend(Candle.from_api(c) for c in raw)
            cursor = chunk_end + timedelta(seconds=1)
        seen: set[datetime] = set()
        unique = []
        for candle in sorted(out, key=lambda c: c.end):
            if candle.end not in seen and start <= candle.end <= end:
                seen.add(candle.end)
                unique.append(candle)
        return unique
