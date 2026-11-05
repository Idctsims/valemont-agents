"""db/069 against the real database: e2e_marker_ok fails closed, and the
Brief's slate leads with the NFL on any night it plays.

Every test runs inside ONE transaction that is always rolled back. The empty
markers case disables e2e_markers' append-only trigger and deletes inside
the transaction; the rollback restores both. Brief rows sit on 2031 days, no
real row there. Skips until db/069 is pasted.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import date

import psycopg

from tests_live.test_feed_rank_sql import _act_as, _rolled_back
from tests_live.test_gameday_sql import GamedaySqlCase, local

THU, MON, WED = date(2031, 11, 13), date(2031, 11, 17), date(2031, 11, 19)


class Db069Case(GamedaySqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT 1 FROM migration_log WHERE name = '069_nfl_first_marker_closed'").fetchone() is None:
                raise unittest.SkipTest("db/069 not pasted")


class MarkerFailsClosed(Db069Case):
    def ok(self, cur: psycopg.Cursor, marker: str | None) -> bool | None:
        return cur.execute("SELECT e2e_marker_ok(%s)", (marker,)).fetchone()[0]

    def test_true_for_the_owner_with_the_marker_false_otherwise_never_null(self) -> None:
        marker = f"tl-069-{uuid.uuid4()}"
        with _rolled_back() as conn, conn.cursor() as cur:
            self.assertIs(self.ok(cur, marker), False, "no session")
            cur.execute("INSERT INTO e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(%s, 'UTF8')), 'hex'))",
                        (marker,))
            with conn.transaction():
                _act_as(cur, "authenticated", self.owner)
                cur.execute("RESET ROLE")  # the claims stay; the check runs as the definer either way
                self.assertIs(self.ok(cur, marker), True)
                self.assertIs(self.ok(cur, "not-the-marker"), False)
                self.assertIs(self.ok(cur, None), False)
            with conn.transaction():
                _act_as(cur, "authenticated", uuid.uuid4())
                cur.execute("RESET ROLE")
                self.assertIs(self.ok(cur, marker), False, "a stranger with the right marker")

    def test_an_empty_markers_table_answers_false_and_the_helpers_refuse(self) -> None:
        marker = f"tl-069-{uuid.uuid4()}"
        with _rolled_back() as conn, conn.cursor() as cur:
            cur.execute("ALTER TABLE e2e_markers DISABLE TRIGGER e2e_markers_immutable")
            cur.execute("DELETE FROM e2e_markers")
            cur.execute("ALTER TABLE e2e_markers ENABLE TRIGGER e2e_markers_immutable")
            self.assertEqual(cur.execute("SELECT count(*) FROM e2e_markers").fetchone()[0], 0)
            _act_as(cur, "authenticated", self.owner)
            cur.execute("RESET ROLE")  # the owner's claims stay
            self.assertIs(self.ok(cur, marker), False)
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                cur.execute("SELECT add_test_gameday_event(%s, 'nfl', now(), 'H', 'A')", (marker,))

    def test_grants_unchanged(self) -> None:
        with _rolled_back() as conn:
            self.assertEqual(conn.execute(
                "SELECT prosecdef, has_function_privilege('anon', oid, 'EXECUTE'), "
                "has_function_privilege('authenticated', oid, 'EXECUTE'), "
                "has_function_privilege('valemont_readonly', oid, 'EXECUTE') "
                "FROM pg_proc WHERE oid = 'public.e2e_marker_ok(text)'::regprocedure").fetchone(),
                (True, False, False, False))


class NflFirstAnyNight(Db069Case):
    def test_thursday_monday_and_a_night_without_the_nfl(self) -> None:
        with _rolled_back() as conn:
            self.add(conn, league="cfb", at=local(THU, "18:00"), home="Thu CFB Home", away="Thu CFB Away", tv=["ABC"])
            self.add(conn, at=local(THU, "19:15"), home="Thu NFL Home", away="Thu NFL Away", tv=["Prime Video"])
            self.assertEqual(self.brief(conn, THU)[:2], ["NFL: Thu NFL Away at Thu NFL Home, 7:15 PM on Prime Video.",
                                                         "CFB: Thu CFB Away at Thu CFB Home, 6:00 PM on ABC."])
            self.add(conn, league="cfb", at=local(MON, "18:00"), home="Mon Opp", away="Alabama Crimson Tide",
                     tv=["ESPN"], participants={"home": {"id": "2"}, "away": {"id": "333"}})
            self.add(conn, league="nba", at=local(MON, "18:30"), home="Mon NBA Home", away="Mon NBA Away", tv=["ABC"])
            self.add(conn, at=local(MON, "19:15"), home="Mon NFL Home", away="Mon NFL Away", tv=["ESPN"])
            self.assertEqual(self.brief(conn, MON), ["CFB: Alabama Crimson Tide at Mon Opp, 6:00 PM on ESPN.",
                                                     "NFL: Mon NFL Away at Mon NFL Home, 7:15 PM on ESPN.",
                                                     "NBA: Mon NBA Away at Mon NBA Home, 6:30 PM on ABC."])
            self.add(conn, league="nba", at=local(WED, "18:00"), home="Wed NBA Home", away="Wed NBA Away", tv=["ESPN"])
            self.add(conn, league="cfb", at=local(WED, "19:00"), home="Wed CFB Home", away="Wed CFB Away", tv=["ABC"])
            self.assertEqual(self.brief(conn, WED)[0], "CFB: Wed CFB Away at Wed CFB Home, 7:00 PM on ABC.")


if __name__ == "__main__":
    unittest.main()
