"""db/088's screening_vibe_searches against the real database (owner,
2026-11-21; docs/screening.md §8): More like…'s stored searches, append-only;
the owner reads and inserts through RLS, nobody updates, deletes or
truncates; the read-only role reads.

Every test runs inside ONE rolled-back transaction with is_test rows only (no
real search is ever written). Skips until db/088 is pasted.
"""

from __future__ import annotations

import unittest

import psycopg
from psycopg.types.json import Jsonb

from tests_live.test_feed_rank_sql import RankSqlCase, _rolled_back

PICKS = Jsonb([{"tmdbId": 652, "kind": "movie", "name": "Troy", "year": 2004, "posterPath": None,
                "reason": "Ancient epic", "services": []}])


class VibeSearches(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.screening_vibe_searches')").fetchone()[0] is None:
                raise unittest.SkipTest("db/088 not pasted")

    def refused(self, conn: psycopg.Connection, sql: str, params: tuple,
                kind: type[Exception] = psycopg.errors.RaiseException) -> None:
        with self.assertRaises(kind), conn.transaction():
            conn.execute(sql, params)

    def insert(self, conn: psycopg.Connection, key: str = "movie:98", label: str = "Gladiator (2000)", picks=PICKS) -> int:
        return conn.execute(
            "INSERT INTO screening_vibe_searches (anchor_key, label, picks, model, is_test) "
            "VALUES (%s, %s, %s, 'mock', true) RETURNING id", (key, label, picks)).fetchone()[0]

    def test_stamped_and_read_back_as_the_cache_reads_it(self) -> None:
        with _rolled_back() as conn:
            sid = self.insert(conn)
            self.insert(conn, key="text:90s heist movies", label="90s heist movies")
            self.assertEqual(conn.execute(
                "SELECT created_at = now(), picks -> 0 ->> 'name' FROM screening_vibe_searches WHERE id = %s",
                (sid,)).fetchone(), (True, "Troy"))
            # The route's cache read: the newest row for the key, test rows apart.
            self.assertEqual(conn.execute(
                "SELECT id FROM screening_vibe_searches WHERE anchor_key = 'movie:98' AND is_test "
                "AND created_at >= now() - interval '30 days' ORDER BY created_at DESC LIMIT 1").fetchone()[0], sid)

    def test_append_only_and_the_checks(self) -> None:
        with _rolled_back() as conn:
            sid = self.insert(conn)
            self.refused(conn, "UPDATE screening_vibe_searches SET label = 'x' WHERE id = %s", (sid,))
            self.refused(conn, "DELETE FROM screening_vibe_searches WHERE id = %s", (sid,))
            self.refused(conn, "TRUNCATE screening_vibe_searches", ())
            check = psycopg.errors.CheckViolation
            for key, label, picks in (("movie:98", "x", Jsonb([])), ("show:98", "x", PICKS), ("text:a", "x", PICKS),
                                      ("movie:98", "   ", PICKS), ("movie:98", "x" * 201, PICKS)):
                with self.subTest(key=key, label=label[:5]):
                    with self.assertRaises(check), conn.transaction():
                        self.insert(conn, key=key, label=label, picks=picks)

    def test_access(self) -> None:
        with _rolled_back() as conn:
            self.assertEqual(conn.execute(
                "SELECT has_table_privilege('anon', 'public.screening_vibe_searches', 'SELECT,INSERT,UPDATE,DELETE'), "
                "has_table_privilege('authenticated', 'public.screening_vibe_searches', 'SELECT'), "
                "has_table_privilege('authenticated', 'public.screening_vibe_searches', 'INSERT'), "
                "has_table_privilege('authenticated', 'public.screening_vibe_searches', 'UPDATE,DELETE,TRUNCATE'), "
                "has_table_privilege('valemont_readonly', 'public.screening_vibe_searches', 'SELECT')").fetchone(),
                (False, True, True, False, True))
            self.assertEqual(conn.execute(
                "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies WHERE tablename = 'screening_vibe_searches') "
                "FROM pg_class WHERE oid = 'public.screening_vibe_searches'::regclass").fetchone(), (True, 2))


if __name__ == "__main__":
    unittest.main()
