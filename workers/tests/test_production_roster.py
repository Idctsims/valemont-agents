"""The production roster is exactly what the owner approved."""

from __future__ import annotations

import unittest

from core.orchestrator import build_production
from tests.support import LedgerTestCase


class ProductionRoster(LedgerTestCase):
    def test_exactly_the_probe_and_nfl_ml(self) -> None:
        orchestrator = build_production()
        self.assertEqual(set(orchestrator._registry), {"_kalshi_probe", "nfl_ml"})

    def test_every_agent_captures_closes_with_a_capture_job(self) -> None:
        for reg in build_production()._registry.values():
            self.assertTrue(reg.agent.captures_close)
            self.assertIsNotNone(reg.schedule.capture)


if __name__ == "__main__":
    unittest.main()
