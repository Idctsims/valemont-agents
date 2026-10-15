"""Screening Room recommendations SQL against the real database (db/053;
Chat 4 Phase 4):

* the db/053 objects: RLS, policies and grants on the nine new tables and
  v_title_state (owner reads; inserts only her own card / title / seen-pass
  reactions, never a seed; anon nothing; readonly reads), the triggers, the
  functions' grants, the seeds and their seen-and-loved reactions, the taste
  notes, entities kind `title`, content_items.youtube_key.
* FOR YOU: exclusions applied live (Not interested, seen, shelved, finished,
  a seed), the quality floor, the kind balance identical to the worker's
  recs.balance on the same rows, the because name, test rows only with
  p_with_tests.
* THE RE-RANK QUEUE: reactions, shelf moves and screening trailer reactions
  leave exactly one queued `screening_rerank` (debounced); a test title or
  a Home reaction never queues.
* THE RELEASE PUSH IN SQL: one real push a day; never the same title and
  event twice; a real alert only for a real Want/Watching title; a test
  push exempt from both; arrivals once a day; screening_events_on.
* NO CROSS-PILLAR LEAKS: a title entity changes nothing on Home (no follow,
  not a sports entity); title_news reads Home stories only; Trailers For
  you lists screening stories only; one YouTube key across both trailer
  sources.
* the worker's queries (core/ledger.py), each run here exactly as written.

Every test runs inside ONE transaction that is always rolled back. Skips
until db/053 is pasted.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg

from core import ledger
from screening import recs, services, tmdb
from tests_live.test_feed_rank_sql import RankSqlCase, _act_as, _rolled_back, unit


def vec(*hot: tuple[int, float]) -> list[float]:
    v = [0.0] * 384
    for i, x in hot:
        v[i] = x
    return v


class RecsSqlCase(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.title_reactions')").fetchone()[0] is None:
                raise unittest.SkipTest("db/053 not pasted")

    def title(self, conn: psycopg.Connection, kind: str = "movie", *, name: str | None = None,
              vote_average: float | None = 7.5, vote_count: int | None = 900, providers: dict | None = None,
              release: date | None = None, next_ep: date | None = None, embedding: list[float] | None = None) -> int:
        t = tmdb.TitleIn(tmdb_id=800000000 + uuid.uuid4().int % 99999999, kind=kind,
                         name=name or f"tl recs {uuid.uuid4().hex[:6]}", release_date=release, poster_path=None,
                         overview=None, genres=(), providers=providers or {}, trailer_key=None,
                         next_episode_date=next_ep, status=None, vote_average=vote_average, vote_count=vote_count,
                         tonight=tuple(sorted({r["service"] for r in services.split(providers or {})["tonight"]})))
        tid = ledger.titles_upsert(t, conn=conn)
        if embedding is not None:
            ledger.titles_set_embedding(tid, embedding, conn=conn)
        return tid

    def candidate(self, conn: psycopg.Connection, tid: int, score: float, because: int | None = None,
                  run: int | None = None) -> None:
        ledger.screening_candidates_upsert([(tid, ["recommendations"], [])], conn=conn)
        conn.execute("INSERT INTO title_scores (title_id, score, because_title_id, run_id) VALUES (%s, %s, %s, %s)",
                     (tid, score, because, run))

    def react_title(self, conn: psycopg.Connection, tid: int, reaction: str, *, seen: bool = False,
                    origin: str = "card") -> None:
        conn.execute("INSERT INTO title_reactions (owner_id, title_id, reaction, seen, origin) VALUES (%s, %s, %s, %s, %s)",
                     (self.owner, tid, reaction, seen, origin))

    def move(self, conn: psycopg.Connection, tid: int, status: str) -> None:
        conn.execute("INSERT INTO watch_status (owner_id, title_id, status) VALUES (%s, %s, %s)",
                     (self.owner, tid, status))

    def for_you(self, conn: psycopg.Connection, limit: int = 1000, tests: bool = False) -> list[tuple]:
        return conn.execute("SELECT title_id, kind, score, because_name, is_test FROM screening_for_you(%s, %s) "
                            "ORDER BY pos", (limit, tests)).fetchall()


# ====================================================================== objects

class Objects(RecsSqlCase):
    TABLES = {
        # table: (policies, auth select, auth insert)
        "screening_seeds": (1, True, False), "title_reactions": (2, True, True),
        "screening_candidates": (1, True, False), "screening_rec_runs": (1, True, False),
        "title_scores": (1, True, False), "screening_seen_pass": (1, True, False),
        "title_provider_arrivals": (1, True, False), "release_pushes": (1, True, False),
        "release_alerts": (1, True, False),
    }

    def test_tables_rls_and_grants(self) -> None:
        with _rolled_back() as conn:
            for table, (policies, s, i) in self.TABLES.items():
                with self.subTest(table=table):
                    row = conn.execute(
                        "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                        "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('authenticated', oid, 'SELECT'), "
                        "has_table_privilege('authenticated', oid, 'INSERT'), "
                        "has_table_privilege('authenticated', oid, 'UPDATE,DELETE'), "
                        "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                        "FROM pg_class c WHERE oid = %s::regclass", (f"public.{table}",)).fetchone()
                    self.assertEqual(row, (True, policies, False, s, i, False, True))
            self.assertEqual(conn.execute(
                "SELECT has_table_privilege('anon', 'public.v_title_state', 'SELECT'), "
                "has_table_privilege('authenticated', 'public.v_title_state', 'SELECT'), "
                "has_table_privilege('authenticated', 'public.v_title_state', 'INSERT,UPDATE,DELETE'), "
                "has_table_privilege('valemont_readonly', 'public.v_title_state', 'SELECT')").fetchone(),
                (False, True, False, True))

    def test_triggers(self) -> None:
        want = {
            "title_reactions": {"title_reactions_stamp", "title_reactions_immutable", "title_reactions_no_truncate",
                                "title_reactions_rerank"},
            "screening_rec_runs": {"screening_rec_runs_stamp", "screening_rec_runs_immutable",
                                   "screening_rec_runs_no_truncate"},
            "release_pushes": {"release_pushes_guard", "release_pushes_immutable", "release_pushes_no_truncate"},
            "release_alerts": {"release_alerts_guard", "release_alerts_immutable", "release_alerts_no_truncate"},
            "title_provider_arrivals": {"title_provider_arrivals_stamp", "title_provider_arrivals_immutable",
                                        "title_provider_arrivals_no_truncate"},
        }
        with _rolled_back() as conn:
            for table, names in want.items():
                got = {r[0] for r in conn.execute(
                    "SELECT tgname FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal", (table,))}
                self.assertEqual(got, names, table)
            ws = {r[0] for r in conn.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = 'watch_status'::regclass AND NOT tgisinternal")}
            self.assertTrue({"watch_status_rerank", "watch_status_title_entity"} <= ws)
            self.assertIn("feed_reactions_screening_rerank", {r[0] for r in conn.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = 'feed_reactions'::regclass AND NOT tgisinternal")})

    def test_function_grants(self) -> None:
        with _rolled_back() as conn:
            for fn, ro in (("public.screening_for_you(integer, boolean)", True),
                           ("public.screening_trailers_for_you(integer, integer, boolean)", True),
                           ("public.screening_events_on(date)", True),
                           ("public.title_news(bigint, integer, boolean)", True),
                           ("public.screening_excluded(bigint)", True),
                           ("public.add_test_rec(text, text, text, text)", False),
                           ("public.add_test_seen_title(text, text, text)", False),
                           ("public.title_entity_ensure(bigint)", False)):
                with self.subTest(fn=fn):
                    self.assertEqual(conn.execute(
                        "SELECT has_function_privilege('valemont_readonly', %s, 'EXECUTE'), "
                        "has_function_privilege('anon', %s, 'EXECUTE')", (fn, fn)).fetchone(), (ro, False))

    def test_seeds_notes_and_entities(self) -> None:
        with _rolled_back() as conn:
            self.assertEqual(dict(conn.execute("SELECT grp, count(*) FROM screening_seeds GROUP BY grp").fetchall()),
                             {"tv": 6, "film": 6, "romcom": 3})
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM title_reactions r JOIN screening_seeds s USING (title_id) "
                "WHERE r.origin = 'seed' AND r.reaction = 'love' AND r.seen").fetchone()[0], 15)
            self.assertEqual(ledger.screening_note_phrases(conn=conn),
                             ["power, money and dynasty dramas", "crime and business thrillers", "romantic comedies"])
            seeds = {r.tmdb_id for r in ledger.screening_records(conn=conn) if r.seed}
            self.assertEqual(seeds, {76331, 62852, 69740, 73586, 236235, 153312, 106646, 50839, 4566, 949, 238,
                                     473033, 8488, 455207, 14736})
            self.assertIn("'title'", conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'entities_kind_check'").fetchone()[0])
            # Every title on Want or Watching has its entity.
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM v_watch_current c LEFT JOIN entities e ON e.title_id = c.title_id "
                "WHERE c.status IN ('want', 'watching') AND e.id IS NULL").fetchone()[0], 0)


# ====================================================================== the owner's writes

class OwnerWrites(RecsSqlCase):
    def test_the_owner_reacts_as_herself_never_as_a_seed(self) -> None:
        with _rolled_back() as conn:
            tid = self.title(conn)
            with conn.cursor() as cur:
                _act_as(cur, "authenticated", self.owner)
                cur.execute("INSERT INTO title_reactions (title_id, reaction, origin) VALUES (%s, 'love', 'card') "
                            "RETURNING owner_id", (tid,))
                self.assertEqual(cur.fetchone()[0], self.owner)
                with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                    cur.execute("INSERT INTO title_reactions (title_id, reaction, seen, origin) "
                                "VALUES (%s, 'love', true, 'seed')", (tid,))
                with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                    cur.execute("UPDATE title_reactions SET reaction = 'neutral' WHERE title_id = %s", (tid,))
                with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                    cur.execute("INSERT INTO title_scores (title_id, score) VALUES (%s, 1)", (tid,))
                cur.execute("SELECT count(*) FROM screening_for_you(12, false)")  # she can read For you
                cur.execute("RESET ROLE")

    def test_append_only(self) -> None:
        with _rolled_back() as conn:
            tid = self.title(conn)
            self.react_title(conn, tid, "love")
            for sql in ("UPDATE title_reactions SET reaction = 'neutral' WHERE title_id = %(t)s",
                        "DELETE FROM title_reactions WHERE title_id = %(t)s"):
                with self.subTest(sql=sql[:6]), self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                    conn.execute(sql, {"t": tid})
            # created_at is the database's.
            conn.execute("INSERT INTO title_reactions (owner_id, title_id, reaction, origin, created_at) "
                         "VALUES (%s, %s, 'neutral', 'title', '2000-01-01')", (self.owner, tid))
            self.assertGreater(conn.execute("SELECT min(created_at) FROM title_reactions WHERE title_id = %s",
                                            (tid,)).fetchone()[0], datetime(2020, 1, 1, tzinfo=timezone.utc))


# ====================================================================== For you

class ForYou(RecsSqlCase):
    def test_live_exclusions_and_the_floor(self) -> None:
        with _rolled_back() as conn:
            loved = self.title(conn, name="tl loved")
            ids = {k: self.title(conn) for k in ("ok", "ni", "seen", "shelf", "finished", "neutral")}
            low = self.title(conn, vote_average=6.5)
            few = self.title(conn, vote_count=150)
            for k, tid in ids.items():
                self.candidate(conn, tid, 40.0, because=loved)
            self.candidate(conn, low, 45.0)
            self.candidate(conn, few, 45.0)
            got = {r[0] for r in self.for_you(conn)}
            self.assertTrue(set(ids.values()) <= got)
            self.assertFalse({low, few} & got)
            self.react_title(conn, ids["ni"], "not_interested")
            self.react_title(conn, ids["seen"], "none", origin="seen_pass")
            self.react_title(conn, ids["seen"], "love", seen=True, origin="seen_pass")
            self.move(conn, ids["shelf"], "want")
            self.move(conn, ids["finished"], "finished")
            self.react_title(conn, ids["neutral"], "neutral", origin="title")
            got = {r[0] for r in self.for_you(conn)}
            self.assertIn(ids["ok"], got)
            for k in ("ni", "seen", "shelf", "finished", "neutral"):
                self.assertNotIn(ids[k], got, k)
                self.assertTrue(conn.execute("SELECT screening_excluded(%s)", (ids[k],)).fetchone()[0], k)
            # A cleared reaction brings it back (it was never seen).
            self.react_title(conn, ids["neutral"], "none", origin="title")
            self.assertIn(ids["neutral"], {r[0] for r in self.for_you(conn)})
            self.assertEqual([r[3] for r in self.for_you(conn) if r[0] == ids["ok"]], ["tl loved"])
            # A seed is never recommended.
            seed = conn.execute("SELECT title_id FROM screening_seeds LIMIT 1").fetchone()[0]
            conn.execute("UPDATE titles SET vote_average = 9, vote_count = 9999 WHERE id = %s", (seed,))
            self.candidate(conn, seed, 49.0)
            self.assertNotIn(seed, {r[0] for r in self.for_you(conn)})

    def test_balance_matches_the_worker(self) -> None:
        with _rolled_back() as conn:
            conn.execute("UPDATE screening_candidates SET last_seen_at = now() - interval '10 days'")  # only ours count
            movies = [self.title(conn, "movie") for _ in range(12)]
            shows = [self.title(conn, "tv") for _ in range(5)]
            scored = [(m, 40.0 - i) for i, m in enumerate(movies)] + [(s, 20.0 - i) for i, s in enumerate(shows)]
            for tid, sc in scored:
                self.candidate(conn, tid, sc)
            ranked = [recs.Scored(t, s, None, {}) for t, s in sorted(scored, key=lambda x: (-x[1], x[0]))]
            kinds = {t: "movie" for t in movies} | {t: "tv" for t in shows}
            for n in (12, 5):
                with self.subTest(n=n):
                    want = [s.title_id for s in recs.balance(ranked, kinds, n=n)]
                    self.assertEqual([r[0] for r in self.for_you(conn, n)], want)
            self.assertEqual(sum(1 for r in self.for_you(conn, 12) if r[1] == "tv"), recs.MIN_EACH_KIND)

    def test_test_rows_only_with_the_flag(self) -> None:
        with _rolled_back() as conn:
            tid = conn.execute("INSERT INTO titles (tmdb_id, kind, name, is_test) VALUES (%s, 'movie', 'tl test rec', true) "
                               "RETURNING id", (900000000 + uuid.uuid4().int % 99999999,)).fetchone()[0]
            self.candidate(conn, tid, 49.0)
            self.assertNotIn(tid, {r[0] for r in self.for_you(conn)})
            self.assertIn((tid, True), {(r[0], r[4]) for r in self.for_you(conn, tests=True)})
            # The worker's record never reads a test title.
            self.react_title(conn, tid, "love")
            self.assertNotIn(tid, {r.title_id for r in ledger.screening_records(conn=conn)})


# ====================================================================== the re-rank queue

class RerankQueue(RecsSqlCase):
    def queued(self, conn: psycopg.Connection) -> int:
        return conn.execute("SELECT count(*) FROM job_queue WHERE kind = 'screening_rerank' AND status = 'queued'"
                            ).fetchone()[0]

    def test_debounced_and_never_for_a_test_title(self) -> None:
        with _rolled_back() as conn:
            conn.execute("DELETE FROM job_queue WHERE kind = 'screening_rerank' AND status = 'queued'")
            test_title = conn.execute("INSERT INTO titles (tmdb_id, kind, name, is_test) VALUES (%s, 'tv', 'tl t', true) "
                                      "RETURNING id", (900000000 + uuid.uuid4().int % 99999999,)).fetchone()[0]
            self.react_title(conn, test_title, "love")
            self.move(conn, test_title, "want")
            self.react(conn, self.story(conn, self.source(conn)), "love", pillar="home")  # Home never queues
            self.assertEqual(self.queued(conn), 0)
            tid = self.title(conn)
            self.react_title(conn, tid, "love")
            self.react_title(conn, tid, "not_interested")
            self.move(conn, tid, "watching")
            self.assertEqual(self.queued(conn), 1)
            self.assertTrue(conn.execute(
                "SELECT run_after > now() + interval '50 seconds' FROM job_queue "
                "WHERE kind = 'screening_rerank' AND status = 'queued'").fetchone()[0])
            # A screening trailer reaction queues too (when none is waiting).
            conn.execute("DELETE FROM job_queue WHERE kind = 'screening_rerank' AND status = 'queued'")
            src = self.source(conn, pillars=("screening",))
            self.react(conn, self.story(conn, src), "love", pillar="screening")
            self.assertEqual(self.queued(conn), 1)


# ====================================================================== release alerts

class ReleasePush(RecsSqlCase):
    def test_once_a_day_never_twice_test_exempt(self) -> None:
        with _rolled_back() as conn:
            today = ledger.owner_today(conn=conn)
            nf = {"flatrate": [{"id": 8, "name": "Netflix"}]}
            film = self.title(conn, name="tl out today", release=today, providers=nf)
            show = self.title(conn, "tv", name="tl new episode", next_ep=today)
            off = self.title(conn, name="tl off the shelves", release=today)
            for tid in (film, show):
                self.move(conn, tid, "want")
            self.move(conn, off, "finished")
            self.assertEqual(ledger.title_arrivals_add(film, ["Netflix"], conn=conn), 1)
            self.assertEqual(ledger.title_arrivals_add(film, ["Netflix"], conn=conn), 0)  # once a day
            events = [e for e in ledger.screening_events(conn=conn) if e.title_id in (film, show, off)]
            self.assertEqual(sorted((e.title_id, e.event_key, e.service) for e in events),
                             sorted([(film, f"release:{today}", "Netflix"), (film, "arrival:Netflix", "Netflix"),
                                     (show, f"episode:{today}", None)]))
            already = conn.execute("SELECT EXISTS (SELECT 1 FROM release_pushes WHERE local_day = owner_today() "
                                   "AND NOT is_test)").fetchone()[0]
            sent: list[int] = []
            push_id = ledger.release_push(events, lambda todo: sent.append(len(todo)) or None, conn=conn)
            if already:  # production pushed today already: the refusal path
                self.assertIsNone(push_id)
                self.assertEqual(sent, [])
            else:
                self.assertIsNotNone(push_id)
                self.assertEqual(sent, [3])
                self.assertTrue(all(e.alerted for e in ledger.screening_events(conn=conn)
                                    if e.title_id in (film, show)))
                # Not twice today; and the same event never again, even on a new push.
                self.assertIsNone(ledger.release_push(events, lambda todo: self.fail("sent twice"), conn=conn))
                with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                    conn.execute("INSERT INTO release_pushes (is_test) VALUES (false)")
                with self.assertRaises(psycopg.errors.UniqueViolation), conn.transaction():
                    conn.execute("INSERT INTO release_alerts (push_id, title_id, event_key) VALUES (%s, %s, %s)",
                                 (push_id, film, f"release:{today}"))
            # An alert for a title off Want/Watching is refused.
            any_push = conn.execute("SELECT id FROM release_pushes WHERE NOT is_test ORDER BY id DESC LIMIT 1").fetchone()
            if any_push:
                with self.assertRaisesRegex(psycopg.errors.RaiseException, "not a real Want or Watching"), conn.transaction():
                    conn.execute("INSERT INTO release_alerts (push_id, title_id, event_key) VALUES (%s, %s, %s)",
                                 (any_push[0], off, f"release:{today}"))
            # A test push is exempt from both rules, and its rows are test rows.
            tp = ledger.release_push(events[:1], lambda todo: None, test=True, conn=conn)
            tp2 = ledger.release_push(events[:1], lambda todo: None, test=True, conn=conn)
            self.assertEqual(conn.execute("SELECT bool_and(is_test), count(*) FROM release_alerts WHERE push_id IN (%s, %s)",
                                          (tp, tp2)).fetchone(), (True, 2))

    def test_the_brief_speaks_on_a_release(self) -> None:
        with _rolled_back() as conn:
            today = ledger.owner_today(conn=conn)
            tid = self.title(conn, name="tl brief film", release=today,
                             providers={"flatrate": [{"id": 1899, "name": "HBO Max"}]})
            self.move(conn, tid, "watching")
            brief = conn.execute("SELECT ctx_screening_brief()").fetchone()[0]
            self.assertIn({"title": "tl brief film", "kind": "movie", "what": "release", "on": "HBO Max"},
                          brief["releases_today"])
            ctx = conn.execute("SELECT ctx_screening()").fetchone()[0]
            self.assertTrue({"for_you_top5", "today", "recent_loves"} <= set(ctx))
            mine = [w for w in ctx["watching"] if w["title"] == "tl brief film"]
            self.assertEqual([w.get("tonight") for w in mine], [["HBO Max"]])
            self.assertIn("tl brief film", [t["title"] for t in ctx["today"]])


# ====================================================================== no cross-pillar leaks

class NoLeaks(RecsSqlCase):
    def test_a_title_entity_changes_nothing_on_home(self) -> None:
        with _rolled_back() as conn:
            before = conn.execute("SELECT id, pos FROM feed_for_you('home', 30, 0) ORDER BY pos").fetchall()
            names_before = ledger.feed_sports_entity_names(conn=conn)
            tid = self.title(conn, "tv", name="tl entity show")
            self.move(conn, tid, "want")
            self.assertEqual(conn.execute("SELECT kind, aliases FROM entities WHERE title_id = %s", (tid,)).fetchone(),
                             ("title", ["tl entity show"]))
            self.assertEqual(conn.execute("SELECT id, pos FROM feed_for_you('home', 30, 0) ORDER BY pos").fetchall(),
                             before)
            self.assertEqual(ledger.feed_sports_entity_names(conn=conn), names_before)
            self.assertEqual(conn.execute("SELECT count(*) FROM follows f JOIN entities e ON e.id = f.entity_id "
                                          "WHERE e.kind = 'title'").fetchone()[0], 0)

    def test_title_news_reads_home_only(self) -> None:
        with _rolled_back() as conn:
            tid = self.title(conn, "tv", name="tl gilded saga")
            self.move(conn, tid, "watching")
            home = self.story(conn, self.source(conn), title="Inside the tl gilded saga finale")
            sports = self.story(conn, self.source(conn, ("sports",), sport="nfl"), title="tl gilded saga on the sideline")
            trailer = self.story(conn, self.source(conn, ("screening",)), title="tl gilded saga | Official Trailer")
            near = self.story(conn, self.source(conn), title="tl gilded sagas everywhere")  # not a whole-word match
            got = [r[0] for r in conn.execute("SELECT story_id FROM title_news(%s, 3, false)", (tid,))]
            self.assertEqual(got, [home])
            self.assertFalse({sports, trailer, near} & set(got))
            # Finished: no news.
            self.move(conn, tid, "finished")
            self.assertEqual(conn.execute("SELECT count(*) FROM title_news(%s, 3, false)", (tid,)).fetchone()[0], 0)

    def test_title_news_with_accents_and_punctuation(self) -> None:
        # Only regex metacharacters are escaped: an accented letter or a
        # bracket in a name must neither raise nor fail to match.
        with _rolled_back() as conn:
            for name, headline in (("tl amélie (redux)", "Why tl Amélie (Redux) still works"),
                                   ("tl mr. robot+ [x]", "tl Mr. Robot+ [x] returns")):
                tid = self.title(conn, "tv", name=name)
                self.move(conn, tid, "want")
                sid = self.story(conn, self.source(conn), title=headline)
                self.story(conn, self.source(conn), title=headline.replace(".", "x").replace("é", "e"))
                got = [r[0] for r in conn.execute("SELECT story_id FROM title_news(%s, 3, false)", (tid,))]
                self.assertEqual(got, [sid], name)

    def test_trailers_for_you_lists_screening_only_ranked_by_score(self) -> None:
        with _rolled_back() as conn:
            src = self.source(conn, ("screening",))
            low, high, unmatched = (self.story(conn, src, title=f"tl trailer {i}") for i in range(3))
            home = self.story(conn, self.source(conn), title="tl home story")
            t_low, t_high = self.title(conn), self.title(conn)
            for sid, tid in ((low, t_low), (high, t_high)):
                item = conn.execute("SELECT lead_item_id FROM stories WHERE id = %s", (sid,)).fetchone()[0]
                ledger.title_trailer_record(item, tid, "tl", conn=conn)
            self.candidate(conn, t_low, -5.0)
            self.candidate(conn, t_high, 30.0)
            order = [r[0] for r in conn.execute("SELECT story_id FROM screening_trailers_for_you(100000, 0, false)")]
            self.assertNotIn(home, order)
            self.assertLess(order.index(high), order.index(low))
            self.assertLess(order.index(low), order.index(unmatched))  # scored before unscored
            self.react(conn, high, "not_interested", pillar="screening")
            self.assertNotIn(high, [r[0] for r in conn.execute(
                "SELECT story_id FROM screening_trailers_for_you(100000, 0, false)")])

    def test_one_youtube_key_across_both_sources(self) -> None:
        with _rolled_back() as conn:
            rss = self.source(conn, ("screening",))
            conn.execute("INSERT INTO sources (slug, name, pillars, kind, url, active) VALUES "
                         "(%s, 'TL tmdb', ARRAY['screening'], 'tmdb', 'https://api.themoviedb.org/3', false)",
                         (slug := f"tl-tmdb-{uuid.uuid4().hex[:8]}",))
            tmdb_src = ledger.feed_source(slug, conn=conn)
            key = "Tl" + uuid.uuid4().hex[:9]
            item = ledger.FeedItemIn(external_id=f"yt:video:{key}", url=f"https://www.youtube.com/watch?v={key}",
                                     google_url=None, title="tl | Official Trailer", teaser=None,
                                     published_at=datetime.now(timezone.utc) - timedelta(hours=1), decode_failed=False)
            first = ledger.feed_new_story(tmdb_src, item, unit(3), unit(3), filtered_reason=None, seed=None, conn=conn)
            self.assertIsNotNone(first)
            self.assertEqual(ledger.screening_known_youtube_keys([key], conn=conn), {key})
            # The RSS path then brings the same video: nothing new (the story rolls back).
            again = ledger.feed_new_story(rss, item, unit(3), unit(3), filtered_reason=None, seed=None, conn=conn)
            self.assertIsNone(again)
            self.assertEqual(conn.execute("SELECT count(*) FROM content_items WHERE youtube_key = %s", (key,))
                             .fetchone()[0], 1)
            # And a tmdb source outside screening is refused.
            with self.assertRaises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute("INSERT INTO sources (slug, name, pillars, kind, url) VALUES "
                             "('tl-tmdb-bad', 'x', ARRAY['home'], 'tmdb', 'https://api.themoviedb.org/3')")
            # The 30-minute ingest never fetches it.
            self.assertNotIn(slug, [s.slug for s in ledger.feed_active_sources(conn=conn)])


# ====================================================================== the worker's queries

class WorkerQueries(RecsSqlCase):
    def test_the_full_cycle(self) -> None:
        with _rolled_back() as conn:
            conn.execute("UPDATE screening_candidates SET last_seen_at = now() - interval '10 days'")
            loved = self.title(conn, name="tl loved film", embedding=vec((1, 1.0)))
            self.react_title(conn, loved, "love")
            pool = [self.title(conn, embedding=vec((1, 1.0), (i + 2, 0.5))) for i in range(3)]
            ledger.screening_candidates_upsert([(t, ["recommendations", "trending"], [loved]) for t in pool], conn=conn)
            # The record and the pool, as the worker reads them.
            rec = {r.title_id: r for r in ledger.screening_records(conn=conn)}
            self.assertEqual((rec[loved].reaction, rec[loved].embedded), ("love", True))
            got = {p.id: p for p in ledger.screening_pool(conn=conn)}
            self.assertTrue(set(pool) <= set(got))
            self.assertTrue(got[pool[0]].trending)
            # The embedding is written once.
            ledger.titles_set_embedding(loved, vec((7, 1.0)), conn=conn)
            self.assertEqual(ledger.screening_title_vectors([loved], conn=conn)[loved][1], 1.0)
            # A run is append-only; scores are replaced in place.
            p = recs.build_profile(ledger.screening_title_vectors([loved], conn=conn)[loved],
                                   [recs.Signal(loved, "love", 1.0)],
                                   ledger.screening_title_vectors([loved] + pool, conn=conn), [])
            run = ledger.screening_insert_run(p, reason="manual", pool_n=3, eligible_n=3, scored_n=3, tmdb_calls=0,
                                              top=[{"title_id": pool[0]}], is_test=True, conn=conn)
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                conn.execute("UPDATE screening_rec_runs SET pool_n = 9 WHERE id = %s", (run,))
            ledger.title_scores_upsert([recs.Scored(t, 10.0 + i, loved, {"x": 1}) for i, t in enumerate(pool)], run, conn=conn)
            ledger.title_scores_upsert([recs.Scored(pool[0], 1.0, loved, {})], run, conn=conn)
            self.assertEqual(conn.execute("SELECT score FROM title_scores WHERE title_id = %s", (pool[0],)).fetchone()[0], 1.0)
            top = [r["title_id"] for r in ledger.screening_top(12, conn=conn)]
            self.assertEqual([t for t in top if t in pool], [pool[2], pool[1], pool[0]])
            # The unembedded queue: a reacted title with no vector.
            bare = self.title(conn)
            self.react_title(conn, bare, "neutral", origin="title")
            self.assertIn(bare, [r.id for r in ledger.screening_unembedded(100000, conn=conn)])
            self.assertNotIn(loved, [r.id for r in ledger.screening_unembedded(100000, conn=conn)])

    def test_seen_pass_written_once(self) -> None:
        with _rolled_back() as conn:
            existing = ledger.screening_seen_pass_count(conn=conn)
            a, b = self.title(conn), self.title(conn)
            n = ledger.screening_seen_pass_write([(a, "film"), (b, "tv")], conn=conn)
            self.assertEqual(n, 0 if existing else 2)
            self.assertEqual(ledger.screening_seen_pass_write([(b, "tv")], conn=conn), 0)

    def test_arrival_baseline_from_providers(self) -> None:
        with _rolled_back() as conn:
            tid = self.title(conn, "tv", providers={"flatrate": [{"id": 8, "name": "Netflix"}]})
            self.move(conn, tid, "watching")
            shelf = {s.id: s for s in ledger.screening_shelf_titles(conn=conn)}
            self.assertEqual(shelf[tid].providers, {"flatrate": [{"id": 8, "name": "Netflix"}]})
            self.assertEqual(conn.execute("SELECT tonight FROM titles WHERE id = %s", (tid,)).fetchone()[0], ["Netflix"])

    def test_the_test_push_title(self) -> None:
        with _rolled_back() as conn:
            tid = self.title(conn, "tv", name="tl newest want", providers={"flatrate": [{"id": 43, "name": "Starz"}]})
            self.move(conn, tid, "want")
            e = ledger.screening_test_push_title(conn=conn)
            self.assertEqual((e.title_id, e.what, e.service), (tid, "episode", "Starz"))
            self.assertEqual(ledger.screening_test_push_title(tid, conn=conn).title_id, tid)


if __name__ == "__main__":
    unittest.main()
