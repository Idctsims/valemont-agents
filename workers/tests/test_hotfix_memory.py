"""The Chat 3 Phase 1 hotfix (owner, 2026-10-12: Railway RAM 348 MB, the
summary cap starved the feed):

* RSS: read on this platform, sampled per job, written to job_health notes
  next to the job's own note ("capped: ... · rss 84 MB, job peak 131 MB"),
  and the same note on the job's log line (its history);
* the candidate vectors are float32, parse exactly, and cluster as before;
* clients are reused (embed, Claude) and the ingest fetcher is closed;
* the summary backlog alert: one push past 200, re-armed below, adopted
  after a restart.

Stubbed only: no database, no network, no Claude.
"""

from __future__ import annotations

import logging
import os
import sys
import unittest
from array import array
from typing import Any
from unittest import mock

from core import ai, embed, jobs, ledger, memory, push, system_jobs
from feed import cluster, ingest
from feed import summarize


class Rss(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux") or sys.platform == "win32", "RSS is read on Linux and Windows")
    def test_rss_is_read_here(self) -> None:
        mb = memory.rss_mb()
        self.assertIsNotNone(mb)
        self.assertGreater(mb, 5)

    def test_the_sampler_reports_end_and_peak(self) -> None:
        with mock.patch.object(memory, "rss_mb", side_effect=[50.0, 80.0, 60.0, 61.0, 61.0, 61.0, 61.0] + [61.0] * 50):
            with memory.PeakSampler(interval_s=0.001) as s:
                pass
        self.assertEqual(s.note(), f"rss {s.end_mb:.0f} MB, job peak {s.peak_mb:.0f} MB")
        self.assertGreaterEqual(s.peak_mb, s.end_mb)

    def test_no_rss_no_note(self) -> None:
        with mock.patch.object(memory, "rss_mb", return_value=None):
            with memory.PeakSampler(interval_s=0.001) as s:
                pass
        self.assertIsNone(s.note())

    def test_release_never_raises(self) -> None:
        memory.release()


class Notes(unittest.TestCase):
    def setUp(self) -> None:
        self.recorded: list[tuple[str, Any]] = []
        for name, fn in {"job_started": lambda n: None,
                         "job_succeeded": lambda n, note=None: self.recorded.append(("ok", note)),
                         "job_failed": lambda n, e: self.recorded.append(("failed", e))}.items():
            p = mock.patch.object(ledger, name, fn)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(memory.PeakSampler, "note", lambda self: "rss 84 MB, job peak 131 MB")
        p.start()
        self.addCleanup(p.stop)

    def run_job(self, fn) -> None:
        jobs.run_tracked(jobs.SystemJob("probe_job", 60, mock.Mock(), fn))

    def test_every_success_logs_rss(self) -> None:
        self.run_job(lambda: None)
        self.assertEqual(self.recorded, [("ok", "rss 84 MB, job peak 131 MB · ~0 KB read")])

    def test_a_job_note_comes_first(self) -> None:
        self.run_job(lambda: summarize.SummaryRun(capped=True, sent_today=2000))
        self.assertEqual(self.recorded[0][1],
                         "capped: the daily cap of 2000 is reached (2000 sent today) · rss 84 MB, job peak 131 MB · ~0 KB read")

    def test_a_skip_keeps_its_reason_and_adds_rss(self) -> None:
        def skipped():
            raise jobs.JobSkipped("AI budget spent")
        self.run_job(skipped)
        self.assertEqual(self.recorded, [("ok", "skipped: AI budget spent · rss 84 MB, job peak 131 MB · ~0 KB read")])

    def logging_on(self) -> None:
        # tests/__init__ silences logging package-wide; these tests read it.
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)

    def test_the_log_line_carries_the_rss(self) -> None:
        # job_health keeps only the latest note; the log line is the history.
        self.logging_on()
        with self.assertLogs("valemont.jobs", "INFO") as logs:
            self.run_job(lambda: None)
        self.assertEqual(logs.output, ["INFO:valemont.jobs:probe_job rss 84 MB, job peak 131 MB · ~0 KB read"])

    def test_the_log_line_matches_the_recorded_note(self) -> None:
        self.logging_on()
        with self.assertLogs("valemont.jobs", "INFO") as logs:
            self.run_job(lambda: summarize.SummaryRun(capped=True, sent_today=2000))
        self.assertEqual(logs.output, [f"INFO:valemont.jobs:probe_job {self.recorded[0][1]}"])

    def test_a_failure_records_only_the_error(self) -> None:
        def broken():
            raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self.run_job(broken)
        self.assertEqual(self.recorded, [("failed", "RuntimeError: boom")])


class Float32Candidates(unittest.TestCase):
    def test_parse_is_float32_and_exact_for_halfvec_values(self) -> None:
        v = ledger._vec_array("[0.125,-0.5,0.0009765625,1]")
        self.assertEqual((v.typecode, list(v)), ("f", [0.125, -0.5, 0.0009765625, 1.0]))
        self.assertIsNone(ledger._vec_array(None))
        self.assertEqual(v.itemsize * len(v), 16)  # 4 bytes a dimension

    def test_clustering_is_unchanged_with_arrays(self) -> None:
        a, b = [1.0, 0.0, 0.0], [0.97, 0.243, 0.0]
        as_tuples = cluster.assign(a, "Title one", [cluster.Candidate(1, 1, (cluster.Member("Title one", tuple(b)),))])
        as_arrays = cluster.assign(a, "Title one", [cluster.Candidate(1, 1, (cluster.Member("Title one", array("f", b)),))])
        self.assertEqual(as_tuples.story_id, as_arrays.story_id)
        self.assertAlmostEqual(as_tuples.best.cosine, as_arrays.best.cosine, places=6)

    def test_the_run_list_is_passed_to_assign_without_a_copy(self) -> None:
        c = ingest._Candidate(7, 3, [cluster.Member("t", array("f", [1.0, 0.0]))])
        self.assertEqual(c.item_count, 3)
        d = cluster.assign([1.0, 0.0], "t", [c])  # type: ignore[list-item]
        self.assertEqual(d.story_id, 7)

    def test_candidates_are_streamed_not_fetched_all(self) -> None:
        import inspect
        src = inspect.getsource(ledger.feed_candidates)
        self.assertNotIn(".fetchall(", src)
        self.assertIn("_vec_array", src)


class Phase2Audit(unittest.TestCase):
    """The memory audit of Phase 2's rebuild (2026-10-13): the window's
    centroids are float32 and streamed; the rebuild hands memory back."""

    def test_rank_stories_carry_float32_centroids(self) -> None:
        s = ledger._rank_story((1, ["home"], None, "t", None, None, "src", "[0.5,-0.25]"))
        self.assertEqual((s.vec.typecode, list(s.vec)), ("f", [0.5, -0.25]))
        self.assertEqual(len(ledger._rank_story((1, ["home"], None, "t", None, None, "src", None)).vec), 0)

    def test_the_window_and_the_signals_are_streamed(self) -> None:
        import inspect
        for fn in (ledger.feed_stories_to_score, ledger.feed_reaction_signals):
            self.assertNotIn(".fetchall(", inspect.getsource(fn), fn.__name__)

    def test_the_rebuild_releases_memory_even_when_it_fails(self) -> None:
        from feed import profile
        with mock.patch.object(ledger, "feed_follows", side_effect=RuntimeError("db down")), \
                mock.patch.object(memory, "release") as release:
            with self.assertRaises(RuntimeError):
                profile.rebuild(embed=lambda t: [])
        release.assert_called_once_with()


class Clients(unittest.TestCase):
    def tearDown(self) -> None:
        embed.close_http()
        ai._SDK = None

    def test_embed_reuses_one_http_client(self) -> None:
        self.assertIs(embed._http(), embed._http())
        first = embed._http()
        embed.close_http()
        self.assertIsNot(embed._http(), first)

    def test_the_claude_client_is_made_once(self) -> None:
        made = []
        fake = mock.Mock()
        fake.Anthropic = lambda: made.append(1) or object()
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
                mock.patch.dict(sys.modules, {"anthropic": fake}):
            self.assertIs(ai._client(), ai._client())
        self.assertEqual(len(made), 1)

    def test_the_ingest_fetcher_is_closed_even_when_the_run_fails(self) -> None:
        closed = []

        class FakeFetcher:
            def __init__(self) -> None: ...
            def __call__(self, url, headers): raise AssertionError("no fetch")
            def close(self) -> None: closed.append(True)

        with mock.patch.object(ingest, "Fetcher", FakeFetcher), \
                mock.patch.object(ingest, "_SYNCED", True), \
                mock.patch.object(ingest.Ingest, "prepare", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                ingest.ingest_job()
        self.assertEqual(closed, [True])


class Backlog(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[push.Message] = []
        self.count = 0
        self.already: bool = False
        system_jobs._backlog_alerted = None
        for target, name, fn in (
            (ledger, "feed_summary_backlog", lambda h, a, **kw: self.count),
            (ledger, "notification_sent_since", lambda kind, hours: self.already),
            (system_jobs, "_alert", lambda m: self.sent.append(m) or True),
        ):
            p = mock.patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, system_jobs, "_backlog_alerted", None)

    def test_one_push_past_200_rearmed_below(self) -> None:
        for n in (150, 201, 900, 1100, 300):
            self.count = n
            system_jobs.check_summary_backlog()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("201 stories older than 2 h", self.sent[0].body)
        self.assertEqual(self.sent[0].kind, "feed_summary_backlog")
        self.count = 200  # back to the threshold: re-armed, no push
        system_jobs.check_summary_backlog()
        self.count = 250
        system_jobs.check_summary_backlog()
        self.assertEqual(len(self.sent), 2)

    def test_after_a_restart_a_recent_push_is_adopted(self) -> None:
        self.count, self.already = 900, True
        system_jobs.check_summary_backlog()
        self.assertEqual(self.sent, [])

    def test_the_monitor_runs_it(self) -> None:
        names = [j.name for j in system_jobs.build_system_jobs({})]
        self.assertIn("health_monitor", names)
        with mock.patch.object(system_jobs, "check_health") as health, \
                mock.patch.object(system_jobs, "check_summary_backlog") as backlog:
            system_jobs.monitor_pass(["x"])
        health.assert_called_once_with(["x"])
        backlog.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
