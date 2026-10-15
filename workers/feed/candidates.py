"""The cluster candidates, kept in worker memory between ingest runs (egress
fix 1; owner, 2026-10-15).

Every ingest run used to read the whole 48 h candidate set from the
database: ~3,500 members, each with its title embedding as ~4.7 KB of text,
so ~17 MB a run and ~800 MB a day of Supabase egress (Free: 5 GB a month).
The cache reads it in full only at boot and once a day (RELOAD_EVERY), so
drift from a migration or a merge heals itself; every other run it reads:

    the window      FEED_CANDIDATE_WINDOW_SQL: each story's id, item count
                    and world, no vector (~50 bytes a story). A story that
                    left the window (aged past 48 h, or deleted) is dropped;
                    every count is the database's.
    new members     FEED_CANDIDATES_SQL with `since` (the previous read's
                    database clock, less SINCE_OVERLAP for a transaction
                    that committed late) and `skip` (every item id already
                    held, the run's own joins among them): only the members
                    other writers added since.

The run's own joins and new stories go into the cache as they happen
(joined / founded), so they are never read back.

Clustering is exactly what a fresh load gives: candidates() lists the
stories by id and each story's members by item id, FEED_CANDIDATES_SQL's
order, and the run inserts its new stories at the front as before
(feed/ingest.py). tests/test_feed_candidates.py and
tests_live/test_feed_ledger.py check a refreshed cache against a fresh load.
"""

from __future__ import annotations

import bisect
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

import psycopg

from core import ledger
from feed import cluster

log = logging.getLogger("valemont.feed")

#: A full 48 h reload at boot and at least this often (owner, 2026-10-15).
RELOAD_EVERY = timedelta(hours=24)
#: How far back past the previous read the incremental read looks, for a
#: member whose transaction began before that read and committed after it.
SINCE_OVERLAP = timedelta(minutes=10)


@dataclass
class Candidate:
    story_id: int
    count: int
    members: list[cluster.Member]
    #: Its world: 'tech', 'screening' or 'main'. Only an item of the same
    #: world is compared with it (db/045, db/051).
    world: str = "main"
    #: Each member's content_items.id, parallel to `members` (ascending).
    item_ids: list[int] = field(default_factory=list)

    @property
    def item_count(self) -> int:
        """cluster.assign reads candidates by these three names, so the run's
        own list is passed as is (no per-item copy; Chat 3 Phase 1 hotfix)."""
        return self.count

    def add(self, item_id: int | None, member: cluster.Member) -> None:
        """A member in item-id order (a fresh load's order). No id (a stub):
        appended, as before the cache."""
        if item_id is None:
            self.members.append(member)
            return
        at = bisect.bisect_right(self.item_ids, item_id)
        self.item_ids.insert(at, item_id)
        self.members.insert(at, member)


@dataclass
class Refresh:
    kind: str  # 'full' or 'incremental'
    stories: int = 0
    members: int = 0
    added: int = 0
    dropped: int = 0
    bytes_read: int = 0

    def line(self) -> str:
        return (f"candidates {self.kind}: {self.stories} stories, {self.members} members"
                + (f", +{self.added} new, -{self.dropped} gone" if self.kind == "incremental" else "")
                + f", ~{self.bytes_read / 1024:.0f} KB read")


class CandidateCache:
    """One per process for the real world (ingest_job); tests make their own."""

    def __init__(self, *, is_test: bool = False, hours: int = cluster.CANDIDATE_HOURS,
                 reload_every: timedelta = RELOAD_EVERY, clock: Callable[[], float] = time.monotonic,
                 conn: psycopg.Connection | None = None) -> None:
        self.is_test, self.hours, self.reload_every, self.clock = is_test, hours, reload_every, clock
        #: A connection for every read (tests_live's rolled-back one); None: the pool.
        self.conn = conn
        self.stories: dict[int, Candidate] = {}
        self.item_ids: set[int] = set()
        #: The database clock of the last read (full or incremental).
        self.since: datetime | None = None
        self.loaded_at: float | None = None

    # -- reads ----------------------------------------------------------------

    def invalidate(self) -> None:
        """Forget everything: the next refresh is a full load."""
        self.stories, self.item_ids, self.since, self.loaded_at = {}, set(), None, None

    def due_full(self) -> bool:
        return self.loaded_at is None or self.clock() - self.loaded_at >= self.reload_every.total_seconds()

    def refresh(self) -> Refresh:
        return self.full() if self.due_full() else self.incremental()

    def full(self) -> Refresh:
        meter = [0]
        window = ledger.feed_candidate_window(self.hours, is_test=self.is_test, meter=meter, conn=self.conn)
        rows = ledger.feed_candidates(self.hours, is_test=self.is_test, meter=meter, conn=self.conn)
        self.stories, self.item_ids = {}, set()
        for r in rows:
            c = Candidate(r.story_id, r.item_count, [cluster.Member(t, v) for t, v in r.members], r.world,
                          list(r.item_ids))
            self.stories[r.story_id] = c
            self.item_ids.update(r.item_ids)
        self.since, self.loaded_at = window.db_now, self.clock()
        return Refresh("full", len(self.stories), len(self.item_ids), bytes_read=meter[0])

    def incremental(self) -> Refresh:
        meter = [0]
        window = ledger.feed_candidate_window(self.hours, is_test=self.is_test, meter=meter, conn=self.conn)
        gone = [sid for sid in self.stories if sid not in window.stories]
        for sid in gone:
            self.item_ids.difference_update(self.stories.pop(sid).item_ids)
        for sid, (count, _) in window.stories.items():
            if sid in self.stories:
                self.stories[sid].count = count
        since = self.since - SINCE_OVERLAP if self.since is not None else None
        rows = ledger.feed_candidates(self.hours, is_test=self.is_test, since=since,
                                      skip=sorted(self.item_ids), meter=meter, conn=self.conn)
        added = 0
        for r in rows:
            if r.story_id not in window.stories:
                continue  # left the window between the two reads
            c = self.stories.get(r.story_id)
            if c is None:
                c = self.stories[r.story_id] = Candidate(r.story_id, window.stories[r.story_id][0], [], r.world)
            for (title, vec), item_id in zip(r.members, r.item_ids):
                if item_id in self.item_ids:
                    continue
                c.add(item_id, cluster.Member(title, vec))
                self.item_ids.add(item_id)
                added += 1
        self.since = window.db_now
        return Refresh("incremental", len(self.stories), len(self.item_ids), added, len(gone), meter[0])

    # -- the run's own writes ---------------------------------------------------

    def candidates(self) -> list[Candidate]:
        """The run's list: by story id, as FEED_CANDIDATES_SQL orders a fresh
        load. The objects are shared: the run's joins land in the cache."""
        return [self.stories[sid] for sid in sorted(self.stories)]

    def joined(self, story_id: int, item_id: int, member: cluster.Member) -> None:
        c = self.stories.get(story_id)
        if c is not None and item_id not in self.item_ids:
            c.add(item_id, member)
            c.count += 1
            self.item_ids.add(item_id)

    def founded(self, story_id: int, item_id: int, member: cluster.Member, world: str) -> Candidate:
        c = Candidate(story_id, 1, [member], world, [item_id])
        self.stories[story_id] = c
        self.item_ids.add(item_id)
        return c
