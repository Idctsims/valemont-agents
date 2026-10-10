"""db/030 the Morning Brief's storage: append-only briefs and deliveries, one
delivery per date and only for a real brief, the morning shape, latest row
wins per date, ctx_brief for Wags, RLS, the test-brief writer's marker check,
and the section registry: a provider flipped to brief_section appears in the
generator's list (ledger.BRIEF_SECTIONS_SQL, run verbatim).

Every test runs inside ONE transaction that is always rolled back: briefs and
deliveries are permanent, so nothing written here may survive.

Skips until db/030 is pasted.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid
from contextlib import contextmanager
from typing import Iterator

import psycopg

from core import ledger
from core.paths import load_env

load_env()

BODY = {"headline": "tests-live headline", "one_move": "tests-live move.",
        "sections": [{"key": "goals", "title": "Goals", "lines": ["Goals: 1 of 2 done."]}]}


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


def _refused(cur: psycopg.Cursor, sql: str, params: tuple = ()) -> str:
    cur.execute("SAVEPOINT probe")
    try:
        cur.execute(sql, params)
    except psycopg.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT probe")
        return str(exc)
    cur.execute("ROLLBACK TO SAVEPOINT probe")
    raise AssertionError(f"not refused: {sql}")


class BriefTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            with _rolled_back() as cur:
                cur.execute("SELECT to_regclass('public.brief_deliveries')")
                if cur.fetchone()[0] is None:
                    raise unittest.SkipTest("db/030 not pasted")
                cur.execute("SELECT owner_id FROM app_settings")
                cls.owner = cur.fetchone()[0]
        except unittest.SkipTest:
            raise
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")

    def one(self, cur: psycopg.Cursor, sql: str, params: tuple = ()) -> object:
        cur.execute(sql, params)
        return cur.fetchone()[0]

    def brief(self, cur: psycopg.Cursor, *, day: str = "today", body: dict | None = None,
              fallback: bool = True, is_test: bool = False, words: int = 12) -> int:
        d = "goal_local_today()" if day == "today" else f"'{day}'::date"
        return self.one(cur, f"""
            INSERT INTO briefs (period_start, period_end, body, kind, brief_date, fallback, word_count, is_test)
            VALUES (now(), now(), %s, 'morning', {d}, %s, %s, %s) RETURNING id""",
                        (json.dumps(body or BODY), fallback, words, is_test))


class AppendOnly(BriefTestCase):
    def test_briefs_and_deliveries_refuse_update_delete_truncate(self) -> None:
        with _rolled_back() as cur:
            b = self.brief(cur)
            d = self.one(cur, "INSERT INTO brief_deliveries (brief_id, kind, brief_date) "
                              "VALUES (%s, 'morning', goal_local_today()) RETURNING id", (b,))
            _refused(cur, "UPDATE briefs SET body = '{}' WHERE id = %s", (b,))
            _refused(cur, "DELETE FROM briefs WHERE id = %s", (b,))
            _refused(cur, "UPDATE brief_deliveries SET late = true WHERE id = %s", (d,))
            _refused(cur, "DELETE FROM brief_deliveries WHERE id = %s", (d,))
            self.assertIn("append-only", _refused(cur, "TRUNCATE brief_deliveries"))
            self.assertIn("append-only", _refused(cur, "TRUNCATE briefs CASCADE"))

    def test_one_delivery_per_date_and_only_for_a_real_brief(self) -> None:
        with _rolled_back() as cur:
            b = self.brief(cur)
            cur.execute("INSERT INTO brief_deliveries (brief_id, kind, brief_date) VALUES (%s, 'morning', goal_local_today())", (b,))
            self.assertIn("brief_deliveries_once", _refused(cur,
                          "INSERT INTO brief_deliveries (brief_id, kind, brief_date) VALUES (%s, 'morning', goal_local_today())", (b,)))
            t = self.brief(cur, day="2026-01-02", is_test=True)
            self.assertIn("not a real", _refused(cur,
                          "INSERT INTO brief_deliveries (brief_id, kind, brief_date) VALUES (%s, 'morning', '2026-01-02')", (t,)))
            self.assertIn("not a real", _refused(cur,
                          "INSERT INTO brief_deliveries (brief_id, kind, brief_date) VALUES (%s, 'morning', '2026-01-03')", (b,)))

    def test_the_morning_shape_is_enforced(self) -> None:
        with _rolled_back() as cur:
            no_move = {k: v for k, v in BODY.items() if k != "one_move"}
            self.assertIn("briefs_morning_shape", _refused(cur, """
                INSERT INTO briefs (period_start, period_end, body, kind, brief_date, fallback, word_count)
                VALUES (now(), now(), %s, 'morning', goal_local_today(), true, 5)""", (json.dumps(no_move),)))
            # An AI brief must name its model and its ai_usage row.
            self.assertIn("briefs_morning_shape", _refused(cur, """
                INSERT INTO briefs (period_start, period_end, body, kind, brief_date, fallback, word_count)
                VALUES (now(), now(), %s, 'morning', goal_local_today(), false, 5)""", (json.dumps(BODY),)))


class LatestAndContext(BriefTestCase):
    def test_the_latest_row_for_a_date_wins_and_test_briefs_never_count(self) -> None:
        with _rolled_back() as cur:
            self.brief(cur, day="2026-01-05", body=dict(BODY, headline="first"))
            second = self.brief(cur, day="2026-01-05", body=dict(BODY, headline="second"))
            self.brief(cur, day="2026-01-05", body=dict(BODY, headline="test"), is_test=True)
            cur.execute("SELECT id, body->>'headline' FROM v_briefs_latest WHERE kind = 'morning' AND brief_date = '2026-01-05'")
            self.assertEqual(cur.fetchall(), [(second, "second")])

    def test_ctx_brief_is_todays_headline_and_one_move(self) -> None:
        with _rolled_back() as cur:
            empty = self.one(cur, "SELECT ctx_brief()")
            if empty.get("headline") is None:
                self.assertEqual(empty, {"today": None, "note": "No morning brief yet today."})
            self.brief(cur, body=dict(BODY, headline="tests-live today"))
            self.brief(cur, body=dict(BODY, headline="tests-live TEST"), is_test=True)
            ctx = self.one(cur, "SELECT ctx_brief()")
            self.assertEqual(ctx["headline"], "tests-live today")
            self.assertEqual(ctx["one_move"], "tests-live move.")
            self.assertTrue(ctx["plain_mode"])
            self.assertEqual(ctx["date"], str(self.one(cur, "SELECT goal_local_today()")))
            # Wags sees it through the snapshot.
            self.assertEqual(self.one(cur, "SELECT context_snapshot()")["brief"]["headline"], "tests-live today")


class TestBriefsAreNeverTodaysBrief(BriefTestCase):
    """The owner's hard requirement (2026-10-10): a test brief (is_test, the
    e2e marker path) is invisible to EVERY selector of today's brief: ctx_brief,
    the web's real views (v_briefs_latest: Home, /brief, /brief/[date],
    history) and the 09:45 delivery job. Run against the real database, in a
    rolled-back transaction; the push is stubbed and the AI never called."""

    def test_a_newer_test_brief_for_today_never_beats_the_real_one(self) -> None:
        with _rolled_back() as cur:
            today = self.one(cur, "SELECT goal_local_today()")
            real = self.brief(cur, body=dict(BODY, headline="tests-live REAL"))
            test = self.brief(cur, body=dict(BODY, headline="tests-live TEST"), is_test=True)
            self.assertGreater(test, real, "the test brief is the newer row")
            # Wags (ctx_brief, and so context_snapshot).
            self.assertEqual(self.one(cur, "SELECT ctx_brief() ->> 'headline'"), "tests-live REAL")
            # The 09:45 delivery job's selector, through the ledger itself.
            row = ledger.latest_brief("morning", today, conn=cur.connection)
            self.assertEqual((row.id, row.body["headline"]), (real, "tests-live REAL"))
            # The web's real views (Home, /brief, /brief/[date], history).
            cur.execute("SELECT id FROM v_briefs_latest WHERE kind = 'morning' AND brief_date = %s", (today,))
            self.assertEqual(cur.fetchall(), [(real,)])
            self.assertEqual(self.one(cur, "SELECT count(*) FROM v_briefs_latest WHERE id = %s", (test,)), 0)

    def test_with_only_a_test_brief_the_push_job_makes_a_real_one_and_never_pushes_the_test(self) -> None:
        from datetime import date, datetime
        from types import SimpleNamespace
        from unittest import mock

        from core import brief

        day = date(2031, 3, 4)  # a date with no real brief: the test brief is all there is
        sent: list = []

        def fake_send(message):
            sent.append(message)
            return [SimpleNamespace(notification_id=None)]

        with _rolled_back() as cur:
            test = self.brief(cur, day=day.isoformat(), body=dict(BODY, headline="tests-live TEST ONLY"), is_test=True)
            conn = cur.connection
            self.assertIsNone(ledger.latest_brief("morning", day, conn=conn), "the selector does not see it")
            with mock.patch.object(brief, "generate_ai", side_effect=brief.BriefRefused("tests-live: no AI call")):
                brief.morning_brief_push_job(conn=conn, now=datetime(2031, 3, 4, 10, 0), send=fake_send)
                brief.morning_brief_push_job(conn=conn, now=datetime(2031, 3, 4, 10, 15), send=fake_send)
            cur.execute("SELECT brief_id, late FROM brief_deliveries WHERE kind = 'morning' AND brief_date = %s", (day,))
            [(delivered, late)] = cur.fetchall()
            cur.execute("SELECT is_test, fallback, body ->> 'headline' FROM briefs WHERE id = %s", (delivered,))
            is_test, fallback, headline = cur.fetchone()
        self.assertNotEqual(delivered, test, "the test brief is never delivered")
        self.assertFalse(is_test, "a REAL brief was made for the push")
        self.assertTrue(fallback, "with the AI unavailable, the fallback")
        self.assertTrue(late)
        self.assertEqual(len(sent), 1, "one push, once: the second run found the delivery")
        self.assertEqual(sent[0].body, f"Late · {headline}")
        self.assertNotIn("TEST ONLY", sent[0].body)


class Registry(BriefTestCase):
    def test_the_brief_sections_are_goals_ventures_capital_and_never_the_brief(self) -> None:
        with _rolled_back() as cur:
            cur.execute(ledger.BRIEF_SECTIONS_SQL)
            keys = [r[0] for r in cur.fetchall()]
        self.assertEqual(keys[:3], ["goals", "ventures", "capital"])
        self.assertNotIn("brief", keys)

    def test_a_provider_flipped_to_brief_section_appears_with_no_code_change(self) -> None:
        with _rolled_back() as cur:
            cur.execute("""CREATE FUNCTION public.ctx_tests_live_pillar() RETURNS jsonb LANGUAGE sql STABLE
                           AS $$ SELECT '{"x": 1}'::jsonb $$""")
            cur.execute("INSERT INTO context_providers (key, label, fn, brief_section, sort) "
                        "VALUES ('tests_live_pillar', 'Test pillar', 'public.ctx_tests_live_pillar()'::regprocedure, false, 25)")
            cur.execute(ledger.BRIEF_SECTIONS_SQL)
            self.assertNotIn("tests_live_pillar", [r[0] for r in cur.fetchall()])
            cur.execute("UPDATE context_providers SET brief_section = true WHERE key = 'tests_live_pillar'")
            cur.execute(ledger.BRIEF_SECTIONS_SQL)
            rows = cur.fetchall()
            self.assertEqual([r[0] for r in rows][:4], ["goals", "ventures", "tests_live_pillar", "capital"], "in sort order")
            self.assertEqual(rows[2][1], "Test pillar", "titled by its label")


class Access(BriefTestCase):
    def test_the_owner_reads_and_cannot_write(self) -> None:
        with _rolled_back() as cur:
            b = self.brief(cur)
            cur.execute("INSERT INTO brief_deliveries (brief_id, kind, brief_date) VALUES (%s, 'morning', goal_local_today())", (b,))
            _act_as(cur, "authenticated", self.owner)
            self.assertGreaterEqual(self.one(cur, "SELECT count(*) FROM briefs WHERE id = %s", (b,)), 1)
            self.assertEqual(self.one(cur, "SELECT count(*) FROM brief_deliveries WHERE brief_id = %s", (b,)), 1)
            self.assertEqual(self.one(cur, "SELECT count(*) FROM v_briefs_latest WHERE id = %s", (b,)), 1)
            _refused(cur, "INSERT INTO briefs (period_start, period_end, body) VALUES (now(), now(), '{}')")
            _refused(cur, "INSERT INTO brief_deliveries (brief_id, kind, brief_date) VALUES (%s, 'morning', '2026-01-09')", (b,))

    def test_a_stranger_and_anon_see_nothing(self) -> None:
        with _rolled_back() as cur:
            b = self.brief(cur)
            for table in ("brief_deliveries", "v_briefs_latest", "briefs"):
                self.assertFalse(self.one(cur, "SELECT has_table_privilege('anon', %s, 'SELECT')", (f"public.{table}",)), table)
            _act_as(cur, "authenticated", uuid.uuid4())
            self.assertEqual(self.one(cur, "SELECT count(*) FROM briefs WHERE id = %s", (b,)), 0)
            self.assertEqual(self.one(cur, "SELECT count(*) FROM v_briefs_latest"), 0)

    def test_the_test_brief_writer_refuses_a_wrong_marker_and_a_non_owner(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            self.assertIn("marker refused", _refused(cur, "SELECT add_test_brief('wrong', goal_local_today(), %s)", (json.dumps(BODY),)))
            cur.execute("RESET ROLE")
            _act_as(cur, "authenticated", uuid.uuid4())
            self.assertIn("owner only", _refused(cur, "SELECT add_test_brief('wrong', goal_local_today(), %s)", (json.dumps(BODY),)))


if __name__ == "__main__":
    unittest.main()
