"""db/058 against the real database (owner, 2026-10-20; the Oct 19 feed
review): summary_rejections is append-only and written by the summarizer's
ledger call; feed_similarity_log takes 'would_join' (never with joined_by);
the summary save keeps an existing drop_category (the drop is retired).

Every test runs inside ONE rolled-back transaction, on its own rows. Skips
until db/058 is pasted.
"""

from __future__ import annotations

import unittest
import uuid

import psycopg

from core import ledger
from tests_live.test_feed_rank_sql import RankSqlCase, _rolled_back


class FeedReview(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.summary_rejections')").fetchone()[0] is None:
                raise unittest.SkipTest("db/058 not pasted")

    def refused(self, conn: psycopg.Connection, sql: str, params: tuple, message: str,
                kind: type[Exception] = psycopg.errors.RaiseException) -> None:
        with self.assertRaises(kind) as cm, conn.transaction():
            conn.execute(sql, params)
        self.assertIn(message, str(cm.exception))

    def story_and_item(self, conn: psycopg.Connection) -> tuple[int, int]:
        src = conn.execute("SELECT id FROM sources WHERE slug = '_e2e' AND is_test").fetchone()[0]
        st = conn.execute("INSERT INTO stories (pillars, title, is_test) VALUES (ARRAY['home'], 'tl 058', true) "
                          "RETURNING id").fetchone()[0]
        it = conn.execute("INSERT INTO content_items (source_id, external_id, url, title, published_at, story_id, "
                          "is_test) VALUES (%s, %s, %s, 'tl item', now(), %s, true) RETURNING id",
                          (src, f"tl:{uuid.uuid4().hex}", f"https://tl.invalid/{uuid.uuid4().hex}", st)).fetchone()[0]
        return int(st), int(it)

    def test_rejections_are_written_as_the_summarizer_writes_them_and_never_change(self) -> None:
        with _rolled_back() as conn:
            st, _ = self.story_and_item(conn)
            n = ledger.feed_record_summary_rejections(
                [ledger.SummaryRejection(st, 1, "copy: copies 8 words from the teaser: 'x'"),
                 ledger.SummaryRejection(st, 2, "missing: the answer had no result for this story")], conn=conn)
            self.assertEqual(n, 2)
            rows = conn.execute("SELECT id, attempt, reason, created_at = now() FROM summary_rejections "
                                "WHERE story_id = %s ORDER BY id", (st,)).fetchall()
            self.assertEqual([r[1:] for r in rows],
                             [(1, "copy: copies 8 words from the teaser: 'x'", True),
                              (2, "missing: the answer had no result for this story", True)])
            rid = rows[0][0]
            self.refused(conn, "UPDATE summary_rejections SET reason = 'x' WHERE id = %s", (rid,), "")
            self.refused(conn, "DELETE FROM summary_rejections WHERE id = %s", (rid,), "")
            self.refused(conn, "TRUNCATE summary_rejections", (), "TRUNCATE is refused")
            self.refused(conn, "INSERT INTO summary_rejections (story_id, attempt, reason) VALUES (%s, 0, 'x')",
                         (st,), "", psycopg.errors.CheckViolation)
            # No foreign key: deleting the story (the prune's job) leaves the record.
            conn.execute("DELETE FROM content_items WHERE story_id = %s", (st,))
            conn.execute("DELETE FROM stories WHERE id = %s", (st,))
            self.assertEqual(conn.execute("SELECT count(*) FROM summary_rejections WHERE story_id = %s",
                                          (st,)).fetchone()[0], 2)

    def test_would_join_is_logged_and_never_a_join(self) -> None:
        with _rolled_back() as conn:
            st, it = self.story_and_item(conn)
            ledger._log_similarity(conn, it, [ledger.SimilarityRow(st, "would_join", 0.93, 64.0)])
            self.assertEqual(conn.execute("SELECT kind, joined_by FROM feed_similarity_log WHERE item_id = %s",
                                          (it,)).fetchall(), [("would_join", None)])
            self.refused(conn, "INSERT INTO feed_similarity_log (item_id, story_id, kind, cosine, fuzz, joined_by) "
                               "VALUES (%s, %s, 'would_join', 0.93, 64, 'cosine')", (it, st), "",
                         psycopg.errors.CheckViolation)

    def test_a_new_summary_keeps_an_old_drop_category(self) -> None:
        with _rolled_back() as conn:
            st, _ = self.story_and_item(conn)
            conn.execute("UPDATE stories SET drop_category = 'gossip' WHERE id = %s", (st,))
            ledger.feed_save_summaries([ledger.SummaryOut(st, "A new summary.")], None, conn=conn)
            self.assertEqual(conn.execute("SELECT summary, drop_category FROM stories WHERE id = %s",
                                          (st,)).fetchone(), ("A new summary.", "gossip"))

    def test_access(self) -> None:
        with _rolled_back() as conn:
            self.assertEqual(conn.execute(
                "SELECT has_table_privilege('anon', 'public.summary_rejections', 'SELECT,INSERT,UPDATE,DELETE'), "
                "has_table_privilege('authenticated', 'public.summary_rejections', 'SELECT'), "
                "has_table_privilege('authenticated', 'public.summary_rejections', 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                "has_table_privilege('valemont_readonly', 'public.summary_rejections', 'SELECT'), "
                "(SELECT relrowsecurity FROM pg_class WHERE oid = 'public.summary_rejections'::regclass), "
                "(SELECT count(*) FROM pg_policies WHERE tablename = 'summary_rejections')").fetchone(),
                (False, True, False, True, True, 1))


if __name__ == "__main__":
    unittest.main()
