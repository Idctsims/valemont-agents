"""db/093 against the real database: the Props tab's reader, kalshi_singles.

Read-only, one rolled-back transaction per test. Every side a page returns is
compared with leg_consensus_for for the same games IN THE SAME STATEMENT, so the
worker refreshing the board mid-test cannot split them. A page is 200 sides at
most, so a test reads a few hundred rows, never the board. Skips when the
database is unreachable, db/093 is unpasted, or no sport is on the board.
"""

from __future__ import annotations

import unittest

from tests_live.test_feed_rank_sql import _rolled_back

SIG = "public.kalshi_singles(text, text, text, text, text, int, int, boolean)"


class Singles093(unittest.TestCase):
    sport: str

    @classmethod
    def setUpClass(cls) -> None:
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regprocedure(%s)", (SIG,)).fetchone()[0] is None:
                raise unittest.SkipTest("db/093 not pasted")
            row = conn.execute("""
                SELECT s.sport FROM kalshi_board b
                  JOIN kalshi_board_events e ON e.event_ticker = b.event_ticker
                  JOIN kalshi_series_board s ON s.series_ticker = e.series_ticker
                 WHERE NOT b.is_test AND b.payout = 'binary' AND e.starts_at > now() + interval '10 minutes'
                 GROUP BY 1 ORDER BY count(*) DESC LIMIT 1""").fetchone()
            if row is None:
                raise unittest.SkipTest("no sport on the board")
            cls.sport = row[0]

    def test_each_side_equals_leg_consensus_for(self) -> None:
        with _rolled_back() as conn:
            for kind in ("prop", "line"):
                for tier in ("safer", "mid", "lotto"):
                    with self.subTest(kind=kind, tier=tier):
                        n, bad = conn.execute("""
                            WITH pg AS (SELECT r FROM jsonb_array_elements(
                                          public.kalshi_singles(%s, %s, %s, NULL, 'edge', 0, 200, false)->'rows') r),
                                 games AS (SELECT array_agg(DISTINCT r->>'gt') g FROM pg)
                            SELECT count(*),
                                   count(*) FILTER (WHERE e.market_ticker IS NULL
                                       OR (pg.r->>'p')::numeric IS DISTINCT FROM
                                          CASE pg.r->>'side' WHEN 'yes' THEN e.p_consensus ELSE 1 - e.p_consensus END
                                       OR (pg.r->>'edge')::numeric IS DISTINCT FROM
                                          round(CASE pg.r->>'side' WHEN 'yes' THEN e.edge_yes ELSE e.edge_no END, 4)
                                       OR pg.r->>'tier' IS DISTINCT FROM e.tier)
                              FROM pg
                              LEFT JOIN public.leg_consensus_for((SELECT g FROM games)) e
                                     ON e.market_ticker = pg.r->>'m'""", (self.sport, kind, tier)).fetchone()
                        self.assertEqual(bad, 0, f"{bad} of {n} sides differ from leg_consensus_for")

    def test_tiers_order_and_kind(self) -> None:
        with _rolled_back() as conn:
            page = conn.execute("SELECT public.kalshi_singles(%s, 'prop', 'mid', NULL, 'edge', 0, 200, false)",
                                (self.sport,)).fetchone()[0]
        rows = page["rows"]
        self.assertLessEqual(set(page["counts"]), {"safer", "mid", "lotto", "low"})
        for r in rows:
            self.assertEqual(r["k"], "prop")
            self.assertGreaterEqual(float(r["ask"]), 0.30)
            self.assertLess(float(r["ask"]), 0.60)
            self.assertNotRegex(r["m"], r"^E2E")
        # Outside-priced sides first, each group by edge, highest first.
        outside = [r["tier"] != "kalshi" for r in rows]
        self.assertEqual(outside, sorted(outside, reverse=True))
        for group in (True, False):
            edges = [float(r["edge"]) for r in rows if (r["tier"] != "kalshi") == group]
            self.assertEqual(edges, sorted(edges, reverse=True))

    def test_access(self) -> None:
        with _rolled_back() as conn:
            q = "SELECT has_function_privilege(%s, %s, 'EXECUTE')"
            self.assertFalse(conn.execute(q, ("anon", SIG)).fetchone()[0])
            self.assertTrue(conn.execute(q, ("authenticated", SIG)).fetchone()[0])
            self.assertTrue(conn.execute(q, ("valemont_readonly", SIG)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
