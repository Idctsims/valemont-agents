"""Sports News SQL against the real database (db/042; Chat 3 Phase 3):

* access: RLS on the new tables, anon nothing, the owner reads, decides
  (UPDATE of `decision` only) and inserts links only through an open
  proposal; valemont_readonly reads.
* decisions are append-only: decide once, through the function, as the
  owner; a confirm ends the old link and adds the new one; a retirement
  ends it; a stale proposal is refused.
* boosts only inside a window, entity windows need the follow, and the
  SQL order equals rank.order_for_you on Sports (boost and entity cap).
* ctx_sports_feed's shape; test proposals invisible.
* the worker's queries: derived team follows, the summary queue priority,
  feed_insert_proposal's None on a duplicate or stale move, the move
  context without test entities, and the prune keeping a proposal's story.

Every test runs inside ONE transaction that is always rolled back; fixture
entities are real ("TL ..."), fixture stories real with scores of 30+.

Skips until db/042 is pasted.
"""

from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.types.json import Jsonb

from core import ledger
from feed import rank
from tests_live import twin_check
from tests_live.test_feed_rank_sql import RankSqlCase, _act_as, _rolled_back


class SportsSqlCase(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.boost_windows')").fetchone()[0] is None:
                raise unittest.SkipTest("db/042 not pasted")

    def entity(self, conn: psycopg.Connection, kind: str, sport: str | None, *, is_test: bool = False,
               follow_tier: int | None = None) -> tuple[int, str]:
        name = f"{'Test' if is_test else 'TL'} {kind} {uuid.uuid4().hex[:8]}"
        eid = conn.execute("INSERT INTO entities (kind, name, aliases, sport, is_test) VALUES (%s, %s, %s, %s, %s) "
                           "RETURNING id", (kind, name, [name.lower()], sport, is_test)).fetchone()[0]
        if follow_tier is not None:
            conn.execute("INSERT INTO follows (entity_id, tier, pillar, floor) VALUES (%s, %s, NULL, false)",
                         (eid, follow_tier))
        return eid, name

    def link(self, conn: psycopg.Connection, frm: int, to: int, kind: str = "plays_for") -> int:
        return conn.execute("INSERT INTO entity_links (from_entity_id, to_entity_id, kind, valid_from, "
                            "confirmed_by_owner) VALUES (%s, %s, %s, '2026-01-01', true) RETURNING id",
                            (frm, to, kind)).fetchone()[0]

    def move(self, conn: psycopg.Connection) -> dict:
        """A followed person on team A, an open trade to B named by a story."""
        person, pname = self.entity(conn, "person", "nba", follow_tier=1)
        a, _ = self.entity(conn, "team", "nba")
        b, bname = self.entity(conn, "team", "nba")
        old = self.link(conn, person, a)
        sid = self.story(conn, self.source(conn, ("sports",), sport="nba"))
        pid = ledger.feed_insert_proposal(ledger.ProposalIn(person, "trade", "plays_for", a, b, sid), conn=conn)
        return {"person": person, "pname": pname, "a": a, "b": b, "bname": bname, "old": old, "story": sid, "id": pid}

    def window(self, conn: psycopg.Connection, sport: str, entity: int | None, start: str, end: str,
               hours: float) -> None:
        conn.execute(f"""INSERT INTO boost_windows (sport, entity_id, label, starts_at, ends_at, boost_hours,
                                                    source_url, fetched_at)
                         VALUES (%s, %s, %s, now() + interval '{start}', now() + interval '{end}', %s,
                                 'https://tests-live.invalid/', now())""",
                     (sport, entity, f"tl window {uuid.uuid4().hex[:8]}", hours))

    def scored(self, conn: psycopg.Connection, sport: str, score: float, follows=(), tier: int | None = None,
               pillar: str = "sports") -> int:
        sid = self.story(conn, self.source(conn, (pillar,), sport=sport))
        conn.execute("UPDATE stories SET sport = %s WHERE id = %s", (sport, sid))
        conn.execute("INSERT INTO story_scores (story_id, pillar, score, components, follow_tier) "
                     "VALUES (%s, %s, %s, %s, %s)",
                     (sid, pillar, score, Jsonb({"follows": list(follows)}), tier))
        return sid


class Access(SportsSqlCase):
    def test_rls_grants_and_readonly(self) -> None:
        with _rolled_back() as conn:
            for t in ("boost_windows", "entity_link_proposals", "entity_link_ends"):
                rls, anon = conn.execute(
                    "SELECT relrowsecurity, has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE') "
                    "FROM pg_class WHERE oid = %s::regclass", (t,)).fetchone()
                self.assertEqual((rls, anon), (True, False), t)
                self.assertTrue(conn.execute("SELECT has_table_privilege('valemont_readonly', %s, 'SELECT')",
                                             (t,)).fetchone()[0], t)
            col = conn.execute("""SELECT has_column_privilege('authenticated', 'entity_link_proposals', 'decision', 'UPDATE'),
                                         has_column_privilege('authenticated', 'entity_link_proposals', 'kind', 'UPDATE'),
                                         has_column_privilege('authenticated', 'entity_link_proposals', 'decided_at', 'UPDATE'),
                                         has_table_privilege('authenticated', 'boost_windows', 'INSERT'),
                                         has_table_privilege('authenticated', 'entity_link_proposals', 'DELETE')""").fetchone()
            self.assertEqual(col, (True, False, False, False, False))

    def test_the_owner_cannot_insert_a_link_outside_a_proposal(self) -> None:
        with _rolled_back() as conn:
            person, _ = self.entity(conn, "person", "nba", follow_tier=1)
            team, _ = self.entity(conn, "team", "nba")
            with conn.cursor() as cur:
                _act_as(cur, "authenticated", self.owner)
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute("INSERT INTO entity_links (from_entity_id, to_entity_id, kind, valid_from, "
                                "confirmed_by_owner) VALUES (%s, %s, 'plays_for', owner_today(), true)", (person, team))


class Decisions(SportsSqlCase):
    def test_the_owner_confirms_once_and_the_old_link_ends(self) -> None:
        with _rolled_back() as conn:
            m = self.move(conn)
            self.assertIsNotNone(m["id"])
            with conn.cursor() as cur:
                _act_as(cur, "authenticated", self.owner)
                res = cur.execute("SELECT decide_entity_link_proposal(%s, 'confirmed')", (m["id"],)).fetchone()[0]
                self.assertEqual((res["ended"], res["decision"]), (1, "confirmed"))
                current = cur.execute("SELECT to_entity_id FROM v_entity_links_current WHERE from_entity_id = %s",
                                      (m["person"],)).fetchall()
                self.assertEqual(current, [(m["b"],)])
                with self.assertRaises(psycopg.errors.RaiseException):
                    cur.execute("SELECT decide_entity_link_proposal(%s, 'dismissed')", (m["id"],))

    def test_append_only_for_every_role(self) -> None:
        with _rolled_back() as conn:
            m = self.move(conn)
            for sql in ("UPDATE entity_link_proposals SET decision = 'confirmed' WHERE id = %s",
                        "UPDATE entity_link_proposals SET kind = 'signing' WHERE id = %s",
                        "DELETE FROM entity_link_proposals WHERE id = %s"):
                with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                    conn.execute(sql, (m["id"],))
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                conn.execute("UPDATE entity_links SET valid_from = '2026-02-02' WHERE id = %s", (m["old"],))
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                conn.execute("DELETE FROM entity_links WHERE id = %s", (m["old"],))
            conn.execute("SELECT decide_entity_link_proposal(%s, 'dismissed')", (m["id"],))
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                conn.execute("UPDATE entity_link_proposals SET decision = 'confirmed' WHERE id = %s", (m["id"],))
            row = conn.execute("SELECT decision, decided_at IS NOT NULL FROM entity_link_proposals WHERE id = %s",
                               (m["id"],)).fetchone()
            self.assertEqual(row, ("dismissed", True))

    def test_retirement_ends_the_link_and_a_stale_or_second_proposal_is_none(self) -> None:
        with _rolled_back() as conn:
            m = self.move(conn)
            # A second open proposal for the person: the database refuses, the ledger says None.
            self.assertIsNone(ledger.feed_insert_proposal(
                ledger.ProposalIn(m["person"], "release", "plays_for", m["a"], None, m["story"]), conn=conn))
            conn.execute("SELECT decide_entity_link_proposal(%s, 'confirmed')", (m["id"],))
            # Stale: `from` A, while the current link is now B.
            self.assertIsNone(ledger.feed_insert_proposal(
                ledger.ProposalIn(m["person"], "release", "plays_for", m["a"], None, m["story"]), conn=conn))
            rid = ledger.feed_insert_proposal(
                ledger.ProposalIn(m["person"], "retirement", "plays_for", m["b"], None, m["story"]), conn=conn)
            res = conn.execute("SELECT decide_entity_link_proposal(%s, 'confirmed')", (rid,)).fetchone()[0]
            self.assertEqual((res["ended"], res["new_link"]), (1, None))
            self.assertIsNone(conn.execute("SELECT 1 FROM v_entity_links_current WHERE from_entity_id = %s",
                                           (m["person"],)).fetchone())


class Boosts(SportsSqlCase):
    def _rank(self, conn, sid: int, pillar: str = "sports") -> float:
        return float(conn.execute("SELECT rank FROM feed_for_you(%s, 100000, 0) WHERE id = %s",
                                  (pillar, sid)).fetchone()[0])

    def test_only_inside_a_window(self) -> None:
        with _rolled_back() as conn:
            sid = self.scored(conn, "golf", 30)
            base = self._rank(conn, sid)
            self.window(conn, "golf", None, "-2 hours", "-1 minute", 40)   # over
            self.window(conn, "golf", None, "1 hour", "2 hours", 40)       # not yet
            self.assertAlmostEqual(self._rank(conn, sid), base, places=4)
            self.window(conn, "golf", None, "-1 hour", "1 hour", 6)
            self.assertAlmostEqual(self._rank(conn, sid) - base, rank.rank_of(0, 0, 6), places=4)

    def test_an_entity_window_needs_the_follow_and_applies_on_home(self) -> None:
        with _rolled_back() as conn:
            prog, name = self.entity(conn, "program", "cfb", follow_tier=1)
            hit = self.scored(conn, "cfb", 30, follows=(name,), tier=1, pillar="home")
            miss = self.scored(conn, "cfb", 30, pillar="home")
            before = self._rank(conn, hit, "home"), self._rank(conn, miss, "home")
            self.window(conn, "cfb", prog, "-1 hour", "1 hour", 12)
            self.assertAlmostEqual(self._rank(conn, hit, "home") - before[0], rank.rank_of(0, 0, 12), places=4)
            self.assertAlmostEqual(self._rank(conn, miss, "home"), before[1], places=4)

    def test_sql_order_equals_the_twin_on_sports(self) -> None:
        with _rolled_back() as conn:
            prog, name = self.entity(conn, "program", "cfb", follow_tier=1)
            for i in range(12):
                self.scored(conn, "cfb", 40 - i * 0.01, follows=(name,), tier=1)
            for i in range(30):
                self.scored(conn, "nba", 38 - i * 0.01)
            self.window(conn, "cfb", prog, "-1 hour", "1 hour", 12)
            self.window(conn, "golf", None, "-1 hour", "1 hour", 6)
            tier1 = {r[0] for r in conn.execute(
                "SELECT e.name FROM follows f JOIN entities e ON e.id = f.entity_id "
                "WHERE f.tier = 1 AND (f.pillar IS NULL OR f.pillar = 'sports')")}
            boosted = {r[0] for r in conn.execute(
                "SELECT e.name FROM boost_windows w JOIN entities e ON e.id = w.entity_id "
                "WHERE w.starts_at <= now() AND now() < w.ends_at")}
            # REAL columns as float8, the exact value the SQL ranks with; the
            # order is compared up to exact ties (tests_live/twin_check.py:
            # the golf window made ids 4943 and 5425 tie, 2026-10-13).
            rows = conn.execute("""
                SELECT f.id, f.pos, sc.score::float8, extract(epoch FROM now() - f.lead_published_at) / 3600.0,
                       sc.floor, sc.cross_pillar, coalesce(f.floored, false), f.boost_hours::float8,
                       coalesce(sc.components -> 'follows', '[]'::jsonb), f.rank
                  FROM feed_for_you('sports', 100000, 0) f
                  JOIN v_story_scores_latest sc ON sc.story_id = f.id AND sc.pillar = 'sports'
                 ORDER BY f.pos""").fetchall()
            cands = [rank.Candidate(r[0], float(r[2]), float(r[3]), r[4], r[5], boost_h=float(r[7]),
                                    tier1=tuple(n for n in r[8] if n in tier1)) for r in rows]
            twin = rank.order_for_you(cands, "sports", boosted)
            twin_check.assert_same_order(self, twin, [(r[0], r[1], r[9], r[6]) for r in rows])
            mine = [r for r in rows[:rank.PAGE_SIZE] if name in r[8]]
            self.assertEqual(len(mine), rank.ENTITY_CAP_BOOSTED)

    def test_a_chip_returns_only_its_sport(self) -> None:
        with _rolled_back() as conn:
            g = self.scored(conn, "golf", 30)
            self.scored(conn, "nba", 30)
            rows = conn.execute("SELECT id, sport FROM feed_for_you('sports', 100000, 0, 'golf')").fetchall()
            self.assertIn(g, [r[0] for r in rows])
            self.assertEqual({r[1] for r in rows}, {"golf"})


class Context(SportsSqlCase):
    def test_ctx_sports_feed_shape_and_test_proposals_invisible(self) -> None:
        with _rolled_back() as conn:
            m = self.move(conn)
            person, tname = self.entity(conn, "person", "nba", is_test=True)
            a, _ = self.entity(conn, "team", "nba", is_test=True)
            b, _ = self.entity(conn, "team", "nba", is_test=True)
            self.link(conn, person, a)
            conn.execute("INSERT INTO entity_link_proposals (person_entity_id, kind, link_kind, from_entity_id, "
                         "to_entity_id, is_test) VALUES (%s, 'trade', 'plays_for', %s, %s, true)", (person, a, b))
            ctx = conn.execute("SELECT ctx_sports_feed()").fetchone()[0]
            self.assertTrue({"about", "top_stories", "boosts_now", "proposed_moves"} <= set(ctx))
            self.assertLessEqual(len(ctx["top_stories"]), 5)
            self.assertIn({"person": m["pname"], "kind": "trade", "to": m["bname"]},
                          [{k: v for k, v in p.items() if k != "from"} for p in ctx["proposed_moves"]])
            self.assertNotIn(tname, str(ctx))
            self.assertFalse(conn.execute(
                "SELECT brief_section FROM context_providers WHERE key = 'sports_feed'").fetchone()[0])

    def test_a_test_entity_is_never_followed_or_in_a_window(self) -> None:
        with _rolled_back() as conn:
            t, _ = self.entity(conn, "person", "nba", is_test=True)
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                conn.execute("INSERT INTO follows (entity_id, tier, pillar, floor) VALUES (%s, 1, 'sports', false)", (t,))
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                self.window(conn, "nba", t, "-1 hour", "1 hour", 1)


class Categories(SportsSqlCase):
    """/news categories (db/042 scope addition)."""

    def test_the_check_the_view_and_the_for_you_filter(self) -> None:
        with _rolled_back() as conn:
            deal = self.scored(conn, "nba", 30, pillar="home")
            film = self.scored(conn, "nba", 30, pillar="home")
            conn.execute("UPDATE stories SET sport = NULL, category = 'deals' WHERE id = %s", (deal,))
            conn.execute("UPDATE stories SET sport = NULL, category = 'film_music' WHERE id = %s", (film,))
            rows = conn.execute("SELECT id FROM feed_for_you('home', 100000, 0, NULL, 'deals')").fetchall()
            ids = {r[0] for r in rows}
            self.assertIn(deal, ids)
            self.assertNotIn(film, ids)
            cats = {r[0] for r in conn.execute(
                "SELECT v.category FROM feed_for_you('home', 100000, 0, NULL, 'deals') f "
                "JOIN v_feed_stories v ON v.id = f.id")}
            self.assertEqual(cats, {"deals"})
            with self.assertRaises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute("UPDATE stories SET category = 'gossip' WHERE id = %s", (deal,))

    def test_the_categorize_pass_reads_uncategorized_and_fills_only_null(self) -> None:
        with _rolled_back() as conn:
            src = self.source(conn)
            a, b = self.story(conn, src, title="TL categorize a"), self.story(conn, src)
            conn.execute("UPDATE stories SET category = 'other' WHERE id = %s", (b,))
            todo = {j.story_id: j for j in ledger.feed_uncategorized_home_stories(conn=conn)}
            self.assertIn(a, todo)
            self.assertNotIn(b, todo)
            self.assertEqual((todo[a].title, todo[a].source_name), ("TL categorize a", src.name))
            self.assertEqual(ledger.feed_set_categories([(a, "deals"), (b, "deals")], conn=conn), 1)
            got = dict(conn.execute("SELECT id, category FROM stories WHERE id IN (%s, %s)", (a, b)).fetchall())
            self.assertEqual(got, {a: "deals", b: "other"})

    def test_the_recategorize_set_and_its_compare_and_set(self) -> None:
        """Owner, 2026-10-13: a re-run over an explicit id set may overwrite,
        but only where the category is still the one it read."""
        with _rolled_back() as conn:
            src = self.source(conn)
            bet, sb, deal = (self.story(conn, src, title=f"TL recat {n}") for n in ("bet", "sb", "deal"))
            for sid, cat in ((bet, "betting"), (sb, "sports_business"), (deal, "deals")):
                conn.execute("UPDATE stories SET category = %s WHERE id = %s", (cat, sid))
            got = {j.story_id: cur for j, cur in ledger.feed_category_jobs(["betting", "sports_business"], conn=conn)}
            self.assertEqual({k: v for k, v in got.items() if k in (bet, sb, deal)},
                             {bet: "betting", sb: "sports_business"})
            # sb's "old" is stale (it is sports_business, not deals): refused.
            self.assertEqual(ledger.feed_recategorize([(bet, "betting", "other"), (sb, "deals", "other")],
                                                      conn=conn), 1)
            now = dict(conn.execute("SELECT id, category FROM stories WHERE id IN (%s, %s, %s)",
                                    (bet, sb, deal)).fetchall())
            self.assertEqual(now, {bet: "other", sb: "sports_business", deal: "deals"})

    def test_the_money_set(self) -> None:
        """Owner, 2026-10-13: the money question decides stories with a sport
        and stories in sports_business; betting and uncategorized are out."""
        with _rolled_back() as conn:
            src = self.source(conn)
            rows = {name: self.story(conn, src, title=f"TL money {name}")
                    for name in ("sport_other", "nosport_sb", "nosport_deals", "sport_betting", "sport_null")}
            for name, sport, cat in (("sport_other", "nfl", "other"), ("nosport_sb", None, "sports_business"),
                                     ("nosport_deals", None, "deals"), ("sport_betting", "nfl", "betting"),
                                     ("sport_null", "golf", None)):
                conn.execute("UPDATE stories SET sport = %s, category = %s WHERE id = %s", (sport, cat, rows[name]))
            got = {j.story_id: (j.sport, cur) for j, cur in ledger.feed_money_jobs(conn=conn)}
            mine = {k: v for k, v in got.items() if k in rows.values()}
            self.assertEqual(mine, {rows["sport_other"]: ("nfl", "other"),
                                    rows["nosport_sb"]: (None, "sports_business")})

    def test_a_summary_sets_a_category_once(self) -> None:
        with _rolled_back() as conn:
            sid = self.story(conn, self.source(conn), done=False)
            # A real story's summary carries its ai_usage row (stories_check2).
            usage = conn.execute(
                """INSERT INTO ai_usage (purpose, model, tokens_in, tokens_out, cost_usd)
                   VALUES ('feed_summarize', 'claude-haiku-5-5', 10, 10, 0) RETURNING id""").fetchone()[0]
            ledger.feed_save_summaries([ledger.SummaryOut(sid, "A summary.", category="betting")], usage, conn=conn)
            ledger.feed_save_summaries([ledger.SummaryOut(sid, "Again.", category="deals")], usage, conn=conn)
            self.assertEqual(conn.execute("SELECT category FROM stories WHERE id = %s", (sid,)).fetchone()[0], "betting")


class WorkerQueries(SportsSqlCase):
    def test_derived_team_follow_from_the_current_link(self) -> None:
        with _rolled_back() as conn:
            person, pname = self.entity(conn, "person", "nba", follow_tier=1)
            team, tname = self.entity(conn, "team", "nba")
            self.link(conn, person, team)
            rows = {(f.name, f.tier, f.pillar, f.kind) for f in ledger.feed_follows(conn=conn)}
            self.assertIn((tname, 2, "sports", "team"), rows)
            self.assertIn((pname, 1, None, "person"), rows)
            # The real seeds: the 76ers and Ferrari come back derived (owner D3).
            names = {(f.name, f.tier) for f in ledger.feed_follows(conn=conn)}
            self.assertIn(("Philadelphia 76ers", 2), names)
            self.assertIn(("Ferrari", 2), names)

    def test_the_summary_queue_puts_a_followed_name_first(self) -> None:
        with _rolled_back() as conn:
            src = self.source(conn, ("sports",), sport="nba")
            tag = uuid.uuid4().hex[:8]
            old = self.story(conn, src, title=f"tlprio{tag} signs", hours_ago=60, done=False)
            self.story(conn, src, title=f"tl fresh {tag}", hours_ago=0, done=False)
            pattern = rank.priority_pattern([rank.Follow("x", 1, None, False, (f"tlprio{tag}",), "nba", kind="person")])
            first = ledger.feed_summary_queue(1, 2, pattern, conn=conn)
            self.assertEqual(first[0].story_id, old)
            plain = ledger.feed_summary_queue(1, 2, None, conn=conn)
            self.assertNotEqual(plain[0].story_id, old)

    def test_the_move_context_has_no_test_entity(self) -> None:
        with _rolled_back() as conn:
            _, tname = self.entity(conn, "person", "nba", is_test=True)
            ctx = ledger.feed_move_context(30, conn=conn)
            self.assertNotIn(tname, {e.name for e in ctx.entities})
            self.assertIn("LeBron James", {e.name for e in ctx.entities if e.followed})

    def test_the_prune_keeps_a_proposals_story(self) -> None:
        with _rolled_back() as conn:
            m = self.move(conn)
            other = self.story(conn, self.source(conn, ("sports",)))
            # Age both ends: last_seen_at >= first_seen_at (stories_check).
            conn.execute("UPDATE stories SET first_seen_at = now() - interval '100 days', "
                         "last_seen_at = now() - interval '100 days' WHERE id IN (%s, %s)",
                         (m["story"], other))
            conn.execute(ledger.FEED_PRUNE_SQL["unreacted_stories_deleted"])
            left = {r[0] for r in conn.execute("SELECT id FROM stories WHERE id IN (%s, %s)", (m["story"], other))}
            self.assertEqual(left, {m["story"]})


if __name__ == "__main__":
    unittest.main()
