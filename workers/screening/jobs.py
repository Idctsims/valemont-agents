"""The Screening Room's worker jobs (Chat 4 Phase 3; db/051).

    screening_match    chained after every feed_ingest: each new trailer
                       (a real screening story's lead item with no
                       title_trailers row) is matched to a TMDB title
                       (screening/trailers.py) and recorded, matched or
                       `unmatched`. A matched title is upserted into titles.
                       At most MATCH_RUN_CAP trailers a run.
    screening_refresh  daily 05:30 America/Chicago, and at boot unless
                       today's run already succeeded: every title on a shelf
                       gets its details again (US providers, the US release
                       date, the next episode for TV, the trailer key). A
                       Want or Watching title newly on one of the owner's
                       services is logged as an arrival (db/053), for the
                       08:30 release push.

Both fail loudly on a missing or rejected TMDB_READ_TOKEN. Memory-lean: the
TMDB client is closed and memory released when each job ends.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from core import ledger, memory
from core.jobs import JobSkipped
from screening import services, tmdb, trailers

log = logging.getLogger("valemont.screening")

MATCH_JOB = "screening_match"
REFRESH_JOB = "screening_refresh"
MATCH_RUN_CAP = 60


@dataclass
class MatchRun:
    matched: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    calls: int = 0

    @property
    def job_note(self) -> str | None:
        if not self.matched and not self.unmatched:
            return None
        return f"{len(self.matched)} matched, {len(self.unmatched)} unmatched; {self.calls} TMDB calls"


def _client(client: tmdb.Tmdb | None, env: Mapping[str, str]) -> tuple[tmdb.Tmdb, Callable[[], None] | None]:
    if client is not None:
        return client, None
    token = tmdb.read_token(env)
    http, close = tmdb.httpx_get()
    return tmdb.Tmdb(token, http), close


def match_job(*, client: tmdb.Tmdb | None = None, env: Mapping[str, str] = os.environ) -> MatchRun:
    """The screening_match system job."""
    queue = ledger.screening_unmatched(MATCH_RUN_CAP)
    run = MatchRun()
    if not queue:
        return run
    t, close = _client(client, env)
    cache: dict[tuple[int, str], int] = {}
    try:
        for item in queue:
            hit, query = trailers.match(t.search_multi, item.title, item.published_at)
            title_id = None
            if hit is not None:
                key = (hit.tmdb_id, hit.kind)
                if key not in cache:
                    details = t.details(hit.kind, hit.tmdb_id)
                    if details is not None:
                        cache[key] = ledger.titles_upsert(tmdb.title_from(hit.kind, details))
                title_id = cache.get(key)
            ledger.title_trailer_record(item.item_id, title_id, query)
            (run.matched if title_id is not None else run.unmatched).append(item.title[:60])
            log.info("screening: %s %r -> %s", "matched" if title_id else "unmatched", item.title[:80],
                     f"{hit.kind}/{hit.tmdb_id} {hit.name!r}" if hit and title_id else f"(searched {query!r})")
    finally:
        run.calls = t.calls
        if close is not None:
            close()
        memory.release()
    return run


@dataclass
class RefreshRun:
    refreshed: int = 0
    gone: int = 0
    calls: int = 0
    arrivals: int = 0

    @property
    def job_note(self) -> str:
        return (f"{self.refreshed} title(s) refreshed, {self.gone} gone from TMDB, {self.arrivals} arrival(s); "
                f"{self.calls} TMDB calls")


def arrivals(s: ledger.ShelfTitle, tonight_now: Sequence[str]) -> list[str]:
    """The owner's services a Want or Watching title streams on now and did
    not at its previous read (db/053 title_provider_arrivals). Nothing for a
    title TMDB was never read for (no baseline), or one off those shelves."""
    if s.status not in ("want", "watching") or s.providers is None:
        return []
    before = {r["service"] for r in services.split(s.providers)["tonight"]}
    return sorted(set(tonight_now) - before)


def refresh_job(*, client: tmdb.Tmdb | None = None, env: Mapping[str, str] = os.environ) -> RefreshRun:
    """The screening_refresh system job. "Today's run happened" is this job's
    own success today (job_health), as the repos job's."""
    if ledger.job_ok_today(REFRESH_JOB):
        raise JobSkipped("today's refresh already happened")
    shelf = ledger.screening_shelf_titles()
    run = RefreshRun()
    if not shelf:
        return run
    t, close = _client(client, env)
    try:
        for s in shelf:
            details = t.details(s.kind, s.tmdb_id)
            if details is None:
                run.gone += 1
                log.warning("screening: %s/%d is gone from TMDB (on the %s shelf)", s.kind, s.tmdb_id, s.status)
                continue
            title = tmdb.title_from(s.kind, details)
            ledger.titles_upsert(title)
            run.refreshed += 1
            new = arrivals(s, title.tonight)
            if new:
                run.arrivals += ledger.title_arrivals_add(s.id, new)
                log.info("screening: %s/%d now on %s", s.kind, s.tmdb_id, ", ".join(new))
    finally:
        run.calls = t.calls
        if close is not None:
            close()
        memory.release()
    log.info("screening: %s", run.job_note)
    return run
