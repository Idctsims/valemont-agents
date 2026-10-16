"""The egress follow-up (owner, 2026-10-15): binary vectors (fix 2), vectors
only when the summarizers' order needs them (fix 3), and the egress meter.
No database: the ledger is stubbed and the pool refuses."""

from __future__ import annotations

import struct
import unittest
from array import array
from datetime import date
from types import SimpleNamespace
from unittest import mock

from core import egress, ledger, push, system_jobs
from feed import summarize, tech


class BinaryHalfvec(unittest.TestCase):
    def test_the_loader_reads_pgvectors_binary_halfvec(self) -> None:
        vals = (0.5, -1.25, 0.0009765625, 0.0)
        data = struct.pack(">HH", len(vals), 0) + struct.pack(f">{len(vals)}e", *vals)
        v = ledger._HalfvecBinaryLoader(0).load(data)
        self.assertEqual((v.typecode, list(v)), ("f", list(vals)))
        # 384 dimensions: 772 bytes on the wire, against ~4.7 KB as text.
        self.assertEqual(len(struct.pack(">HH", 384, 0) + struct.pack(">384e", *([0.1] * 384))), 772)

    def test_parsers_take_the_loaders_arrays(self) -> None:
        a = array("f", [0.5, 0.25])
        self.assertEqual(ledger._vec_parse(a), (0.5, 0.25))
        self.assertEqual(list(ledger._vec_array(a)), [0.5, 0.25])

    def test_no_vector_is_read_as_text_any_more(self) -> None:
        import inspect
        import re
        src = inspect.getsource(ledger)
        self.assertEqual(re.findall(r"(?:centroid|embedding|_vec)::text", src), [])


class FakeResult:
    def __init__(self, rows: list[list[bytes | None]]) -> None:
        self.rows = rows
        self.ntuples = len(rows)
        self.nfields = len(rows[0]) if rows else 0

    def get_value(self, r: int, c: int) -> bytes | None:
        return self.rows[r][c]


class Meter(unittest.TestCase):
    def setUp(self) -> None:
        p = mock.patch.object(egress, "_DAY", None)
        p.start()
        self.addCleanup(p.stop)

    def test_result_bytes_are_the_values_plus_row_framing(self) -> None:
        cur = SimpleNamespace(pgresult=FakeResult([[b"12", b"hello"], [b"3", None]]))
        self.assertEqual(egress.result_bytes(cur), (2 + 5 + 1) + 2 * (7 + 4 * 2))  # type: ignore[arg-type]
        self.assertEqual(egress.result_bytes(SimpleNamespace(pgresult=None)), 0)  # type: ignore[arg-type]

    def test_reads_count_against_the_running_job_and_the_day(self) -> None:
        with egress.run("feed_ingest") as m:
            egress.add(1000)
            egress.add(24)
        egress.add(10)  # outside any job
        self.assertEqual((m.bytes, m.note()), (1024, "~1 KB read"))
        day = egress.snapshot()
        self.assertEqual(day.by_job, {"feed_ingest": 1024, "other": 10})
        self.assertEqual(day.total, 1034)

    def test_the_days_note_round_trips_through_a_restart(self) -> None:
        with egress.run("feed_ingest"):
            egress.add(int(12.5 * egress.MB))
        egress.mark_alerted()
        note = egress.day_note(egress.snapshot()) + " · rss 130 MB, job peak 131 MB · ~1 KB read"
        self.assertTrue(note.startswith(f"today {egress.owner_today().isoformat()} ~12.5 MB read (feed_ingest 12.5)"))
        with mock.patch.object(egress, "_DAY", None):
            egress.carry(note)
            day = egress.snapshot()
            self.assertEqual((round(day.total / egress.MB, 1), day.alerted), (12.5, True))

    def test_yesterdays_note_is_not_carried(self) -> None:
        egress.carry("today 2000-01-01 ~900.0 MB read (x 900.0) · alerted")
        day = egress.snapshot()
        self.assertEqual((day.total, day.alerted), (0, False))


class EgressJob(unittest.TestCase):
    def setUp(self) -> None:
        for target, name, value in ((egress, "_DAY", None), (system_jobs, "_EGRESS_CARRIED", False)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ledger, "job_note", return_value=None)
        self.job_note = p.start()
        self.addCleanup(p.stop)
        self.sent: list[push.Message] = []

    def send(self, m: push.Message) -> bool:
        self.sent.append(m)
        return True

    def test_under_budget_it_only_writes_the_note(self) -> None:
        with egress.run("feed_ingest"):
            egress.add(5 * egress.MB)
        run = system_jobs.egress_job(send=self.send)
        self.assertIn("~5.0 MB read (feed_ingest 5.0)", run.job_note)
        self.assertEqual(self.sent, [])

    def test_one_push_a_day_past_the_budget(self) -> None:
        with egress.run("feed_ingest"):
            egress.add(151 * egress.MB)
        first = system_jobs.egress_job(send=self.send)
        system_jobs.egress_job(send=self.send)
        self.assertEqual(len(self.sent), 1)
        m = self.sent[0]
        self.assertEqual((m.kind, m.deep_link, m.tag), ("egress", "/settings/health", "egress"))
        self.assertIn("~151 MB today", m.title)
        self.assertIn("feed_ingest 151 MB", m.body or "")
        self.assertTrue(first.job_note.endswith("· alerted"))

    def test_a_push_no_device_took_is_tried_again(self) -> None:
        with egress.run("feed_ingest"):
            egress.add(200 * egress.MB)
        system_jobs.egress_job(send=lambda m: False)
        self.assertFalse(egress.snapshot().alerted)
        system_jobs.egress_job(send=self.send)
        self.assertEqual(len(self.sent), 1)

    def test_a_restart_carries_the_day_and_its_alert(self) -> None:
        today = egress.owner_today().isoformat()
        self.job_note.return_value = f"today {today} ~160.0 MB read (feed_ingest 160.0) · alerted · rss 120 MB"
        run = system_jobs.egress_job(send=self.send)
        self.assertIn(f"today {today} ~160.0 MB read", run.job_note)
        self.assertEqual(self.sent, [])  # already alerted today, before the restart

    def test_registered_every_15_minutes(self) -> None:
        built = {j.name: j for j in system_jobs.build_system_jobs({})}
        self.assertEqual(built["egress"].expected_interval_s, 900)


def sjob(i: int, pillars: tuple[str, ...] = ("home",)) -> ledger.SummaryJob:
    return ledger.SummaryJob(i, f"Story {i}", "A teaser.", "Src", None, 0, "src", pillars)


class SummarizeReadsVectorsOnlyWhenTheOrderMatters(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[str] = []
        self.pool: list[ledger.SummaryJob] = []
        for name, fn in {
            "feed_retire_stale": lambda *a, **k: 0,
            "feed_summarized_today": lambda **k: 0,
            "feed_follows": lambda **k: [],
            "feed_summary_queue": lambda *a, **k: list(self.pool),
            "feed_story_centroids": lambda ids, **k: self.calls.append("centroids") or {i: array("f", [1.0]) for i in ids},
            "feed_latest_profiles": lambda **k: self.calls.append("profiles") or {},
            "feed_sports_entity_names": lambda **k: frozenset(),
            "_pool": mock.Mock(side_effect=AssertionError("database reached")),
        }.items():
            p = mock.patch.object(ledger, name, fn)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(summarize, "summarize_group", lambda group, **kw: summarize.SummaryRun())
        p.start()
        self.addCleanup(p.stop)

    def test_room_for_the_whole_pool_reads_no_vector_and_no_profile(self) -> None:
        self.pool = [sjob(i) for i in range(5)]
        summarize.summarize_job()
        self.assertEqual(self.calls, [])

    def test_more_work_than_room_reads_them(self) -> None:
        self.pool = [sjob(i) for i in range(summarize.RUN_CAP + 1)]
        summarize.summarize_job()
        self.assertEqual(self.calls, ["centroids", "profiles"])

    def test_the_pool_is_200(self) -> None:
        self.assertEqual((summarize.POOL, tech.POOL), (200, 200))


class TechNeedsRank(unittest.TestCase):
    def j(self, i: int, official: bool) -> ledger.TechSummaryJob:
        return ledger.TechSummaryJob(i, "t", None, "s", official, 0)

    def test_only_when_the_run_must_leave_something(self) -> None:
        few = [self.j(1, True), self.j(2, False)]
        self.assertFalse(tech.needs_rank(few, community_room=10))
        self.assertTrue(tech.needs_rank(few, community_room=0))          # community over its room
        many_official = [self.j(i, True) for i in range(tech.RUN_CAP + 1)]
        self.assertTrue(tech.needs_rank(many_official, community_room=10))
        exact = [self.j(i, True) for i in range(tech.RUN_CAP)]
        self.assertFalse(tech.needs_rank(exact, community_room=10))


if __name__ == "__main__":
    unittest.main()
