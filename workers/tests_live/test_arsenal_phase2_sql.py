"""The Arsenal, Phase 2's SQL against the real database (db/059; Chat 5
Phase 2; docs/arsenal.md §9):

* OBJECTS: RLS, policies and grants on arm_requests, arsenal_saves,
  arsenal_quizzes and arsenal_quiz_reviews (the owner reads her rows and
  inserts saves, quizzes and reviews; arm_requests only through its
  function; anon nothing; readonly reads); the two views select-only; who
  may execute the new functions.
* ARM ME, as the owner's session: a new key enters arm / pending / arm_me;
  an existing key comes back as it is; a seed is "From your Arsenal"; a real
  request never shows a test row and a test request never a real one; the
  rate count; an arm card never drops.
* SAVES: a save of a pending arm card queues ONE arsenal_arm_verify job,
  ever (save, unsave, save); a redundant event is refused; a test save is
  test and never trains the weights; a real save moves its category like
  two Loves, and an unsave takes it back.
* THE QUIZ: arsenal_quiz_line on a Monday with 4 saved, and not on a
  Tuesday; the review guard recomputes `correct` and the rating; a question
  is answered once; the attempts view.
* CONTEXT: ctx_arsenal's saved_count and recent_saves (real only).
* the worker's queries (core/ledger.py), each run here exactly as written.

Every test runs inside ONE transaction that is always rolled back and reads
only its own rows (by id), except the weights and counts the product itself
reads. Skips until db/059 is pasted. A throwaway e2e marker (an e2e_markers
row inside the transaction) stands in for the web's.
"""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import date, timedelta

import psycopg

from core import ledger
from tests_live.test_arsenal_sql import ArsenalSqlCase
from tests_live.test_feed_rank_sql import _act_as, _rolled_back

MARKER = "tests-live-059-marker"


def arm_card(key: str, **kw: object) -> str:
    card = {"canonical_key": key, "category": "history_military", "title": f"TL {key}",
            "origin": "An origin long enough to pass.", "meaning": "A meaning.", "deploy_line": "A line to deploy.",
            "room": "pitch", "source_work": "A work (2000)", "quote": None, "quote_by": None,
            "source_kind": "wikipedia", "source_page": "TL page"}
    card.update(kw)
    return json.dumps(card)


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


class Phase2Case(ArsenalSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.arsenal_saves')").fetchone()[0] is None:
                raise unittest.SkipTest("db/059 not pasted")

    def as_owner(self, conn: psycopg.Connection, *, marker: bool = False) -> None:
        """The owner's session (authenticated, her sub), optionally with a
        throwaway marker that e2e_marker_ok accepts until the rollback."""
        if marker:
            conn.execute("INSERT INTO e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(%s, 'UTF8')), 'hex'))",
                         (MARKER,))
        _act_as(conn.cursor(), "authenticated", self.owner)

    def as_worker(self, conn: psycopg.Connection) -> None:
        conn.execute("RESET ROLE")

    def card(self, conn: psycopg.Connection, key: str, *, marker: bool = False, **kw: object) -> dict:
        return conn.execute("SELECT arsenal_arm_card(%s::jsonb, %s)",
                            (arm_card(key, **kw), MARKER if marker else None)).fetchone()[0]

    def key(self) -> str:
        return f"tl_{uuid.uuid4().hex[:16]}"

    def jobs(self, conn: psycopg.Connection, ref_id: int) -> int:
        return conn.execute("SELECT count(*) FROM job_queue WHERE kind = 'arsenal_arm_verify' "
                            "AND payload ->> 'ref_id' = %s", (str(ref_id),)).fetchone()[0]

    def weight(self, conn: psycopg.Connection, dim: str, key: str) -> float:
        return conn.execute("SELECT weight FROM arsenal_weights() WHERE dim = %s AND key = %s", (dim, key)).fetchone()[0]


# ====================================================================== objects

class Objects(Phase2Case):
    TABLES = {
        # table: (policies, authenticated may insert)
        "arm_requests": (1, False), "arsenal_saves": (2, True), "arsenal_quizzes": (2, True),
        "arsenal_quiz_reviews": (2, True),
    }

    def test_tables_rls_and_grants(self) -> None:
        with _rolled_back() as conn:
            for table, (policies, insert) in self.TABLES.items():
                with self.subTest(table=table):
                    row = conn.execute(
                        "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                        "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('authenticated', oid, 'SELECT'), "
                        "has_table_privilege('authenticated', oid, 'INSERT'), "
                        "has_table_privilege('authenticated', oid, 'UPDATE,DELETE,TRUNCATE'), "
                        "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                        "FROM pg_class c WHERE oid = %s::regclass", (f"public.{table}",)).fetchone()
                    self.assertEqual(row, (True, policies, False, True, insert, False, True))

    def test_views_are_select_only_and_invoker(self) -> None:
        with _rolled_back() as conn:
            for view in ("v_arsenal_saved", "v_arsenal_quiz_attempts"):
                with self.subTest(view=view):
                    row = conn.execute(
                        "SELECT has_table_privilege('anon', oid, 'SELECT'), "
                        "has_table_privilege('authenticated', oid, 'SELECT'), "
                        "has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('valemont_readonly', oid, 'SELECT'), "
                        "coalesce(array_to_string(reloptions, ','), '') LIKE '%%security_invoker=true%%' "
                        "FROM pg_class WHERE oid = %s::regclass", (f"public.{view}",)).fetchone()
                    self.assertEqual(row, (False, True, False, True, True))

    def test_function_grants(self) -> None:
        with _rolled_back() as conn:
            for fn, auth, readonly in (
                    ("arsenal_arm_card(jsonb,text)", True, False),
                    ("arsenal_arm_record(text,text,text,integer,integer,bigint,bigint[],text,text,text)", True, False),
                    ("arsenal_arm_requests_since(integer)", True, True),
                    ("arsenal_quiz_line(date,boolean)", True, True),
                    ("add_test_arm_ref(text,text,text,text)", True, False),
                    ("add_test_save(text,bigint,bigint,text)", True, False),
                    ("arsenal_saves_guard()", False, False),
                    ("arsenal_saves_queue_verify()", False, False)):
                with self.subTest(fn=fn):
                    row = conn.execute(
                        "SELECT has_function_privilege('anon', %s, 'EXECUTE'), "
                        "has_function_privilege('authenticated', %s, 'EXECUTE'), "
                        "has_function_privilege('valemont_readonly', %s, 'EXECUTE')",
                        (f"public.{fn}",) * 3).fetchone()
                    self.assertEqual(row, (False, auth, readonly))

    def test_the_migration_is_logged(self) -> None:
        with _rolled_back() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM migration_log WHERE name = '059_arsenal_phase2'")
                             .fetchone()[0], 1)


# ====================================================================== arm me

class ArmMe(Phase2Case):
    def test_a_new_key_enters_arm_pending_and_comes_back_as_it_is(self) -> None:
        with _rolled_back() as conn:
            self.as_owner(conn)
            key = self.key()
            new = self.card(conn, key)
            self.assertEqual((new["existing"], new["status"], new["verification"], new["is_test"], new["in_arsenal"]),
                             (False, "arm", "pending", False, False))
            again = self.card(conn, key, title="A different title")
            self.assertEqual((again["existing"], again["id"], again["title"]), (True, new["id"], f"TL {key}"))
            self.as_worker(conn)
            self.assertEqual(conn.execute("SELECT origin_kind, pool_run_id FROM arsenal_refs WHERE id = %s",
                                          (new["id"],)).fetchone(), ("arm_me", None))

    def test_a_seed_is_from_your_arsenal(self) -> None:
        with _rolled_back() as conn:
            self.as_owner(conn)
            seed = self.card(conn, "crossing_the_rubicon")
            self.assertEqual((seed["existing"], seed["status"], seed["in_arsenal"]), (True, "seed", True))

    def test_the_two_worlds_never_meet(self) -> None:
        with _rolled_back() as conn:
            self.as_owner(conn, marker=True)
            real = self.card(conn, self.key())
            self.assertIsNone(conn.execute("SELECT arsenal_arm_card(%s::jsonb, %s)",
                                           (arm_card(real["canonical_key"]), MARKER)).fetchone()[0])
            test = self.card(conn, f"e2e_{uuid.uuid4().hex[:12]}", marker=True)
            self.assertTrue(test["is_test"])
            self.assertIsNone(self.card(conn, test["canonical_key"]))
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                conn.execute("SELECT arsenal_arm_card(%s::jsonb, 'not the marker')", (arm_card(self.key()),))

    def test_a_request_is_recorded_and_counted_and_never_mixes(self) -> None:
        with _rolled_back() as conn:
            self.as_owner(conn, marker=True)
            before = conn.execute("SELECT arsenal_arm_requests_since(60)").fetchone()[0]
            real = self.card(conn, self.key())
            test = self.card(conn, f"e2e_{uuid.uuid4().hex[:12]}", marker=True)
            rid = conn.execute("SELECT arsenal_arm_record('A situation.', 'pitch', 'claude-sonnet-5-5', 900, 4100, "
                               "NULL, %s, NULL, NULL)", ([real["id"]],)).fetchone()[0]
            self.assertEqual(conn.execute("SELECT arsenal_arm_requests_since(60)").fetchone()[0], before + 1)
            self.assertEqual(conn.execute("SELECT is_test, ref_ids, first_card_ms FROM arm_requests WHERE id = %s",
                                          (rid,)).fetchone(), (False, [real["id"]], 900))
            with self.assertRaises(psycopg.errors.RaiseException) as cm, conn.transaction():
                conn.execute("SELECT arsenal_arm_record('Mixed.', NULL, 'mock', 1, 2, NULL, %s, NULL, NULL)",
                             ([real["id"], test["id"]],))
            self.assertIn("real request shows real references only", str(cm.exception))
            # The owner has no direct write path.
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute("INSERT INTO arm_requests (owner_id, situation) VALUES (%s, 'x')", (self.owner,))

    def test_an_arm_card_never_drops(self) -> None:
        with _rolled_back() as conn:
            refs = [self.ref(conn, "finance_business", test=True), self.ref(conn, "film_tv", test=True)]
            self.as_owner(conn, marker=True)
            arm = self.card(conn, f"e2e_{uuid.uuid4().hex[:12]}", marker=True, category="sports")
            self.as_worker(conn)
            word = self.word(conn, test=True)
            self.refused(conn, "INSERT INTO arsenal_drops (drop_date, ref_ids, word_id, is_test) VALUES "
                         "('2000-03-01', %s, %s, true)", ([refs[0], refs[1], arm["id"]], word),
                         "only a pool reference drops")
            self.refused(conn, "UPDATE arsenal_refs SET status = 'pool' WHERE id = %s", (arm["id"],),
                         "arm reference never changes status")


# ====================================================================== saves

class Saves(Phase2Case):
    def test_a_save_of_a_pending_arm_card_queues_one_verify_ever(self) -> None:
        with _rolled_back() as conn:
            self.as_owner(conn)
            arm = self.card(conn, self.key())
            for action in ("save", "unsave", "save"):
                conn.execute("INSERT INTO arsenal_saves (ref_id, action) VALUES (%s, %s)", (arm["id"], action))
            self.assertEqual(self.jobs(conn, arm["id"]), 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM v_arsenal_saved WHERE ref_id = %s",
                                          (arm["id"],)).fetchone()[0], 1)
            self.refused(conn, "INSERT INTO arsenal_saves (ref_id, action) VALUES (%s, 'save')", (arm["id"],),
                         "already saved")

    def test_a_pool_save_queues_nothing_and_an_unsave_needs_a_save(self) -> None:
        with _rolled_back() as conn:
            ref = self.ref(conn, "sports")
            self.as_owner(conn)
            self.refused(conn, "INSERT INTO arsenal_saves (ref_id, action) VALUES (%s, 'unsave')", (ref,), "not saved")
            conn.execute("INSERT INTO arsenal_saves (ref_id, action) VALUES (%s, 'save')", (ref,))
            self.assertEqual(self.jobs(conn, ref), 0)

    def test_a_real_save_is_two_loves_and_an_unsave_takes_it_back(self) -> None:
        with _rolled_back() as conn:
            ref = self.ref(conn, "music_culture", room="homies")
            before = self.weight(conn, "category", "music_culture")
            if before > 1.69:
                self.skipTest(f"music_culture already near the clamp ({before})")
            self.as_owner(conn)
            conn.execute("INSERT INTO arsenal_saves (ref_id, action) VALUES (%s, 'save')", (ref,))
            self.assertAlmostEqual(self.weight(conn, "category", "music_culture") - before, 0.30, places=3)
            conn.execute("INSERT INTO arsenal_saves (ref_id, action) VALUES (%s, 'unsave')", (ref,))
            self.assertAlmostEqual(self.weight(conn, "category", "music_culture"), before, places=5)

    def test_a_test_save_is_test_and_never_trains(self) -> None:
        with _rolled_back() as conn:
            ref = self.ref(conn, "music_culture", test=True)
            before = self.weight(conn, "category", "music_culture")
            self.as_owner(conn, marker=True)
            sid = conn.execute("SELECT add_test_save(%s, %s)", (MARKER, ref)).fetchone()[0]
            self.assertTrue(conn.execute("SELECT is_test FROM arsenal_saves WHERE id = %s", (sid,)).fetchone()[0])
            self.assertEqual(self.weight(conn, "category", "music_culture"), before)
            real = self.card(conn, self.key())
            self.refused(conn, "SELECT add_test_save(%s, %s)", (MARKER, real["id"]), "test save is on a test")
            arm_id = conn.execute("SELECT add_test_arm_ref(%s, 'TL helper arm')", (MARKER,)).fetchone()[0]
            self.assertEqual(conn.execute("SELECT status, verification, is_test FROM arsenal_refs WHERE id = %s",
                                          (arm_id,)).fetchone(), ("arm", "pending", True))


# ====================================================================== the quiz

class Quiz(Phase2Case):
    STATE = {"due": "2026-11-01T00:00:00.000Z", "stability": 1.2, "difficulty": 5.1, "reps": 1, "lapses": 0,
             "state": 1, "elapsed_days": 0, "scheduled_days": 3, "learning_steps": 0,
             "last_review": "2026-10-21T00:00:00.000Z"}

    def test_the_quiz_line_is_a_monday_with_four_saved(self) -> None:
        with _rolled_back() as conn:
            refs = [self.ref(conn, c, test=True) for c in ("sports", "film_tv", "philosophy")]
            word = self.word(conn, test=True)
            self.as_owner(conn, marker=True)
            mon = monday(date.today())
            # Leftover e2e test saves count too: compare with the view itself.
            for r in refs[:2]:
                conn.execute("SELECT add_test_save(%s, %s)", (MARKER, r))
            three = conn.execute("SELECT count(*) FROM v_arsenal_saved WHERE is_test").fetchone()[0]
            line = conn.execute("SELECT arsenal_quiz_line(%s, true)", (mon,)).fetchone()[0]
            self.assertEqual(line is None, three < 4)
            conn.execute("SELECT add_test_save(%s, %s)", (MARKER, refs[2]))
            conn.execute("SELECT add_test_save(%s, NULL, %s)", (MARKER, word))
            refs_n, words_n = conn.execute(
                "SELECT count(*) FILTER (WHERE ref_id IS NOT NULL), count(*) FILTER (WHERE word_id IS NOT NULL) "
                "FROM v_arsenal_saved WHERE is_test").fetchone()
            self.assertEqual(conn.execute("SELECT arsenal_quiz_line(%s, true)", (mon,)).fetchone()[0],
                             {"questions": min(10, 2 * refs_n + words_n)})
            self.assertIsNone(conn.execute("SELECT arsenal_quiz_line(%s, true)", (mon + timedelta(days=1),)).fetchone()[0])

    def test_a_review_is_held_to_the_stored_answer_and_answered_once(self) -> None:
        with _rolled_back() as conn:
            ref = self.ref(conn, "sports", test=True)
            self.as_owner(conn, marker=True)
            conn.execute("SELECT add_test_save(%s, %s)", (MARKER, ref))
            questions = [{"no": 1, "kind": "means", "ref_id": ref, "prompt": "TL", "options": ["A", "B", "C", "D"],
                          "answer": 3}]
            quiz = conn.execute("INSERT INTO arsenal_quizzes (week_start, questions) VALUES (%s, %s::jsonb) RETURNING id",
                                (monday(date.today()), json.dumps(questions))).fetchone()[0]
            self.assertTrue(conn.execute("SELECT is_test FROM arsenal_quizzes WHERE id = %s", (quiz,)).fetchone()[0])
            review = ("INSERT INTO arsenal_quiz_reviews (quiz_id, question_no, week_start, question_kind, chosen, "
                      "correct, rating, fsrs_state, due_at) VALUES (%s, 1, %s, 'means', %s, %s, %s, %s::jsonb, "
                      "'2026-11-01T00:00:00Z') RETURNING id")
            mon = monday(date.today())
            self.refused(conn, review, (quiz, mon, 0, True, "good", json.dumps(self.STATE)), "disagrees with the stored answer")
            self.refused(conn, review, (quiz, mon, 0, False, "good", json.dumps(self.STATE)), "rated good")
            rid = conn.execute(review, (quiz, mon, 0, False, "again", json.dumps(self.STATE))).fetchone()[0]
            self.assertEqual(conn.execute("SELECT ref_id, is_test, correct FROM v_arsenal_quiz_attempts WHERE id = %s",
                                          (rid,)).fetchone(), (ref, True, False))
            with self.assertRaises(psycopg.errors.UniqueViolation), conn.transaction():
                conn.execute(review, (quiz, mon, 3, True, "good", json.dumps(self.STATE)))

    def test_a_quiz_asks_about_saved_items_only(self) -> None:
        with _rolled_back() as conn:
            ref = self.ref(conn, "sports", test=True)
            self.as_owner(conn)
            self.refused(conn, "INSERT INTO arsenal_quizzes (week_start, questions) VALUES (%s, %s::jsonb)",
                         (monday(date.today()), json.dumps([{"no": 1, "kind": "fits", "ref_id": ref, "prompt": "TL",
                                                             "options": ["A", "B", "C", "D"], "answer": 0}])),
                         "not saved")


# ====================================================================== context and the worker

class ContextAndWorker(Phase2Case):
    def test_ctx_arsenal_counts_real_saves_only(self) -> None:
        with _rolled_back() as conn:
            real = self.ref(conn, "sports")
            test = self.ref(conn, "film_tv", test=True)
            self.as_owner(conn, marker=True)
            before = conn.execute("SELECT ctx_arsenal() -> 'saved_count'").fetchone()[0]
            conn.execute("INSERT INTO arsenal_saves (ref_id, action) VALUES (%s, 'save')", (real,))
            conn.execute("SELECT add_test_save(%s, %s)", (MARKER, test))
            ctx = conn.execute("SELECT ctx_arsenal()").fetchone()[0]
            self.assertEqual(ctx["saved_count"], before + 1)
            titles = [s["title"] for s in ctx["recent_saves"]]
            self.assertIn(f"TL {conn.execute('SELECT canonical_key FROM arsenal_refs WHERE id = %s', (real,)).fetchone()[0]}",
                          titles)
            self.assertEqual(ctx["recent_saves"][0]["kind"], "reference")

    def test_the_worker_reads_an_arm_cards_state(self) -> None:
        with _rolled_back() as conn:
            self.as_owner(conn)
            arm = self.card(conn, self.key())
            self.as_worker(conn)
            self.assertEqual(ledger.arsenal_arm_state(arm["id"], conn=conn),
                             ledger.ArsenalArmState("arm", "pending", False, False, False))
            self.assertIsNone(ledger.arsenal_arm_state(-1, conn=conn))

    def test_the_pool_never_repeat_list_skips_arm_cards(self) -> None:
        with _rolled_back() as conn:
            self.as_owner(conn)
            arm = self.card(conn, self.key())
            self.as_worker(conn)
            self.assertNotIn(arm["canonical_key"], ledger.arsenal_recent_keys(60, conn=conn))
