"""db/091 against the real database: the Parlays page's readers, the brief line, Wags.

Read-only checks of the real parlay tickets (one rolled-back transaction each).
Every comparison runs the reader and an independent read of the raw rows in
ONE statement, so the worker settling a ticket mid-test cannot split them.
Skips when the database is unreachable or db/091 is unpasted.
"""

from __future__ import annotations

import math
import unittest
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from core import ledger
from tests_live.test_feed_rank_sql import _rolled_back

PARLAYS = "('parlay_safer', 'parlay_mid', 'parlay_lotto')"

RAW_TICKETS = f"""
    SELECT coalesce(json_agg(json_build_object(
               'tier', c.payload->>'tier', 'scope', coalesce(c.payload->>'scope', 'sport'),
               'rv', coalesce(c.payload->>'rule_version', 'P2'),
               'd', (c.committed_at AT TIME ZONE 'America/Chicago')::date,
               'stake', (c.payload->>'capital_at_risk')::numeric, 'outcome', r.outcome, 'pnl', r.pnl,
               'paid', (r.detail->>'proceeds')::numeric, 'cs', s.status, 'clv', s.clv)), '[]')
      FROM commitments c JOIN agents a ON a.id = c.agent_id
      LEFT JOIN resolutions r ON r.commitment_id = c.id
      LEFT JOIN closing_snapshots s ON s.commitment_id = c.id
     WHERE a.slug IN {PARLAYS} AND NOT a.is_test AND c.mode = 'paper'
"""


def _record_from_raw(raw: list[dict]) -> dict[tuple[str, str, str], dict]:
    """The record, computed in Python: per agent (tier, scope) and cohort."""
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for t in raw:
        groups[(t["tier"], t["scope"], t["rv"])].append(t)
    out = {}
    for key, ts in groups.items():
        settled = [t for t in ts if t["outcome"] is not None]
        pnls = [Decimal(str(t["pnl"])) for t in settled if t["pnl"] is not None]
        mean = sum(pnls) / len(pnls) if pnls else None
        se = None
        if pnls:
            by_day: dict[str, Decimal] = defaultdict(Decimal)
            for t in settled:
                if t["pnl"] is not None:
                    by_day[t["d"]] += Decimal(str(t["pnl"])) - mean
            g = len(by_day)
            if g >= 2:
                se = math.sqrt(float(sum(e * e for e in by_day.values())) * g / (g - 1)) / len(pnls)
        clvs = [Decimal(str(t["clv"])) for t in ts if t["cs"] == "captured"]
        out[key] = {
            "tickets": len(ts), "settled": len(settled), "hit": sum(t["outcome"] == "hit" for t in ts),
            "open": len(ts) - len(settled), "r_mean": mean, "r_se": se,
            "staked": sum((Decimal(str(t["stake"])) for t in settled), Decimal(0)),
            "paid": sum((Decimal(str(t["paid"])) for t in settled), Decimal(0)),
            "clv_n": len(clvs), "clv_mean": (sum(clvs) / len(clvs)) if clvs else None,
            "missed": sum(t["cs"] == "missed" for t in ts),
        }
    return out


class ParlaysPage091(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regprocedure('public.parlay_day(date, boolean)')").fetchone()[0] is None:
                raise unittest.SkipTest("db/091 not pasted")

    def test_the_record_matches_a_computation_from_the_raw_rows(self) -> None:
        with _rolled_back() as conn:
            record, raw = conn.execute(f"SELECT public.parlay_record(false), ({RAW_TICKETS})").fetchone()
        want = _record_from_raw(raw)
        got = {(r["tier"], r["scope"], r["rv"]): r for r in record["rows"] if "sport" not in r}
        self.assertEqual(set(got), set(want))
        for key, w in want.items():
            g = got[key]
            with self.subTest(agent=key):
                for f in ("tickets", "settled", "hit", "open", "clv_n", "missed"):
                    self.assertEqual(g[f], w[f], f)
                if w["r_mean"] is None:
                    self.assertNotIn("r_mean", g)
                else:
                    self.assertAlmostEqual(float(g["r_mean"]), float(w["r_mean"]), places=4)
                    self.assertAlmostEqual(float(g["staked"]), float(w["staked"]), places=2)
                    self.assertAlmostEqual(float(g["pl"]), float(w["paid"] - w["staked"]), places=2)
                if w["r_se"] is None:
                    self.assertNotIn("r_se", g)
                else:
                    self.assertAlmostEqual(float(g["r_se"]), w["r_se"], places=4)
                if w["clv_mean"] is not None:
                    self.assertAlmostEqual(float(g["clv_mean"]), float(w["clv_mean"]), places=4)
        # Never a figure across agents or cohorts: every row names one tier, one scope, one cohort.
        for r in record["rows"]:
            self.assertIn(r["tier"], ("safer", "mid", "lotto"))
            self.assertIn(r["scope"], ("sport", "cross_sport"))
            self.assertIn(r["rv"], record["cohorts"])
        self.assertTrue({"P2", "P2.1"} <= set(record["cohorts"]))

    def test_each_day_holds_exactly_that_days_tickets_and_their_legs(self) -> None:
        with _rolled_back() as conn:
            for back in (0, 1, 2):
                day, j, ids, legs = conn.execute(f"""
                    WITH d AS (SELECT (now() AT TIME ZONE 'America/Chicago')::date - %s AS day)
                    SELECT d.day, public.parlay_day(d.day, false),
                           (SELECT coalesce(array_agg(c.id ORDER BY c.id), '{{}}') FROM commitments c
                              JOIN agents a ON a.id = c.agent_id
                             WHERE a.slug IN {PARLAYS} AND NOT a.is_test AND c.mode = 'paper'
                               AND (c.committed_at AT TIME ZONE 'America/Chicago')::date = d.day),
                           (SELECT coalesce(json_object_agg(c.id, jsonb_array_length(c.payload->'legs')), '{{}}')
                              FROM commitments c JOIN agents a ON a.id = c.agent_id
                             WHERE a.slug IN {PARLAYS} AND NOT a.is_test
                               AND (c.committed_at AT TIME ZONE 'America/Chicago')::date = d.day)
                      FROM d""", (back,)).fetchone()
                with self.subTest(day=day):
                    self.assertEqual(sorted(t["id"] for t in j["tickets"]), list(ids))
                    for t in j["tickets"]:
                        self.assertEqual(len(t["legs"]), legs[str(t["id"])])
                        self.assertGreater(t["stake"], 0)
                        for leg in t["legs"]:
                            # Named in words: never a bare ticker (KX...-...).
                            self.assertNotRegex(leg["label"], r"^KX[A-Z0-9]+-")
                            if "g" in leg:
                                self.assertIn(str(leg["g"]), j["games"])
                    for r in j["results"]:
                        self.assertIn(r["slot"], ("10:45", "16:45"))
                        self.assertTrue(r.get("rule"), r)

    def test_details_carry_the_selection_log_verbatim(self) -> None:
        with _rolled_back() as conn:
            row = conn.execute(f"""
                SELECT public.parlay_ticket(c.id, false), c.payload->'leg_reasons', c.payload->>'fair_p'
                  FROM commitments c JOIN agents a ON a.id = c.agent_id
                 WHERE a.slug IN {PARLAYS} AND NOT a.is_test ORDER BY c.id DESC LIMIT 1""").fetchone()
        if row is None:
            self.skipTest("no parlay ticket yet")
        j, reasons, fair = row
        self.assertEqual(j["reasons"], reasons)
        self.assertAlmostEqual(float(j["fair"]), float(fair), places=6)
        self.assertEqual(len(j["legs"]), len(reasons))
        self.assertEqual(j["mode"], "paper")

    def test_the_brief_line_and_wags(self) -> None:
        with _rolled_back() as conn:
            brief, ctx, enabled = conn.execute(f"""
                SELECT public.ctx_parlays_brief(), public.ctx_parlays(),
                       EXISTS (SELECT 1 FROM agents WHERE slug IN {PARLAYS} AND enabled)""").fetchone()
        if not enabled:
            self.assertIsNone(brief)
            return
        self.assertTrue(1 <= len(brief["lines"]) <= 2, brief)
        self.assertTrue(brief["lines"][0].startswith("Parlays (paper)"), brief["lines"][0])
        for line in brief["lines"]:
            # Each tier at most once per line: no line totals tiers.
            for name in ("Safer ", "Mid "):
                self.assertLessEqual(line.count(name), 1, line)
        self.assertIn("never add tiers", ctx["about"])
        self.assertLess(len(str(ctx)), 30_000)

    def test_the_workers_texts_run(self) -> None:
        with _rolled_back() as conn:
            self.assertGreaterEqual(ledger.parlay_legs_capture(conn=conn), 0)
            hits = ledger.parlay_hits_due(timedelta(hours=12), conn=conn)
            self.assertTrue(all(h.tier in ("safer", "mid", "lotto") for h in hits))

    def test_access(self) -> None:
        with _rolled_back() as conn:
            q = "SELECT has_function_privilege(%s, %s, 'EXECUTE')"
            self.assertFalse(conn.execute(q, ("anon", "public.parlay_day(date, boolean)")).fetchone()[0])
            self.assertTrue(conn.execute(q, ("authenticated", "public.parlay_day(date, boolean)")).fetchone()[0])
            self.assertTrue(conn.execute(q, ("valemont_readonly", "public.parlay_record(boolean)")).fetchone()[0])
            self.assertFalse(conn.execute(q, ("authenticated", "public.parlay_leg_display_capture(interval)")).fetchone()[0])
            for table in ("parlay_leg_display", "parlay_hit_pushes"):
                self.assertFalse(conn.execute("SELECT has_table_privilege('authenticated', %s, 'INSERT')",
                                              (f"public.{table}",)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
