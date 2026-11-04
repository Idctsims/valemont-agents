"""The sports data layer's worker jobs (Chat 7 Phase 1; db/065;
docs/sports_data.md §5).

    sports_schedule  daily 04:00 America/Chicago, and at boot: every active
                     league from yesterday to today + 7 (Eastern dates), one
                     call per date for a team league, one range call for the
                     others. It is also the daily contract test: every
                     response is validated, an empty day included. Fills the
                     in-memory cache. One league failing does not stop the
                     others; the run then fails naming each failed league.
    sports_live      every 60 s: reads nothing from the database and calls
                     ESPN only for a league with something in play, from the
                     cache (hot: every run; warm: every 10 min); otherwise
                     idle, zero calls.

Rows are sent only when their content hash differs from the cache, and the
database writes only a row whose stored hash differs (ledger.sports_events_upsert).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from core import ledger, memory
from sports.espn import EspnError, League, SportsEvent
from sports.provider import Provider, provider_from_env

log = logging.getLogger("valemont.sports")

SCHEDULE_JOB = "sports_schedule"
LIVE_JOB = "sports_live"
LIVE_INTERVAL_S = 60
WARM_INTERVAL = timedelta(minutes=10)
EASTERN = ZoneInfo("America/New_York")
OWNER_TZ = ZoneInfo("America/Chicago")
#: Yesterday (a final the worker missed) through today + 7.
DAYS_BACK, DAYS_AHEAD = 1, 7

LEAD = timedelta(minutes=15)
#: A scheduled event with a real time stays hot this long past its start
#: (ESPN is often a few minutes late flipping a game to in-progress).
SCHEDULED_HOT_AFTER = timedelta(hours=3)
#: A live event stays hot until starts_at plus its kind's ceiling.
LIVE_CEILING = {
    "game": timedelta(hours=8),
    "race_session": timedelta(hours=5),
    "golf_round": timedelta(hours=20),
    "bout": timedelta(hours=8),
    "match": timedelta(hours=8),
}
#: Past every hot window, an unfinished event is still polled warm until this.
WARM_UNTIL = timedelta(hours=24)
DONE = frozenset({"final", "postponed", "canceled", "suspended"})


def league_of(row: ledger.SportsLeagueRow) -> League:
    return League(row.code, row.espn_path, dict(row.espn_params), row.fetch_mode, row.kind, row.owner_five, row.sort)


def eastern_day(t: datetime) -> date:
    return t.astimezone(EASTERN).date()


# ------------------------------------------------------------------ cache

@dataclass(frozen=True)
class Cached:
    league: str
    kind: str
    starts_at: datetime
    time_tbd: bool
    status: str
    content_hash: str


def tier(ev: Cached, now: datetime) -> str:
    """'hot' (every 60 s), 'warm' (every 10 min) or 'cold' (never)."""
    if ev.status in DONE:
        return "cold"
    age = now - ev.starts_at
    if ev.status in ("live", "delayed"):
        if age < LIVE_CEILING[ev.kind]:
            return "hot"
        return "warm" if age < WARM_UNTIL else "cold"
    # scheduled
    if ev.time_tbd:
        return "warm" if timedelta(0) <= age < WARM_UNTIL else "cold"
    if -LEAD <= age <= SCHEDULED_HOT_AFTER:
        return "hot"
    return "warm" if SCHEDULED_HOT_AFTER < age < WARM_UNTIL else "cold"


@dataclass
class EventCache:
    """The last schedule run's events plus every live update since. Never
    read back from the database: the boot run rebuilds it from ESPN."""

    events: dict[tuple[str, str], Cached] = field(default_factory=dict)
    last_poll: dict[str, datetime] = field(default_factory=dict)

    @staticmethod
    def entry(ev: SportsEvent, h: str) -> Cached:
        return Cached(ev.league, ev.kind, ev.starts_at, ev.time_tbd, ev.status, h)

    def unchanged(self, ev: SportsEvent, h: str) -> bool:
        c = self.events.get(ev.key)
        return c is not None and c.content_hash == h

    def replace_league(self, league: str, rows: Iterable[tuple[SportsEvent, str]]) -> None:
        for k in [k for k in self.events if k[0] == league]:
            del self.events[k]
        self.update(rows)

    def update(self, rows: Iterable[tuple[SportsEvent, str]]) -> None:
        for ev, h in rows:
            self.events[ev.key] = self.entry(ev, h)

    def prune(self, before: datetime) -> None:
        for k in [k for k, c in self.events.items() if c.starts_at < before]:
            del self.events[k]

    def plan(self, now: datetime) -> dict[str, tuple[str, list[date]]]:
        """{league: (tier, Eastern dates to fetch)} for every league to poll now."""
        by_league: dict[str, tuple[bool, set[date]]] = {}
        for (league, _), c in self.events.items():
            t = tier(c, now)
            if t == "cold":
                continue
            hot, days = by_league.get(league, (False, set()))
            by_league[league] = (hot or t == "hot", days | {eastern_day(c.starts_at)})
        out: dict[str, tuple[str, list[date]]] = {}
        for league, (hot, days) in by_league.items():
            if hot:
                out[league] = ("hot", sorted(days))
            else:
                last = self.last_poll.get(league)
                if last is None or now - last >= WARM_INTERVAL:
                    out[league] = ("warm", sorted(days))
        return out


CACHE = EventCache()
#: The registry as the last schedule run read it, so the live job never
#: reads the database. Empty until the boot schedule run.
LEAGUES: dict[str, League] = {}


# ------------------------------------------------------------------- runs

@dataclass
class SportsRun:
    leagues: dict[str, int] = field(default_factory=dict)  # league -> events parsed
    failed: dict[str, str] = field(default_factory=dict)
    sent: int = 0
    inserted: int = 0
    updated: int = 0
    calls: int = 0
    wire_bytes: int = 0
    missing: int = 0
    idle: bool = False

    @property
    def job_note(self) -> str:
        if self.idle:
            return "idle: nothing in play, 0 ESPN calls"
        parts = [", ".join(f"{k} {v}" for k, v in self.leagues.items()) or "no league fetched",
                 f"sent {self.sent}, inserted {self.inserted}, updated {self.updated}",
                 f"{self.calls} ESPN calls, ~{round(self.wire_bytes / 1024)} KB over the wire"]
        if self.missing:
            parts.append(f"{self.missing} expected event(s) missing from ESPN")
        if self.failed:
            parts.append("FAILED " + ", ".join(sorted(self.failed)))
        return "; ".join(parts)


class SportsFetchFailed(RuntimeError):
    """One or more leagues failed this run (the others were written)."""


def _write(run: SportsRun, events: Sequence[SportsEvent], cache: EventCache) -> list[tuple[SportsEvent, str]]:
    hashed = [(ev, ev.content_hash()) for ev in events]
    changed = [(ev, h) for ev, h in hashed if not cache.unchanged(ev, h)]
    if changed:
        res = ledger.sports_events_upsert([dict(ev.row(), content_hash=h) for ev, h in changed])
        run.inserted += res.inserted
        run.updated += res.updated
    run.sent += len(changed)
    return hashed


def _fetch_each(provider: Provider, targets: Mapping[str, tuple[League, list[date]]], run: SportsRun,
                on_events: Callable[[League, list[SportsEvent]], None]) -> None:
    for code, (lg, days) in targets.items():
        try:
            events = provider.events(lg, days)
        except EspnError as exc:
            run.failed[code] = str(exc)
            log.error("sports: %s", exc)
            continue
        run.leagues[code] = len(events)
        on_events(lg, events)


def _finish(run: SportsRun, provider: Provider) -> None:
    run.calls = provider.calls
    run.wire_bytes = provider.wire_bytes
    provider.close()
    memory.release()
    if run.failed:
        raise SportsFetchFailed(f"{len(run.failed)} league(s) failed: "
                                + "; ".join(run.failed[k] for k in sorted(run.failed))
                                + f" ({run.job_note})")


def schedule_window(now: datetime) -> list[date]:
    today = now.astimezone(OWNER_TZ).date()
    return [today + timedelta(days=d) for d in range(-DAYS_BACK, DAYS_AHEAD + 1)]


def schedule_job(*, provider: Provider | None = None, env: Mapping[str, str] = os.environ,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 cache: EventCache = CACHE, registry: dict[str, League] = LEAGUES) -> SportsRun:
    """The sports_schedule system job (and the daily contract test)."""
    leagues = [league_of(r) for r in ledger.sports_leagues()]
    if not leagues:
        raise RuntimeError("sports_leagues has no active league (db/065 not pasted?)")
    registry.clear()
    registry.update({lg.code: lg for lg in leagues})
    p = provider or provider_from_env(env)
    run = SportsRun()
    days = schedule_window(now())
    try:
        def store(lg: League, events: list[SportsEvent]) -> None:
            cache.replace_league(lg.code, _write(run, events, cache))

        _fetch_each(p, {lg.code: (lg, days) for lg in leagues}, run, store)
        cache.prune(now() - timedelta(days=DAYS_BACK + 1))
    finally:
        _finish(run, p)
    log.info("sports schedule: %s", run.job_note)
    return run


def live_job(*, provider_factory: Callable[[], Provider] | None = None, env: Mapping[str, str] = os.environ,
             now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
             cache: EventCache = CACHE, leagues: Mapping[str, League] | None = None) -> SportsRun:
    """The sports_live system job: idle (no ESPN call, no database read)
    unless the cache has a league in play."""
    t = now()
    plan = cache.plan(t)
    if not plan:
        return SportsRun(idle=True)
    known = leagues if leagues is not None else LEAGUES
    targets = {code: (known[code], days) for code, (_, days) in plan.items() if code in known}
    if not targets:
        return SportsRun(idle=True)
    p = provider_factory() if provider_factory else provider_from_env(env)
    run = SportsRun()
    try:
        def store(lg: League, events: list[SportsEvent]) -> None:
            cache.last_poll[lg.code] = t
            got = {ev.key for ev in events}
            expected = [k for k, c in cache.events.items()
                        if k[0] == lg.code and tier(c, t) != "cold" and k not in got]
            if expected:
                run.missing += len(expected)
                log.warning("sports live: %s: %d in-play event(s) not in ESPN's response: %s", lg.code,
                            len(expected), ", ".join(k[1] for k in expected[:5]))
            cache.update(_write(run, events, cache))

        _fetch_each(p, targets, run, store)
    finally:
        _finish(run, p)
    return run
