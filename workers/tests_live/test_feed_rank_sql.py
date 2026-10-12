"""The feed ranking's SQL against the real database (db/039; Chat 3 Phase 2):

* feed_reactions: the owner inserts as the owner only; UPDATE and DELETE are
  refused for every role; anon has nothing; v_feed_reaction_latest
  (latest non-open wins, undo restores, opens never override).
* the worker's ledger queries: the 7-day mean vector (pgvector avg in the
  database), reaction signals, stories to score (unscored only, done
  summarizing, never a test story), profiles and scores round-trip.
* feed_for_you: the SQL order equals feed/rank.order_for_you on the same
  candidates (the twin the fast suite tests); reactions remove stories.
* ctx_feed: shape, top 3 of For you, test stories invisible.
* the why queue and saves; ai_calls_today.
* the prune never deletes a reacted story; it deletes unreacted 90-day ones
  and superseded scores.

Every test runs inside ONE transaction that is always rolled back. Its
stories are REAL (is_test = false, throwaway real sources) so the real-only
queries see them; scores of 40+ keep real scored stories from mattering.

Skips until db/039 is pasted.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import psycopg

from core import ledger
from core.paths import load_env
from feed import rank

load_env()


def unit(i: int) -> list[float]:
    v = [0.0] * 384
    v[i] = 1.0
    return v


@contextmanager
def _rolled_back() -> Iterator[psycopg.Connection]:
    """One outer transaction, always rolled back (see test_feed_ledger: the
    SELECT 1 makes every ledger conn.transaction() a savepoint)."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("SELECT 1")
        try:
            yield conn
        finally:
            conn.rollback()


def _act_as(cur: psycopg.Cursor, role: str, sub: uuid.UUID | str | None = None) -> None:
    claims = {"role": role} | ({"sub": str(sub)} if sub else {})
    cur.execute("SELECT set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(sub) if sub else "",))
    cur.execute(f"SET LOCAL ROLE {role}")


class RankSqlCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            with _rolled_back() as conn:
                if conn.execute("SELECT to_regclass('public.feed_reactions')").fetchone()[0] is None:
                    raise unittest.SkipTest("db/039 not pasted")
                cls.owner = conn.execute("SELECT owner_id FROM app_settings").fetchone()[0]
        except unittest.SkipTest:
            raise
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")

    # -- fixtures -------------------------------------------------------------

    def source(self, conn: psycopg.Connection, pillars=("home",), *, is_test: bool = False,
               sport: str | None = None) -> ledger.FeedSource:
        if is_test:
            return ledger.feed_source("_e2e", conn=conn)
        slug = f"tl-{uuid.uuid4().hex[:10]}"
        conn.execute(
            """INSERT INTO sources (slug, name, pillars, sport, kind, url, active)
               VALUES (%s, %s, %s, %s, 'rss', 'https://tests-live.invalid/feed', false)""",
            (slug, f"TL {slug}", list(pillars), sport))
        return ledger.feed_source(slug, conn=conn)

    def story(self, conn: psycopg.Connection, src: ledger.FeedSource, *, title: str = "tests-live story",
              vec: list[float] | None = None, hours_ago: float = 0, done: bool = True) -> int:
        item = ledger.FeedItemIn(
            external_id=f"tl:{uuid.uuid4().hex}", url=f"https://tests-live.invalid/{uuid.uuid4().hex}",
            google_url=None, title=title, teaser=None,
            published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago), decode_failed=False)
        v = vec or unit(0)
        sid = ledger.feed_new_story(src, item, v, v, filtered_reason=None, seed=None, conn=conn)[0]
        if done:  # out of summary attempts: entities final, eligible to score
            conn.execute("UPDATE stories SET summary_attempts = 2 WHERE id = %s", (sid,))
        return sid

    def score(self, conn: psycopg.Connection, sid: int, value: float, *, pillar: str = "home",
              floor: bool = False, cross: bool = False) -> None:
        conn.execute(
            """INSERT INTO story_scores (story_id, pillar, score, components, follow_tier, floor, cross_pillar)
               VALUES (%s, %s, %s, '{}', %s, %s, %s)""",
            (sid, pillar, value, 1 if floor or cross else None, floor, cross))

    def react(self, conn: psycopg.Connection, sid: int, reaction: str, pillar: str = "home") -> None:
        conn.execute("INSERT INTO feed_reactions (owner_id, story_id, pillar, reaction) VALUES (%s, %s, %s, %s)",
                     (self.owner, sid, pillar, reaction))


class Reactions(RankSqlCase):
    def test_the_owner_inserts_for_the_owner_only(self) -> None:
        with _rolled_back() as conn, conn.cursor() as cur:
            sid = self.story(conn, self.source(conn))
            _act_as(cur, "authenticated", self.owner)
            cur.execute("INSERT INTO feed_reactions (story_id, pillar, reaction) VALUES (%s, 'home', 'love')", (sid,))
            cur.execute("SELECT owner_id, reaction FROM feed_reactions WHERE story_id = %s", (sid,))
            self.assertEqual(cur.fetchall(), [(self.owner, "love")])
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SAVEPOINT s")
                cur.execute("INSERT INTO feed_reactions (owner_id, story_id, pillar, reaction) "
                            "VALUES (%s, %s, 'home', 'love')", (str(uuid.uuid4()), sid))
            cur.execute("ROLLBACK TO SAVEPOINT s")
            for sql in ("UPDATE feed_reactions SET reaction = 'neutral' WHERE story_id = %s",
                        "DELETE FROM feed_reactions WHERE story_id = %s"):
                cur.execute("SAVEPOINT s")
                with self.subTest(sql=sql), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(sql, (sid,))
                cur.execute("ROLLBACK TO SAVEPOINT s")

    def test_no_role_may_update_or_delete_and_anon_sees_nothing(self) -> None:
        with _rolled_back() as conn, conn.cursor() as cur:
            sid = self.story(conn, self.source(conn))
            self.react(conn, sid, "love")
            for sql in ("UPDATE feed_reactions SET reaction = 'neutral' WHERE story_id = %s",
                        "DELETE FROM feed_reactions WHERE story_id = %s"):
                cur.execute("SAVEPOINT s")
                with self.subTest(sql=sql), self.assertRaises(psycopg.errors.RaiseException):
                    cur.execute(sql, (sid,))
                cur.execute("ROLLBACK TO SAVEPOINT s")
            _act_as(cur, "anon")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT count(*) FROM feed_reactions")

    def test_latest_non_open_wins_undo_restores_open_never_overrides(self) -> None:
        with _rolled_back() as conn:
            sid = self.story(conn, self.source(conn))

            def latest():
                return conn.execute("SELECT reaction, opened FROM v_feed_reaction_latest "
                                    "WHERE story_id = %s AND pillar = 'home'", (sid,)).fetchone()

            self.react(conn, sid, "open")
            self.assertEqual(latest(), (None, True))
            self.react(conn, sid, "not_interested")
            self.react(conn, sid, "open")
            self.assertEqual(latest(), ("not_interested", True))
            self.react(conn, sid, "undo")
            self.assertEqual(latest(), ("undo", True))
            self.react(conn, sid, "love", pillar="sports")
            self.assertEqual(latest(), ("undo", True))  # the other pillar is separate


class WorkerQueries(RankSqlCase):
    def test_mean_vector_is_averaged_in_the_database(self) -> None:
        with _rolled_back() as conn:
            _, before = ledger.feed_mean_vector("home", 7, conn=conn)
            src = self.source(conn)
            self.story(conn, src, vec=unit(3))
            self.story(conn, src, vec=unit(4))
            vec, after = ledger.feed_mean_vector("home", 7, conn=conn)
            self.assertEqual(after, before + 2)
            self.assertEqual(len(vec), 384)
            self.story(conn, self.source(conn, is_test=True), vec=unit(5))  # a test story never counts
            self.assertEqual(ledger.feed_mean_vector("home", 7, conn=conn)[1], after)

    def test_signals_are_real_reacted_stories_with_their_latest_reaction(self) -> None:
        with _rolled_back() as conn:
            real = self.story(conn, self.source(conn), title="Real reacted", vec=unit(7))
            fake = self.story(conn, self.source(conn, is_test=True), title="Test reacted")
            self.react(conn, real, "love")
            self.react(conn, real, "open")
            self.react(conn, fake, "not_interested")
            sigs = {s.story.id: s for s in ledger.feed_reaction_signals("home", conn=conn)}
            self.assertNotIn(fake, sigs)
            s = sigs[real]
            self.assertEqual((s.reaction, s.opened, s.story.title), ("love", True, "Real reacted"))
            self.assertAlmostEqual(s.story.vec[7], 1.0, places=3)

    def test_stories_to_score(self) -> None:
        with _rolled_back() as conn:
            src = self.source(conn)
            done = self.story(conn, src)
            pending = self.story(conn, src, done=False)
            old = self.story(conn, src)
            conn.execute("UPDATE stories SET first_seen_at = now() - interval '73 hours', "
                         "last_seen_at = now() - interval '73 hours' WHERE id = %s", (old,))
            test = self.story(conn, self.source(conn, is_test=True))

            def ids(unscored: bool) -> set[int]:
                return {s.id for s in ledger.feed_stories_to_score(72, 2, unscored=unscored, conn=conn)}

            self.assertIn(done, ids(True))
            self.assertFalse({pending, old, test} & ids(False))
            self.score(conn, done, 1.0)
            self.assertNotIn(done, ids(True))
            self.assertIn(done, ids(False))

    def test_profiles_and_scores_round_trip(self) -> None:
        with _rolled_back() as conn:
            sid = self.story(conn, self.source(conn))
            prof = rank.Profile("home", tuple(unit(1)), 5, tuple(unit(2)), None, {"m_and_a": 0.1},
                                {"skift": -0.05}, {}, 3, 0, 1, 2, 0.8, 0.0)
            pid = ledger.feed_insert_profile(prof, conn=conn)
            latest = ledger.feed_latest_profiles(conn=conn)["home"]
            self.assertEqual((latest.id, latest.love_count, latest.ni, latest.topic_weights),
                             (pid, 3, None, {"m_and_a": 0.1}))
            self.assertAlmostEqual(latest.love[2], 1.0, places=3)
            self.assertTrue(ledger.feed_profile_built_today(conn=conn))
            s = rank.Score(sid, "home", 1.25, {"affinity": 0.1}, 1, True, False)
            self.assertEqual(ledger.feed_insert_scores([s], {"home": pid}, conn=conn), 1)
            row = conn.execute("SELECT score, floor, profile_id, components->>'affinity' FROM v_story_scores_latest "
                               "WHERE story_id = %s", (sid,)).fetchone()
            self.assertEqual(row, (1.25, True, pid, "0.1"))


class ForYou(RankSqlCase):
    def test_sql_order_equals_the_python_twin(self) -> None:
        with _rolled_back() as conn:
            home, sports = self.source(conn), self.source(conn, ("sports",), sport="cfb")
            for i in range(40):
                self.score(conn, self.story(conn, home, hours_ago=i * 0.7), 45 - i * 0.05)
            for i in range(6):
                self.score(conn, self.story(conn, home, hours_ago=i), 41 - i * 0.1, floor=True)
            for i in range(8):
                self.score(conn, self.story(conn, sports, hours_ago=i), 46 - i * 0.1, cross=True)
            rows = conn.execute("""
                SELECT f.id, f.pos, sc.score, extract(epoch FROM now() - f.lead_published_at) / 3600.0,
                       sc.floor, sc.cross_pillar, coalesce(f.floored, false)
                  FROM feed_for_you('home', 100000, 0) f
                  JOIN v_story_scores_latest sc ON sc.story_id = f.id AND sc.pillar = 'home'
                 ORDER BY f.pos""").fetchall()
            cands = [rank.Candidate(r[0], float(r[2]), float(r[3]), r[4], r[5]) for r in rows]
            twin = rank.order_for_you(cands)
            self.assertEqual([(p.id, p.pos, p.floored) for p in twin], [(r[0], r[1], r[6]) for r in rows])
            self.assertEqual(sum(1 for r in rows[:rank.PAGE_SIZE] if r[5]), rank.CROSS_CAP)
            self.assertEqual(sum(1 for r in rows if r[6]), rank.FLOOR_MAX)

    def test_reactions_remove_and_mark(self) -> None:
        with _rolled_back() as conn:
            src = self.source(conn)
            gone, kept, loved = (self.story(conn, src) for _ in range(3))
            for sid in (gone, kept, loved):
                self.score(conn, sid, 45)
            self.react(conn, gone, "not_interested")
            self.react(conn, loved, "love")
            got = {r.id: r for r in ledger.feed_for_you("home", 100000, conn=conn)}
            self.assertNotIn(gone, got)
            self.assertIn(kept, got)
            reaction = conn.execute("SELECT reaction FROM feed_for_you('home', 100000, 0) WHERE id = %s",
                                    (loved,)).fetchone()[0]
            self.assertEqual(reaction, "love")


class CtxFeed(RankSqlCase):
    def test_shape_top_three_and_no_test_story(self) -> None:
        with _rolled_back() as conn:
            src = self.source(conn)
            ids = [self.story(conn, src, title=f"tests-live top {i}") for i in range(4)]
            for i, sid in enumerate(ids):
                self.score(conn, sid, 49 - i)
            fake = self.story(conn, self.source(conn, is_test=True), title="tests-live TEST story")
            self.score(conn, fake, 49.9)
            ctx = conn.execute("SELECT ctx_feed()").fetchone()[0]
            self.assertEqual([s["title"] for s in ctx["top_stories"]],
                             ["tests-live top 0", "tests-live top 1", "tests-live top 2"])
            self.assertNotIn("TEST story", json.dumps(ctx))
            self.assertIn("feed", conn.execute("SELECT context_snapshot()").fetchone()[0])


class WhyQueue(RankSqlCase):
    def test_queue_saves_and_the_call_count(self) -> None:
        with _rolled_back() as conn:
            src = self.source(conn)
            a, b = self.story(conn, src, title="why a"), self.story(conn, src, title="why b")
            self.score(conn, a, 49)
            self.score(conn, b, 48.5)
            queued = [j.story_id for j in ledger.feed_why_queue(8, conn=conn)]
            self.assertEqual(queued[:2], [a, b])
            before = ledger.ai_calls_today("feed_why", conn=conn)
            uid = conn.execute("""INSERT INTO ai_usage (purpose, model, tokens_in, tokens_out, cost_usd)
                                  VALUES ('feed_why', 'claude-haiku-5-5', 1, 1, 0) RETURNING id""").fetchone()[0]
            self.assertEqual(ledger.ai_calls_today("feed_why", conn=conn), before + 1)
            self.assertEqual(ledger.feed_save_why({a: "A Clipd rival raised money.", b: None}, uid, conn=conn), 2)
            self.assertEqual(ledger.feed_save_why({a: "again"}, uid, conn=conn), 0)  # asked once
            self.assertFalse({a, b} & {j.story_id for j in ledger.feed_why_queue(8, conn=conn)})
            row = conn.execute("SELECT why_it_matters FROM v_feed_stories WHERE id = %s", (a,)).fetchone()
            self.assertEqual(row[0], "A Clipd rival raised money.")


class Prune(RankSqlCase):
    def test_a_reacted_story_is_never_pruned(self) -> None:
        with _rolled_back() as conn:
            src = self.source(conn)
            kept_reacted, kept_quiet, filt_reacted, filt_quiet = (self.story(conn, src) for _ in range(4))
            conn.execute("UPDATE stories SET filtered_reason = 'title_regex:1' WHERE id = ANY(%s)",
                         ([filt_reacted, filt_quiet],))
            conn.execute("UPDATE stories SET first_seen_at = now() - interval '100 days', "
                         "last_seen_at = now() - interval '100 days' WHERE id = ANY(%s)",
                         ([kept_reacted, kept_quiet, filt_reacted, filt_quiet],))
            self.react(conn, kept_reacted, "open")
            self.react(conn, filt_reacted, "love")
            ledger.feed_prune(conn=conn)
            left = {r[0] for r in conn.execute("SELECT id FROM stories WHERE id = ANY(%s)",
                                               ([kept_reacted, kept_quiet, filt_reacted, filt_quiet],))}
            self.assertEqual(left, {kept_reacted, filt_reacted})

    def test_superseded_scores_go_after_seven_days_and_the_latest_stays(self) -> None:
        with _rolled_back() as conn:
            sid = self.story(conn, self.source(conn))
            for days, value in ((9, 1.0), (8, 2.0), (1, 3.0)):
                conn.execute("INSERT INTO story_scores (story_id, pillar, score, components, scored_at) "
                             "VALUES (%s, 'home', %s, '{}', now() - make_interval(days => %s))", (sid, value, days))
            lonely = self.story(conn, self.source(conn))
            conn.execute("INSERT INTO story_scores (story_id, pillar, score, components, scored_at) "
                         "VALUES (%s, 'home', 5, '{}', now() - interval '20 days')", (lonely,))
            counts = ledger.feed_prune(conn=conn)
            self.assertEqual(set(counts), set(ledger.FEED_PRUNE_SQL))
            self.assertEqual([r[0] for r in conn.execute(
                "SELECT score FROM story_scores WHERE story_id = %s ORDER BY scored_at", (sid,))], [3.0])
            self.assertEqual(conn.execute("SELECT count(*) FROM story_scores WHERE story_id = %s",
                                          (lonely,)).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
