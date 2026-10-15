"""The worker's ONLY TMDB client (Chat 4 Phase 3; db/051).

Endpoint shapes cribbed from celiao/tmdbsimple (search.py `multi`;
movies.py/tv.py `info`, `videos`, `watch_providers`, `release_dates`),
called with plain httpx, no new dependency:

    GET /3/search/multi?query=...          movies and TV (people dropped)
    GET /3/movie/{id}?append_to_response=videos,watch/providers,release_dates
    GET /3/tv/{id}?append_to_response=videos,watch/providers

Region US, language en-US. Auth: TMDB_READ_TOKEN, the v4 read access token,
as a bearer. A missing token, or a 401, raises TmdbTokenError naming the
variable, never its value. At most RATE requests a second (TMDB's soft limit
is ~40/s; we stay at 20); a 429 waits Retry-After (or a backoff) and tries
again, MAX_429 times, then raises.

The web's twin is apps/web/src/lib/tmdb/client.ts.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping

from screening import services

log = logging.getLogger("valemont.screening.tmdb")

TOKEN_ENV = "TMDB_READ_TOKEN"
API = "https://api.themoviedb.org/3"
REGION = "US"
LANGUAGE = "en-US"
RATE = 20.0
MAX_429 = 3
TIMEOUT_S = 15.0


class TmdbTokenError(RuntimeError):
    """The token is missing or TMDB rejected it. Never carries its value."""


class TmdbError(RuntimeError):
    """Any other non-200 answer, or a rate limit that did not clear."""


@dataclass(frozen=True)
class Resp:
    status: int
    data: Any
    headers: Mapping[str, str]


#: (url, params, headers) -> Resp. Tests inject one.
Http = Callable[[str, Mapping[str, Any], Mapping[str, str]], Resp]


def httpx_get() -> tuple[Http, Callable[[], None]]:
    import httpx

    client = httpx.Client(timeout=TIMEOUT_S)

    def get(url: str, params: Mapping[str, Any], headers: Mapping[str, str]) -> Resp:
        r = client.get(url, params=dict(params), headers=dict(headers))
        try:
            data = r.json()
        except ValueError:
            data = None
        return Resp(r.status_code, data, {k.lower(): v for k, v in r.headers.items()})

    return get, client.close


def read_token(env: Mapping[str, str] = os.environ) -> str:
    token = (env.get(TOKEN_ENV) or "").strip()
    if not token:
        raise TmdbTokenError(f"{TOKEN_ENV} is not set (Railway, Vercel Production and the root .env)")
    return token


class Tmdb:
    def __init__(self, token: str, http: Http, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.http, self.sleep, self.clock = http, sleep, clock
        self.gap = 1.0 / RATE
        self._last: float | None = None
        self.calls = 0

    def _wait(self) -> None:
        if self._last is not None:
            remaining = self.gap - (self.clock() - self._last)
            if remaining > 0:
                self.sleep(remaining)
        self._last = self.clock()

    def get(self, path: str, **params: Any) -> Any:
        params.setdefault("language", LANGUAGE)
        for attempt in range(MAX_429 + 1):
            self._wait()
            self.calls += 1
            r = self.http(API + path, params, self._headers)
            if r.status == 401:
                raise TmdbTokenError(f"TMDB rejected {TOKEN_ENV} (HTTP 401 on {path.split('?')[0]})")
            if r.status == 429:
                if attempt == MAX_429:
                    raise TmdbError(f"TMDB rate limit on {path}: still 429 after {MAX_429} waits")
                try:
                    wait = float(r.headers.get("retry-after") or 0)
                except ValueError:
                    wait = 0.0
                self.sleep(max(wait, 1.0 * (2 ** attempt)))
                continue
            if r.status == 404:
                return None
            if r.status != 200:
                raise TmdbError(f"TMDB {path} answered HTTP {r.status}")
            return r.data
        raise TmdbError(f"TMDB {path}: no answer")

    # -- endpoints -------------------------------------------------------

    def search_multi(self, query: str) -> list[dict[str, Any]]:
        """Movies and TV only (people dropped), TMDB's order."""
        data = self.get("/search/multi", query=query, region=REGION, include_adult="false") or {}
        return [r for r in data.get("results") or [] if isinstance(r, dict) and r.get("media_type") in ("movie", "tv")]

    def details(self, kind: str, tmdb_id: int) -> dict[str, Any] | None:
        if kind not in ("movie", "tv"):
            raise ValueError(f"unknown kind {kind!r}")
        append = "videos,watch/providers" + (",release_dates" if kind == "movie" else "")
        return self.get(f"/{kind}/{int(tmdb_id)}", append_to_response=append)


# ---------------------------------------------------------------- shaping

def _date(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def us_release_date(details: Mapping[str, Any]) -> date | None:
    """A movie's US date: its earliest US theatrical (3) release, else the
    earliest US digital (4), else limited (2), else TMDB's release_date. TV:
    first_air_date."""
    if "first_air_date" in details and "release_dates" not in details:
        return _date(details.get("first_air_date"))
    us = [r for r in (details.get("release_dates") or {}).get("results") or [] if r.get("iso_3166_1") == REGION]
    by_type: dict[int, list[date]] = {}
    for r in us:
        for d in r.get("release_dates") or []:
            day = _date(d.get("release_date"))
            if day is not None:
                by_type.setdefault(int(d.get("type") or 0), []).append(day)
    for t in (3, 4, 2):
        if by_type.get(t):
            return min(by_type[t])
    return _date(details.get("release_date"))


def trailer_key(details: Mapping[str, Any]) -> str | None:
    """The official YouTube trailer's key: an official Trailer first, then
    any Trailer, then a Teaser; the newest of each."""
    vids = [v for v in (details.get("videos") or {}).get("results") or []
            if v.get("site") == "YouTube" and isinstance(v.get("key"), str)]

    def best(pred: Callable[[dict], bool]) -> str | None:
        hits = sorted((v for v in vids if pred(v)), key=lambda v: str(v.get("published_at") or ""), reverse=True)
        return hits[0]["key"] if hits else None

    return (best(lambda v: v.get("type") == "Trailer" and v.get("official"))
            or best(lambda v: v.get("type") == "Trailer") or best(lambda v: v.get("type") == "Teaser"))


def us_providers(details: Mapping[str, Any]) -> dict[str, Any]:
    return services.compact(((details.get("watch/providers") or {}).get("results") or {}).get(REGION))


@dataclass(frozen=True)
class TitleIn:
    tmdb_id: int
    kind: str
    name: str
    release_date: date | None
    poster_path: str | None
    overview: str | None
    genres: tuple[str, ...]
    providers: dict[str, Any]
    trailer_key: str | None
    next_episode_date: date | None
    status: str | None


def title_from(kind: str, details: Mapping[str, Any]) -> TitleIn:
    nxt = (details.get("next_episode_to_air") or {}) if kind == "tv" else {}
    return TitleIn(
        tmdb_id=int(details["id"]), kind=kind,
        name=str(details.get("title") or details.get("name") or "")[:300],
        release_date=us_release_date(details),
        poster_path=details.get("poster_path") or None,
        overview=(str(details.get("overview") or "")[:4000] or None),
        genres=tuple(str(g.get("name")) for g in details.get("genres") or [] if g.get("name"))[:20],
        providers=us_providers(details),
        trailer_key=trailer_key(details),
        next_episode_date=_date(nxt.get("air_date")) if nxt else None,
        status=(str(details.get("status"))[:60] if details.get("status") else None),
    )
