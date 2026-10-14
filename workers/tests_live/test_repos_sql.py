"""GitHub repos SQL against the real database (db/049; Chat 4 Phase 2):

* the db/049 objects: the three tables' RLS and grants (owner read only,
  anon nothing, readonly reads), the triggers, the functions' privileges,
  the `github` source kind and the `_e2e-repos` test source.
* THE RULES IN SQL: a repo surfaces once; again only when its stars_7d at
  least doubles its baseline (the first-shown stars_7d when measured, else,
  since db/050, its first measured one) AND 14 days have passed
  (repo_may_surface asked about day + 13 and day + 14); at most 8 real
  surfacings a day (repo_surface_room, and the trigger through
  ledger.repo_surface, which returns None and writes nothing when refused).
* a repo reaction is pillar tech, never home or sports (db/045's guard).
* the worker's queries: upsert and one snapshot a day (the day is the
  database's), the repo why queue and its save (story and repo), Home's why
  queue never asking a repo story, the prune keeping a surfaced story, the
  30-minute ingest never listing the github source, ctx_tech's repos, and
  summaries keeping a repo story's topics.

FIXTURES AND TODAY'S CAP. From the first production run there are 8 real
surfacings every day, so a fixture that needs a real surfacing cannot go
through the guard after 07:00 Chicago. Such fixtures are written with the
guard switched off (`guard_off`: ALTER TABLE ... DISABLE TRIGGER, which is
transactional) INSIDE the test's rolled-back transaction, dated a day no
real row will ever have (FAR) when the date matters. The guard itself is
exercised through the real path wherever a test is about it: refusals are
checked whether today's room is 0 or not, never skipped.

Every test runs inside ONE transaction that is always rolled back. Skips
until db/049 is pasted.
"""

from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Iterator

import psycopg

from core import ledger
from tests_live.test_feed_rank_sql import RankSqlCase, _rolled_back, unit

#: A day no real surfacing will ever carry.
FAR = date(2099, 1, 1)


class RepoSqlCase(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.repo_surfacings')").fetchone()[0] is None:
                raise unittest.SkipTest("db/049 not pasted")

    @staticmethod
    @contextmanager
    def guard_off(conn: psycopg.Connection) -> Iterator[None]:
        """Fixtures only, inside the rolled-back transaction (see the module doc)."""
        conn.execute("ALTER TABLE repo_surfacings DISABLE TRIGGER repo_surfacings_guard")
        try:
            yield
        finally:
            conn.execute("ALTER TABLE repo_surfacings ENABLE TRIGGER repo_surfacings_guard")

    def gh_source(self, conn: psycopg.Connection) -> ledger.FeedSource:
        slug = f"tl-gh-{uuid.uuid4().hex[:8]}"
        conn.execute("""INSERT INTO sources (slug, name, pillars, kind, url, active)
                        VALUES (%s, %s, ARRAY['tech'], 'github', 'https://github.com/tests-live', false)""",
                     (slug, f"TL {slug}"))
        return ledger.feed_source(slug, conn=conn)

    def repo(self, conn: psycopg.Connection, stars: int = 100) -> tuple[int, str]:
        name = f"tests-live/r-{uuid.uuid4().hex[:10]}"
        ids = ledger.repos_upsert([ledger.RepoIn(name, f"https://tests-live.invalid/{name}", "A test repo.",
                                                 "Python", ("ai-agents",), "MIT", False, False, None, None, stars)],
                                  conn=conn)
        return ids[name.lower()], name

    def row(self, name: str, conn: psycopg.Connection, stars_7d: int | None = 10,
            source: str | None = "measured") -> ledger.RepoSurfaceIn:
        return ledger.RepoSurfaceIn(name, f"https://tests-live.invalid/{name}", "A test repo.", ("ai_agents",),
                                    unit(1), ledger.owner_today(conn=conn), 100, stars_7d, source)

    def surfaced(self, conn: psycopg.Connection, src: ledger.FeedSource | None = None,
                 stars_7d: int | None = 10, source: str | None = "measured") -> tuple[int, int, str]:
        """A real repo surfaced today through ledger.repo_surface, the guard
        off (a fixture: today's real 8 may already be used). (story, repo, name)."""
        src = src or self.gh_source(conn)
        rid, name = self.repo(conn)
        with self.guard_off(conn):
            sid = ledger.repo_surface(src, rid, self.row(name, conn, stars_7d, source), conn=conn)
        assert sid is not None
        return sid, rid, name

    def far_surfacing(self, conn: psycopg.Connection, rid: int, day: date, stars_7d: int | None) -> None:
        """A real surfacing row dated `day` (a fixture for the rule's arithmetic)."""
        sid = conn.execute("INSERT INTO stories (pillars, title, category) VALUES (ARRAY['tech'], 'tl repo', 'repos') "
                           "RETURNING id").fetchone()[0]
        with self.guard_off(conn):
            conn.execute("INSERT INTO repo_surfacings (repo_id, story_id, surfaced_on, stars, stars_7d, stars_7d_source) "
                         "VALUES (%s, %s, %s, 100, %s, %s)",
                         (rid, sid, day, stars_7d, "measured" if stars_7d is not None else None))


class Objects(RepoSqlCase):
    def test_tables_rls_and_grants(self) -> None:
        with _rolled_back() as conn:
            for table in ("repos", "repo_star_snapshots", "repo_surfacings"):
                with self.subTest(table=table):
                    row = conn.execute(
                        "SELECT relrowsecurity, "
                        "(SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                        "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('authenticated', oid, 'SELECT'), "
                        "has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                        "FROM pg_class c WHERE oid = %s::regclass", (f"public.{table}",)).fetchone()
                    self.assertEqual(row, (True, 1, False, True, False, True))
            triggers = {r[0] for r in conn.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid IN ('repo_star_snapshots'::regclass, "
                "'repo_surfacings'::regclass) AND NOT tgisinternal").fetchall()}
            self.assertEqual(triggers, {"repo_star_snapshots_guard", "repo_star_snapshots_immutable",
                                        "repo_star_snapshots_no_truncate", "repo_surfacings_guard",
                                        "repo_surfacings_immutable", "repo_surfacings_no_truncate"})
            for fn, ro in (("public.repo_may_surface(bigint, integer, date)", True),
                           ("public.repo_surface_room(date)", True), ("public.ctx_tech()", True),
                           ("public.ctx_tech_brief()", True),
                           ("public.add_test_repo(text, text, text, integer, integer, text)", False)):
                with self.subTest(fn=fn):
                    self.assertEqual(conn.execute(
                        "SELECT has_function_privilege('valemont_readonly', %s, 'EXECUTE'), "
                        "has_function_privilege('anon', %s, 'EXECUTE')", (fn, fn)).fetchone(), (ro, False))
            self.assertEqual(conn.execute("SELECT active, is_test, kind, official FROM sources "
                                          "WHERE slug = '_e2e-repos'").fetchone(), (False, True, "github", False))

    def test_a_github_source_is_on_github_and_never_official(self) -> None:
        with _rolled_back() as conn:
            for url, official in (("https://tests-live.invalid/x", False), ("https://github.com/x", True)):
                with self.subTest(url=url, official=official), self.assertRaises(psycopg.errors.CheckViolation):
                    with conn.transaction():
                        conn.execute("INSERT INTO sources (slug, name, pillars, kind, url, official) "
                                     "VALUES ('tl-ghx', 'x', ARRAY['tech'], 'github', %s, %s)", (url, official))

    def test_the_30_minute_ingest_never_lists_the_github_source(self) -> None:
        with _rolled_back() as conn:
            src = self.gh_source(conn)
            conn.execute("UPDATE sources SET active = true WHERE id = %s", (src.id,))
            self.assertNotIn(src.slug, [s.slug for s in ledger.feed_active_sources(conn=conn)])


class TheRulesInSql(RepoSqlCase):
    def test_14_days_and_double_the_first_shown_stars_7d(self) -> None:
        with _rolled_back() as conn:
            rid, _ = self.repo(conn)
            ask = "SELECT repo_may_surface(%s, %s, %s)"
            self.assertTrue(conn.execute(ask, (rid, None, FAR)).fetchone()[0])  # never surfaced
            self.far_surfacing(conn, rid, FAR, 10)
            self.assertFalse(conn.execute(ask, (rid, 1000, FAR)).fetchone()[0])
            self.assertFalse(conn.execute(ask, (rid, 1000, FAR + timedelta(days=13))).fetchone()[0])
            self.assertTrue(conn.execute(ask, (rid, 20, FAR + timedelta(days=14))).fetchone()[0])
            self.assertFalse(conn.execute(ask, (rid, 19, FAR + timedelta(days=14))).fetchone()[0])
            self.assertFalse(conn.execute(ask, (rid, None, FAR + timedelta(days=30))).fetchone()[0])
            # A second surfacing: the 14 days run from the LAST one, the doubling
            # from the FIRST one's stars_7d (10), not the second's (40).
            self.far_surfacing(conn, rid, FAR + timedelta(days=20), 40)
            self.assertFalse(conn.execute(ask, (rid, 1000, FAR + timedelta(days=33))).fetchone()[0])
            self.assertTrue(conn.execute(ask, (rid, 20, FAR + timedelta(days=34))).fetchone()[0])

    def test_no_first_stars_7d_and_no_measurement_means_no_resurfacing_yet(self) -> None:
        with _rolled_back() as conn:
            rid, _ = self.repo(conn)
            self.far_surfacing(conn, rid, FAR, None)
            self.assertFalse(conn.execute("SELECT repo_may_surface(%s, 100000, %s)",
                                          (rid, FAR + timedelta(days=60))).fetchone()[0])


    def test_the_guard_refuses_the_same_repo_again_today(self) -> None:
        with _rolled_back() as conn:
            sid, rid, name = self.surfaced(conn, stars_7d=10)
            stories = conn.execute("SELECT count(*) FROM stories").fetchone()[0]
            # The real path, guard on: refused (by the rule, or by the cap when
            # today's 8 are used), and nothing at all is written.
            self.assertIsNone(ledger.repo_surface(self.gh_source(conn), rid, self.row(name, conn, 1000), conn=conn))
            self.assertEqual(conn.execute("SELECT count(*) FROM stories").fetchone()[0], stories)
            self.assertFalse(ledger.repo_may_surface(rid, 1000, conn=conn))
            today = ledger.owner_today(conn=conn)
            self.assertEqual(conn.execute("SELECT surfaced_on, stars_7d, stars_7d_source FROM repo_surfacings "
                                          "WHERE story_id = %s", (sid,)).fetchone(), (today, 10, "measured"))
            self.assertEqual(conn.execute("SELECT category, pillars FROM stories WHERE id = %s", (sid,)).fetchone(),
                             ("repos", ["tech"]))
            self.assertEqual(conn.execute(
                "SELECT i.published_precision, i.published_at = date_trunc('day', i.published_at) "
                "FROM stories st JOIN content_items i ON i.id = st.lead_item_id WHERE st.id = %s",
                (sid,)).fetchone(), ("date", True))

    def test_the_cap_of_8_a_day(self) -> None:
        with _rolled_back() as conn:
            self.assertEqual(conn.execute("SELECT repo_surface_room(%s)", (FAR,)).fetchone()[0], 8)
            for _ in range(8):
                self.far_surfacing(conn, self.repo(conn)[0], FAR, 5)
            self.assertEqual(conn.execute("SELECT repo_surface_room(%s)", (FAR,)).fetchone()[0], 0)
            # Today, through the guard: fill whatever room is left, then one more is refused.
            src = self.gh_source(conn)
            for _ in range(ledger.repos_surfaced_today(conn=conn), 8):
                rid, name = self.repo(conn)
                self.assertIsNotNone(ledger.repo_surface(src, rid, self.row(name, conn), conn=conn))
            rid, name = self.repo(conn)
            self.assertIsNone(ledger.repo_surface(src, rid, self.row(name, conn), conn=conn))
            self.assertEqual(ledger.repos_surfaced_today(conn=conn), 8)
            self.assertEqual(conn.execute("SELECT repo_surface_room(owner_today())").fetchone()[0], 0)

    def test_append_only(self) -> None:
        with _rolled_back() as conn:
            sid, rid, _ = self.surfaced(conn)
            ledger.repo_snapshot([(rid, 100)], conn=conn)
            for sql in ("UPDATE repo_surfacings SET stars = 1 WHERE story_id = %(s)s",
                        "DELETE FROM repo_surfacings WHERE story_id = %(s)s",
                        "UPDATE repo_star_snapshots SET stars = 1 WHERE repo_id = %(r)s",
                        "DELETE FROM repo_star_snapshots WHERE repo_id = %(r)s"):
                with self.subTest(sql=sql), self.assertRaises(psycopg.errors.RaiseException):
                    with conn.transaction():
                        conn.execute(sql, {"s": sid, "r": rid})
            with self.assertRaises(psycopg.errors.ForeignKeyViolation), conn.transaction():
                conn.execute("DELETE FROM stories WHERE id = %s", (sid,))


class MeasuredBaseline(RepoSqlCase):
    """db/050 (owner, 2026-10-14): a repo that first surfaced without a
    MEASURED stars_7d takes its first measured stars_7d (snapshots on D and
    D - 7) as the baseline; doubling and the 14-day floor still apply."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            src = conn.execute("SELECT prosrc FROM pg_proc WHERE proname = 'repo_may_surface'").fetchone()[0]
            if "first_measured" not in src:
                raise unittest.SkipTest("db/050 not pasted")

    @staticmethod
    def snapshots(conn: psycopg.Connection, rid: int, by_day: dict[date, int]) -> None:
        """Snapshots on chosen days: a fixture, the snapshot guard (which
        stamps today) off inside the rolled-back transaction."""
        conn.execute("ALTER TABLE repo_star_snapshots DISABLE TRIGGER repo_star_snapshots_guard")
        try:
            for day, stars in by_day.items():
                conn.execute("INSERT INTO repo_star_snapshots (repo_id, snapped_on, stars) VALUES (%s, %s, %s)",
                             (rid, day, stars))
        finally:
            conn.execute("ALTER TABLE repo_star_snapshots ENABLE TRIGGER repo_star_snapshots_guard")

    def ask(self, conn: psycopg.Connection, rid: int, stars_7d: int | None, day: date) -> bool:
        return conn.execute("SELECT repo_may_surface(%s, %s, %s)", (rid, stars_7d, day)).fetchone()[0]

    def estimate_surfacing(self, conn: psycopg.Connection, rid: int, stars_7d: int | None, source: str | None) -> None:
        sid = conn.execute("INSERT INTO stories (pillars, title, category) VALUES (ARRAY['tech'], 'tl repo', 'repos') "
                           "RETURNING id").fetchone()[0]
        with self.guard_off(conn):
            conn.execute("INSERT INTO repo_surfacings (repo_id, story_id, surfaced_on, stars, stars_7d, stars_7d_source) "
                         "VALUES (%s, %s, %s, 100, %s, %s)", (rid, sid, FAR, stars_7d, source))

    def test_an_estimate_is_never_the_baseline_until_measured(self) -> None:
        with _rolled_back() as conn:
            rid, _ = self.repo(conn)
            self.estimate_surfacing(conn, rid, 500, "trending_weekly")
            self.snapshots(conn, rid, {FAR + timedelta(days=i): 100 + i for i in range(7)})
            self.assertFalse(self.ask(conn, rid, 1_000_000, FAR + timedelta(days=30)))
            # Day 7: first measured = 130 - 100 = 30; day 8's 99 is not the first.
            self.snapshots(conn, rid, {FAR + timedelta(days=7): 130, FAR + timedelta(days=8): 200})
            self.assertTrue(self.ask(conn, rid, 60, FAR + timedelta(days=14)))
            self.assertFalse(self.ask(conn, rid, 59, FAR + timedelta(days=14)))
            self.assertFalse(self.ask(conn, rid, 1000, FAR + timedelta(days=13)))

    def test_a_null_first_stars_7d_takes_the_measured_baseline(self) -> None:
        with _rolled_back() as conn:
            rid, _ = self.repo(conn)
            self.estimate_surfacing(conn, rid, None, None)
            self.snapshots(conn, rid, {FAR - timedelta(days=3): 50, FAR + timedelta(days=4): 90})  # 40
            self.assertTrue(self.ask(conn, rid, 80, FAR + timedelta(days=14)))
            self.assertFalse(self.ask(conn, rid, 79, FAR + timedelta(days=14)))

    def test_a_measured_first_surfacing_keeps_its_own_value(self) -> None:
        with _rolled_back() as conn:
            rid, _ = self.repo(conn)
            self.far_surfacing(conn, rid, FAR, 10)
            # A bigger later measurement does not move the baseline off the first shown (10).
            self.snapshots(conn, rid, {FAR: 100, FAR + timedelta(days=7): 900})
            self.assertTrue(self.ask(conn, rid, 20, FAR + timedelta(days=14)))

    def test_a_measured_zero_counts_as_one(self) -> None:
        with _rolled_back() as conn:
            rid, _ = self.repo(conn)
            self.far_surfacing(conn, rid, FAR, 0)
            self.assertFalse(self.ask(conn, rid, 1, FAR + timedelta(days=14)))
            self.assertTrue(self.ask(conn, rid, 2, FAR + timedelta(days=14)))


class Reactions(RepoSqlCase):
    def test_a_repo_reaction_is_pillar_tech_never_home_or_sports(self) -> None:
        with _rolled_back() as conn:
            sid, _, _ = self.surfaced(conn)
            self.react(conn, sid, "love", pillar="tech")
            for pillar in ("home", "sports"):
                with self.subTest(pillar=pillar), self.assertRaisesRegex(psycopg.errors.RaiseException,
                                                                         "crosses the tech line"):
                    with conn.transaction():
                        self.react(conn, sid, "not_interested", pillar=pillar)
            self.assertEqual(conn.execute("SELECT pillar, reaction FROM v_feed_reaction_latest "
                                          "WHERE story_id = %s", (sid,)).fetchall(), [("tech", "love")])


class WorkerQueries(RepoSqlCase):
    def test_upsert_and_one_snapshot_a_day_on_the_databases_day(self) -> None:
        with _rolled_back() as conn:
            rid, name = self.repo(conn, stars=100)
            again = ledger.repos_upsert([ledger.RepoIn(name.upper(), f"https://tests-live.invalid/{name}", None,
                                                       None, ("x",), None, False, False, None, None, 150)], conn=conn)
            self.assertEqual(again[name.lower()], rid)
            self.assertEqual(conn.execute("SELECT stars, description FROM repos WHERE id = %s", (rid,)).fetchone(),
                             (150, "A test repo."))
            self.assertEqual(ledger.repo_snapshot([(rid, 100)], conn=conn), 1)
            self.assertEqual(ledger.repo_snapshot([(rid, 120)], conn=conn), 0)
            conn.execute("INSERT INTO repo_star_snapshots (repo_id, snapped_on, stars) VALUES (%s, '2000-01-01', 1) "
                         "ON CONFLICT DO NOTHING", (rid,))
            self.assertEqual(conn.execute("SELECT snapped_on, stars FROM repo_star_snapshots WHERE repo_id = %s",
                                          (rid,)).fetchall(), [(ledger.owner_today(conn=conn), 100)])
            # No snapshot 7 days ago: nothing measured (the worker estimates, labelled).
            self.assertEqual(ledger.repo_stars_7d_measured([rid], conn=conn), {})
            self.assertIsInstance(ledger.job_ok_today("tech_repos", conn=conn), bool)

    def test_repos_to_track(self) -> None:
        with _rolled_back() as conn:
            rid, name = self.repo(conn)
            self.assertIn((rid, name), ledger.repos_to_track(30, 100000, conn=conn))
            self.assertNotIn((rid, name), ledger.repos_to_track(30, 100000, exclude=[rid], conn=conn))
            ledger.repo_snapshot([(rid, 100)], conn=conn)  # snapshotted today: not again
            self.assertNotIn((rid, name), ledger.repos_to_track(30, 100000, conn=conn))

    def test_the_repo_why_queue_and_save(self) -> None:
        with _rolled_back() as conn:
            sid, rid, name = self.surfaced(conn)
            # Waiting for its summary: not yet.
            self.assertNotIn(sid, [j.story_id for j in ledger.feed_repo_why_queue(1000, 2, conn=conn)])
            usage = conn.execute("SELECT max(id) FROM ai_usage").fetchone()[0]
            conn.execute("UPDATE stories SET summary = 'Does a thing.', summarized_at = now(), ai_usage_id = %s "
                         "WHERE id = %s", (usage, sid))
            job = [j for j in ledger.feed_repo_why_queue(1000, 2, conn=conn) if j.story_id == sid]
            self.assertEqual(len(job), 1)
            self.assertEqual((job[0].full_name, job[0].topics), (name, ("ai-agents",)))
            self.assertEqual(ledger.feed_save_repo_why({sid: "General interest."}, usage, conn=conn), 1)
            self.assertEqual(ledger.feed_save_repo_why({sid: "Other."}, usage, conn=conn), 0)
            self.assertEqual(conn.execute("SELECT why_it_matters FROM stories WHERE id = %s", (sid,)).fetchone()[0],
                             "General interest.")
            self.assertEqual(conn.execute("SELECT why_it_matters FROM repos WHERE id = %s", (rid,)).fetchone()[0],
                             "General interest.")

    def test_reset_repo_why_clears_story_and_repo_and_requeues(self) -> None:
        with _rolled_back() as conn:
            sid, rid, _ = self.surfaced(conn)
            usage = conn.execute("SELECT max(id) FROM ai_usage").fetchone()[0]
            conn.execute("UPDATE stories SET summary = 'Does a thing.', summarized_at = now(), ai_usage_id = %s "
                         "WHERE id = %s", (usage, sid))
            ledger.feed_save_repo_why({sid: "General interest."}, usage, conn=conn)
            self.assertNotIn(sid, [j.story_id for j in ledger.feed_repo_why_queue(1000, 2, conn=conn)])
            self.assertEqual(ledger.feed_reset_repo_why([sid], conn=conn), 1)
            self.assertEqual(conn.execute("SELECT why_it_matters, why_generated_at FROM stories WHERE id = %s",
                                          (sid,)).fetchone(), (None, None))
            self.assertIsNone(conn.execute("SELECT why_it_matters FROM repos WHERE id = %s", (rid,)).fetchone()[0])
            self.assertIn(sid, [j.story_id for j in ledger.feed_repo_why_queue(1000, 2, conn=conn)])
            # A non-repo story is never touched by it.
            home = self.story(conn, self.source(conn))
            self.assertEqual(ledger.feed_reset_repo_why([home], conn=conn), 0)

    def test_home_why_queue_never_asks_a_repo_story(self) -> None:
        with _rolled_back() as conn:
            sid, _, _ = self.surfaced(conn)
            self.score(conn, sid, 49.0, pillar="tech")
            self.assertIn(sid, [r[0] for r in conn.execute("SELECT id FROM feed_for_you('tech', 8, 0)").fetchall()])
            self.assertNotIn(sid, [j.story_id for j in ledger.feed_why_queue(8, conn=conn)])

    def test_the_why_queue_is_page_one_in_rank_order_across_home_and_tech(self) -> None:
        """Owner, 2026-10-14: position 1 of each pillar, then 2, ... (Home
        before Tech at a tie), never Home's whole list first."""
        with _rolled_back() as conn:
            home = self.story(conn, self.source(conn))
            tech = self.story(conn, self.gh_source(conn))  # a tech story, not category repos
            self.score(conn, home, 49.9)
            self.score(conn, tech, 49.9, pillar="tech")
            pos = {}
            for feed, pillar in ((0, "home"), (1, "tech")):
                for sid, p in conn.execute("SELECT id, pos FROM feed_for_you(%s, 30, 0)", (pillar,)).fetchall():
                    pos[sid] = (p, feed)
            queue = [j.story_id for j in ledger.feed_why_queue(30, conn=conn)]
            self.assertIn(home, queue)
            self.assertIn(tech, queue)
            self.assertEqual([pos[s] for s in queue], sorted(pos[s] for s in queue))
            self.assertTrue(all(pos[s][0] <= 30 for s in queue))

    def test_the_prune_keeps_a_surfaced_story(self) -> None:
        with _rolled_back() as conn:
            sid, _, _ = self.surfaced(conn)
            conn.execute("UPDATE stories SET first_seen_at = now() - interval '100 days', "
                         "last_seen_at = now() - interval '100 days' WHERE id = %s", (sid,))
            ledger.feed_prune(conn=conn)
            self.assertEqual(conn.execute("SELECT count(*) FROM stories WHERE id = %s", (sid,)).fetchone()[0], 1)

    def test_ctx_tech_lists_this_weeks_repos_and_summaries_keep_their_topics(self) -> None:
        with _rolled_back() as conn:
            sid, _, name = self.surfaced(conn, stars_7d=300, source="trending_weekly")
            usage = conn.execute("SELECT max(id) FROM ai_usage").fetchone()[0]
            ledger.feed_save_summaries([ledger.SummaryOut(sid, "Does a thing.", ("X",), ("model_topic",),
                                                          category="community")], usage, conn=conn)
            self.assertEqual(conn.execute("SELECT category, topics FROM stories WHERE id = %s", (sid,)).fetchone(),
                             ("repos", ["ai_agents"]))
            week = conn.execute("SELECT ctx_tech() -> 'repos_this_week'").fetchone()[0]
            mine = [w for w in week if w["repo"] == name]
            self.assertEqual(len(mine), 1)
            self.assertEqual((mine[0]["stars_7d"], mine[0]["stars_7d_source"]), (300, "trending_weekly"))


if __name__ == "__main__":
    unittest.main()
