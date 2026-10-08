"""The suite's own guarantees: no database, no network, no writes as a real agent."""

from __future__ import annotations

import unittest
from typing import ClassVar

from adapters import _fake
from adapters.crypto import CoinbaseFeed
from core import ledger
from tests.support import REAL_AGENTS, LedgerTestCase, ScriptedAgent, proposal


class ImpersonatesCrypto(ScriptedAgent):
    slug: ClassVar[str] = "crypto"


class Quarantine(LedgerTestCase):

    def test_tripwire_catches_a_write_as_a_real_agent(self) -> None:
        ImpersonatesCrypto(proposals=proposal("A")).run_once()
        leaked = self.ledger.writes_to_non_test()
        self.assertTrue(leaked)
        self.assertEqual({c.agent_id for c in leaked}, {REAL_AGENTS["crypto"]})
        # Cleared so tearDown's own check stays meaningful for every other test.
        self.ledger.calls.clear()

    def test_unattributable_write_counts_as_a_leak(self) -> None:
        ledger.record_resolution_attempt(987654, "deferred")
        self.assertEqual(len(self.ledger.writes_to_non_test()), 1)
        self.ledger.calls.clear()

    def test_fake_harness_refuses_a_non_test_row(self) -> None:
        self.ledger.agents["_fake"] = (4, False)
        with self.assertRaises(RuntimeError):
            _fake.build()
        self.assertEqual(self.ledger.writes(), [])

    def test_the_real_pool_is_unreachable(self) -> None:
        with self.assertRaises(AssertionError):
            ledger._pool()

    def test_the_network_is_unreachable(self) -> None:
        with self.assertRaises(AssertionError):
            CoinbaseFeed().ticker("BTC-USD")


if __name__ == "__main__":
    unittest.main()
