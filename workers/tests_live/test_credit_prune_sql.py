"""This PR's SQL against the real database (db/061; owner, 2026-11-03):

* FEED_PRUNE_RUNS: one append-only row per prune, in the prune's own
  transaction; `deleted` counts cascades (items, scores, similarity rows),
  `held` counts each protection the age test met; RLS, grants, triggers.
* QUOTAS: arsenal_log_run stores a submitted run's quotas; the CHECK refuses
  them anywhere else.
* STRANDED SAVES: a real saved Arm Me card whose one verify job failed for
  good is listed; one with a queued job, or unsaved, is not.
* THE CREDIT OUTAGE: AI_CREDIT_OUTAGE_SQL reads the latest delivered credit
  push; QUEUE_DEFER_SQL requeues and gives the attempt back.

Every test runs inside ONE transaction that is always rolled back and reads
only its own rows (by id), except the prune, which is the real prune.
Skips until db/061 is pasted.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import timedelta

import psycopg
from psycopg.types.json import Jsonb

from core import ai, ledger
from tests_live.test_arsenal_phase2_sql import Phase2Case
from tests_live.test_feed_rank_sql import RankSqlCase, _rolled_back


def _skip_without_061() -> None:
    with _rolled_back() as conn:
        if conn.execute("SELECT to_regclass('public.feed_prune_runs')").fetchone()[0] is None:
            raise unittest.SkipTest("db/061 not pasted")


class PruneRuns(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        _skip_without_061()

    def test_objects(self) -> None:
        with _rolled_back() as conn:
            row = conn.execute(
                "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                "has_table_privilege('authenticated', oid, 'SELECT'), "
                "has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                "has_table_privilege('valemont_readonly', oid, 'SELECT'), "
                "(SELECT count(*) FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal) "
                "FROM pg_class c WHERE oid = 'public.feed_prune_runs'::regclass").fetchone()
            self.assertEqual(row, (True, 1, False, True, False, True, 3))

    def test_the_prune_records_cascades_and_what_it_held(self) -> None:
        with _rolled_back() as conn:
            src = self.source(conn)
            old, reacted, trailer = (self.story(conn, src) for _ in range(3))
            ids = [old, reacted, trailer]
            conn.execute("UPDATE stories SET first_seen_at = now() - interval '23 days', "
                         "last_seen_at = now() - interval '22 days' WHERE id = ANY(%s)", (ids,))
            conn.execute("INSERT INTO title_trailers (item_id, status) "
                         "SELECT id, 'unmatched' FROM content_items WHERE story_id = %s", (trailer,))
            self.react(conn, reacted, "open")
            self.score(conn, old, 1.0)
            items = conn.execute("SELECT count(*) FROM content_items WHERE story_id = %s", (old,)).fetchone()[0]
            before = ledger.feed_prune_dry_run(conn=conn)["unreacted_stories_deleted"]
            counts = ledger.feed_prune(conn=conn)
            self.assertEqual(counts["unreacted_stories_deleted"], before["stories"])
            run = conn.execute("SELECT deleted, steps, held, embeddings_cleared, created_at = now() "
                               "FROM feed_prune_runs WHERE id = %s", (counts.run_id,)).fetchone()
            deleted, steps, held, cleared, stamped = run
            self.assertTrue(stamped)
            self.assertEqual(deleted, counts.deleted)
            self.assertEqual(set(deleted), set(ledger.PRUNE_TABLES))
            # The dry run counted the same cascade the prune then recorded. A
            # similarity row may also name an item of a filtered story, which
            # the earlier filtered step already took: never more, maybe fewer.
            step = steps["unreacted_stories_deleted"]
            for table in ("stories", "content_items", "story_scores"):
                self.assertEqual(step[table], before[table], table)
            self.assertLessEqual(step["feed_similarity_log"], before["feed_similarity_log"])
            self.assertGreaterEqual(step["content_items"], items)
            self.assertGreaterEqual(step["story_scores"], 1)
            # Totals are the steps' sums.
            for table in ledger.PRUNE_TABLES:
                self.assertEqual(deleted[table], sum(s.get(table, 0) for s in steps.values()), table)
            self.assertEqual(cleared, counts["embeddings_cleared"])
            h = held["unreacted_stories_deleted"]
            self.assertEqual(set(h), set(ledger.HELD_KEYS))
            self.assertGreaterEqual(h["reactions"], 1)
            self.assertGreaterEqual(h["trailers"], 1)
            self.assertGreaterEqual(h["stories"], 2)
            self.assertEqual({r[0] for r in conn.execute("SELECT id FROM stories WHERE id = ANY(%s)", (ids,))},
                             {reacted, trailer})

    def test_append_only(self) -> None:
        with _rolled_back() as conn:
            rid = conn.execute("INSERT INTO feed_prune_runs (deleted, steps, held, is_test) "
                               "VALUES ('{}', '{}', '{}', true) RETURNING id").fetchone()[0]
            for sql in ("UPDATE feed_prune_runs SET embeddings_cleared = 1 WHERE id = %s",
                        "DELETE FROM feed_prune_runs WHERE id = %s"):
                with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                    conn.execute(sql, (rid,))
            with self.assertRaises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute("INSERT INTO feed_prune_runs (deleted, steps, held, is_test) "
                             "VALUES ('{\"stories\": -1}', '{}', '{}', true)")


class Quotas(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        _skip_without_061()

    def test_a_submitted_run_keeps_its_quotas(self) -> None:
        with _rolled_back() as conn:
            rid = ledger.arsenal_log_run("topup", "submitted", model="claude-sonnet-5-5",
                                         quotas={"music_culture": 8, "sports": 0},
                                         refs_requested=8, words_requested=2, conn=conn)
            self.assertEqual(conn.execute("SELECT quotas FROM arsenal_pool_runs WHERE id = %s", (rid,)).fetchone()[0],
                             {"music_culture": 8, "sports": 0})
            plain = ledger.arsenal_log_run("topup", "submitted", model="claude-sonnet-5-5", conn=conn)
            self.assertIsNone(conn.execute("SELECT quotas FROM arsenal_pool_runs WHERE id = %s",
                                           (plain,)).fetchone()[0])
            with self.assertRaises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute("INSERT INTO arsenal_pool_runs (kind, status, run_id, quotas) "
                             "VALUES ('topup', 'collected', %s, '{\"sports\": 1}')", (rid,))


class StrandedSaves(Phase2Case):
    #: saved_at is this transaction's now(): -1 h lets a save made here count as old.
    def stranded(self, conn: psycopg.Connection) -> list[int]:
        return [r[0] for r in conn.execute(ledger.ARSENAL_STRANDED_SAVES_SQL,
                                           {"limit": 1000, "min_age_h": -1}).fetchall()]

    def test_a_save_whose_job_failed_for_good_is_stranded(self) -> None:
        with _rolled_back() as conn:
            self.as_owner(conn)
            arm = self.card(conn, self.key())
            conn.execute("INSERT INTO arsenal_saves (ref_id, action) VALUES (%s, 'save')", (arm["id"],))
            self.as_worker(conn)
            self.assertNotIn(arm["id"], self.stranded(conn), "its job is still queued")
            conn.execute("UPDATE job_queue SET status = 'failed', attempts = 5, finished_at = now(), "
                         "last_error = 'tl' WHERE kind = 'arsenal_arm_verify' AND payload ->> 'ref_id' = %s",
                         (str(arm["id"]),))
            self.assertIn(arm["id"], self.stranded(conn))
            # Too young for the real threshold.
            self.assertNotIn(arm["id"], ledger.arsenal_stranded_saves(1000, 1, conn=conn))
            self.as_owner(conn)
            conn.execute("INSERT INTO arsenal_saves (ref_id, action) VALUES (%s, 'unsave')", (arm["id"],))
            self.as_worker(conn)
            self.assertNotIn(arm["id"], self.stranded(conn), "no longer saved")


class CreditAndQueue(RankSqlCase):
    def test_the_outage_is_the_latest_delivered_credit_push(self) -> None:
        with _rolled_back() as conn:
            def push(kind: str, status: str = "sent") -> None:
                conn.execute("INSERT INTO notifications (owner_id, kind, title, status, sent_at) "
                             "VALUES (%s, %s, 'tl', %s, CASE WHEN %s = 'queued' THEN NULL ELSE now() END)",
                             (self.owner, kind, status, status))
            push(ai.CREDIT_EMPTY_KIND)
            self.assertTrue(ledger.ai_credit_outage_open(conn=conn))
            push(ai.CREDIT_RESTORED_KIND, "failed")
            self.assertTrue(ledger.ai_credit_outage_open(conn=conn), "an undelivered restore closes nothing")
            push(ai.CREDIT_RESTORED_KIND)
            self.assertFalse(ledger.ai_credit_outage_open(conn=conn))

    def test_defer_requeues_and_gives_the_attempt_back(self) -> None:
        with _rolled_back() as conn:
            # Inserted 'running' inside the transaction: invisible to the worker's poller.
            jid = conn.execute("INSERT INTO job_queue (kind, payload, status, attempts, locked_at) "
                               "VALUES ('noop', %s, 'running', 5, now()) RETURNING id",
                               (Jsonb({"tl": uuid.uuid4().hex}),)).fetchone()[0]
            ledger.queue_defer(jid, ai.CREDIT_NOTE, timedelta(hours=1), conn=conn)
            row = conn.execute("SELECT status, attempts, locked_at, last_error, "
                               "run_after - now() BETWEEN interval '59 minutes' AND interval '61 minutes' "
                               "FROM job_queue WHERE id = %s", (jid,)).fetchone()
            self.assertEqual(row, ("queued", 4, None, ai.CREDIT_NOTE, True))


if __name__ == "__main__":
    unittest.main()
