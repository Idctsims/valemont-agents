"""Tech Hub SQL against the real database (db/045; Chat 4 Phase 1):

* the db/045 objects: columns, CHECKs, the push table's access, the
  providers, the seeds, readonly reads and executes, anon nothing.
* the free-credits cap IN SQL: 3 real pushes a Chicago day, never the same
  story twice, test pushes not counted; and ledger.tech_credit_push never
  sends once the database says no.
* no cross-in: feed_for_you never lists a tech story on Home (any cap) or
  Sports, nor a Home or Sports story on Tech; an unscored official tech
  story is listed; the pillar guard refuses a cross-line score or reaction.
* the worker's queries: the tech summary queue (official first), the Home
  queue without tech, candidates carrying their world, sources carrying
  official/config, the prune keeping a pushed story.

Every test runs inside ONE transaction that is always rolled back. Skips
until db/045 is pasted.
"""

from __future__ import annotations

import unittest
import uuid

import psycopg
from psycopg.types.json import Jsonb

from core import ledger
from tests_live.test_feed_rank_sql import RankSqlCase, _act_as, _rolled_back


class TechSqlCase(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.tech_credit_pushes')").fetchone()[0] is None:
                raise unittest.SkipTest("db/045 not pasted")

    def tech_source(self, conn: psycopg.Connection, *, official: bool = True) -> ledger.FeedSource:
        slug = f"tl-{uuid.uuid4().hex[:10]}"
        conn.execute(
            """INSERT INTO sources (slug, name, pillars, kind, url, active, official)
               VALUES (%s, %s, ARRAY['tech'], 'rss', 'https://tests-live.invalid/tech', false, %s)""",
            (slug, f"TL {slug}", official))
        return ledger.feed_source(slug, conn=conn)

    def tech_story(self, conn: psycopg.Connection, *, official: bool = True, score: float | None = None,
                   category: str | None = None, hours_ago: float = 0, done: bool = True) -> int:
        sid = self.story(conn, self.tech_source(conn, official=official), hours_ago=hours_ago, done=done)
        if category:
            conn.execute("UPDATE stories SET category = %s WHERE id = %s", (category, sid))
        if score is not None:
            self.score(conn, sid, score, pillar="tech")
        return sid


class Objects(TechSqlCase):
    def test_columns_checks_and_access(self) -> None:
        with _rolled_back() as conn:
            cols = {r[0] for r in conn.execute(
                "SELECT table_name || '.' || column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name IN ('official', 'config', 'claimable_offer', "
                "'backfill', 'published_precision')").fetchall()}
            self.assertTrue({"sources.official", "sources.config", "stories.claimable_offer",
                             "content_items.backfill", "content_items.published_precision"} <= cols)
            rls, anon, auth_write, ro = conn.execute(
                "SELECT relrowsecurity, has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                "has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE'), "
                "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                "FROM pg_class WHERE oid = 'public.tech_credit_pushes'::regclass").fetchone()
            self.assertEqual((rls, anon, auth_write, ro), (True, False, False, True))
            for fn in ("public.ctx_tech()", "public.ctx_tech_brief()", "public.tech_credit_push_room()",
                       "public.feed_for_you(text, integer, integer, text, text, integer)"):
                with self.subTest(fn=fn):
                    self.assertEqual(conn.execute(
                        "SELECT has_function_privilege('valemont_readonly', %s, 'EXECUTE'), "
                        "has_function_privilege('anon', %s, 'EXECUTE')", (fn, fn)).fetchone(), (True, False))
            providers = conn.execute("SELECT key, brief_section, enabled FROM context_providers "
                                     "WHERE key IN ('tech', 'tech_brief') ORDER BY key").fetchall()
            self.assertEqual(providers, [("tech", False, True), ("tech_brief", True, True)])
            seeds = conn.execute("SELECT seed_interests #> '{feed,tech}' FROM app_settings").fetchone()[0]
            self.assertTrue(seeds["love"] and seeds["not_interested"])
            self.assertIn("crypto price chatter", seeds["not_interested"])

    def test_the_checks_refuse_a_story_in_two_worlds(self) -> None:
        with _rolled_back() as conn:
            for sql in ("INSERT INTO stories (pillars, title) VALUES (ARRAY['tech','home'], 'x')",
                        "INSERT INTO stories (pillars, title, category) VALUES (ARRAY['home'], 'x', 'tooling')",
                        "INSERT INTO stories (pillars, title, category) VALUES (ARRAY['tech'], 'x', 'deals')",
                        "INSERT INTO sources (slug, name, pillars, kind, url) VALUES ('tl-x', 'x', ARRAY['tech'], "
                        "'page', 'https://tests-live.invalid/x')"):
                with self.subTest(sql=sql), self.assertRaises(psycopg.errors.CheckViolation), conn.transaction():
                    conn.execute(sql)


class PushCap(TechSqlCase):
    def push_stories(self, conn: psycopg.Connection, n: int) -> list[int]:
        return [self.tech_story(conn, category="pricing_credits") for _ in range(n)]

    def test_three_a_day_never_twice_tests_not_counted(self) -> None:
        with _rolled_back() as conn:
            room = ledger.tech_credit_push_room(conn=conn)
            stories = self.push_stories(conn, room + 1)
            sent: list[int] = []
            for sid in stories[:room]:
                self.assertIsNotNone(ledger.tech_credit_push(sid, lambda s=sid: sent.append(s), conn=conn))
            self.assertEqual(ledger.tech_credit_push_room(conn=conn), 0)
            # The 4th of the day: the database says no, and nothing is sent.
            self.assertIsNone(ledger.tech_credit_push(stories[room], lambda: sent.append(-1), conn=conn))
            self.assertNotIn(-1, sent)
            # A forced test push is recorded, not counted, and may repeat.
            ledger.tech_credit_test_push(stories[room], lambda: None, conn=conn)
            ledger.tech_credit_test_push(stories[room], lambda: None, conn=conn)
            self.assertEqual(ledger.tech_credit_push_room(conn=conn), 0)
            # The trigger holds even for a writer that skips the check.
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                conn.execute("INSERT INTO tech_credit_pushes (story_id) VALUES (%s)", (stories[room],))

    def test_never_the_same_story_twice(self) -> None:
        with _rolled_back() as conn:
            if ledger.tech_credit_push_room(conn=conn) == 0:
                self.skipTest("today's 3 real pushes are already spent; the cap test covers the repeat")
            sid = self.push_stories(conn, 1)[0]
            self.assertIsNotNone(ledger.tech_credit_push(sid, lambda: None, conn=conn))
            self.assertIsNone(ledger.tech_credit_push(sid, lambda: None, conn=conn))
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                conn.execute("INSERT INTO tech_credit_pushes (story_id) VALUES (%s)", (sid,))

    def test_append_only_and_only_tech_stories(self) -> None:
        with _rolled_back() as conn:
            sid = self.push_stories(conn, 1)[0]
            ledger.tech_credit_test_push(sid, lambda: None, conn=conn)
            for sql in ("UPDATE tech_credit_pushes SET is_test = false WHERE story_id = %s",
                        "DELETE FROM tech_credit_pushes WHERE story_id = %s"):
                with self.subTest(sql=sql), self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                    conn.execute(sql, (sid,))
            home = self.story(conn, self.source(conn))
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                conn.execute("INSERT INTO tech_credit_pushes (story_id, is_test) VALUES (%s, true)", (home,))


class NoCrossIn(TechSqlCase):
    def test_feed_for_you_keeps_every_world_to_itself(self) -> None:
        with _rolled_back() as conn:
            t_scored = self.tech_story(conn, score=45)
            t_unscored = self.tech_story(conn, done=False)
            t_old = self.tech_story(conn, score=45, hours_ago=100)
            conn.execute("UPDATE stories SET first_seen_at = now() - interval '100 hours', "
                         "last_seen_at = now() - interval '100 hours' WHERE id = %s", (t_old,))
            home = self.story(conn, self.source(conn))
            self.score(conn, home, 45)
            sport = self.story(conn, self.source(conn, ("sports",)))
            self.score(conn, sport, 45, pillar="sports")
            conn.execute("""INSERT INTO story_scores (story_id, pillar, score, components, follow_tier, cross_pillar)
                            VALUES (%s, 'home', 46, '{}', 1, true)""", (sport,))

            def ids(*args) -> set[int]:
                return {r[0] for r in conn.execute("SELECT id FROM feed_for_you(%s, 100000, 0, NULL, NULL, %s)",
                                                   args).fetchall()}

            tech_ids = ids("tech", None)
            self.assertTrue({t_scored, t_unscored, t_old} <= tech_ids)
            self.assertFalse({home, sport} & tech_ids)
            for cap in (None, 2, 0):
                with self.subTest(cap=cap):
                    self.assertFalse({t_scored, t_unscored, t_old} & ids("home", cap))
            self.assertFalse({t_scored, t_unscored, t_old} & ids("sports", None))
            self.assertIn(sport, ids("home", None))  # Home's own cross-in rule unchanged
            official, score = conn.execute("SELECT official, score FROM feed_for_you('tech', 100000, 0) "
                                           "WHERE id = %s", (t_unscored,)).fetchone()
            self.assertEqual((official, score), (True, 0))
            # Nothing in the real data crosses either.
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM feed_for_you('tech', 100000, 0) WHERE NOT ('tech' = ANY (pillars))"
            ).fetchone()[0], 0)
            for pillar in ("home", "sports"):
                self.assertEqual(conn.execute(
                    "SELECT count(*) FROM feed_for_you(%s, 100000, 0) WHERE 'tech' = ANY (pillars)",
                    (pillar,)).fetchone()[0], 0, pillar)

    def test_the_guard_refuses_cross_line_scores_and_reactions(self) -> None:
        with _rolled_back() as conn:
            t = self.tech_story(conn)
            home = self.story(conn, self.source(conn))
            from feed import rank
            bad = [rank.Score(t, "home", 1.0, {}, None, False, False)]
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                ledger.feed_insert_scores(bad, {}, conn=conn)
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                ledger.feed_insert_scores([rank.Score(home, "tech", 1.0, {}, 1, False, True)], {}, conn=conn)
            with conn.cursor() as cur:
                _act_as(cur, "authenticated", self.owner)
                cur.execute("INSERT INTO feed_reactions (story_id, pillar, reaction) VALUES (%s, 'tech', 'love')", (t,))
                cur.execute("SAVEPOINT s")
                with self.assertRaises(psycopg.errors.RaiseException):
                    cur.execute("INSERT INTO feed_reactions (story_id, pillar, reaction) VALUES (%s, 'home', 'love')",
                                (t,))
                cur.execute("ROLLBACK TO SAVEPOINT s")
                cur.execute("SELECT pillar, reaction FROM feed_reactions WHERE story_id = %s", (t,))
                self.assertEqual(cur.fetchall(), [("tech", "love")])


class OfficialDecay(TechSqlCase):
    """db/046: an official tech story decays at rank.TECH_OFFICIAL_HALF_LIFE_H,
    and only a push-eligible item may push."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            body = conn.execute("SELECT pg_get_functiondef('public.feed_for_you(text, integer, integer, text, "
                                "text, integer)'::regprocedure)").fetchone()[0]
            if "tech_official_half_life_h" not in body:
                raise unittest.SkipTest("db/046 not pasted")

    def test_the_sql_rank_is_the_twins(self) -> None:
        from feed import rank
        with _rolled_back() as conn:
            off = self.tech_story(conn, official=True, score=5, hours_ago=48)
            com = self.tech_story(conn, official=False, score=5, hours_ago=20)
            rows = dict(conn.execute("SELECT id, rank FROM feed_for_you('tech', 100000, 0) WHERE id = ANY(%s)",
                                     ([off, com],)).fetchall())
            self.assertAlmostEqual(rows[off], rank.rank_of(5, 48, 0, rank.half_life("tech", True)), places=2)
            self.assertAlmostEqual(rows[com], rank.rank_of(5, 20, 0, rank.half_life("tech", False)), places=2)
            self.assertGreater(rows[off], rows[com])

    def test_a_backfill_or_old_item_never_pushes(self) -> None:
        # Owner, 2026-10-14: eligible only if not a first-fetch backfill AND
        # published within 24 h of first seen; the database refuses the rest.
        from datetime import datetime, timedelta, timezone
        with _rolled_back() as conn:
            src = self.tech_source(conn)
            ids = {}
            for name, backfill, age in (("backfill", True, 1), ("old", False, 30), ("fresh", False, 1)):
                item = ledger.FeedItemIn(f"tl:{uuid.uuid4().hex}", f"https://tests-live.invalid/{uuid.uuid4().hex}",
                                         None, f"TL push {name}", None,
                                         datetime.now(timezone.utc) - timedelta(hours=age), False, "time", backfill)
                v = [1.0] + [0.0] * 383
                ids[name] = ledger.feed_new_story(src, item, v, v, filtered_reason=None, seed=None, conn=conn)[0]
                conn.execute("UPDATE stories SET category = 'pricing_credits' WHERE id = %s", (ids[name],))
            got = {n: conn.execute("SELECT tech_push_eligible(%s)", (sid,)).fetchone()[0] for n, sid in ids.items()}
            self.assertEqual(got, {"backfill": False, "old": False, "fresh": True})
            sent: list[int] = []
            for n in ("backfill", "old"):
                self.assertIsNone(ledger.tech_credit_push(ids[n], lambda: sent.append(1), conn=conn))
                with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                    conn.execute("INSERT INTO tech_credit_pushes (story_id) VALUES (%s)", (ids[n],))
            self.assertEqual(sent, [])
            # The Vercel backfill item that pushed before the rule (story 8359).
            if conn.execute("SELECT EXISTS (SELECT 1 FROM stories WHERE id = 8359)").fetchone()[0]:
                self.assertFalse(conn.execute("SELECT tech_push_eligible(8359)").fetchone()[0])


class CapsAndRetirement(TechSqlCase):
    """Owner, 2026-10-14: separate daily counts per pillar group, and the
    48-hour retirement, in the real SQL."""

    def stamp(self, conn: psycopg.Connection, sid: int) -> None:
        conn.execute("UPDATE stories SET summarized_at = now(), summary_attempts = 1 WHERE id = %s", (sid,))

    def test_each_group_counts_only_its_own(self) -> None:
        with _rolled_back() as conn:
            home0 = ledger.feed_summarized_today(conn=conn)
            tech0 = ledger.feed_tech_community_summarized_today(conn=conn)
            self.stamp(conn, self.tech_story(conn, official=True, done=False))
            self.assertEqual((ledger.feed_summarized_today(conn=conn),
                              ledger.feed_tech_community_summarized_today(conn=conn)), (home0, tech0))
            self.stamp(conn, self.tech_story(conn, official=False, done=False))
            self.assertEqual((ledger.feed_summarized_today(conn=conn),
                              ledger.feed_tech_community_summarized_today(conn=conn)), (home0, tech0 + 1))
            self.stamp(conn, self.story(conn, self.source(conn), done=False))
            self.assertEqual((ledger.feed_summarized_today(conn=conn),
                              ledger.feed_tech_community_summarized_today(conn=conn)), (home0 + 1, tech0 + 1))

    def test_a_story_waiting_over_48_hours_retires_headline_only(self) -> None:
        with _rolled_back() as conn:
            old = self.story(conn, self.source(conn), done=False)
            fresh = self.story(conn, self.source(conn), done=False)
            old_tech = self.tech_story(conn, done=False)
            conn.execute("UPDATE stories SET first_seen_at = now() - interval '50 hours', "
                         "last_seen_at = now() - interval '50 hours' WHERE id = ANY(%s)", ([old, old_tech],))
            self.assertGreaterEqual(ledger.feed_retire_stale(48, 2, tech=False, conn=conn), 1)
            rows = dict(conn.execute("SELECT id, summary_attempts FROM stories WHERE id = ANY(%s)",
                                     ([old, fresh, old_tech],)).fetchall())
            self.assertEqual((rows[old], rows[fresh], rows[old_tech]), (2, 0, 0))  # tech is its own group
            pool = {j.story_id for j in ledger.feed_summary_queue(100000, 2, conn=conn)}
            self.assertNotIn(old, pool)
            self.assertIn(fresh, pool)
            ledger.feed_retire_stale(48, 2, tech=True, conn=conn)
            self.assertEqual(conn.execute("SELECT summary_attempts, summarized_at FROM stories WHERE id = %s",
                                          (old_tech,)).fetchone(), (2, None))

    def test_the_pools_carry_what_the_rank_order_needs(self) -> None:
        with _rolled_back() as conn:
            sid = self.story(conn, self.source(conn, ("home",)), done=False)
            job = next(j for j in ledger.feed_summary_queue(100000, 2, conn=conn) if j.story_id == sid)
            self.assertEqual((job.pillars, len(job.vec), job.published_at is not None), (("home",), 384, True))
            tid = self.tech_story(conn, done=False)
            tj = next(j for j in ledger.feed_tech_summary_queue(100000, 2, conn=conn) if j.story_id == tid)
            self.assertEqual((len(tj.vec), bool(tj.source_slug)), (384, True))


class HideLateItems(TechSqlCase):
    """db/047: a story whose every item was stored > 72 h after publication
    is hidden (filtered, never deleted); reacted, mixed and fresh ones stay."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regprocedure('public.feed_hide_late_stories()')").fetchone()[0] is None:
                raise unittest.SkipTest("db/047 not pasted")

    def test_late_hidden_reacted_kept_nothing_deleted(self) -> None:
        from datetime import datetime, timedelta, timezone
        with _rolled_back() as conn:
            src = self.tech_source(conn)
            ids = []
            for name, hours in (("late", 100), ("fresh", 1), ("late-reacted", 100)):
                item = ledger.FeedItemIn(f"tl:{uuid.uuid4().hex}", f"https://tests-live.invalid/{uuid.uuid4().hex}",
                                         None, f"TL {name}", None, datetime.now(timezone.utc) - timedelta(hours=hours),
                                         False)
                v = [1.0] + [0.0] * 383
                ids.append(ledger.feed_new_story(src, item, v, v, filtered_reason=None, seed=None, conn=conn)[0])
            self.react(conn, ids[2], "open")
            conn.execute("SELECT feed_hide_late_stories()")
            reasons = dict(conn.execute("SELECT id, filtered_reason FROM stories WHERE id = ANY(%s)", (ids,)).fetchall())
            self.assertEqual([reasons[i] for i in ids], ["stale:first_seen_after_72h", None, None])
            self.assertEqual(conn.execute("SELECT count(*) FROM content_items WHERE story_id = ANY(%s)",
                                          (ids,)).fetchone()[0], 3)
            self.assertNotIn(ids[0], {r[0] for r in conn.execute("SELECT id FROM feed_for_you('tech', 100000, 0)")})


class WorkerQueries(TechSqlCase):
    def test_the_tech_queue_puts_official_first_and_the_home_queue_leaves_tech_out(self) -> None:
        with _rolled_back() as conn:
            community = self.tech_story(conn, official=False, done=False)
            official = self.tech_story(conn, official=True, done=False, hours_ago=5)
            home = self.story(conn, self.source(conn), done=False)
            q = [j.story_id for j in ledger.feed_tech_summary_queue(100000, 2, conn=conn)]
            self.assertLess(q.index(official), q.index(community))
            self.assertNotIn(home, q)
            home_q = [j.story_id for j in ledger.feed_summary_queue(100000, 2, conn=conn)]
            self.assertIn(home, home_q)
            self.assertFalse({official, community} & set(home_q))

    def test_candidates_carry_their_world_and_sources_their_config(self) -> None:
        with _rolled_back() as conn:
            t = self.tech_story(conn)
            h = self.story(conn, self.source(conn))
            worlds = {c.story_id: c.tech for c in ledger.feed_candidates(48, conn=conn)}
            self.assertEqual((worlds[t], worlds[h]), (True, False))
            slug = f"tl-{uuid.uuid4().hex[:10]}"
            conn.execute("""INSERT INTO sources (slug, name, pillars, kind, url, active, official, config)
                            VALUES (%s, 'TL page', ARRAY['tech'], 'page', 'https://tests-live.invalid/p', false,
                                    true, %s)""", (slug, Jsonb({"mode": "links", "href": "^/news/"})))
            src = ledger.feed_source(slug, conn=conn)
            self.assertEqual((src.official, src.config), (True, {"mode": "links", "href": "^/news/"}))

    def test_an_item_keeps_its_precision_and_backfill(self) -> None:
        with _rolled_back() as conn:
            from datetime import datetime, timezone
            src = self.tech_source(conn)
            item = ledger.FeedItemIn(f"tl:{uuid.uuid4().hex}", f"https://tests-live.invalid/{uuid.uuid4().hex}#h_1",
                                     None, "TL page item", None, datetime.now(timezone.utc), False, "date", True)
            v = [1.0] + [0.0] * 383
            sid = ledger.feed_new_story(src, item, v, v, filtered_reason=None, seed=None, conn=conn)[0]
            self.assertEqual(conn.execute(
                "SELECT published_precision, backfill, url FROM content_items WHERE story_id = %s", (sid,)
            ).fetchone(), ("date", True, item.url))
            self.assertEqual(conn.execute("SELECT pillars FROM stories WHERE id = %s", (sid,)).fetchone()[0], ["tech"])

    def test_the_prune_keeps_a_pushed_story(self) -> None:
        with _rolled_back() as conn:
            sid = self.tech_story(conn, category="pricing_credits")
            ledger.tech_credit_test_push(sid, lambda: None, conn=conn)
            conn.execute("UPDATE stories SET first_seen_at = now() - interval '100 days', "
                         "last_seen_at = now() - interval '100 days' WHERE id = %s", (sid,))
            ledger.feed_prune(conn=conn)
            self.assertEqual(conn.execute("SELECT count(*) FROM stories WHERE id = %s", (sid,)).fetchone()[0], 1)


class Context(TechSqlCase):
    def test_ctx_tech_and_the_brief_slice(self) -> None:
        with _rolled_back() as conn:
            t = self.tech_story(conn, score=40, category="tooling")
            title = conn.execute("SELECT title FROM stories WHERE id = %s", (t,)).fetchone()[0]
            ctx = conn.execute("SELECT ctx_tech()").fetchone()[0]
            self.assertIn(title, [o["title"] for o in ctx["official_7d"]])
            brief = conn.execute("SELECT ctx_tech_brief()").fetchone()[0]
            self.assertIsNotNone(brief)
            self.assertIn(title, [o["title"] for o in brief["official_24h"]])
            # First seen two days ago: it has not landed in the last 24 h.
            conn.execute("""UPDATE stories SET first_seen_at = now() - interval '2 days',
                                   last_seen_at = now() - interval '2 days' WHERE id = %s""", (t,))
            brief = conn.execute("SELECT ctx_tech_brief()").fetchone()[0]
            self.assertNotIn(title, [o["title"] for o in (brief or {}).get("official_24h", [])])


if __name__ == "__main__":
    unittest.main()
