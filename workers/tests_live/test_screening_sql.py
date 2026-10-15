"""Screening Room SQL against the real database (db/051; Chat 4 Phase 3):

* the db/051 objects: RLS and grants on titles, title_trailers and
  watch_status (owner reads; adds and refreshes titles; appends moves; anon
  nothing; readonly reads), the triggers, the providers, the test source.
* NO CROSS-IN for screening: a screening reaction or score only on a
  screening story, and never a home, sports or tech one on it; feed_for_you
  never lists a screening story on Home, Sports or Tech; the Home/Sports
  summary queue and the backlog leave trailers out; clustering candidates
  carry their world.
* WATCH HISTORY APPEND-ONLY: every move a new row; finished_at is the
  trigger's (set on a move to finished, never kept from the writer); UPDATE,
  DELETE and TRUNCATE refused; a re-watch is a new row; v_watch_current is
  the latest move. The owner appends as herself through RLS.
* the worker's queries: titles_upsert (insert, then refresh in place),
  screening_unmatched / title_trailer_record, screening_shelf_titles,
  ctx_screening.

Every test runs inside ONE transaction that is always rolled back. Skips
until db/051 is pasted.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import date

import psycopg

from core import ledger
from screening import tmdb
from tests_live.test_feed_rank_sql import RankSqlCase, _act_as, _rolled_back



def candidate_worlds(conn, ids, *, hours: int = 48, is_test: bool = False) -> dict:
    """Each of `ids`' world as FEED_CANDIDATES_SQL gives it, decided in the
    database: reading the whole real window would pull ~17-25 MB of vectors
    (egress fix 1, 2026-10-15)."""
    return dict(conn.execute(
        f"SELECT DISTINCT x.id, x.w FROM ({ledger.FEED_CANDIDATES_SQL}) x(id, n, t, v, w, i) WHERE x.id = ANY (%(ids)s)",
        {"is_test": is_test, "hours": hours, "since": None, "skip": [], "ids": list(ids)}).fetchall())

class ScreeningSqlCase(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.watch_status')").fetchone()[0] is None:
                raise unittest.SkipTest("db/051 not pasted")

    def yt_source(self, conn: psycopg.Connection) -> ledger.FeedSource:
        slug = f"tl-yt-{uuid.uuid4().hex[:8]}"
        conn.execute("""INSERT INTO sources (slug, name, pillars, kind, url, active)
                        VALUES (%s, %s, ARRAY['screening'], 'rss', 'https://tests-live.invalid/yt', false)""",
                     (slug, f"TL {slug}"))
        return ledger.feed_source(slug, conn=conn)

    def title(self, conn: psycopg.Connection, kind: str = "tv", **kw) -> int:
        t = tmdb.TitleIn(tmdb_id=kw.get("tmdb_id", 800000000 + uuid.uuid4().int % 99999999), kind=kind,
                         name=kw.get("name", "tests-live title"), release_date=kw.get("release"),
                         poster_path=None, overview=None, genres=(), providers=kw.get("providers", {}),
                         trailer_key=None, next_episode_date=kw.get("next"), status=None)
        return ledger.titles_upsert(t, conn=conn)

    def move(self, conn: psycopg.Connection, title_id: int, status: str, **kw) -> None:
        conn.execute("INSERT INTO watch_status (owner_id, title_id, status, finished_at) VALUES (%s, %s, %s, %s)",
                     (self.owner, title_id, status, kw.get("finished_at")))


class Objects(ScreeningSqlCase):
    def test_tables_rls_and_grants(self) -> None:
        with _rolled_back() as conn:
            want = {"titles": (3, True, True, True, False), "title_trailers": (1, True, False, False, False),
                    "watch_status": (2, True, True, False, False)}
            for table, (policies, s, i, u, d) in want.items():
                with self.subTest(table=table):
                    row = conn.execute(
                        "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                        "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('authenticated', oid, 'SELECT'), "
                        "has_table_privilege('authenticated', oid, 'INSERT'), "
                        "has_table_privilege('authenticated', oid, 'UPDATE'), "
                        "has_table_privilege('authenticated', oid, 'DELETE'), "
                        "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                        "FROM pg_class c WHERE oid = %s::regclass", (f"public.{table}",)).fetchone()
                    self.assertEqual(row, (True, policies, False, s, i, u, d, True))
            # Every public view is read-only for the owner (db/052 fixed v_watch_current).
            writable = [r[0] for r in conn.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'v' "
                "AND has_table_privilege('authenticated', c.oid, 'INSERT,UPDATE,DELETE')")]
            fixed = conn.execute("SELECT 1 FROM migration_log WHERE name = '052_watch_current_grants'").fetchone()
            self.assertEqual(writable, [] if fixed else ["v_watch_current"])
            # db/053 adds two AFTER INSERT triggers (the re-rank queue, the title entity).
            recs = conn.execute("SELECT 1 FROM migration_log WHERE name = '053_screening_recs'").fetchone()
            self.assertEqual({r[0] for r in conn.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = 'watch_status'::regclass AND NOT tgisinternal")},
                {"watch_status_stamp", "watch_status_immutable", "watch_status_no_truncate"}
                | ({"watch_status_rerank", "watch_status_title_entity"} if recs else set()))
            self.assertEqual(conn.execute("SELECT key, brief_section, enabled FROM context_providers "
                                          "WHERE key LIKE 'screening%%' ORDER BY key").fetchall(),
                             [("screening", False, True), ("screening_brief", True, True)])
            if not recs:  # silent until db/053; from then it speaks (tests_live/test_screening_recs_sql.py)
                self.assertIsNone(conn.execute("SELECT ctx_screening_brief()").fetchone()[0])
            self.assertEqual(conn.execute("SELECT active, is_test, pillars FROM sources WHERE slug = '_e2e-screening'")
                             .fetchone(), (False, True, ["screening"]))
            for fn, ro in (("public.ctx_screening()", True), ("public.add_test_title(text, text, text)", False)):
                self.assertEqual(conn.execute("SELECT has_function_privilege('valemont_readonly', %s, 'EXECUTE'), "
                                              "has_function_privilege('anon', %s, 'EXECUTE')", (fn, fn)).fetchone(),
                                 (ro, False))


class NoCrossIn(ScreeningSqlCase):
    def test_a_screening_story_lives_only_in_screening(self) -> None:
        with _rolled_back() as conn:
            trailer = self.story(conn, self.yt_source(conn))
            home = self.story(conn, self.source(conn))
            tech_src = ledger.feed_source(
                conn.execute("INSERT INTO sources (slug, name, pillars, kind, url, active) VALUES "
                             "(%s, 'TL tech', ARRAY['tech'], 'rss', 'https://tests-live.invalid/t', false) RETURNING slug",
                             (f"tl-t-{uuid.uuid4().hex[:8]}",)).fetchone()[0], conn=conn)
            tech = self.story(conn, tech_src)
            for sid, pillar in ((trailer, "home"), (trailer, "sports"), (trailer, "tech"), (home, "screening"),
                                (tech, "screening")):
                for sql in ("INSERT INTO feed_reactions (owner_id, story_id, pillar, reaction) VALUES (%(o)s, %(s)s, %(p)s, 'love')",
                            "INSERT INTO story_scores (story_id, pillar, score, components) VALUES (%(s)s, %(p)s, 1, '{}')"):
                    with self.subTest(story=sid, pillar=pillar, sql=sql[:24]), \
                            self.assertRaisesRegex(psycopg.errors.RaiseException, "crosses the tech line"), conn.transaction():
                        conn.execute(sql, {"o": self.owner, "s": sid, "p": pillar})
            self.react(conn, trailer, "love", pillar="screening")
            self.score(conn, trailer, 49.0, pillar="screening")
            for pillar in ("home", "sports", "tech"):
                ids = {r[0] for r in conn.execute("SELECT id FROM feed_for_you(%s, 100000, 0)", (pillar,))}
                self.assertNotIn(trailer, ids, pillar)
            self.assertNotIn(trailer, {r[0] for r in conn.execute("SELECT id FROM feed_for_you('home', 100000, 0, NULL, NULL, 0)")})

    def test_trailers_are_never_summarized_or_a_backlog_and_cluster_in_their_world(self) -> None:
        with _rolled_back() as conn:
            trailer = self.story(conn, self.yt_source(conn), done=False)
            conn.execute("UPDATE stories SET first_seen_at = now() - interval '30 hours', "
                         "last_seen_at = now() - interval '30 hours' WHERE id = %s", (trailer,))
            queued = {j.story_id for j in ledger.feed_summary_queue(100000, 2, conn=conn)}
            self.assertNotIn(trailer, queued)
            before = ledger.feed_summary_backlog(24, 2, conn=conn)
            conn.execute("UPDATE stories SET pillars = ARRAY['home'] WHERE id = %s", (trailer,))
            self.assertEqual(ledger.feed_summary_backlog(24, 2, conn=conn), before + 1)  # the same row counts as Home
            conn.execute("UPDATE stories SET pillars = ARRAY['screening'] WHERE id = %s", (trailer,))
            worlds = candidate_worlds(conn, [trailer], hours=72)
            self.assertEqual(worlds.get(trailer), "screening")


class WatchHistory(ScreeningSqlCase):
    def test_moves_are_rows_and_finished_at_is_the_triggers(self) -> None:
        with _rolled_back() as conn:
            tid = self.title(conn)
            self.move(conn, tid, "want", finished_at="2000-01-01")
            self.move(conn, tid, "watching")
            self.assertEqual(conn.execute("SELECT status FROM v_watch_current WHERE title_id = %s", (tid,)).fetchone()[0],
                             "watching")
            self.move(conn, tid, "finished")
            self.move(conn, tid, "watching")  # a re-watch is a new row
            self.move(conn, tid, "finished")
            rows = conn.execute("SELECT status, finished_at IS NOT NULL FROM watch_status WHERE title_id = %s ORDER BY id",
                                (tid,)).fetchall()
            self.assertEqual(rows, [("want", False), ("watching", False), ("finished", True), ("watching", False),
                                    ("finished", True)])
            cur = conn.execute("SELECT status, finished_at FROM v_watch_current WHERE title_id = %s", (tid,)).fetchone()
            self.assertEqual(cur[0], "finished")
            self.assertIsNotNone(cur[1])

    def test_append_only(self) -> None:
        with _rolled_back() as conn:
            tid = self.title(conn)
            self.move(conn, tid, "want")
            for sql in ("UPDATE watch_status SET status = 'finished' WHERE title_id = %(t)s",
                        "DELETE FROM watch_status WHERE title_id = %(t)s", "TRUNCATE watch_status"):
                with self.subTest(sql=sql[:20]), self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                    conn.execute(sql, {"t": tid})

    def test_the_owner_appends_as_herself_and_never_edits(self) -> None:
        with _rolled_back() as conn, conn.cursor() as cur:
            tid = self.title(conn)
            _act_as(cur, "authenticated", self.owner)
            cur.execute("INSERT INTO watch_status (title_id, status) VALUES (%s, 'finished')", (tid,))
            cur.execute("SELECT owner_id, finished_at IS NOT NULL FROM watch_status WHERE title_id = %s", (tid,))
            self.assertEqual(cur.fetchall(), [(self.owner, True)])
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SAVEPOINT s")
                cur.execute("DELETE FROM watch_status WHERE title_id = %s", (tid,))
            cur.execute("ROLLBACK TO SAVEPOINT s")
            # Titles: the owner adds a real one, never a test one.
            cur.execute("INSERT INTO titles (tmdb_id, kind, name) VALUES (%s, 'movie', 'owner add') RETURNING id",
                        (700000000 + uuid.uuid4().int % 99999999,))
            self.assertTrue(cur.fetchone()[0])
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SAVEPOINT t")
                cur.execute("INSERT INTO titles (tmdb_id, kind, name, is_test) VALUES (1, 'movie', 'x', true)")
            cur.execute("ROLLBACK TO SAVEPOINT t")


class WorkerQueries(ScreeningSqlCase):
    def test_upsert_refreshes_in_place(self) -> None:
        with _rolled_back() as conn:
            tid = self.title(conn, tmdb_id=812345678, name="First", next=date(2099, 1, 1))
            again = self.title(conn, tmdb_id=812345678, name="Renamed", providers={"flatrate": [{"id": 8, "name": "Netflix", "logo": None}]})
            self.assertEqual(again, tid)
            self.assertEqual(conn.execute("SELECT name, next_episode_date, providers -> 'flatrate' -> 0 ->> 'id', "
                                          "providers_refreshed_at IS NOT NULL FROM titles WHERE id = %s", (tid,)).fetchone(),
                             ("Renamed", None, "8", True))
            # A movie has no next episode: the ledger never writes one, and the
            # database refuses it from any writer (titles_next_episode_tv).
            movie = self.title(conn, kind="movie", next=date(2099, 1, 1))
            self.assertIsNone(conn.execute("SELECT next_episode_date FROM titles WHERE id = %s", (movie,)).fetchone()[0])
            with self.assertRaises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute("INSERT INTO titles (tmdb_id, kind, name, next_episode_date) VALUES (%s, 'movie', 'x', '2099-01-01')",
                             (811111111,))

    def test_unmatched_queue_and_record(self) -> None:
        with _rolled_back() as conn:
            sid = self.story(conn, self.yt_source(conn), title="Dune | Official Trailer")
            item = conn.execute("SELECT lead_item_id FROM stories WHERE id = %s", (sid,)).fetchone()[0]
            self.assertIn(item, [t.item_id for t in ledger.screening_unmatched(100000, conn=conn)])
            self.assertTrue(ledger.title_trailer_record(item, None, "Dune", conn=conn))
            self.assertFalse(ledger.title_trailer_record(item, None, "Dune", conn=conn))  # once
            self.assertNotIn(item, [t.item_id for t in ledger.screening_unmatched(100000, conn=conn)])
            self.assertEqual(conn.execute("SELECT status, title_id FROM title_trailers WHERE item_id = %s", (item,)).fetchone(),
                             ("unmatched", None))

    def test_shelf_titles_and_context(self) -> None:
        with _rolled_back() as conn:
            tid = self.title(conn, name="tests-live shelf show", next=ledger.owner_today(conn=conn))
            self.move(conn, tid, "watching")
            self.assertIn(tid, [s.id for s in ledger.screening_shelf_titles(conn=conn)])
            ctx = conn.execute("SELECT ctx_screening()").fetchone()[0]
            self.assertIn("tests-live shelf show", [w["title"] for w in ctx["watching"]])
            self.assertIn("tests-live shelf show", [u["title"] for u in ctx["upcoming_30d"]])


if __name__ == "__main__":
    unittest.main()
