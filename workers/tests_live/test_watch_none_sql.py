"""db/089's off-the-shelf move against the real database (owner,
2026-11-22: the Screening Room's buttons as Letterboxd's). A 'none' move takes
a title off every shelf reader (v_watch_current, v_title_state.shelf) and
keeps it ever finished; a later move puts it back. One rolled-back
transaction, a test title only. Skips until db/089 is pasted.
"""

from __future__ import annotations

import unittest
import uuid

from tests_live.test_feed_rank_sql import RankSqlCase, _rolled_back


class WatchNone(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            done = conn.execute("SELECT 1 FROM migration_log WHERE name = '089_watch_status_none'").fetchone()
            if not done:
                raise unittest.SkipTest("db/089 not pasted")

    def test_off_the_shelf_and_back(self) -> None:
        with _rolled_back() as conn:
            tid = conn.execute(
                "INSERT INTO titles (tmdb_id, kind, name, is_test) VALUES (%s, 'tv', %s, true) RETURNING id",
                (900_000_000 + uuid.uuid4().int % 9_000_000, f"tl-089 {uuid.uuid4().hex[:8]}")).fetchone()[0]

            def move(status: str) -> None:
                conn.execute("INSERT INTO watch_status (owner_id, title_id, status) VALUES (%s, %s, %s)",
                             (self.owner, tid, status))

            def state():
                cur = conn.execute("SELECT status FROM v_watch_current WHERE title_id = %s", (tid,)).fetchone()
                st = conn.execute("SELECT shelf, finished FROM v_title_state WHERE title_id = %s", (tid,)).fetchone()
                return (cur[0] if cur else None, st[0], st[1])

            move("finished")
            self.assertEqual(state(), ("finished", "finished", True))
            move("none")
            self.assertEqual(state(), (None, None, True))
            self.assertIsNone(conn.execute(
                "SELECT finished_at FROM watch_status WHERE title_id = %s AND status = 'none'", (tid,)).fetchone()[0])
            move("want")
            self.assertEqual(state(), ("want", "want", True))


if __name__ == "__main__":
    unittest.main()
