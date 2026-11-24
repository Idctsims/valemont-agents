"""Phase 4.4: one parlay agent's full loop on the real database: commit, then capture, then resolve.

As the `_test` agent (is_test), the live suite's one writer (tests_live/support.py). Rows are
permanent and quarantined, like every row here: a single transaction cannot hold the loop, because
the database refuses a commitment whose resolves_after is not after now() and a resolution before
resolves_after, and now() is frozen inside a transaction. The rows are finished (captured and
resolved) before the test ends, so nothing is left in either due set.

Real here: ledger.commit and its CHECKs and triggers, the stamp guard against production's
preregistrations (v2.1 must be stamped: db/090), the database clock for the started-game refusal,
the capture and resolution writes. Faked: Kalshi (settlement values and candles)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal as D
from typing import ClassVar

from adapters import parlays as ap
from consensus import correlation as cr
from consensus import parlay_legs as pl
from consensus import parlay_slot as ps
from core import ledger
from tests import test_parlays as tp
from tests_live.support import LiveLedgerTestCase


class _LoopParlay(ap.ParlayAgent):
    slug: ClassVar[str] = "_test"
    tier: ClassVar[str] = "safer"


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


class FullLoop(LiveLedgerTestCase):
    def test_commit_then_capture_then_resolve(self) -> None:
        now = self.server_now()
        kick = now + timedelta(seconds=3)
        expires = now + timedelta(seconds=5)
        ms = [pl.Market(**(tp.market(i).__dict__ | {"starts_at": kick, "game_ticker": f"LG{i}",
                                                     "market_ticker": f"KXNBAPTS-LG{i}-P{i}-10",
                                                     "event_ticker": f"KXNBAPTS-LG{i}"})) for i in range(5)]
        quotes = {m.market_ticker: pl.Quote(m.yes_bid, m.yes_ask, now, expires) for m in ms}
        data = ps.SlotQuotes(key=ps.slot_key(now), markets=pl.with_fresh_p(ms, quotes), quotes=quotes, cut={},
                             requote=pl.Requote(quotes=quotes), started_at=now, finished_at=now, read_seconds=0.0)
        clock = Clock(now)
        client = tp.FakeClient()
        agent = _LoopParlay(client=client, clock=clock, slots=lambda key: data, rho=cr.load_table(), draws=2000)

        out = agent.run_once()  # the real stamp guard and the database clock
        self.assertEqual(out.status, "ok", out.error)
        self.assertEqual(len(out.commitment_ids), 1)
        (cid,) = out.commitment_ids
        self._created.append(cid)
        self.addCleanup(self.seal, cid, True)  # tolerated once finished; seals only on a failure
        row = self.row("SELECT kind, mode, closes_at, resolves_after, committed_at < closes_at, "
                       "payload->>'rule_version', payload->>'scored_price', payload->>'capital_at_risk', "
                       "(SELECT count(*) FROM legs WHERE commitment_id = c.id) FROM commitments c WHERE id = %s", cid)
        kind, mode, closes_at, resolves_after, before_kickoff, rule, scored, capital, n_legs = row
        self.assertEqual((kind, mode, rule, before_kickoff), ("event_contract", "paper", "P2.1", True))
        self.assertEqual(closes_at, kick)
        # P2.1 §5: never before the close; the 5 s expected expiration is earlier than closes_at + 1 min.
        self.assertEqual(resolves_after, max(expires, kick + timedelta(minutes=1)))
        self.assertTrue(D("0.15") <= D(scored) <= D("0.40"))
        legs = [r[0] for r in self.rows("SELECT subject FROM legs WHERE commitment_id = %s ORDER BY id", cid)]
        self.assertEqual(len(legs), n_legs)

        self.wait_for_server(resolves_after)
        # The close proxy: every leg two-sided in the minute before its kickoff.
        client.candle_map = {t: [tp.candle(kick - timedelta(minutes=1), "0.73", "0.75")] for t in legs}
        clock.at = kick + timedelta(minutes=6)
        cap = agent.capture_due()
        self.assertEqual((cap.captured, cap.missed, cap.failed), (1, 0, 0))
        snap = self.row("SELECT status, clv, detail FROM closing_snapshots WHERE commitment_id = %s", cid)
        self.assertEqual(snap[0], "captured")
        # P2.1 §6: every captured leg carries its close age; the ticket its median and maximum.
        self.assertEqual({x["age_min"] for x in snap[2]["legs"]}, {1.0})
        self.assertEqual((snap[2]["age_min_median"], snap[2]["age_min_max"], snap[2]["rule_version"]), (1.0, 1.0, "P2.1"))

        client.settled = {t: "1" for t in legs}
        client.settled[legs[0]] = "0"  # one leg misses: the ticket misses
        res = agent.resolve_due()
        self.assertEqual((res.resolved, res.voided, res.failed), (1, 0, 0))
        outcome, pnl = self.row("SELECT outcome, pnl FROM resolutions WHERE commitment_id = %s", cid)
        self.assertEqual((outcome, pnl), ("miss", D(-1)))

    # -- small reads, as the test agent's own rows -------------------------------------------------------------
    def row(self, sql: str, *args):
        import os
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            return conn.execute(sql, args).fetchone()

    def rows(self, sql: str, *args):
        import os
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            return conn.execute(sql, args).fetchall()


if __name__ == "__main__":
    unittest.main()
