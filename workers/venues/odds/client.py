"""The Odds API (the-odds-api.com, v4): a read-only client. No SQL: every
call is handed to a `record` callback (core/odds_credits.py writes it to
odds_api_calls), and every PAID call first asks a `guard` (the credit guard,
core/odds_credits.py), which raises to refuse it.

Cost (measured Nov 9, Step 0): x-requests-last = markets x regions per call,
whatever the number of events; /sports and /events cost 0. The key rides in
the query string, so a URL is NEVER logged, raised or recorded: errors name
the endpoint template only (/sports/:sport/odds).

401 (a bad or revoked key), 429 (rate or quota) and any other non-200 raise
OddsApiError: a run that cannot read the books fails loud, never quietly
falls back to the kalshi tier.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

log = logging.getLogger("valemont.odds")

BASE = "https://api.the-odds-api.com/v4"
TIMEOUT_S = 30.0
REGIONS = "us"
#: The Odds API sport keys for the leagues the consensus pulls.
SPORT_KEYS: Mapping[str, str] = {"nfl": "americanfootball_nfl", "nba": "basketball_nba"}
MAINLINES = "h2h,spreads,totals"


class OddsApiError(RuntimeError):
    """A failed Odds API call (named by endpoint template, never by URL)."""


@dataclass(frozen=True)
class Call:
    """One call's record (an odds_api_calls row)."""

    endpoint: str
    sport: str | None
    event_id: str | None
    markets: str | None
    regions: str | None
    status: int
    cost: int | None
    used: int | None
    remaining: int | None
    bytes: int

    def row(self) -> dict[str, Any]:
        return {"endpoint": self.endpoint, "sport": self.sport, "event_id": self.event_id,
                "markets": self.markets, "regions": self.regions, "status": self.status, "cost": self.cost,
                "used": self.used, "remaining": self.remaining, "bytes": self.bytes}


@dataclass
class Resp:
    status: int
    data: Any
    headers: Mapping[str, str]
    nbytes: int


Http = Callable[[str, Mapping[str, str]], Resp]


def httpx_get() -> tuple[Http, Callable[[], None]]:
    import httpx

    client = httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": "valemont-agents"})

    def get(url: str, params: Mapping[str, str]) -> Resp:
        r = client.get(url, params=dict(params))
        try:
            data = r.json()
        except ValueError:
            data = None
        return Resp(r.status_code, data, {k.lower(): v for k, v in r.headers.items()}, len(r.content))

    return get, client.close


def _int(v: str | None) -> int | None:
    try:
        return None if v is None else int(float(v))
    except ValueError:
        return None


class OddsClient:
    """`guard(cost_estimate)` runs before every paid call and raises to refuse;
    `record(Call)` runs after every call, paid or free, success or not."""

    def __init__(self, http: Http, key: str, *, record: Callable[[Call], None],
                 guard: Callable[[int], None]) -> None:
        if not key:
            raise OddsApiError("ODDS_API_KEY is not set")
        self._http, self._key = http, key
        self._record, self._guard = record, guard
        self.calls: list[Call] = []

    @property
    def spent(self) -> int:
        return sum(c.cost or 0 for c in self.calls)

    @property
    def remaining(self) -> int | None:
        for c in reversed(self.calls):
            if c.remaining is not None:
                return c.remaining
        return None

    def _get(self, endpoint: str, path: str, *, sport: str | None = None, event_id: str | None = None,
             markets: str | None = None, regions: str | None = None, paid_estimate: int = 0) -> Any:
        if paid_estimate:
            self._guard(paid_estimate)
        params = {"apiKey": self._key, "dateFormat": "iso"}
        if markets:
            params |= {"markets": markets, "oddsFormat": "american"}
        if regions:
            params["regions"] = regions
        t0 = time.monotonic()
        try:
            r = self._http(BASE + path, params)
        except Exception as exc:  # transport: the URL (and key) never leave here
            raise OddsApiError(f"{endpoint}: {type(exc).__name__}") from None
        call = Call(endpoint, sport, event_id, markets, regions, r.status, _int(r.headers.get("x-requests-last")),
                    _int(r.headers.get("x-requests-used")), _int(r.headers.get("x-requests-remaining")), r.nbytes)
        self.calls.append(call)
        self._record(call)
        log.info("odds api %s %s%s: %s, cost %s, remaining %s, %.1f s, %d KB", endpoint, sport or "",
                 f" {markets}" if markets else "", r.status, call.cost, call.remaining,
                 time.monotonic() - t0, r.nbytes // 1024)
        if r.status != 200:
            why = {401: "unauthorized (the key)", 429: "rate or quota limited"}.get(r.status, "failed")
            raise OddsApiError(f"{endpoint} {sport or ''}: HTTP {r.status}, {why}")
        if r.data is None:
            raise OddsApiError(f"{endpoint} {sport or ''}: the body is not JSON")
        return r.data

    def events(self, sport: str) -> list[dict[str, Any]]:
        """Free: the sport's listed events."""
        return self._get("/sports/:sport/events", f"/sports/{sport}/events", sport=sport)

    def odds(self, sport: str, markets: str = MAINLINES, regions: str = REGIONS) -> list[dict[str, Any]]:
        """Paid: markets x regions credits, every listed event."""
        cost = len(markets.split(",")) * len(regions.split(","))
        return self._get("/sports/:sport/odds", f"/sports/{sport}/odds", sport=sport, markets=markets,
                         regions=regions, paid_estimate=cost)

    def event_odds(self, sport: str, event_id: str, markets: str, regions: str = REGIONS) -> dict[str, Any]:
        """Paid: (markets returned) x regions credits, one event."""
        cost = len(markets.split(",")) * len(regions.split(","))
        return self._get("/sports/:sport/events/:event/odds", f"/sports/{sport}/events/{event_id}/odds",
                         sport=sport, event_id=event_id, markets=markets, regions=regions, paid_estimate=cost)


def key_from_env(env: Mapping[str, str] = os.environ) -> str:
    return env.get("ODDS_API_KEY", "").strip()
