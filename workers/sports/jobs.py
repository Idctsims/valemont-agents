"""The sports data layer's worker jobs (Chat 7 Phase 1; db/065;
docs/sports_data.md §5).

    sports_schedule  daily 04:00 America/Chicago, and at boot: every active
                     league from yesterday to today + its days_ahead (db/066:
                     14 for NFL and CFB, 7 for the rest; Eastern dates), one
                     call per date for a team league, one range call for the
                     others. It is also the daily contract test: every
                     response is validated, an empty day included. Fills the
                     in-memory cache. One league failing does not stop the
                     others; the run then fails naming each failed league.
    sports_live      every 60 s: reads nothing from the database and calls
                     ESPN only for a league with something in play, from the
                     cache (hot: every run; warm, a no-time golf round or
                     tennis match on its own day: every 10 min); a TBD start
                     never wakes it; an event live past its stuck-live cap
                     is no longer polled, flagged and logged once; otherwise
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
#: (ESPN is often a few minutes late flipping a game to in-progress); then
#: it is left to the next daily pull.
SCHEDULED_HOT_AFTER = timedelta(hours=3)
#: Stuck-live caps (owner, 2026-11-04): an event still live or delayed this
#: long past its start stops being polled, is flagged and logged once
#: (docs/sports_data.md §5). By league first, then by kind.
STUCK_AFTER_LEAGUE = {
    "nfl": timedelta(hours=6), "cfb": timedelta(hours=6),
    "nba": timedelta(hours=4), "nhl": timedelta(hours=4), "mcbb": timedelta(hours=4),
    "epl": timedelta(hours=3), "mls": timedelta(hours=3), "ucl": timedelta(hours=3),
    "mlb": timedelta(hours=5),
}
STUCK_AFTER_KIND = {
    "game": timedelta(hours=4),          # a team league not listed above
    "race_session": timedelta(hours=4),  # a race with red flags runs ~3 h
    "golf_round": timedelta(hours=14),   # from first seen live: a full day of tee times
    "bout": timedelta(hours=8),          # bouts carry the card's start; a card runs ~6 h
    "match": timedelta(hours=6),
}
#: Kinds ESPN gives no start time for (time_tbd): checked every 10 min on
#: the event's own day only. A TBD start of any other kind never wakes the
#: live job: the daily pull replaces the time when ESPN publishes it.
NO_TIME_KINDS = frozenset({"golf_round", "match"})
DONE = frozenset({"final", "postponed", "canceled", "suspended"})


def league_of(row: ledger.SportsLeagueRow) -> League:
    return League(row.code, row.espn_path, dict(row.espn_params), row.fetch_mode, row.kind, row.owner_five, row.sort,
                  row.days_ahead)


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


def stuck_after(ev: Cached) -> timedelta:
    return STUCK_AFTER_LEAGUE.get(ev.league) or STUCK_AFTER_KIND[ev.kind]


def tier(ev: Cached, now: datetime, live_since: datetime | None = None) -> str:
    """'hot' (every 60 s), 'warm' (every 10 min), 'stuck' (live past its
    cap: never polled, flagged) or 'cold' (never polled).

    The stuck clock runs from starts_at; for a time-TBD event (whose
    starts_at is only ESPN's day start) from when the worker first saw it
    live (`live_since`)."""
    if ev.status in DONE:
        return "cold"
    if ev.status in ("live", "delayed"):
        base = (live_since or now) if ev.time_tbd else ev.starts_at
        return "hot" if now - base < stuck_after(ev) else "stuck"
    # scheduled
    if ev.time_tbd:
        own_day = ev.starts_at <= now and eastern_day(now) == eastern_day(ev.starts_at)
        return "warm" if ev.kind in NO_TIME_KINDS and own_day else "cold"
    return "hot" if -LEAD <= now - ev.starts_at <= SCHEDULED_HOT_AFTER else "cold"


#: Full event objects (what plan() reads) are kept from this long before now.
EVENT_KEEP = timedelta(days=DAYS_BACK + 1)
#: The compact seen map drops an entry once it is settled (final or
#: canceled) and started this long ago (owner, 2026-11-04)...
SEEN_SETTLED_KEEP = timedelta(days=14)
#: ...and any entry at all past this, so an event ESPN leaves postponed or
#: scheduled forever cannot pin memory.
SEEN_MAX_AGE = timedelta(days=60)
SETTLED = frozenset({"final", "canceled"})


def digest(h: str) -> int:
    """The first 8 bytes of a content hash, as an int (a skip decision only:
    the database still compares every column before it writes)."""
    return int(h[:16], 16)


@dataclass
class EventCache:
    """The last schedule run's events plus every live update since. Never
    read back from the database: the boot run rebuilds it from ESPN.

    Two maps. `events` holds full Cached objects for what can still be in
    play, pruned by age (EVENT_KEEP). `seen` is compact, key -> (hash
    digest, start epoch seconds, settled), and decides whether a fetched row
    is re-sent: a tournament's call returns its whole draw, so a match too
    old for `events` must still read as unchanged (production, Nov 4: 81
    re-sent rows a minute). `seen` drops an entry only once it is settled
    and 14 days old (or 60 days old, whatever its status)."""

    events: dict[tuple[str, str], Cached] = field(default_factory=dict)
    seen: dict[tuple[str, str], tuple[int, int, bool]] = field(default_factory=dict)
    last_poll: dict[str, datetime] = field(default_factory=dict)
    #: When the worker first saw each event live (the stuck clock of a
    #: time-TBD event). In memory: a restart starts it again.
    live_since: dict[tuple[str, str], datetime] = field(default_factory=dict)
    #: Events flagged stuck-live now, and those already logged (once each).
    stuck: set[tuple[str, str]] = field(default_factory=set)
    logged_stuck: set[tuple[str, str]] = field(default_factory=set)

    @staticmethod
    def entry(ev: SportsEvent, h: str) -> Cached:
        return Cached(ev.league, ev.kind, ev.starts_at, ev.time_tbd, ev.status, h)

    def unchanged(self, ev: SportsEvent, h: str) -> bool:
        s = self.seen.get(ev.key)
        return s is not None and s[0] == digest(h)

    def replace_league(self, league: str, rows: Iterable[tuple[SportsEvent, str]],
                       now: datetime | None = None) -> None:
        """The league's full entries become what the schedule fetched (then
        the age prune); its `seen` entries are kept."""
        for k in [k for k in self.events if k[0] == league]:
            del self.events[k]
        self.update(rows, now)

    def update(self, rows: Iterable[tuple[SportsEvent, str]], now: datetime | None = None) -> None:
        t = now or datetime.now(timezone.utc)
        for ev, h in rows:
            self.seen[ev.key] = (digest(h), int(ev.starts_at.timestamp()), ev.status in SETTLED)
            self.events[ev.key] = self.entry(ev, h)
            if ev.status in ("live", "delayed"):
                self.live_since.setdefault(ev.key, t)
            else:
                self.live_since.pop(ev.key, None)

    def prune(self, now: datetime) -> None:
        """Full objects by age; the compact map by settled + 14 days (60 days
        regardless). Run after every schedule and live run."""
        before = now - EVENT_KEEP
        for k in [k for k, c in self.events.items()
                  if c.starts_at < before and c.status not in ("live", "delayed")]:
            del self.events[k]
            self.live_since.pop(k, None)
        settled_before = (now - SEEN_SETTLED_KEEP).timestamp()
        max_before = (now - SEEN_MAX_AGE).timestamp()
        for k in [k for k, (_, start, settled) in self.seen.items()
                  if start < max_before or (settled and start < settled_before)]:
            del self.seen[k]

    def tier_of(self, key: tuple[str, str], now: datetime) -> str:
        return tier(self.events[key], now, self.live_since.get(key))

    def plan(self, now: datetime) -> dict[str, tuple[str, list[date]]]:
        """{league: (tier, Eastern dates to fetch)} for every league to poll
        now. Flags (and logs, once each) every event stuck live."""
        by_league: dict[str, tuple[bool, set[date]]] = {}
        stuck: set[tuple[str, str]] = set()
        for key, c in self.events.items():
            league = key[0]
            t = self.tier_of(key, now)
            if t == "stuck":
                stuck.add(key)
                if key not in self.logged_stuck:
                    self.logged_stuck.add(key)
                    log.warning("sports live: %s %s is still %s %.1f h after its start; no longer polled "
                                "(stuck-live cap %.0f h)", league, key[1], c.status,
                                (now - c.starts_at).total_seconds() / 3600, stuck_after(c).total_seconds() / 3600)
                continue
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
        self.stuck = stuck
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
    #: Events flagged stuck-live (live past their cap; never polled).
    stuck: list[str] = field(default_factory=list)

    @property
    def job_note(self) -> str:
        stuck = f"stuck-live flagged {len(self.stuck)}: {', '.join(self.stuck[:5])}" if self.stuck else None
        if self.idle:
            return "; ".join(p for p in ("idle: nothing in play, 0 ESPN calls", stuck) if p)
        parts = [", ".join(f"{k} {v}" for k, v in self.leagues.items()) or "no league fetched",
                 f"sent {self.sent}, inserted {self.inserted}, updated {self.updated}",
                 f"{self.calls} ESPN calls, ~{round(self.wire_bytes / 1024)} KB over the wire"]
        if self.missing:
            parts.append(f"{self.missing} expected event(s) missing from ESPN")
        if stuck:
            parts.append(stuck)
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


def schedule_window(now: datetime, days_ahead: int = DAYS_AHEAD) -> list[date]:
    """Yesterday through today + days_ahead (the league's, db/066: 14 for NFL
    and CFB, whose Kalshi games list up to ~12 days out; 7 for the rest)."""
    today = now.astimezone(OWNER_TZ).date()
    return [today + timedelta(days=d) for d in range(-DAYS_BACK, days_ahead + 1)]


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
    t = now()
    try:
        def store(lg: League, events: list[SportsEvent]) -> None:
            cache.replace_league(lg.code, _write(run, events, cache), now())

        _fetch_each(p, {lg.code: (lg, schedule_window(t, lg.days_ahead)) for lg in leagues}, run, store)
        # A league no longer in the registry leaves the cache with it.
        for k in [k for k in cache.events if k[0] not in registry]:
            del cache.events[k]
            cache.live_since.pop(k, None)
        cache.prune(now())
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
    stuck = sorted(f"{k[0]} {k[1]}" for k in cache.stuck)
    if not plan:
        return SportsRun(idle=True, stuck=stuck)
    known = leagues if leagues is not None else LEAGUES
    targets = {code: (known[code], days) for code, (_, days) in plan.items() if code in known}
    if not targets:
        return SportsRun(idle=True, stuck=stuck)
    p = provider_factory() if provider_factory else provider_from_env(env)
    run = SportsRun(stuck=stuck)
    try:
        def store(lg: League, events: list[SportsEvent]) -> None:
            cache.last_poll[lg.code] = t
            got = {ev.key for ev in events}
            expected = [k for k, c in cache.events.items()
                        if k[0] == lg.code and cache.tier_of(k, t) in ("hot", "warm") and k not in got]
            if expected:
                run.missing += len(expected)
                log.warning("sports live: %s: %d in-play event(s) not in ESPN's response: %s", lg.code,
                            len(expected), ", ".join(k[1] for k in expected[:5]))
            cache.update(_write(run, events, cache), t)

        _fetch_each(p, targets, run, store)
        cache.prune(t)
    finally:
        _finish(run, p)
    return run
