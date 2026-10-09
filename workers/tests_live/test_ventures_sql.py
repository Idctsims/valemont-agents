"""db/022 ventures: RLS on all four tables, the append-only log (and the one
cascade it permits), the auto-log, v_venture_today in Chicago time, the seed,
and goals.venture_id going NULL when its venture is deleted.

Every test runs inside ONE transaction that is always rolled back (as in
test_goals_sql): these are app tables in the owner's real account, so a
leftover row would be a real venture on the owner's phone. Test ventures are
slugged `tests-live-*`.

The seed test only READS: it checks the seeded rows exist, not exact counts,
because the owner edits these ventures (a workstream added, a state changed)
and the test must not fail for that.

Skips until db/022 is pasted.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Iterator

import psycopg

from core.paths import load_env

load_env()


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


TABLES = ("ventures", "venture_workstreams", "venture_dates", "venture_log")


class VenturesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            with _rolled_back() as cur:
                cur.execute("SELECT to_regclass('public.ventures')")
                if cur.fetchone()[0] is None:
                    raise unittest.SkipTest("db/022 not pasted")
                cur.execute("SELECT owner_id FROM app_settings")
                cls.owner = cur.fetchone()[0]
        except unittest.SkipTest:
            raise
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")

    def venture(self, cur: psycopg.Cursor, slug: str = "tests-live-v", **cols: object) -> uuid.UUID:
        cols = {"name": "tests_live venture", **cols}
        names = ", ".join(["owner_id", "slug", *cols])
        marks = ", ".join(["%s"] * (2 + len(cols)))
        cur.execute(f"INSERT INTO ventures ({names}) VALUES ({marks}) RETURNING id",
                    (self.owner, slug, *cols.values()))
        return cur.fetchone()[0]

    def workstream(self, cur: psycopg.Cursor, venture: uuid.UUID, name: str = "Track", **cols: object) -> uuid.UUID:
        names = ", ".join(["owner_id", "venture_id", "name", *cols])
        marks = ", ".join(["%s"] * (3 + len(cols)))
        cur.execute(f"INSERT INTO venture_workstreams ({names}) VALUES ({marks}) RETURNING id",
                    (self.owner, venture, name, *cols.values()))
        return cur.fetchone()[0]

    def log(self, cur: psycopg.Cursor, venture: uuid.UUID) -> list[tuple[str, str]]:
        cur.execute("SELECT kind, entry FROM venture_log WHERE venture_id = %s ORDER BY id", (venture,))
        return cur.fetchall()


# ------------------------------------------------------------------------ RLS

class Rls(VenturesTestCase):
    def test_rls_on_with_owner_policies_to_authenticated(self) -> None:
        with _rolled_back() as cur:
            cur.execute("""
                SELECT c.relname, c.relrowsecurity,
                       (SELECT array_agg(p.policyname || ':' || array_to_string(p.roles, ',') ORDER BY p.policyname)
                          FROM pg_policies p WHERE p.tablename = c.relname)
                  FROM pg_class c WHERE c.relname = ANY(%s) ORDER BY c.relname""", (list(TABLES),))
            self.assertEqual(cur.fetchall(), [
                ("venture_dates", True, ["venture_dates_owner:authenticated"]),
                ("venture_log", True, ["venture_log_owner_append:authenticated",
                                       "venture_log_owner_read:authenticated"]),
                ("venture_workstreams", True, ["venture_workstreams_owner:authenticated"]),
                ("ventures", True, ["ventures_owner:authenticated"]),
            ])

    def test_anon_reaches_nothing(self) -> None:
        for table in (*TABLES, "v_venture_today"):
            with self.subTest(table=table), _rolled_back() as cur:
                _act_as(cur, "anon")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(f"SELECT 1 FROM {table} LIMIT 1")

    def test_another_user_sees_and_changes_nothing(self) -> None:
        with _rolled_back() as cur:
            v = self.venture(cur)
            w = self.workstream(cur, v)
            cur.execute("INSERT INTO venture_dates (owner_id, venture_id, workstream_id, label, due_on) "
                        "VALUES (%s, %s, %s, 'd', current_date)", (self.owner, v, w))
            cur.execute("INSERT INTO venture_log (owner_id, venture_id, entry) VALUES (%s, %s, 'x')", (self.owner, v))
            _act_as(cur, "authenticated", uuid.uuid4())
            for table in TABLES:
                cur.execute(f"SELECT count(*) FROM {table} WHERE {'id' if table == 'ventures' else 'venture_id'} = %s", (v,))
                self.assertEqual(cur.fetchone()[0], 0, table)
            cur.execute("SELECT count(*) FROM v_venture_today WHERE venture_id = %s", (v,))
            self.assertEqual(cur.fetchone()[0], 0, "the view honours RLS (security_invoker)")
            cur.execute("UPDATE ventures SET name = 'taken' WHERE id = %s", (v,))
            self.assertEqual(cur.rowcount, 0)

    def test_a_child_row_cannot_point_into_someone_elses_venture(self) -> None:
        with _rolled_back() as cur:
            v = self.venture(cur)  # the owner's, as postgres
            stranger = uuid.uuid4()
            _act_as(cur, "authenticated", stranger)
            with self.assertRaisesRegex(psycopg.errors.InsufficientPrivilege, "row-level security"):
                cur.execute("INSERT INTO venture_workstreams (owner_id, venture_id, name) VALUES (%s, %s, 'w')",
                            (stranger, v))

    def test_owner_writes_through_the_api_roles(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            cur.execute("INSERT INTO ventures (name, slug) VALUES ('tests_live', 'tests-live-own') RETURNING id, owner_id")
            v, owner = cur.fetchone()
            self.assertEqual(owner, self.owner)
            cur.execute("INSERT INTO venture_log (venture_id, entry, kind) VALUES (%s, 'decided', 'decision')", (v,))
            cur.execute("UPDATE ventures SET next_action = 'go' WHERE id = %s", (v,))
            self.assertEqual(self.log(cur, v), [("decision", "decided"), ("auto", "Next action: — → go")])


# ----------------------------------------------------------- append-only log

class AppendOnlyLog(VenturesTestCase):
    def test_update_and_delete_are_refused_even_for_postgres(self) -> None:
        for sql in ("UPDATE venture_log SET entry = 'rewritten' WHERE venture_id = %s",
                    "DELETE FROM venture_log WHERE venture_id = %s"):
            with self.subTest(sql=sql[:6]), _rolled_back() as cur:
                v = self.venture(cur)
                cur.execute("INSERT INTO venture_log (owner_id, venture_id, entry) VALUES (%s, %s, 'kept')", (self.owner, v))
                with self.assertRaisesRegex(psycopg.errors.RaiseException, "append-only"):
                    cur.execute(sql, (v,))

    def test_the_owner_has_no_update_or_delete_grant_either(self) -> None:
        for sql in ("UPDATE venture_log SET entry = 'x'", "DELETE FROM venture_log"):
            with self.subTest(sql=sql[:6]), _rolled_back() as cur:
                _act_as(cur, "authenticated", self.owner)
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(sql)

    def test_truncate_is_refused(self) -> None:
        with _rolled_back() as cur:
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "append-only"):
                cur.execute("TRUNCATE venture_log")

    def test_deleting_the_venture_cascades_through_its_log(self) -> None:
        with _rolled_back() as cur:
            v = self.venture(cur)
            cur.execute("INSERT INTO venture_log (owner_id, venture_id, entry) VALUES (%s, %s, 'a'), (%s, %s, 'b')",
                        (self.owner, v, self.owner, v))
            cur.execute("DELETE FROM ventures WHERE id = %s", (v,))
            self.assertEqual(cur.rowcount, 1)
            cur.execute("SELECT count(*) FROM venture_log WHERE venture_id = %s", (v,))
            self.assertEqual(cur.fetchone()[0], 0)


# ------------------------------------------------------------------ auto-log

class AutoLog(VenturesTestCase):
    def test_stage_next_action_and_blockers_log(self) -> None:
        with _rolled_back() as cur:
            v = self.venture(cur, stage="idea", next_action="old step")
            cur.execute("UPDATE ventures SET stage = 'building' WHERE id = %s", (v,))
            cur.execute("UPDATE ventures SET next_action = 'new step' WHERE id = %s", (v,))
            cur.execute("UPDATE ventures SET blockers = 'waiting on DEP' WHERE id = %s", (v,))
            cur.execute("UPDATE ventures SET blockers = NULL WHERE id = %s", (v,))
            self.assertEqual(self.log(cur, v), [
                ("auto", "Stage: idea → building"),
                ("auto", "Next action: old step → new step"),
                ("auto", "Blockers: — → waiting on DEP"),
                ("auto", "Blockers: waiting on DEP → —"),
            ])

    def test_workstream_state_and_next_action_log(self) -> None:
        with _rolled_back() as cur:
            v = self.venture(cur)
            w = self.workstream(cur, v, "Permitting", next_action="Submit DEP application")
            cur.execute("UPDATE venture_workstreams SET state = 'parked' WHERE id = %s", (w,))
            cur.execute("UPDATE venture_workstreams SET next_action = 'Wait for DEP' WHERE id = %s", (w,))
            self.assertEqual(self.log(cur, v), [
                ("auto", "Permitting · state: active → parked"),
                ("auto", "Permitting · next action: Submit DEP application → Wait for DEP"),
            ])

    def test_notes_names_and_unchanged_values_do_not_log(self) -> None:
        with _rolled_back() as cur:
            v = self.venture(cur, next_action="same")
            w = self.workstream(cur, v)
            cur.execute("UPDATE ventures SET notes = 'n', name = 'renamed', tagline = 't', sort_order = 9 WHERE id = %s", (v,))
            cur.execute("UPDATE ventures SET next_action = 'same' WHERE id = %s", (v,))
            cur.execute("UPDATE venture_workstreams SET notes = 'n', name = 'renamed' WHERE id = %s", (w,))
            self.assertEqual(self.log(cur, v), [])


# --------------------------------------------------------------------- today

class Today(VenturesTestCase):
    def test_today_and_overdue_in_chicago_not_future_not_done_not_archived(self) -> None:
        with _rolled_back() as cur:
            cur.execute("SELECT goal_local_today(), (now() AT TIME ZONE 'America/Chicago')::date")
            today, chicago = cur.fetchone()
            self.assertEqual(today, chicago, "the view's 'today' is Chicago's date")
            v = self.venture(cur, name="tests_live today")
            w = self.workstream(cur, v, "Permitting")
            for label, due, done in (("overdue", today - timedelta(days=1), False),
                                     ("today", today, False),
                                     ("future", today + timedelta(days=1), False),
                                     ("done", today, True)):
                cur.execute("INSERT INTO venture_dates (owner_id, venture_id, workstream_id, label, due_on, done_at) "
                            "VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() END)",
                            (self.owner, v, w, label, due, done))
            cur.execute("SELECT label, overdue, venture_name, venture_slug, workstream_name "
                        "FROM v_venture_today WHERE venture_id = %s ORDER BY due_on", (v,))
            self.assertEqual(cur.fetchall(), [
                ("overdue", True, "tests_live today", "tests-live-v", "Permitting"),
                ("today", False, "tests_live today", "tests-live-v", "Permitting"),
            ])
            cur.execute("UPDATE ventures SET archived_at = now() WHERE id = %s", (v,))
            cur.execute("SELECT count(*) FROM v_venture_today WHERE venture_id = %s", (v,))
            self.assertEqual(cur.fetchone()[0], 0, "an archived venture's dates leave Today")


# -------------------------------------------------------------------- goals

class GoalsLink(VenturesTestCase):
    def test_goal_venture_goes_null_when_the_venture_is_deleted(self) -> None:
        with _rolled_back() as cur:
            v = self.venture(cur)
            cur.execute("INSERT INTO goals (owner_id, title, horizon, period_start, venture_id) "
                        "VALUES (%s, 'tests_live linked', 'weekly', '2020-01-06', %s) RETURNING id", (self.owner, v))
            g = cur.fetchone()[0]
            cur.execute("DELETE FROM ventures WHERE id = %s", (v,))
            cur.execute("SELECT venture_id FROM goals WHERE id = %s", (g,))
            self.assertEqual(cur.fetchone(), (None,))

    def test_a_carried_goal_keeps_its_venture(self) -> None:
        with _rolled_back() as cur:
            v = self.venture(cur)
            cur.execute("INSERT INTO goals (owner_id, title, horizon, period_start, venture_id) "
                        "VALUES (%s, 'tests_live carried', 'weekly', '2020-01-06', %s)", (self.owner, v))
            cur.execute("SELECT carry_over_goals('weekly', '2020-01-06')")
            cur.execute("SELECT venture_id FROM goals WHERE period_start = '2020-01-13' AND title = 'tests_live carried'")
            self.assertEqual(cur.fetchone(), (v,))

    def test_the_owner_may_link_a_goal(self) -> None:
        with _rolled_back() as cur:
            v = self.venture(cur)
            cur.execute("INSERT INTO goals (owner_id, title, horizon, period_start) "
                        "VALUES (%s, 'tests_live link me', 'weekly', '2020-01-06') RETURNING id", (self.owner,))
            g = cur.fetchone()[0]
            _act_as(cur, "authenticated", self.owner)
            cur.execute("UPDATE goals SET venture_id = %s WHERE id = %s", (v, g))
            self.assertEqual(cur.rowcount, 1)


# ---------------------------------------------------------------------- seed

class Seed(VenturesTestCase):
    SLUGS = ["sail-beach-club", "perfect-timing-management", "clipd", "valemont-grow",
             "excursion", "sims-vale-capital", "freelance-web-dev"]

    def test_the_seed_is_present(self) -> None:
        with _rolled_back() as cur:
            cur.execute("SELECT slug FROM ventures WHERE owner_id = %s AND slug = ANY(%s) ORDER BY sort_order",
                        (self.owner, self.SLUGS))
            self.assertEqual([r[0] for r in cur.fetchall()], self.SLUGS, "seven seeded ventures, in order")
            cur.execute("""
                SELECT w.name, w.state FROM venture_workstreams w JOIN ventures v ON v.id = w.venture_id
                 WHERE v.slug = 'sail-beach-club' AND v.owner_id = %s
                   AND w.name IN ('Network', 'Vessel renders', 'Permitting', 'Investor readiness', 'Legal')
                 ORDER BY w.sort_order""", (self.owner,))
            rows = cur.fetchall()
            self.assertEqual([r[0] for r in rows],
                             ["Network", "Vessel renders", "Permitting", "Investor readiness", "Legal"])
            cur.execute("""
                SELECT count(*) FROM venture_log l JOIN ventures v ON v.id = l.venture_id
                 WHERE v.slug = 'sail-beach-club' AND l.kind = 'milestone'
                   AND l.entry LIKE 'Venture HQ created.%%'""")
            self.assertEqual(cur.fetchone()[0], 1, "the one seeded milestone")
