"""The in-memory cluster candidates (egress fix 1; feed/candidates.py).

A refreshed cache must equal a fresh 48 h load: the same stories in the same
order, each with the same members (title and vector) in item order and the
database's count. Checked after other writers' items, the run's own joins and
new stories, a deleted story, a story aging out, and a migration that moves
items between stories (healed by the daily reload).
"""

from __future__ import annotations

import itertools
import unittest
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from unittest import mock

from core import ledger
from feed import candidates as cmod
from feed import cluster, ingest

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


@dataclass
class Item:
    id: int
    story: int
    title: str
    vec: tuple[float, ...]
    fetched_at: datetime


@dataclass
class Story:
    id: int
    first_seen: datetime
    world: str = "main"
    items: list[int] = field(default_factory=list)


class FakeDb:
    """Stories and items with ids, fetch times and a database clock; the two
    ledger reads behave as FEED_CANDIDATES_SQL and the window SQL do."""

    def __init__(self) -> None:
        self.now = T0
        self.ids = itertools.count(1000)
        self.stories: dict[int, Story] = {}
        self.items: dict[int, Item] = {}
        self.reads: list[str] = []

    def install(self, case: unittest.TestCase) -> None:
        for name in ("feed_candidates", "feed_candidate_window"):
            p = mock.patch.object(ledger, name, getattr(self, name))
            p.start()
            case.addCleanup(p.stop)
        p = mock.patch.object(ledger, "_pool", side_effect=AssertionError("database reached"))
        p.start()
        case.addCleanup(p.stop)

    # -- writers ---------------------------------------------------------------

    def story(self, title: str, vec: tuple[float, ...], *, world: str = "main", age: timedelta = timedelta(0)) -> tuple[int, int]:
        sid = next(self.ids)
        self.stories[sid] = Story(sid, self.now - age, world)
        return sid, self.item(sid, title, vec)

    def item(self, sid: int, title: str, vec: tuple[float, ...]) -> int:
        iid = next(self.ids)
        self.items[iid] = Item(iid, sid, title, vec, self.now)
        self.stories[sid].items.append(iid)
        return iid

    def move(self, iid: int, to: int) -> None:
        """A migration moving an item to another story (no such code path in
        the worker; db/034-style data fixes do it)."""
        it = self.items[iid]
        self.stories[it.story].items.remove(iid)
        self.stories[to].items.append(iid)
        it.story = to

    def delete(self, sid: int) -> None:
        for iid in self.stories.pop(sid).items:
            del self.items[iid]

    # -- the two reads -----------------------------------------------------------

    def _window(self, hours: int) -> list[Story]:
        return sorted((s for s in self.stories.values() if s.first_seen > self.now - timedelta(hours=hours)),
                      key=lambda s: s.id)

    def feed_candidates(self, hours, *, is_test=False, since=None, skip=(), meter=None, **kw):
        self.reads.append("full" if since is None and not skip else "since")
        out = []
        for s in self._window(hours):
            members = [self.items[i] for i in sorted(s.items)
                       if (since is None or self.items[i].fetched_at > since) and i not in set(skip)]
            if members:
                out.append(ledger.CandidateStory(s.id, len(s.items), tuple((m.title, array("f", m.vec)) for m in members),
                                                 s.world, tuple(m.id for m in members)))
                if meter is not None:
                    meter[0] += 4800 * len(members)
        return out

    def feed_candidate_window(self, hours, *, is_test=False, meter=None, **kw):
        if meter is not None:
            meter[0] += 50 * len(self.stories)
        return ledger.CandidateWindow(self.now, {s.id: (len(s.items), s.world) for s in self._window(hours)})


def shape(cands) -> list[tuple]:
    return [(c.story_id, c.count, c.world, [(m.title, tuple(m.vector)) for m in c.members]) for c in cands]


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class CacheCase(unittest.TestCase):
    def setUp(self) -> None:
        self.db = FakeDb()
        self.db.install(self)
        self.clock = Clock()
        self.cache = cmod.CandidateCache(clock=self.clock)

    def fresh(self) -> list[tuple]:
        """What a full load would give now (a second cache, loaded fresh)."""
        c = cmod.CandidateCache()
        c.full()
        return shape(c.candidates())

    def tick(self, minutes: int = 30) -> None:
        self.db.now += timedelta(minutes=minutes)
        self.clock.t += minutes * 60


class FullAndIncremental(CacheCase):
    def test_the_first_refresh_is_full_and_ordered_like_the_sql(self) -> None:
        b, _ = self.db.story("beta", (0.0, 1.0))
        a, _ = self.db.story("alpha", (1.0, 0.0))
        self.db.item(b, "beta two", (0.1, 0.9))
        r = self.cache.refresh()
        self.assertEqual((r.kind, r.stories, r.members), ("full", 2, 3))
        self.assertEqual([c.story_id for c in self.cache.candidates()], sorted([a, b]))
        self.assertEqual(shape(self.cache.candidates()), self.fresh())

    def test_another_writers_items_are_read_once_and_match_a_fresh_load(self) -> None:
        a, _ = self.db.story("alpha", (1.0, 0.0))
        self.cache.refresh()
        self.tick()
        self.db.item(a, "alpha two", (0.9, 0.1))      # another writer joins
        n, _ = self.db.story("new one", (0.0, 1.0))   # and founds a story
        r = self.cache.refresh()
        self.assertEqual((r.kind, r.added, r.dropped), ("incremental", 2, 0))
        self.assertEqual(shape(self.cache.candidates()), self.fresh())
        self.assertIn(n, self.cache.stories)
        # Nothing new: nothing added (the overlap re-reads nothing held).
        self.tick()
        self.assertEqual(self.cache.refresh().added, 0)
        self.assertEqual(shape(self.cache.candidates()), self.fresh())

    def test_the_runs_own_writes_are_never_read_back(self) -> None:
        a, _ = self.db.story("alpha", (1.0, 0.0))
        self.cache.refresh()
        # The run joins one item and founds one story, writing both to the db.
        iid = self.db.item(a, "alpha two", (0.9, 0.1))
        self.cache.joined(a, iid, cluster.Member("alpha two", array("f", (0.9, 0.1))))
        sid, first = self.db.story("founded", (0.0, 1.0))
        self.cache.founded(sid, first, cluster.Member("founded", array("f", (0.0, 1.0))), "main")
        self.tick()
        self.assertEqual(self.cache.refresh().added, 0)
        self.assertEqual(shape(self.cache.candidates()), self.fresh())

    def test_a_late_commit_inside_the_overlap_is_still_read(self) -> None:
        a, _ = self.db.story("alpha", (1.0, 0.0))
        self.cache.refresh()
        # A member stamped 5 minutes BEFORE the last read (its transaction
        # began earlier) but committed after it.
        iid = self.db.item(a, "late", (0.8, 0.2))
        self.db.items[iid].fetched_at = self.db.now - timedelta(minutes=5)
        self.tick()
        self.assertEqual(self.cache.refresh().added, 1)
        self.assertEqual(shape(self.cache.candidates()), self.fresh())

    def test_counts_are_the_databases(self) -> None:
        a, _ = self.db.story("alpha", (1.0, 0.0))
        self.cache.refresh()
        self.cache.stories[a].count = 99  # drift
        self.tick()
        self.cache.refresh()
        self.assertEqual(self.cache.stories[a].count, 1)


class Leaving(CacheCase):
    def test_a_deleted_story_drops_at_the_next_run(self) -> None:
        a, _ = self.db.story("alpha", (1.0, 0.0))
        b, _ = self.db.story("beta", (0.0, 1.0))
        self.cache.refresh()
        self.db.delete(a)
        self.tick()
        r = self.cache.refresh()
        self.assertEqual((r.kind, r.dropped), ("incremental", 1))
        self.assertEqual(list(self.cache.stories), [b])
        self.assertEqual(shape(self.cache.candidates()), self.fresh())

    def test_a_story_past_48_hours_drops(self) -> None:
        self.db.story("old", (1.0, 0.0), age=timedelta(hours=47, minutes=50))
        young, _ = self.db.story("young", (0.0, 1.0))
        self.cache.refresh()
        self.tick()
        self.cache.refresh()
        self.assertEqual(list(self.cache.stories), [young])

    def test_a_merge_heals_at_the_daily_reload(self) -> None:
        a, ia = self.db.story("alpha", (1.0, 0.0), age=timedelta(hours=3))
        b, _ = self.db.story("beta", (0.0, 1.0), age=timedelta(hours=3))
        for it in self.db.items.values():
            it.fetched_at = self.db.now - timedelta(hours=3)  # long before any overlap
        self.cache.refresh()
        self.db.move(ia, b)   # a's only item moved into b
        self.db.delete(a)     # and a removed
        self.tick()
        self.cache.refresh()  # the window drops a at once ...
        self.assertNotIn(a, self.cache.stories)
        # ... but the moved member is only seen by a full reload.
        self.assertNotEqual(shape(self.cache.candidates()), self.fresh())
        self.clock.t += cmod.RELOAD_EVERY.total_seconds()
        self.assertEqual(self.cache.refresh().kind, "full")
        self.assertEqual(shape(self.cache.candidates()), self.fresh())


class Reloads(CacheCase):
    def test_full_at_boot_then_daily(self) -> None:
        self.db.story("alpha", (1.0, 0.0))
        kinds = []
        for _ in range(49):           # 24.5 h of 30-minute runs
            kinds.append(self.cache.refresh().kind)
            self.tick()
        self.assertEqual(kinds[0], "full")
        self.assertEqual(kinds.count("full"), 2)
        self.assertEqual(kinds.index("full", 1), 48)

    def test_invalidate_forces_a_full_load(self) -> None:
        self.db.story("alpha", (1.0, 0.0))
        self.cache.refresh()
        self.cache.invalidate()
        self.assertEqual(self.cache.refresh().kind, "full")

    def test_the_incremental_read_is_small(self) -> None:
        for n in range(200):
            self.db.story(f"story {n}", (1.0, float(n)))
        full = self.cache.refresh().bytes_read
        self.tick()
        inc = self.cache.refresh().bytes_read
        self.assertGreater(full, 900_000)
        self.assertLess(inc, full / 50)


class IngestUsesTheCache(CacheCase):
    """The run's list comes from the cache and its writes go into it once."""

    def test_prepare_takes_the_cache_list_and_writes_update_it_once(self) -> None:
        a, _ = self.db.story("alpha", (1.0, 0.0))
        run = ingest.Ingest(get=lambda *a, **k: None, embed=lambda t: [[0.0]] * len(t), cache=self.cache)  # type: ignore[arg-type]
        with mock.patch.object(ledger, "feed_rules", return_value=[]):
            run.prepare({})
        self.assertIs(run.candidates[0], self.cache.stories[a])
        self.cache.joined(a, 5000, cluster.Member("j", array("f", (1.0, 0.0))))
        self.cache.joined(a, 5000, cluster.Member("j", array("f", (1.0, 0.0))))  # never twice
        self.assertEqual((self.cache.stories[a].count, len(self.cache.stories[a].members)), (2, 2))

    def test_a_failed_run_invalidates_the_cache(self) -> None:
        self.db.story("alpha", (1.0, 0.0))
        self.cache.refresh()

        class Boom(ingest.Ingest):
            def run_source(self, src):  # type: ignore[override]
                raise RuntimeError("boom")

        run = Boom(get=lambda *a, **k: None, embed=lambda t: [[0.0]] * len(t), cache=self.cache)  # type: ignore[arg-type]
        src = mock.Mock(slug="s")
        with mock.patch.object(ledger, "feed_rules", return_value=[]), \
             mock.patch.object(ledger, "feed_active_sources", return_value=[src]), \
             mock.patch.object(ingest, "_SYNCED", True), self.assertRaises(RuntimeError):
            ingest.ingest_job(ingest=run)
        self.assertIsNone(self.cache.loaded_at)

    def test_the_job_uses_the_process_cache(self) -> None:
        self.assertIsInstance(ingest.CANDIDATES, cmod.CandidateCache)
        self.assertFalse(ingest.CANDIDATES.is_test)


if __name__ == "__main__":
    unittest.main()
