"""db/021 goals: carry-over idempotency, the "period has ended" refusal,
carry_count, the per-horizon period CHECKs, the carry trigger, and RLS.

Every test runs inside ONE transaction that is always rolled back, like
test_push_rls. goals is an app table: a leftover row would be a real goal on
the owner's phone, not a quarantined `_test` row. So nothing is committed.

Test goals live in January 2020, a period no real goal will ever have, so a
carry in these tests can only ever touch the rows the test inserted (the
transaction still sees every real row, and would carry real ones too if they
shared a period).

PostgREST runs a request as role `authenticated` (or `anon`) with the JWT's
claims in `request.jwt.claims`; the tests do the same with SET LOCAL ROLE.

Skips until db/021 is pasted.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid
from contextlib import contextmanager
from datetime import date
from typing import Iterator

import psycopg

from core.paths import load_env

load_env()

WEEK1 = date(2020, 1, 6)    # a Monday
WEEK2 = date(2020, 1, 13)
WEEK3 = date(2020, 1, 20)
MONTH1 = date(2020, 1, 1)


@contextmanager
def _rolled_back() -> Iterator[psycopg.Cursor]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        try:
            yield cur
        finally:
            conn.rollback()


def _act_as(cur: psycopg.Cursor, role: str, sub: uuid.UUID | str | None = None) -> None:
    claims = {"role": role} | ({"sub": str(sub)} if sub else {})
    cur.execute("SELECT set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(sub) if sub else "",))
    cur.execute(f"SET LOCAL ROLE {role}")


class GoalsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            with _rolled_back() as cur:
                cur.execute("SELECT to_regclass('public.goals')")
                if cur.fetchone()[0] is None:
                    raise unittest.SkipTest("db/021 not pasted")
                cur.execute("SELECT owner_id FROM app_settings")
                row = cur.fetchone()
        except unittest.SkipTest:
            raise
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")
        if row is None:
            raise unittest.SkipTest("app_settings is empty")
        cls.owner = row[0]

    def add(self, cur: psycopg.Cursor, title: str, *, horizon: str = "weekly",
            period: date | None = WEEK1, status: str = "open") -> uuid.UUID:
        cur.execute(
            """
            INSERT INTO goals (owner_id, title, horizon, period_start, status, completed_at)
            VALUES (%s, %s, %s, %s, %s, CASE WHEN %s = 'done' THEN now() END)
            RETURNING id
            """,
            (self.owner, title, horizon, period, status, status),
        )
        return cur.fetchone()[0]

    def carry(self, cur: psycopg.Cursor, horizon: str, period: date) -> int:
        cur.execute("SELECT carry_over_goals(%s, %s)", (horizon, period))
        return cur.fetchone()[0]


# ----------------------------------------------------------------- carry-over

class CarryOver(GoalsTestCase):
    def test_second_run_inserts_zero(self) -> None:
        with _rolled_back() as cur:
            self.add(cur, "tests_live: carry me")
            self.add(cur, "tests_live: and me")
            self.assertEqual(self.carry(cur, "weekly", WEEK1), 2)
            self.assertEqual(self.carry(cur, "weekly", WEEK1), 0)
            cur.execute("SELECT count(*) FROM goals WHERE period_start = %s AND horizon = 'weekly'", (WEEK2,))
            self.assertEqual(cur.fetchone()[0], 2)

    def test_only_open_goals_carry_and_the_original_is_untouched(self) -> None:
        with _rolled_back() as cur:
            gid = self.add(cur, "tests_live: open")
            self.add(cur, "tests_live: done", status="done")
            self.add(cur, "tests_live: dropped", status="dropped")
            self.assertEqual(self.carry(cur, "weekly", WEEK1), 1)
            cur.execute("SELECT status, period_start, carry_count FROM goals WHERE id = %s", (gid,))
            self.assertEqual(cur.fetchone(), ("open", WEEK1, 0))
            cur.execute("SELECT title, period_start, carry_count, carried_from FROM goals "
                        "WHERE carried_from = %s", (gid,))
            self.assertEqual(cur.fetchone(), ("tests_live: open", WEEK2, 1, gid))

    def test_carry_count_increments_on_each_carry(self) -> None:
        with _rolled_back() as cur:
            gid = self.add(cur, "tests_live: slips twice")
            self.carry(cur, "weekly", WEEK1)
            self.carry(cur, "weekly", WEEK2)
            cur.execute("""
                SELECT g2.carry_count, g3.carry_count, g3.period_start
                  FROM goals g2 JOIN goals g3 ON g3.carried_from = g2.id
                 WHERE g2.carried_from = %s""", (gid,))
            self.assertEqual(cur.fetchone(), (1, 2, WEEK3))

    def test_monthly_carries_to_the_next_first(self) -> None:
        with _rolled_back() as cur:
            self.add(cur, "tests_live: monthly", horizon="monthly", period=MONTH1)
            self.assertEqual(self.carry(cur, "monthly", MONTH1), 1)
            cur.execute("SELECT count(*) FROM goals WHERE horizon = 'monthly' AND period_start = '2020-02-01'")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_an_unended_period_is_refused(self) -> None:
        for horizon, period_sql in (
            ("weekly", "date_trunc('week', goal_local_today())::date"),
            ("monthly", "date_trunc('month', goal_local_today())::date"),
        ):
            with self.subTest(horizon=horizon), _rolled_back() as cur:
                cur.execute(f"SELECT {period_sql}")
                current = cur.fetchone()[0]
                with self.assertRaisesRegex(psycopg.errors.RaiseException, "has not ended"):
                    self.carry(cur, horizon, current)

    def test_last_week_has_ended(self) -> None:
        with _rolled_back() as cur:
            cur.execute("SELECT date_trunc('week', goal_local_today())::date - 7")
            self.assertGreaterEqual(self.carry(cur, "weekly", cur.fetchone()[0]), 0)

    def test_misaligned_dates_and_long_term_are_refused(self) -> None:
        for horizon, period, message in (
            ("weekly", date(2020, 1, 7), "not a Monday"),
            ("monthly", date(2020, 1, 6), "not the 1st"),
            ("long_term", WEEK1, "weekly or monthly"),
        ):
            with self.subTest(horizon=horizon), _rolled_back() as cur:
                with self.assertRaisesRegex(psycopg.errors.RaiseException, message):
                    self.carry(cur, horizon, period)

    def test_catch_up_list_is_oldest_first_and_advances(self) -> None:
        with _rolled_back() as cur:
            self.add(cur, "tests_live: two weeks behind")
            cur.execute("SELECT goal_periods_to_roll('weekly')")
            self.assertEqual(cur.fetchone()[0], WEEK1)
            self.carry(cur, "weekly", WEEK1)
            cur.execute("SELECT goal_periods_to_roll('weekly')")
            self.assertEqual(cur.fetchone()[0], WEEK2, "the carried row is now the one due")

    def test_move_to_next_week_works_once(self) -> None:
        with _rolled_back() as cur:
            cur.execute("SELECT date_trunc('week', goal_local_today())::date")
            this_week = cur.fetchone()[0]
            gid = self.add(cur, "tests_live: move me", period=this_week)
            cur.execute("SELECT carry_goal(%s)", (gid,))
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT carry_goal(%s)", (gid,))
            self.assertEqual(cur.fetchone()[0], 0)
            done = self.add(cur, "tests_live: done", period=this_week, status="done")
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "not an open"):
                cur.execute("SELECT carry_goal(%s)", (done,))


# ------------------------------------------------------------------ the rules

class Rules(GoalsTestCase):
    def test_period_checks_per_horizon(self) -> None:
        for horizon, period, constraint in (
            ("weekly", date(2020, 1, 7), "goals_weekly_period"),
            ("weekly", None, "goals_weekly_period"),
            ("monthly", date(2020, 1, 6), "goals_monthly_period"),
            ("monthly", None, "goals_monthly_period"),
            ("long_term", WEEK1, "goals_long_term_period"),
        ):
            with self.subTest(horizon=horizon, period=period), _rolled_back() as cur:
                with self.assertRaises(psycopg.errors.CheckViolation) as ctx:
                    self.add(cur, "tests_live: bad period", horizon=horizon, period=period)
                self.assertEqual(ctx.exception.diag.constraint_name, constraint)

    def test_valid_periods_are_accepted(self) -> None:
        with _rolled_back() as cur:
            self.add(cur, "tests_live: weekly", horizon="weekly", period=WEEK1)
            self.add(cur, "tests_live: monthly", horizon="monthly", period=MONTH1)
            self.add(cur, "tests_live: long", horizon="long_term", period=None)

    def test_completed_at_iff_done_and_the_title_bounds(self) -> None:
        for sql, constraint in (
            ("UPDATE goals SET status = 'done' WHERE id = %s", "goals_completed_iff_done"),
            ("UPDATE goals SET completed_at = now() WHERE id = %s", "goals_completed_iff_done"),
            ("UPDATE goals SET title = '' WHERE id = %s", "goals_title_length"),
            ("UPDATE goals SET title = repeat('x', 201) WHERE id = %s", "goals_title_length"),
            ("UPDATE goals SET area = 'travel' WHERE id = %s", "goals_area_check"),
        ):
            with self.subTest(sql=sql), _rolled_back() as cur:
                gid = self.add(cur, "tests_live: rules")
                with self.assertRaises(psycopg.errors.CheckViolation) as ctx:
                    cur.execute(sql, (gid,))
                self.assertEqual(ctx.exception.diag.constraint_name, constraint)

    def test_a_carried_row_must_match_its_original(self) -> None:
        with _rolled_back() as cur:
            gid = self.add(cur, "tests_live: original")
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "next period"):
                # Skips a week and resets the count: the trigger refuses both.
                cur.execute(
                    "INSERT INTO goals (owner_id, title, horizon, period_start, carried_from, carry_count) "
                    "VALUES (%s, 'x', 'weekly', %s, %s, 1)", (self.owner, WEEK3, gid))

    def test_a_carried_goal_cannot_be_deleted(self) -> None:
        with _rolled_back() as cur:
            gid = self.add(cur, "tests_live: original")
            self.carry(cur, "weekly", WEEK1)
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                cur.execute("DELETE FROM goals WHERE id = %s", (gid,))


# ------------------------------------------------------------------------ RLS

class Rls(GoalsTestCase):
    def test_owner_reads_writes_and_carries_their_own(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            cur.execute("INSERT INTO goals (title, horizon, period_start) "
                        "VALUES ('tests_live: mine', 'weekly', %s) RETURNING id, owner_id", (WEEK1,))
            gid, owner = cur.fetchone()
            self.assertEqual(owner, self.owner, "owner_id defaults to auth.uid()")
            cur.execute("UPDATE goals SET status = 'done', completed_at = now() WHERE id = %s", (gid,))
            self.assertEqual(cur.rowcount, 1)
            cur.execute("UPDATE goals SET status = 'open', completed_at = NULL WHERE id = %s", (gid,))
            self.assertEqual(self.carry(cur, "weekly", WEEK1), 1)

    def test_owner_cannot_rewrite_the_carry_history(self) -> None:
        for column, value in (("carry_count", "0"), ("period_start", "'2020-01-13'"),
                              ("carried_from", "NULL"), ("owner_id", "gen_random_uuid()")):
            with self.subTest(column=column), _rolled_back() as cur:
                gid = self.add(cur, "tests_live: history")
                _act_as(cur, "authenticated", self.owner)
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(f"UPDATE goals SET {column} = {value} WHERE id = %s", (gid,))

    def test_another_user_sees_and_changes_nothing(self) -> None:
        with _rolled_back() as cur:
            gid = self.add(cur, "tests_live: hidden")  # as postgres, for the owner
            _act_as(cur, "authenticated", uuid.uuid4())
            cur.execute("SELECT count(*) FROM goals WHERE id = %s", (gid,))
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("UPDATE goals SET title = 'taken' WHERE id = %s", (gid,))
            self.assertEqual(cur.rowcount, 0)
            cur.execute("DELETE FROM goals WHERE id = %s", (gid,))
            self.assertEqual(cur.rowcount, 0)
            self.assertEqual(self.carry(cur, "weekly", WEEK1), 0, "RLS hides the owner's goals from the carry")

    def test_another_user_cannot_write_a_row_for_the_owner(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", uuid.uuid4())
            with self.assertRaisesRegex(psycopg.errors.InsufficientPrivilege, "row-level security"):
                cur.execute("INSERT INTO goals (owner_id, title, horizon, period_start) "
                            "VALUES (%s, 'x', 'weekly', %s)", (self.owner, WEEK1))

    def test_anon_reaches_nothing(self) -> None:
        for sql in ("SELECT 1 FROM goals LIMIT 1",
                    "SELECT carry_over_goals('weekly', '2020-01-06')",
                    "SELECT goal_periods_to_roll('weekly')"):
            with self.subTest(sql=sql), _rolled_back() as cur:
                _act_as(cur, "anon")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(sql)

    def test_rls_is_on_with_exactly_one_owner_policy(self) -> None:
        with _rolled_back() as cur:
            cur.execute("""
                SELECT c.relrowsecurity,
                       (SELECT array_agg(p.policyname || ':' || array_to_string(p.roles, ','))
                          FROM pg_policies p WHERE p.tablename = 'goals')
                  FROM pg_class c WHERE c.relname = 'goals'""")
            self.assertEqual(cur.fetchone(), (True, ["goals_owner:authenticated"]))

    def test_readonly_role_can_read(self) -> None:
        with _rolled_back() as cur:
            cur.execute("SELECT has_table_privilege('valemont_readonly', 'public.goals', 'SELECT'), "
                        "has_table_privilege('valemont_readonly', 'public.goals', 'INSERT')")
            self.assertEqual(cur.fetchone(), (True, False))


if __name__ == "__main__":
    unittest.main()
