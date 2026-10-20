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
* THE PICKER never takes a reference or a Power Word whose latest reaction
  is Already know it; taking it back ('none') makes it eligible again.
* db/055 (skips until pasted): three distinct rooms whenever the pool
  allows, the word's room off the references' majority, a seed word never
  picked and never back to pool, the owner's example words known; ONE
  re-verification, recorded append-only, never a second; a held item never
  picked and never counted ready; the new tables' access; item 11 written.
  The worker's pool-status SQL reads arsenal_holds, so the tests that run it
  fail until db/055 is pasted (paste before the merge).
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
            cls.has_checks = conn.execute(
                "SELECT to_regclass('public.arsenal_outcome_checks')").fetchone()[0] is not None

    # -- fixtures (own rows, rolled back) ------------------------------------

    def ref(self, conn: psycopg.Connection, category: str, *, test: bool = False, verification: str = "verified",
            room: str = "boardroom", embedding: list[float] | None = None, checked: bool = True) -> int:
        """A pool reference. A real one enters pending and is settled as the
        worker settles it; a test one enters settled. From db/057 a settled
        one also gets a passing outcome check (the picker needs one), unless
        `checked` is False."""
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
        if checked and self.has_checks and verification in ("verified", "corrected"):
            ledger.arsenal_record_outcome(int(rid), conflict=False, votes="nnn", page="TL page", note="tl",
                                          controls="3/3", source="worker", conn=conn)
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

    def _only_mine_eligible(self, conn: psycopg.Connection) -> None:
        """Every other eligible TEST pool row (e2e leftovers) is reacted
        'known' inside this rolled-back transaction, so the picker can only
        take this test's own rows. Ids only."""
        react = "INSERT INTO arsenal_reactions (owner_id, {col}, reaction) SELECT %s, id, 'known' FROM {table} " \
                "WHERE is_test AND status = 'pool' AND verification IN ('verified', 'corrected')"
        conn.execute(react.format(col="ref_id", table="arsenal_refs"), (self.owner,))
        conn.execute(react.format(col="word_id", table="arsenal_words"), (self.owner,))

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
            # db/055's two tables reference refs and words: name them too once they exist.
            extra = [t for t in ("arsenal_holds", "arsenal_reverifications", "arsenal_hold_releases",
                                 "arsenal_outcome_checks")
                     if conn.execute("SELECT to_regclass(%s)", (f"public.{t}",)).fetchone()[0] is not None]
            self.refused(conn, "TRUNCATE " + ", ".join(["arsenal_pool_runs", "arsenal_refs", "arsenal_words",
                                                        "arsenal_drops", "arsenal_reactions", *extra]),
                         None, "TRUNCATE is refused")


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

    def test_the_nearest_query_agrees_with_arsenal_near_dup(self) -> None:
        """The worker logs every skip's cosine and matched key from
        ARSENAL_NEAREST_SQL; it must apply the database's own rule."""
        with _rolled_back() as conn:
            probe = onehot(101)
            r = self.ref(conn, "sports", embedding=probe)
            key = conn.execute("SELECT canonical_key FROM arsenal_refs WHERE id = %s", (r,)).fetchone()[0]
            nearest = ledger.arsenal_nearest(probe, conn=conn)
            self.assertEqual(nearest[0], key)
            self.assertAlmostEqual(nearest[1], 1.0, places=3)
            # A test row and an unverified pool row are never the match.
            self.ref(conn, "film_tv", test=True, embedding=onehot(102))
            self.ref(conn, "film_tv", verification="unverified", embedding=onehot(103))
            for i in (102, 103):
                got = ledger.arsenal_nearest(onehot(i), conn=conn)
                self.assertLess(got[1], 0.92)
            for v in (probe, onehot(102), onehot(103), [0.5] * 384):
                for t in (0.5, 0.92, 0.999):
                    got = ledger.arsenal_nearest(v, conn=conn)
                    self.assertEqual(ledger.arsenal_near_dup(v, t, conn=conn), got is not None and got[1] >= t)

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

    def test_a_known_word_is_never_picked_like_a_known_ref(self) -> None:
        """Already know it (the latest reaction) takes a Power Word out of
        the picker exactly as it takes a reference out; 'none' puts it back."""
        with _rolled_back() as conn:
            self._only_mine_eligible(conn)
            refs = [self.ref(conn, c, test=True) for c in ("finance_business", "sports", "film_tv")]
            w = self.word(conn, test=True)
            react = "INSERT INTO arsenal_reactions (owner_id, ref_id, word_id, reaction) VALUES (%s, %s, %s, %s)"
            pick = "SELECT arsenal_pick_drop('2001-04-01', true)"
            # The word.
            conn.execute(react, (self.owner, None, w, "known"))
            self.refused(conn, pick, None, "no eligible word")
            conn.execute(react, (self.owner, None, w, "none"))
            with conn.transaction(force_rollback=True):
                did = conn.execute(pick).fetchone()[0]
                self.assertEqual(conn.execute("SELECT word_id FROM arsenal_drops WHERE id = %s", (did,)).fetchone()[0], w)
            # A reference, the same way.
            conn.execute(react, (self.owner, refs[1], None, "known"))
            self.refused(conn, pick, None, "the verified pool is short (2 categories")
            conn.execute(react, (self.owner, refs[1], None, "none"))
            did = conn.execute(pick).fetchone()[0]
            self.assertEqual(sorted(conn.execute("SELECT ref_ids FROM arsenal_drops WHERE id = %s",
                                                 (did,)).fetchone()[0]), sorted(refs))

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


# ================================================== db/055: rooms, word seeds

class RoomsAndWordSeeds(ArsenalSqlCase):
    """db/055: the picker rotates rooms (three distinct when the pool allows;
    the word's room off the references' majority), and the owner's example
    words are seeds. Every pick is a TEST pick inside a rolled-back savepoint."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if "seed" not in conn.execute(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'arsenal_words_status_check'"
            ).fetchone()[0]:
                raise unittest.SkipTest("db/055 not pasted")

    def _pick_rooms(self, conn: psycopg.Connection, day: str) -> tuple[list[str], str]:
        did = conn.execute("SELECT arsenal_pick_drop(%s, true)", (day,)).fetchone()[0]
        rooms = [r[0] for r in conn.execute(
            "SELECT r.room FROM arsenal_drops d CROSS JOIN LATERAL unnest(d.ref_ids) x JOIN arsenal_refs r ON r.id = x "
            "WHERE d.id = %s", (did,)).fetchall()]
        word_room = conn.execute("SELECT w.room FROM arsenal_drops d JOIN arsenal_words w ON w.id = d.word_id "
                                 "WHERE d.id = %s", (did,)).fetchone()[0]
        return rooms, word_room

    def test_three_distinct_rooms_whenever_the_pool_allows(self) -> None:
        with _rolled_back() as conn:
            self._only_mine_eligible(conn)
            for cat, other in (("finance_business", "pitch"), ("sports", "negotiation"), ("film_tv", "homies")):
                self.ref(conn, cat, test=True, room="boardroom")
                self.ref(conn, cat, test=True, room=other)
            self.word(conn, test=True)
            for i in range(8):  # the draw is random; the preference must hold on every pick
                with self.subTest(pick=i), conn.transaction(force_rollback=True):
                    rooms, _ = self._pick_rooms(conn, "2001-06-01")
                    self.assertEqual(len(set(rooms)), 3, rooms)

    def test_the_words_room_is_off_the_references_majority(self) -> None:
        with _rolled_back() as conn:
            self._only_mine_eligible(conn)
            for cat in ("finance_business", "sports", "film_tv"):
                self.ref(conn, cat, test=True, room="boardroom")
            for room in ("boardroom", "pitch"):
                conn.execute(
                    "INSERT INTO arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence, "
                    "verification, is_test) VALUES (%s, '/x/', 'From x.', 'https://tl.invalid/w', 'A meaning.', %s, "
                    "'A sentence.', 'verified', true)", (f"tlword {uuid.uuid4().hex[:10]}", room))
            for i in range(8):
                with self.subTest(pick=i), conn.transaction(force_rollback=True):
                    rooms, word_room = self._pick_rooms(conn, "2001-06-02")
                    self.assertEqual(rooms, ["boardroom"] * 3)
                    self.assertEqual(word_room, "pitch")

    def test_a_seed_word_is_never_picked_and_never_leaves_seed(self) -> None:
        with _rolled_back() as conn:
            self._only_mine_eligible(conn)
            for cat in ("finance_business", "sports", "film_tv"):
                self.ref(conn, cat, test=True)
            w = self.word(conn, test=True)
            conn.execute("UPDATE arsenal_words SET status = 'seed' WHERE id = %s", (w,))
            self.refused(conn, "SELECT arsenal_pick_drop('2001-06-03', true)", None, "no eligible word")
            self.refused(conn, "UPDATE arsenal_words SET status = 'pool' WHERE id = %s", (w,),
                         "a seed word never changes status")

    # -- db/055 sections 3, 4, 6 and 7: re-verification, holds, access, item 11

    def unverified_ref(self, conn: psycopg.Connection, note: str = "insufficient: x") -> int:
        key = f"tl_{uuid.uuid4().hex[:16]}"
        return int(conn.execute(
            "INSERT INTO arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work, "
            "source_kind, status, verification, verification_note, origin_kind, is_test) VALUES (%s, 'sports', %s, "
            "'An origin long enough.', 'A meaning.', 'A line.', 'pitch', 'A work', 'wikipedia', 'pool', 'unverified', "
            "%s, 'pool', true) RETURNING id", (key, f"TL {key}", note)).fetchone()[0])

    def test_one_re_verification_recorded_and_never_a_second(self) -> None:
        with _rolled_back() as conn:
            r = self.unverified_ref(conn)
            self.refused(conn, "UPDATE arsenal_refs SET verification_note = 'rewritten' WHERE id = %s", (r,),
                         "settles once")
            rec = conn.execute("SELECT arsenal_reverify_ref(%s, 'corrected', 'second look', 'tests_live', "
                               "'https://tl.invalid/r', 'A corrected origin, long enough.')", (r,)).fetchone()[0]
            self.assertEqual(conn.execute(
                "SELECT verification, verification_note, origin, verified_at IS NOT NULL FROM arsenal_refs WHERE id = %s",
                (r,)).fetchone(), ("corrected", "second look", "A corrected origin, long enough.", True))
            self.assertEqual(conn.execute(
                "SELECT ref_id, old_verification, new_verification, old_note, new_note, by_whom, db_role = current_user "
                "FROM arsenal_reverifications WHERE id = %s", (rec,)).fetchone(),
                (r, "unverified", "corrected", "insufficient: x", "second look", "tests_live", True))
            self.refused(conn, "SELECT arsenal_reverify_ref(%s, 'verified', 'again', 'tests_live')", (r,),
                         "only an unverified pool reference")
            # Staying unverified still spends the one chance.
            r2 = self.unverified_ref(conn)
            conn.execute("SELECT arsenal_reverify_ref(%s, 'unverified', 'still nothing', 'tests_live')", (r2,))
            self.refused(conn, "SELECT arsenal_reverify_ref(%s, 'verified', 'third', 'tests_live', 'https://tl.invalid/r')",
                         (r2,), "already re-verified once")
            self.refused(conn, "UPDATE arsenal_reverifications SET by_whom = 'someone' WHERE id = %s", (rec,), "")
            self.refused(conn, "DELETE FROM arsenal_reverifications WHERE id = %s", (rec,), "")

    def test_a_held_reference_or_word_is_never_picked_nor_ready(self) -> None:
        with _rolled_back() as conn:
            self._only_mine_eligible(conn)
            refs = [self.ref(conn, c, test=True) for c in ("finance_business", "sports", "film_tv")]
            w = self.word(conn, test=True)
            conn.execute("INSERT INTO arsenal_holds (ref_id, reason, detail) VALUES (%s, 'outcome_conflict', 'tl')",
                         (refs[0],))
            self.refused(conn, "SELECT arsenal_pick_drop('2001-06-10', true)", None, "(2 categories")
            self.ref(conn, "finance_business", test=True)
            ledger.arsenal_hold(word_id=w, reason="outcome_conflict", detail="tl", conn=conn)
            self.refused(conn, "SELECT arsenal_pick_drop('2001-06-10', true)", None, "no eligible word")
            self.refused(conn, "INSERT INTO arsenal_holds (ref_id, reason) VALUES (%s, 'Outcome Conflict')", (refs[1],),
                         "", psycopg.errors.CheckViolation)
            # The worker's "ready" never counts a held REAL item.
            before = ledger.arsenal_pool_status(conn=conn)
            real = self.ref(conn, "philosophy")
            mid = ledger.arsenal_pool_status(conn=conn)
            ledger.arsenal_hold(ref_id=real, reason="outcome_conflict", detail="tl", conn=conn)
            after = ledger.arsenal_pool_status(conn=conn)
            self.assertEqual((mid.refs_ready - before.refs_ready, after.refs_ready), (1, before.refs_ready))
            self.assertIn(real, [p.ref.id for p in ledger.arsenal_undropped_checked_refs(conn=conn) if p.held])

    def test_access_to_the_new_tables_and_functions(self) -> None:
        with _rolled_back() as conn:
            for table in ("arsenal_holds", "arsenal_reverifications"):
                with self.subTest(table=table):
                    self.assertEqual(conn.execute(
                        "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                        "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('authenticated', oid, 'SELECT'), "
                        "has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                        "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                        "FROM pg_class c WHERE oid = %s::regclass", (f"public.{table}",)).fetchone(),
                        (True, 1, False, True, False, True))
            for fn in ("public.arsenal_reverify_ref(bigint, text, text, text, text, text, text, text, text, boolean)",
                       "public.arsenal_reverify_word(bigint, text, text, text, text, text, text)"):
                with self.subTest(fn=fn):
                    self.assertEqual(conn.execute(
                        "SELECT has_function_privilege('anon', %s, 'EXECUTE'), "
                        "has_function_privilege('authenticated', %s, 'EXECUTE'), "
                        "has_function_privilege('valemont_readonly', %s, 'EXECUTE')", (fn, fn, fn)).fetchone(),
                        (False, False, False))

    def test_item_11_was_written_once(self) -> None:
        with _rolled_back() as conn:
            self.assertEqual(conn.execute(
                "SELECT id, verification FROM arsenal_refs WHERE id IN (206, 216) ORDER BY id").fetchall(),
                [(206, "corrected"), (216, "verified")])
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM arsenal_reverifications WHERE ref_id IN (206, 216)").fetchone()[0], 2)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM arsenal_refs WHERE id IN (190, 192, 201, 209, 212, 215) "
                "AND verification = 'unverified'").fetchone()[0], 6)

    def test_the_owners_example_words_are_known(self) -> None:
        with _rolled_back() as conn:
            rows = dict(conn.execute(
                "SELECT lemma, status FROM arsenal_words WHERE NOT is_test "
                "AND lemma IN ('asymmetric', 'inflection', 'provenance', 'fiduciary')").fetchall())
            self.assertNotIn("pool", rows.values(), rows)
            self.assertIn(ledger.arsenal_seed_lemmas(conn=conn), [sorted(k for k, v in rows.items() if v == "seed")])


# ==================================================== db/056: hold releases

class HoldReleases(ArsenalSqlCase):
    """db/056: a hold is released at most once, on the record; only an
    ACTIVE hold (v_arsenal_active_holds) keeps an item out of the picker and
    out of the worker's ready counts."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.arsenal_hold_releases')").fetchone()[0] is None:
                raise unittest.SkipTest("db/056 not pasted")

    def test_a_released_hold_frees_the_item_once_and_stays_as_history(self) -> None:
        with _rolled_back() as conn:
            self._only_mine_eligible(conn)
            refs = [self.ref(conn, c, test=True) for c in ("finance_business", "sports", "film_tv")]
            self.word(conn, test=True)
            h = ledger.arsenal_hold(ref_id=refs[0], reason="outcome_conflict", detail="tl", conn=conn)
            self.refused(conn, "SELECT arsenal_pick_drop('2001-08-01', true)", None, "(2 categories")
            rel = ledger.arsenal_release_hold(h, "tests_live: a false positive", conn=conn)
            self.assertEqual(conn.execute(
                "SELECT hold_id, reason, released_by = current_user, created_at IS NOT NULL "
                "FROM arsenal_hold_releases WHERE id = %s", (rel,)).fetchone(),
                (h, "tests_live: a false positive", True, True))
            self.assertEqual(conn.execute("SELECT count(*) FROM arsenal_holds WHERE id = %s", (h,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM v_arsenal_active_holds WHERE id = %s",
                                          (h,)).fetchone()[0], 0)
            with conn.transaction(force_rollback=True):
                did = conn.execute("SELECT arsenal_pick_drop('2001-08-01', true)").fetchone()[0]
                self.assertIn(refs[0], conn.execute("SELECT ref_ids FROM arsenal_drops WHERE id = %s",
                                                    (did,)).fetchone()[0])
            self.refused(conn, "SELECT arsenal_release_hold(%s, 'again')", (h,), "already released")
            self.refused(conn, "SELECT arsenal_release_hold(%s, '  ')",
                         (ledger.arsenal_hold(ref_id=refs[1], reason="outcome_conflict", detail=None, conn=conn),),
                         "a reason is required")
            self.refused(conn, "UPDATE arsenal_hold_releases SET reason = 'x' WHERE id = %s", (rel,), "")
            self.refused(conn, "DELETE FROM arsenal_hold_releases WHERE id = %s", (rel,), "")
            self.refused(conn, "TRUNCATE arsenal_hold_releases", None, "TRUNCATE is refused")

    def test_only_an_active_hold_hides_a_real_item_from_the_ready_counts(self) -> None:
        with _rolled_back() as conn:
            before = ledger.arsenal_pool_status(conn=conn)
            real = self.ref(conn, "philosophy")
            h = ledger.arsenal_hold(ref_id=real, reason="outcome_conflict", detail="tl", conn=conn)
            self.assertEqual(ledger.arsenal_pool_status(conn=conn).refs_ready, before.refs_ready)
            self.assertIn(real, [p.ref.id for p in ledger.arsenal_undropped_checked_refs(conn=conn) if p.held])
            ledger.arsenal_release_hold(h, "tests_live", conn=conn)
            self.assertEqual(ledger.arsenal_pool_status(conn=conn).refs_ready, before.refs_ready + 1)
            self.assertNotIn(real, [p.ref.id for p in ledger.arsenal_undropped_checked_refs(conn=conn) if p.held])
            self.assertEqual([x.released for x in ledger.arsenal_holds_by_id([h], conn=conn)], [True])

    def test_access(self) -> None:
        with _rolled_back() as conn:
            for rel in ("arsenal_hold_releases", "v_arsenal_active_holds"):
                with self.subTest(rel=rel):
                    self.assertEqual(conn.execute(
                        "SELECT has_table_privilege('anon', %s, 'SELECT,INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('authenticated', %s, 'SELECT'), "
                        "has_table_privilege('authenticated', %s, 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                        "has_table_privilege('valemont_readonly', %s, 'SELECT')", (f"public.{rel}",) * 4).fetchone(),
                        (False, True, False, True))
            self.assertEqual(conn.execute(
                "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies WHERE tablename = 'arsenal_hold_releases') "
                "FROM pg_class WHERE oid = 'public.arsenal_hold_releases'::regclass").fetchone(), (True, 1))
            fn = "public.arsenal_release_hold(bigint, text)"
            self.assertEqual(conn.execute(
                "SELECT has_function_privilege('anon', %s, 'EXECUTE'), has_function_privilege('authenticated', %s, "
                "'EXECUTE'), has_function_privilege('valemont_readonly', %s, 'EXECUTE')", (fn, fn, fn)).fetchone(),
                (False, False, False))


class OutcomeChecks(ArsenalSqlCase):
    """db/057 (owner, 2026-10-20: "record checks, the picker gates"): a
    reference drops only with an outcome check on record; checks are
    append-only; a conflict holds in the same transaction; the worker's
    ready counts and its unchecked list agree with the picker."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if not cls.has_checks:
            raise unittest.SkipTest("db/057 not pasted")

    def test_an_unchecked_reference_never_drops_and_a_check_lets_it(self) -> None:
        with _rolled_back() as conn:
            self._only_mine_eligible(conn)
            refs = [self.ref(conn, c, test=True, checked=False) for c in ("finance_business", "sports", "film_tv")]
            self.word(conn, test=True)
            self.refused(conn, "SELECT arsenal_pick_drop('2001-10-01', true)", None, "(0 categories")
            for r in refs[:2]:
                ledger.arsenal_record_outcome(r, conflict=False, votes="nnn", page="p", note="tl", controls="3/3",
                                              source="worker", conn=conn)
            self.refused(conn, "SELECT arsenal_pick_drop('2001-10-01', true)", None, "(2 categories")
            # A conflict is a check too, but it holds in the same transaction; a release frees it.
            check, hold = ledger.arsenal_record_outcome(refs[2], conflict=True, votes="YYn", page="p", note="lost",
                                                        controls="3/3", source="worker", conn=conn)
            self.assertIsNotNone(hold)
            self.assertEqual(conn.execute("SELECT ref_id, reason, detail FROM arsenal_holds WHERE id = %s",
                                          (hold,)).fetchone(), (refs[2], "outcome_conflict", "lost"))
            self.refused(conn, "SELECT arsenal_pick_drop('2001-10-01', true)", None, "(2 categories")
            ledger.arsenal_release_hold(hold, "tests_live: a false positive", conn=conn)
            did = conn.execute("SELECT arsenal_pick_drop('2001-10-01', true)").fetchone()[0]
            self.assertEqual(sorted(conn.execute("SELECT ref_ids FROM arsenal_drops WHERE id = %s",
                                                 (did,)).fetchone()[0]), sorted(refs))
            self.assertEqual(conn.execute("SELECT is_test, created_at = now() FROM arsenal_outcome_checks "
                                          "WHERE id = %s", (check,)).fetchone(), (True, True))

    def test_the_ready_counts_and_the_unchecked_list_agree_with_the_picker(self) -> None:
        with _rolled_back() as conn:
            before = ledger.arsenal_pool_status(conn=conn)
            real = self.ref(conn, "philosophy", checked=False)
            after = ledger.arsenal_pool_status(conn=conn)
            self.assertEqual((after.refs_ready, after.refs_unchecked), (before.refs_ready, before.refs_unchecked + 1))
            self.assertIn(real, [r.id for r in ledger.arsenal_unchecked_refs(conn=conn)])
            ledger.arsenal_record_outcome(real, conflict=False, votes="nnn", page="p", note="tl", controls="3/3",
                                          source="worker", conn=conn)
            done = ledger.arsenal_pool_status(conn=conn)
            self.assertEqual((done.refs_ready, done.refs_unchecked), (before.refs_ready + 1, before.refs_unchecked))
            self.assertNotIn(real, [r.id for r in ledger.arsenal_unchecked_refs(conn=conn)])
            # A held unchecked reference is neither unchecked-to-do nor ready.
            held = self.ref(conn, "philosophy", checked=False)
            ledger.arsenal_hold(ref_id=held, reason="outcome_conflict", detail="tl", conn=conn)
            self.assertNotIn(held, [r.id for r in ledger.arsenal_unchecked_refs(conn=conn)])
            self.assertEqual(ledger.arsenal_pool_status(conn=conn).refs_unchecked, before.refs_unchecked)

    def test_the_guard_and_append_only(self) -> None:
        with _rolled_back() as conn:
            r = self.ref(conn, "sports", test=True, checked=False)
            unv = self.ref(conn, "sports", test=True, verification="unverified")
            real = self.ref(conn, "sports", checked=False)
            ins = ("INSERT INTO arsenal_outcome_checks (ref_id, conflict, votes, controls, source, is_test) "
                   "VALUES (%s, %s, %s, %s, %s, %s)")
            self.refused(conn, ins, (unv, False, "nnn", "3/3", "worker", True), "not verified or corrected")
            self.refused(conn, ins, (real, False, "nnn", "3/3", "worker", True), "test exactly when")
            self.refused(conn, ins, (r, False, "YYn", "3/3", "worker", True), "disagrees with votes")
            self.refused(conn, ins, (r, True, "", "3/3", "worker", True), "disagrees with votes")
            self.refused(conn, ins, (r, False, "nnn", "2/3", "worker", True), "every control must pass")
            self.refused(conn, ins, (r, False, "nnx", "3/3", "worker", True), "", psycopg.errors.CheckViolation)
            self.refused(conn, ins, (r, False, "nnn", "3/3", "by hand", True), "", psycopg.errors.CheckViolation)
            c, _ = ledger.arsenal_record_outcome(r, conflict=False, votes="", page=None, note="no page to read",
                                                 controls="3/3", source="script", conn=conn)
            self.refused(conn, "UPDATE arsenal_outcome_checks SET conflict = true WHERE id = %s", (c,), "")
            self.refused(conn, "DELETE FROM arsenal_outcome_checks WHERE id = %s", (c,), "")
            self.refused(conn, "TRUNCATE arsenal_outcome_checks", None, "TRUNCATE is refused")

    def test_the_backfill_covers_the_pool_it_was_written_for(self) -> None:
        with _rolled_back() as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*), count(DISTINCT ref_id), bool_and(votes = 'nnn' AND NOT conflict AND NOT is_test) "
                "FROM arsenal_outcome_checks WHERE source = 'backfill'").fetchone(), (26, 26, True))

    def test_access(self) -> None:
        with _rolled_back() as conn:
            self.assertEqual(conn.execute(
                "SELECT has_table_privilege('anon', 'public.arsenal_outcome_checks', 'SELECT,INSERT,UPDATE,DELETE'), "
                "has_table_privilege('authenticated', 'public.arsenal_outcome_checks', 'SELECT'), "
                "has_table_privilege('authenticated', 'public.arsenal_outcome_checks', 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                "has_table_privilege('valemont_readonly', 'public.arsenal_outcome_checks', 'SELECT'), "
                "(SELECT relrowsecurity FROM pg_class WHERE oid = 'public.arsenal_outcome_checks'::regclass), "
                "(SELECT count(*) FROM pg_policies WHERE tablename = 'arsenal_outcome_checks')").fetchone(),
                (False, True, False, True, True, 1))


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
                                        title="Crossing the Rubicon", origin="An origin long enough.", meaning="A meaning.",
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
