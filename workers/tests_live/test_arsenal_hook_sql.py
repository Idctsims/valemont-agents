"""The Screening Room hook's SQL against the real database (db/062; Chat 5
Phase 3; docs/arsenal.md §10):

* OBJECTS: RLS, the one read policy and grants on arsenal_title_hooks and
  arsenal_title_hook_reruns (the owner reads; nobody but the worker and the
  trigger writes; anon nothing; readonly reads); the triggers; who may
  execute add_test_title_hook_cards.
* ONCE PER TITLE, as the owner's session: a move to watching writes no hook;
  the first finish writes ONE queued hook and ONE arsenal_title_hook job; a
  re-watch (watching, finished again) writes nothing more.
* A TEST TITLE: its finish writes a test hook and no job; the e2e helper
  drafts it (3 cards, one unverified, and a word).
* THE WORKER'S WRITE (core/ledger.py, exactly as written): the draft writes
  title_hook cards tagged to the title and settles the hook drafted in one
  transaction; a drafted hook never changes and takes no new card; a failed
  hook drafts again only through a recorded hand re-run; the stranded query.
* CONTEXT: ctx_arsenal recent_title_cards and ctx_screening arsenal_cards,
  real only.

Every test runs inside ONE transaction that is always rolled back and reads
only its own rows (by id). Skips until db/062 is pasted.
"""

from __future__ import annotations

import random
import unittest
import uuid

import psycopg

from core import ledger
from tests_live.test_arsenal_phase2_sql import MARKER, Phase2Case
from tests_live.test_feed_rank_sql import _rolled_back


def card(key: str, **kw: object) -> ledger.ArsenalCardIn:
    base = dict(canonical_key=key, category="film_tv", title=f"TL {key}", origin="An origin long enough to pass.",
                meaning="A meaning.", deploy_line="A line to deploy.", room="boardroom",
                source_work="TL Hook Show (2026)", quote=None, quote_by=None, source_kind="wikipedia",
                source_page="TL page")
    base.update(kw)
    return ledger.ArsenalCardIn(**base)


class HookCase(Phase2Case):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.arsenal_title_hooks')").fetchone()[0] is None:
                raise unittest.SkipTest("db/062 not pasted")

    def title(self, conn: psycopg.Connection, name: str = "TL Hook Show", kind: str = "tv") -> int:
        """A real title from tmdb_id 899000000 up (never a real TMDB id)."""
        return conn.execute("INSERT INTO titles (tmdb_id, kind, name, overview) VALUES (%s, %s, %s, 'A TL title.') "
                            "RETURNING id", (899_000_000 + random.randrange(999_999), kind, name)).fetchone()[0]

    def move(self, conn: psycopg.Connection, title_id: int, status: str) -> int:
        return conn.execute("INSERT INTO watch_status (title_id, status) VALUES (%s, %s) RETURNING id",
                            (title_id, status)).fetchone()[0]

    def hooks(self, conn: psycopg.Connection, title_id: int) -> list[tuple]:
        return conn.execute("SELECT id, status, is_test, settled_at IS NULL FROM arsenal_title_hooks "
                            "WHERE title_id = %s", (title_id,)).fetchall()

    def jobs(self, conn: psycopg.Connection, title_id: int) -> int:  # type: ignore[override]
        return conn.execute("SELECT count(*) FROM job_queue WHERE kind = 'arsenal_title_hook' "
                            "AND payload ->> 'title_id' = %s", (str(title_id),)).fetchone()[0]


class Objects(HookCase):
    def test_tables_rls_and_grants(self) -> None:
        with _rolled_back() as conn:
            for table in ("arsenal_title_hooks", "arsenal_title_hook_reruns"):
                with self.subTest(table=table):
                    row = conn.execute(
                        "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                        "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('authenticated', oid, 'SELECT'), "
                        "has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                        "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                        "FROM pg_class c WHERE oid = %s::regclass", (f"public.{table}",)).fetchone()
                    self.assertEqual(row, (True, 1, False, True, False, True))

    def test_triggers_and_function_grants(self) -> None:
        with _rolled_back() as conn:
            got = {r[0] for r in conn.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname IN "
                                              "('watch_status_title_hook', 'arsenal_title_hooks_guard', "
                                              "'arsenal_title_hooks_no_truncate', 'arsenal_title_hook_reruns_stamp', "
                                              "'arsenal_title_hook_reruns_immutable', "
                                              "'arsenal_title_hook_reruns_no_truncate')").fetchall()}
            self.assertEqual(len(got), 6, got)
            fn = "public.add_test_title_hook_cards(text,bigint,integer)"
            self.assertEqual(conn.execute(
                "SELECT has_function_privilege('authenticated', %s, 'EXECUTE'), "
                "has_function_privilege('anon', %s, 'EXECUTE'), "
                "has_function_privilege('authenticated', 'public.watch_status_title_hook()', 'EXECUTE')",
                (fn, fn)).fetchone(), (True, False, False))


class OncePerTitle(HookCase):
    def test_the_first_finish_queues_one_and_a_rewatch_nothing(self) -> None:
        with _rolled_back() as conn:
            tid = self.title(conn)
            self.as_owner(conn)
            self.move(conn, tid, "watching")
            self.assertEqual(self.hooks(conn, tid), [])
            ws = self.move(conn, tid, "finished")
            [(hook_id, status, is_test, unsettled)] = self.hooks(conn, tid)
            self.assertEqual((status, is_test, unsettled), ("queued", False, True))
            self.assertEqual(self.jobs(conn, tid), 1)
            self.assertEqual(conn.execute("SELECT watch_status_id FROM arsenal_title_hooks WHERE id = %s",
                                          (hook_id,)).fetchone()[0], ws)
            self.move(conn, tid, "watching")
            self.move(conn, tid, "finished")
            self.assertEqual(len(self.hooks(conn, tid)), 1)
            self.assertEqual(self.jobs(conn, tid), 1)
            # The owner reads it; she cannot write it.
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE arsenal_title_hooks SET note = 'x' WHERE id = %s", (hook_id,))

    def test_a_test_title_gets_a_hook_and_no_job_and_the_helper_drafts_it(self) -> None:
        with _rolled_back() as conn:
            self.as_owner(conn, marker=True)
            tid = conn.execute("SELECT add_test_title(%s, 'tv', 'TL test hook show')", (MARKER,)).fetchone()[0]
            self.move(conn, tid, "finished")
            [(hook_id, status, is_test, _)] = self.hooks(conn, tid)
            self.assertEqual((status, is_test, self.jobs(conn, tid)), ("queued", True, 0))
            self.assertEqual(conn.execute("SELECT add_test_title_hook_cards(%s, %s, 3)", (MARKER, tid)).fetchone()[0],
                             hook_id)
            self.as_worker(conn)
            rows = conn.execute("SELECT verification, origin_kind, is_test, status FROM arsenal_refs "
                                "WHERE source_title_id = %s ORDER BY id", (tid,)).fetchall()
            self.assertEqual(rows, [("unverified", "title_hook", True, "pool")] + [("verified", "title_hook", True, "pool")] * 2)
            h = ledger.arsenal_title_hook(hook_id, conn=conn)
            self.assertEqual((h.status, len(h.ref_ids), h.word_id is not None, h.is_test), ("drafted", 3, True, True))


class WorkerWrite(HookCase):
    def finished(self, conn: psycopg.Connection) -> tuple[int, int]:
        tid = self.title(conn)
        self.as_owner(conn)
        self.move(conn, tid, "finished")
        self.as_worker(conn)
        return tid, self.hooks(conn, tid)[0][0]

    def test_the_draft_writes_title_cards_and_settles_once(self) -> None:
        with _rolled_back() as conn:
            tid, hook_id = self.finished(conn)
            k = f"tl_{uuid.uuid4().hex[:12]}"
            lemma = f"tlhook{uuid.uuid4().hex[:8]}".translate(str.maketrans("0123456789", "abcdefghij"))
            word = ledger.ArsenalWordIn(lemma, "a test meaning", "boardroom", f"Say {lemma} in the room.")
            d = ledger.arsenal_title_hook_draft(hook_id, tid, [(card(f"{k}_a"), None), (card(f"{k}_b"), None)], word,
                                                ai_usage_id=None, note="tl draft", conn=conn)
            self.assertEqual((d.status, len(d.ref_ids), d.word_id is not None, d.skipped_dup), ("drafted", 2, True, 0))
            rows = conn.execute("SELECT origin_kind, source_title_id, status, verification, is_test FROM arsenal_refs "
                                "WHERE id = ANY(%s)", (list(d.ref_ids),)).fetchall()
            self.assertEqual(rows, [("title_hook", tid, "pool", "pending", False)] * 2)
            h = ledger.arsenal_title_hook(hook_id, conn=conn)
            self.assertEqual((h.status, h.ref_ids, h.note), ("drafted", d.ref_ids, "tl draft"))
            self.assertIsNotNone(h.settled_at)
            # Once per title: no second draft, no late card.
            for attempt in (lambda: ledger.arsenal_title_hook_fail(hook_id, "again", conn=conn),
                            lambda: ledger.arsenal_insert_ref(card(f"{k}_late"), pool_run_id=None, vector=None,
                                                              source_title_id=tid, conn=conn)):
                with self.subTest(attempt=attempt), self.assertRaises(psycopg.errors.RaiseException):
                    with conn.transaction():
                        attempt()
            # A drafted hook with a pending card and no job is stranded (an hour on).
            conn.execute("UPDATE job_queue SET status = 'done', finished_at = now() WHERE kind = 'arsenal_title_hook' "
                         "AND payload ->> 'hook_id' = %s", (str(hook_id),))
            # created_at is the guard's now() (this transaction), so the age
            # filter is swapped for "any age" to see the rest of the rule.
            age = "h.created_at < now() - make_interval(hours => %(min_age_h)s)"
            self.assertIn(age, ledger.ARSENAL_STRANDED_HOOKS_SQL)
            any_age = ledger.ARSENAL_STRANDED_HOOKS_SQL.replace(age, "h.created_at <= now()")
            self.assertIn((hook_id,), conn.execute(any_age, {"limit": 1000}).fetchall())
            self.assertNotIn(hook_id, ledger.arsenal_stranded_title_hooks(1000, 1, conn=conn))

    def test_a_failed_hook_drafts_again_only_by_a_recorded_rerun(self) -> None:
        with _rolled_back() as conn:
            tid, hook_id = self.finished(conn)
            ledger.arsenal_title_hook_fail(hook_id, "tl: no card passed", conn=conn)
            k = f"tl_{uuid.uuid4().hex[:12]}"
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                ledger.arsenal_title_hook_draft(hook_id, tid, [(card(f"{k}_x"), None)], None, ai_usage_id=None,
                                                note=None, conn=conn)
            d = ledger.arsenal_title_hook_draft(hook_id, tid, [(card(f"{k}_y"), None)], None, ai_usage_id=None,
                                                note="rerun", rerun_by="tests_live", conn=conn)
            self.assertEqual(d.status, "drafted")
            self.assertEqual(conn.execute("SELECT old_status, by_whom FROM arsenal_title_hook_reruns WHERE hook_id = %s",
                                          (hook_id,)).fetchall(), [("failed", "tests_live")])

    def test_context_lists_real_hooks_only(self) -> None:
        with _rolled_back() as conn:
            tid, hook_id = self.finished(conn)
            k = f"tl_{uuid.uuid4().hex[:12]}"
            ledger.arsenal_title_hook_draft(hook_id, tid, [(card(f"{k}_c"), None)], None, ai_usage_id=None, note=None,
                                            conn=conn)
            self.as_owner(conn, marker=True)
            ttid = conn.execute("SELECT add_test_title(%s, 'movie', 'TL test ctx film')", (MARKER,)).fetchone()[0]
            self.move(conn, ttid, "finished")
            ctx = conn.execute("SELECT ctx_arsenal() -> 'recent_title_cards'").fetchone()[0]
            self.assertIn({"title": "TL Hook Show", "kind": "tv", "state": "drafted", "cards": 1, "checked": 0},
                          [{k2: x[k2] for k2 in ("title", "kind", "state", "cards", "checked")} for x in ctx])
            self.assertFalse(any(x["title"] == "TL test ctx film" for x in ctx))
            fin = conn.execute("SELECT ctx_screening() -> 'finished_recent'").fetchone()[0]
            self.assertTrue(any(x["title"] == "TL Hook Show" and x.get("arsenal_cards") == 1 for x in fin), fin)
            self.assertFalse(any(x["title"] == "TL test ctx film" for x in fin))


if __name__ == "__main__":
    unittest.main()
