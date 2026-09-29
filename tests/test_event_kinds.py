"""Single-commitment adapters emit `committed`, never `slate_committed`."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from typing import ClassVar

from adapters import _fake
from adapters.crypto import Candle, CryptoAgent
from core.agent import Proposal, declared_risk
from tests.support import LedgerTestCase, ScriptedAgent, pending, proposal


class TestCrypto(CryptoAgent):
    slug: ClassVar[str] = "_test_crypto"


class DislocatedFeed:
    """60 closed hourly candles oscillating 100/101, and a spot price chosen by the test."""

    def __init__(self, spot: D) -> None:
        self.spot = spot
        newest = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        self._candles = []
        for i in range(60):
            close = D(100) if i % 2 == 0 else D(101)
            self._candles.append(Candle(
                start=newest - timedelta(hours=59 - i), low=close - D("0.5"),
                high=close + D("0.5"), open=close, close=close, volume=D(1),
            ))

    def ticker(self, symbol: str) -> tuple[D, datetime]:
        return self.spot, datetime.now(timezone.utc)

    def candles(self, symbol: str, granularity: int = 3600) -> list[Candle]:
        return list(self._candles)


class CryptoEmitsCommitted(LedgerTestCase):

    def agent(self, spot: D) -> TestCrypto:
        return TestCrypto(feed=DislocatedFeed(spot), universe=("BTC-USD",))

    def test_dislocation_commits_one_and_emits_committed(self) -> None:
        outcome = self.agent(D(98)).run_once()

        self.assertEqual(outcome.status, "ok")
        self.assertIsNotNone(outcome.commitment_id)
        kinds = self.ledger.event_kinds()
        self.assertEqual(kinds.count("committed"), 1)
        self.assertNotIn("slate_committed", kinds)

        [commit] = self.ledger.named("commit")
        self.assertEqual(commit.kwargs["kind"], "paper_position")
        [leg] = commit.kwargs["legs"]
        self.assertEqual(leg.direction, "long")
        payload = commit.kwargs["payload"]
        self.assertEqual(
            D(payload["capital_at_risk"]),
            declared_risk(leg.line, D(payload["invalidation"]), leg.size),
        )
        self.assertIsNone(commit.kwargs["closes_at"])

    def test_build_commitment_returns_a_single_proposal(self) -> None:
        agent = self.agent(D(98))
        thesis = agent.form_thesis(agent.observe())
        self.assertIsInstance(agent.build_commitment(thesis), Proposal)

    def test_stretched_above_the_mean_goes_short(self) -> None:
        self.agent(D(103)).run_once()
        [commit] = self.ledger.named("commit")
        self.assertEqual(commit.kwargs["legs"][0].direction, "short")
        self.assertEqual(self.ledger.event_kinds().count("committed"), 1)

    def test_calm_market_is_idle(self) -> None:
        outcome = self.agent(D("100.5")).run_once()
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(self.ledger.named("commit", ok=None), [])
        self.assertIn("idle", self.ledger.event_kinds())

    def test_crypto_never_captures_a_close(self) -> None:
        self.assertIs(CryptoAgent.captures_close, False)
        self.assertIsNone(self.agent(D(98)).capture_due().run_id)


class FakeEmitsCommitted(LedgerTestCase):

    def test_whole_script_emits_committed_per_run_and_never_slate(self) -> None:
        agent = _fake.build()
        outcomes = [agent.run_once() for _ in _fake.SCRIPT]

        self.assertTrue(all(o.commitment_id is not None for o in outcomes))
        kinds = self.ledger.event_kinds()
        self.assertEqual(kinds.count("committed"), len(_fake.SCRIPT))
        self.assertNotIn("slate_committed", kinds)

        idle = agent.run_once()
        self.assertFalse(idle.committed)
        self.assertEqual(self.ledger.event_kinds().count("committed"), len(_fake.SCRIPT))

    def test_clv_spec_carries_its_close_and_factors_into_the_commit(self) -> None:
        agent = _fake.build()
        for _ in _fake.SCRIPT:
            agent.run_once()
        commits = self.ledger.named("commit")
        with_close = [c for c in commits if c.kwargs["closes_at"] is not None]
        self.assertEqual(len(with_close), 1)
        names = {f.name for f in with_close[0].kwargs["factors"]}
        self.assertEqual(names, {"injury", "short_week"})

    def test_scripted_results_are_the_documented_r_multiples(self) -> None:
        agent = _fake.build()
        for _ in _fake.SCRIPT:
            agent.run_once()
        pnls = []
        for i, commit in enumerate(self.ledger.named("commit")):
            p = pending(
                2000 + i, agent_id=commit.kwargs["agent_id"], slug="_fake",
                kind=commit.kwargs["kind"], legs=commit.kwargs["legs"],
                payload=commit.kwargs["payload"], attempts=1,
            )
            verdict = agent.resolve(p)
            pnls.append(None if verdict is None else verdict.pnl)
        self.assertEqual(pnls, [D(1), D(2), D(-1), None, D("1.5")])


class SlateKindContrast(LedgerTestCase):
    """The same machinery, for contrast: a real slate is one slate_committed row."""

    def test_two_proposals_emit_one_slate_committed_and_no_committed(self) -> None:
        ScriptedAgent(proposals=[proposal("A"), proposal("B")]).run_once()
        kinds = self.ledger.event_kinds()
        self.assertEqual(kinds.count("slate_committed"), 1)
        self.assertNotIn("committed", kinds)

    def test_a_one_element_sequence_still_emits_committed(self) -> None:
        ScriptedAgent(proposals=[proposal("A")]).run_once()
        kinds = self.ledger.event_kinds()
        self.assertEqual(kinds.count("committed"), 1)
        self.assertNotIn("slate_committed", kinds)


if __name__ == "__main__":
    unittest.main()
