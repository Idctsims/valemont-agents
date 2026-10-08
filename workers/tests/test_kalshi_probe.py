"""`_kalshi_probe`: one contract, the week's first game, regardless of edge."""

from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal as D

from adapters._kalshi_probe import KalshiProbe
from core.ledger import Leg
from tests.kalshi_fakes import T0, FakeKalshi, fee_schedule, game, quote, schedule
from tests.support import TEST_AGENTS, LedgerTestCase, pending

THU = T0 + timedelta(hours=23)                       # first game of week 5
SUN = T0 + timedelta(days=3)


class Probe(LedgerTestCase):
    def build(self, *, now=T0, bid="0.62", ask="0.64") -> KalshiProbe:
        first = game(THU, game_id="2026_05_BAL_DAL")
        later = game(SUN, game_id="2026_05_KC_DEN", away="KC", home="DEN")
        exp = THU + timedelta(hours=6)
        fake = FakeKalshi(quotes={
            "KXNFLGAME-26OCT04BALDAL-DAL": quote("KXNFLGAME-26OCT04BALDAL-DAL", bid=bid, ask=ask,
                                                 expected_expiration=exp),
            "KXNFLGAME-26OCT04BALDAL-BAL": quote("KXNFLGAME-26OCT04BALDAL-BAL", expected_expiration=exp),
        })
        return KalshiProbe(client=fake, clock=lambda: now,
                           load_schedule=lambda seasons: schedule(first, later),
                           load_fees=lambda c, s: fee_schedule())

    def test_commits_one_home_contract_on_the_weeks_first_game_regardless_of_edge(self) -> None:
        self.build().run_once()
        [call] = self.ledger.named("commit")
        [leg] = call.kwargs["legs"]
        self.assertEqual((leg.subject, leg.direction, leg.size, leg.line),
                         ("KXNFLGAME-26OCT04BALDAL-DAL", "yes", D(1), D("0.64")))
        self.assertEqual(call.kwargs["payload"]["purpose"], "plumbing_probe")
        self.assertEqual(call.kwargs["closes_at"], THU)
        self.assertEqual(call.kwargs["factors"], ())

    def test_only_the_first_game_of_the_week_is_eligible(self) -> None:
        self.build(now=SUN - timedelta(hours=20)).run_once()   # inside the Sunday game's window
        self.assertEqual(self.ledger.named("commit"), [])

    def test_never_twice_on_the_same_game(self) -> None:
        agent = self.build()
        self.ledger.open.append(pending(
            1, agent_id=TEST_AGENTS["_kalshi_probe"], slug="_kalshi_probe",
            legs=(Leg("KXNFLGAME-26OCT04BALDAL-DAL", "KXNFLGAME", D("0.6"), "yes", D(1)),)))
        agent.run_once()
        self.assertEqual(self.ledger.named("commit"), [])

    def test_its_writes_are_quarantined_test_rows(self) -> None:
        """LedgerTestCase fails any write by a non-test agent; this one passes."""
        self.build().run_once()
        self.assertEqual(self.ledger.writes_to_non_test(), [])


if __name__ == "__main__":
    unittest.main()
