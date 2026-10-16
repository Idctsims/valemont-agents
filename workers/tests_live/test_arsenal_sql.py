"""The Arsenal's SQL against the real database (db/054; Chat 5 Phase 1):

* OBJECTS: RLS, policies and grants on the five tables (the owner reads
  all, inserts only her own reactions; anon nothing; readonly reads), the
  functions' grants (the picker is the worker's alone), the seeds, the
  registry rows.
* THE GUARDS: a drop refuses a seed, an unverified or pending reference, a
  repeat (reference or word), two in one category, and a mixed real/test
  drop; a dropped row is frozen; a real ref enters pending; verification
  settles once; nothing is deleted or truncated.
* NEAR-DUPLICATES inside Postgres: the vector goes in, a boolean comes out.
* WEIGHTS move after a real Love or Not interested; Known and test
  reactions move nothing.
* TEST ROWS are invisible to every real selector: the worker's pool status,
  the real picker, ctx_arsenal and ctx_arsenal_brief, the weights.
* the worker's queries (core/ledger.py), each run here exactly as written.

Every test runs inside ONE transaction that is always rolled back and reads
only its own rows (by id), except the few aggregates the worker itself
reads. Skips until db/054 is pasted.
"""

from __future__ import annotations

import unittest
import uuid
from dataclasses import replace
from datetime import date
from typing import Any

import psycopg

from core import ledger
from tests_live.test_feed_rank_sql import RankSqlCase, _act_as, _rolled_back

VEC_A = [0.0] * 383 + [1.0]


def onehot(i: int) -> list[float]:
    v = [0.0] * 384
    v[i] = 1.0
    return v


class ArsenalSqlCase(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.arsenal_drops')").fetchone()[0] is None:
                raise unittest.SkipTest("db/054 not pasted")

    # -- fixtures (own rows, rolled back) ------------------------------------

    def ref(self, conn: psycopg.Connection, category: str, *, test: bool = False, verification: str = "verified",
            room: str = "boardroom", embedding: list[float] | None = None) -> int:
        """A pool reference. A real one enters pending and is settled as the
        worker settles it; a test one enters settled."""
        key = f"tl_{uuid.uuid4().hex[:16]}"
        rid = conn.execute(
            "INSERT INTO arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, "
            "source_work, source_kind, source_url, status, verification, origin_kind, is_test, embedding) "
            "VALUES (%s, %s, %s, 'An origin long enough to pass.', 'A meaning.', 'A line to deploy.', %s, "
            "'A work', 'wikipedia', %s, 'pool', %s, 'pool', %s, %s::extensions.halfvec) RETURNING id",
            (key, category, f"TL {key}", room, f"https://tl.invalid/{key}",
             verification if test else "pending", test,
             ledger._vec_text(embedding) if embedding else None)).fetchone()[0]
        if not test and verification != "pending":
            conn.execute("UPDATE arsenal_refs SET verification = %s WHERE id = %s", (verification, rid))
        return int(rid)

    def word(self, conn: psycopg.Connection, *, test: bool = False, verification: str = "verified") -> int:
        lemma = f"tlword {uuid.uuid4().hex[:10]}" if test else "tl" + "".join(
            chr(97 + int(c, 16) % 26) for c in uuid.uuid4().hex[:12])
        wid = conn.execute(
            "INSERT INTO arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence, "
            "verification, is_test) VALUES (%s, '/x/', 'From x.', 'https://tl.invalid/w', 'A meaning.', 'pitch', "
            "'A sentence.', %s, %s) RETURNING id",
            (lemma, verification if test else "pending", test)).fetchone()[0]
        if not test and verification != "pending":
            conn.execute("UPDATE arsenal_words SET verification = %s WHERE id = %s", (verification, wid))
        return int(wid)

    def drop(self, conn: psycopg.Connection, day: date, refs: list[int], word: int, *, test: bool = False) -> int:
        return int(conn.execute("INSERT INTO arsenal_drops (drop_date, ref_ids, word_id, is_test) "
                                "VALUES (%s, %s, %s, %s) RETURNING id", (day, refs, word, test)).fetchone()[0])

    def refused(self, conn: psycopg.Connection, sql: str, params: Any, message: str,
                kind: type[Exception] = psycopg.errors.RaiseException) -> None:
        with self.assertRaises(kind) as cm, conn.transaction():
            conn.execute(sql, params)
        self.assertIn(message, str(cm.exception))


# ====================================================================== objects

class Objects(ArsenalSqlCase):
    TABLES = {
        # table: (policies, auth insert)
        "arsenal_pool_runs": (1, False), "arsenal_refs": (1, False), "arsenal_words": (1, False),
        "arsenal_drops": (1, False), "arsenal_reactions": (2, True),
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

    def test_function_grants(self) -> None:
        with _rolled_back() as conn:
            for fn, auth, ro in (("public.arsenal_pick_drop(date, boolean, boolean)", False, False),
                                 ("public.arsenal_near_dup(extensions.halfvec, real)", False, True),
                                 ("public.arsenal_weights()", True, True),
                                 ("public.ctx_arsenal()", True, True),
                                 ("public.ctx_arsenal_brief()", True, True),
                                 ("public.add_test_arsenal_ref(text, text, text, text, text, text, text)", True, False),
                                 ("public.add_test_arsenal_word(text, text, text)", True, False),
                                 ("public.add_test_arsenal_drop(text, bigint[], bigint, date)", True, False)):
                with self.subTest(fn=fn):
                    self.assertEqual(conn.execute(
                        "SELECT has_function_privilege('anon', %s, 'EXECUTE'), "
                        "has_function_privilege('authenticated', %s, 'EXECUTE'), "
                        "has_function_privilege('valemont_readonly', %s, 'EXECUTE')", (fn, fn, fn)).fetchone(),
                        (False, auth, ro))

    def test_seeds_and_registry(self) -> None:
        with _rolled_back() as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM arsenal_refs WHERE status = 'seed' AND origin_kind = 'seed' AND NOT is_test"
            ).fetchone()[0], 27)
            self.assertEqual(conn.execute(
                "SELECT key, brief_section, sort FROM context_providers WHERE key LIKE 'arsenal%%' ORDER BY key"
            ).fetchall(), [("arsenal", False, 60), ("arsenal_brief", True, 60)])
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM arsenal_drops d CROSS JOIN LATERAL unnest(d.ref_ids) x "
                "JOIN arsenal_refs r ON r.id = x WHERE r.origin_kind = 'seed'").fetchone()[0], 0)


# ======================================================================= guards

class Guards(ArsenalSqlCase):
    def test_a_drop_refuses_seeds_unverified_pending_and_mixed_rows(self) -> None:
        with _rolled_back() as conn:
            a, b, c = (self.ref(conn, cat) for cat in ("finance_business", "sports", "film_tv"))
            unv = self.ref(conn, "music_culture", verification="unverified")
            pend = self.ref(conn, "philosophy", verification="pending")
            same = self.ref(conn, "sports")
            t = self.ref(conn, "history_military", test=True)
            w = self.word(conn)
            seed = conn.execute("SELECT id FROM arsenal_refs WHERE canonical_key = 'pyrrhic_victory'").fetchone()[0]
            sql = "INSERT INTO arsenal_drops (drop_date, ref_ids, word_id) VALUES ('2001-01-01', %s, %s)"
            self.refused(conn, sql, ([a, b, seed], w), "a seed never drops")
            self.refused(conn, sql, ([a, b, unv], w), "only a verified or corrected reference drops")
            self.refused(conn, sql, ([a, b, pend], w), "only a verified or corrected reference drops")
            self.refused(conn, sql, ([a, b, same], w), "three different categories")
            self.refused(conn, sql, ([a, b, t], w), "a real drop holds real rows only")
            self.refused(conn, sql, ([a, b], w), "exactly three distinct references")
            self.refused(conn, sql, ([a, b, c], self.word(conn, verification="unverified")),
                         "the word must be a verified pool word")

    def test_repeats_are_refused_and_dropped_rows_frozen(self) -> None:
        with _rolled_back() as conn:
            a, b, c, d = (self.ref(conn, cat) for cat in ("finance_business", "sports", "film_tv", "philosophy"))
            w1, w2 = self.word(conn), self.word(conn)
            did = self.drop(conn, date(2001, 1, 1), [a, b, c], w1)
            self.assertEqual(conn.execute("SELECT count(*) FROM arsenal_refs WHERE id = ANY(%s) AND status = 'dropped'",
                                          ([a, b, c],)).fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT owner_id FROM arsenal_drops WHERE id = %s", (did,)).fetchone()[0],
                             self.owner)
            sql = "INSERT INTO arsenal_drops (drop_date, ref_ids, word_id) VALUES ('2001-01-02', %s, %s)"
            self.refused(conn, sql, ([a, d, self.ref(conn, "sports")], w2), "never drops again")
            self.refused(conn, sql, ([d, self.ref(conn, "sports"), self.ref(conn, "film_tv")], w1),
                         "a word already dropped never drops again")
            self.refused(conn, "UPDATE arsenal_refs SET status = 'rejected' WHERE id = %s", (a,),
                         "a dropped reference never changes status")
            self.refused(conn, "UPDATE arsenal_refs SET title = 'Rewritten' WHERE id = %s", (a,),
                         "changes only while it is pending")
            self.refused(conn, "DELETE FROM arsenal_refs WHERE id = %s", (a,), "never deleted")
            self.refused(conn, "UPDATE arsenal_drops SET topped_up = true WHERE id = %s", (did,), "")
            self.refused(conn, "DELETE FROM arsenal_drops WHERE id = %s", (did,), "")
            # One real drop a day.
            self.refused(conn, "INSERT INTO arsenal_drops (drop_date, ref_ids, word_id) VALUES ('2001-01-01', %s, %s)",
                         ([d, self.ref(conn, "sports"), self.ref(conn, "film_tv")], w2), "arsenal_drops_one_a_day",
                         psycopg.errors.UniqueViolation)

    def test_a_real_ref_enters_pending_and_settles_once(self) -> None:
        with _rolled_back() as conn:
            self.refused(conn,
                         "INSERT INTO arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, "
                         "source_work, source_kind, source_url, status, verification, origin_kind) VALUES "
                         "(%s, 'sports', 'x title', 'An origin long enough.', 'A meaning.', 'A line.', 'pitch', "
                         "'A work', 'wikipedia', 'https://tl.invalid/x', 'pool', 'verified', 'pool')",
                         (f"tl_{uuid.uuid4().hex[:12]}",), "enters pending")
            r = self.ref(conn, "sports", verification="pending")
            self.assertTrue(ledger.arsenal_settle_ref(r, verification="corrected", note="tl", conn=conn,
                                                      source_url="https://tl.invalid/r", drop_quote=True))
            self.assertFalse(ledger.arsenal_settle_ref(r, verification="verified", note=None, source_url=None,
                                                       conn=conn))
            self.assertEqual(conn.execute("SELECT verification, verified_at IS NOT NULL FROM arsenal_refs WHERE id = %s",
                                          (r,)).fetchone(), ("corrected", True))

    def test_truncate_is_refused_with_every_referencing_table_named(self) -> None:
        with _rolled_back() as conn:
            self.refused(conn, "TRUNCATE arsenal_pool_runs, arsenal_refs, arsenal_words, arsenal_drops, "
                         "arsenal_reactions", None, "TRUNCATE is refused")


# ============================================================ near-dup, weights

class NearDupAndWeights(ArsenalSqlCase):
    def test_the_vector_goes_in_and_a_boolean_comes_out(self) -> None:
        with _rolled_back() as conn:
            probe = onehot(97)
            before = ledger.arsenal_near_dup(probe, 0.92, conn=conn)
            self.ref(conn, "sports", embedding=probe)
            self.assertTrue(ledger.arsenal_near_dup(probe, 0.92, conn=conn))
            self.assertEqual(ledger.arsenal_near_dup(onehot(98), 0.92, conn=conn), before)
            # A test reference never counts.
            self.ref(conn, "film_tv", test=True, embedding=onehot(98))
            self.assertEqual(ledger.arsenal_near_dup(onehot(98), 0.92, conn=conn), before)

    def test_love_and_not_interested_move_weights_known_and_tests_do_not(self) -> None:
        with _rolled_back() as conn:
            w = lambda: ledger.arsenal_weights(conn=conn)  # noqa: E731
            r = self.ref(conn, "music_culture", room="negotiation")
            t = self.ref(conn, "music_culture", test=True, room="negotiation")
            base = w()
            react = "INSERT INTO arsenal_reactions (owner_id, ref_id, reaction) VALUES (%s, %s, %s)"
            conn.execute(react, (self.owner, t, "love"))
            self.assertEqual(w(), base)
            conn.execute(react, (self.owner, r, "love"))
            up = w()
            if base[("category", "music_culture")] < 2.0:
                self.assertGreater(up[("category", "music_culture")], base[("category", "music_culture")])
                self.assertGreater(up[("room", "negotiation")], base[("room", "negotiation")])
            conn.execute(react, (self.owner, r, "known"))
            self.assertEqual(w(), base)  # the latest reaction counts; known moves nothing
            conn.execute(react, (self.owner, r, "not_interested"))
            if base[("category", "music_culture")] > 0.3:
                self.assertLess(w()[("category", "music_culture")], base[("category", "music_culture")])


# ======================================================================== RLS

class Access(ArsenalSqlCase):
    def test_the_owner_reads_and_reacts_as_herself_only(self) -> None:
        with _rolled_back() as conn, conn.cursor() as cur:
            r = self.ref(conn, "sports")
            _act_as(cur, "authenticated", self.owner)
            cur.execute("SELECT count(*) FROM arsenal_refs WHERE id = %s", (r,))
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("INSERT INTO arsenal_reactions (ref_id, reaction) VALUES (%s, 'love') RETURNING owner_id, is_test",
                        (r,))
            self.assertEqual(cur.fetchone(), (self.owner, False))
            for sql, params in (("INSERT INTO arsenal_reactions (owner_id, ref_id, reaction) VALUES (%s, %s, 'love')",
                                 (str(uuid.uuid4()), r)),
                                ("UPDATE arsenal_refs SET title = 'x' WHERE id = %s", (r,)),
                                ("SELECT arsenal_pick_drop('2001-01-03')", None)):
                cur.execute("SAVEPOINT s")
                with self.subTest(sql=sql), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(sql, params)
                cur.execute("ROLLBACK TO SAVEPOINT s")

    def test_anon_sees_nothing_and_the_helpers_refuse_without_the_marker(self) -> None:
        with _rolled_back() as conn, conn.cursor() as cur:
            cur.execute("SAVEPOINT s")
            _act_as(cur, "anon")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT count(*) FROM arsenal_refs")
            cur.execute("ROLLBACK TO SAVEPOINT s")
            _act_as(cur, "authenticated", self.owner)
            with self.assertRaises(psycopg.errors.RaiseException):
                cur.execute("SELECT add_test_arsenal_ref('not-the-marker', 'sports', 'x', 'pitch')")


# ============================================================ test rows hidden

class TestRowsHidden(ArsenalSqlCase):
    def test_a_test_drop_today_is_invisible_to_every_real_selector(self) -> None:
        with _rolled_back() as conn:
            today = ledger.arsenal_owner_today(conn=conn)
            status0 = ledger.arsenal_pool_status(conn=conn)
            ctx0 = conn.execute("SELECT ctx_arsenal() -> 'today', ctx_arsenal_brief()").fetchone()
            weights0 = ledger.arsenal_weights(conn=conn)
            refs = [self.ref(conn, cat, test=True) for cat in ("finance_business", "sports", "film_tv")]
            w = self.word(conn, test=True)
            test_drop = self.drop(conn, today, refs, w, test=True)
            conn.execute("INSERT INTO arsenal_reactions (owner_id, ref_id, reaction) VALUES (%s, %s, 'love')",
                         (self.owner, refs[0]))
            status1 = ledger.arsenal_pool_status(conn=conn)
            self.assertEqual((status1.refs_ready, status1.words_ready, status1.ready_categories),
                             (status0.refs_ready, status0.words_ready, status0.ready_categories))
            self.assertEqual(conn.execute("SELECT ctx_arsenal() -> 'today', ctx_arsenal_brief()").fetchone(), ctx0)
            self.assertEqual(ledger.arsenal_weights(conn=conn), weights0)
            # The worker's "does today have a drop" never sees it.
            self.assertNotEqual(ledger.arsenal_drop_on(today, conn=conn), test_drop)

    def test_the_real_picker_never_takes_a_test_row_and_the_test_picker_never_a_real_one(self) -> None:
        with _rolled_back() as conn:
            for cat in ("finance_business", "sports", "film_tv", "philosophy"):
                self.ref(conn, cat)
                self.ref(conn, cat, test=True)
            self.word(conn)
            self.word(conn, test=True)
            real = ledger.arsenal_pick_drop(date(2001, 2, 1), conn=conn)
            test = conn.execute("SELECT arsenal_pick_drop('2001-02-01', true)").fetchone()[0]
            for did, want in ((real, False), (test, True)):
                with self.subTest(test=want):
                    flags = conn.execute(
                        "SELECT d.is_test, bool_and(r.is_test = %s), count(DISTINCT r.category), "
                        "(SELECT w.is_test FROM arsenal_words w WHERE w.id = d.word_id) "
                        "FROM arsenal_drops d CROSS JOIN LATERAL unnest(d.ref_ids) x JOIN arsenal_refs r ON r.id = x "
                        "WHERE d.id = %s GROUP BY d.id, d.is_test, d.word_id", (want, did)).fetchone()
                    self.assertEqual(flags, (want, True, 3, want))
            # The same date again returns the real drop; a test pick is always new.
            self.assertEqual(ledger.arsenal_pick_drop(date(2001, 2, 1), conn=conn), real)

    def test_no_slot_repeats_yesterdays_category(self) -> None:
        with _rolled_back() as conn:
            cats = ("finance_business", "sports", "film_tv")
            y = [self.ref(conn, c, test=True) for c in cats]
            self.drop(conn, date(2001, 3, 1), y, self.word(conn, test=True), test=True)
            # Only the same three categories are left: the picker must rotate them.
            for c in cats:
                self.ref(conn, c, test=True)
            self.word(conn, test=True)
            # Other test rows (e2e leftovers) may also be eligible; the rule holds either way.
            did = conn.execute("SELECT arsenal_pick_drop('2001-03-02', true)").fetchone()[0]
            got = [r[0] for r in conn.execute(
                "SELECT r.category FROM arsenal_drops d CROSS JOIN LATERAL unnest(d.ref_ids) WITH ORDINALITY s(x, o) "
                "JOIN arsenal_refs r ON r.id = s.x WHERE d.id = %s ORDER BY s.o", (did,)).fetchall()]
            self.assertEqual(len(set(got)), 3)
            for slot, (was, now) in enumerate(zip(cats, got), start=1):
                with self.subTest(slot=slot):
                    self.assertNotEqual(was, now)


# ============================================================== worker queries

class WorkerQueries(ArsenalSqlCase):
    def test_pool_runs_open_batch_close_and_stale(self) -> None:
        with _rolled_back() as conn:
            run = ledger.arsenal_log_run("batch", "submitted", batch_id=f"msgbatch_tl{uuid.uuid4().hex[:8]}",
                                         model="claude-sonnet-5-5", refs_requested=30, words_requested=10, conn=conn)
            ob = ledger.arsenal_open_batch(conn=conn)
            self.assertEqual(ob.run_id, run)
            r = conn.execute(
                "INSERT INTO arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, "
                "source_work, source_kind, status, origin_kind, pool_run_id) VALUES (%s, 'sports', 'TL run ref', "
                "'An origin long enough.', 'A meaning.', 'A line.', 'pitch', 'A work', 'wikipedia', 'pool', 'pool', %s) "
                "RETURNING id", (f"tl_{uuid.uuid4().hex[:12]}", run)).fetchone()[0]
            # now() is the transaction's start, which is also this row's
            # created_at: -1 h makes "older than" include it.
            self.assertIn(("ref", r), ledger.arsenal_stale_pending(-1, conn=conn))
            ledger.arsenal_queue_verify("ref", r, conn=conn)
            self.assertNotIn(("ref", r), ledger.arsenal_stale_pending(-1, conn=conn))
            self.assertFalse(ledger.arsenal_close_run_if_settled(run, conn=conn))  # still pending
            ledger.arsenal_log_run("batch", "collected", run_id=run, inserted_refs=1, conn=conn)
            self.assertNotEqual(getattr(ledger.arsenal_open_batch(conn=conn), "run_id", None), run)
            ledger.arsenal_settle_ref(r, verification="unverified", note="tl", source_url=None, conn=conn)
            self.assertTrue(ledger.arsenal_close_run_if_settled(run, conn=conn))
            self.assertFalse(ledger.arsenal_close_run_if_settled(run, conn=conn))  # once
            self.assertEqual(conn.execute(
                "SELECT unverified FROM arsenal_pool_runs WHERE run_id = %s AND status = 'verified'", (run,)
            ).fetchone()[0], 1)

    def test_insert_skips_a_taken_key_and_the_check_counts(self) -> None:
        with _rolled_back() as conn:
            card = ledger.ArsenalCardIn(canonical_key="crossing_the_rubicon", category="history_military",
                                        title="Crossing the Rubicon", origin="An origin long enough.", meaning="M.",
                                        deploy_line="A line.", room="boardroom", source_work="Caesar", quote=None,
                                        quote_by=None, source_kind="wikipedia", source_page="Crossing the Rubicon")
            self.assertIsNone(ledger.arsenal_insert_ref(card, pool_run_id=None, vector=None, conn=conn))
            fresh = ledger.arsenal_insert_ref(replace(card, canonical_key=f"tl_{uuid.uuid4().hex[:12]}"),
                                              pool_run_id=None, vector=onehot(5), conn=conn)
            self.assertIsNotNone(fresh)
            self.assertIn(fresh, [i for t, i in ledger.arsenal_stale_pending(-1, conn=conn) if t == "ref"])
            check = ledger.arsenal_check(conn=conn)
            self.assertEqual((check.repeated_refs, check.repeated_words, check.seeds_dropped), (0, 0, 0))
            self.assertGreaterEqual(check.refs_by_verification.get("pending", 0), 1)  # this one, at least


if __name__ == "__main__":
    unittest.main()
