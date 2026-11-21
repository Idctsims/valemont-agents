"""The production roster is exactly what the owner approved."""

from __future__ import annotations

import unittest
from datetime import timedelta

from apscheduler.triggers.cron import CronTrigger

from core.orchestrator import build_production
from tests.support import LedgerTestCase


class ProductionRoster(LedgerTestCase):
    def test_the_probe_nfl_ml_and_the_three_parlay_agents(self) -> None:
        orchestrator = build_production()
        self.assertEqual(set(orchestrator._registry),
                         {"_kalshi_probe", "nfl_ml", "parlay_safer", "parlay_mid", "parlay_lotto"})

    def test_every_agent_captures_closes_with_a_capture_job(self) -> None:
        for reg in build_production()._registry.values():
            self.assertTrue(reg.agent.captures_close)
            self.assertIsNotNone(reg.schedule.capture)

    def test_the_parlay_agents_commit_at_the_stamped_slots_with_ten_days_patience(self) -> None:
        # Pre-registration §2 (10:45 and 16:45 Chicago) and §5 (max_overdue 10 days, set explicitly).
        regs = build_production()._registry
        for slug in ("parlay_safer", "parlay_mid", "parlay_lotto"):
            with self.subTest(slug=slug):
                reg = regs[slug]
                self.assertIsInstance(reg.schedule.run, CronTrigger)
                self.assertIn("hour='10,16'", str(reg.schedule.run))
                self.assertIn("minute='45'", str(reg.schedule.run))
                self.assertEqual(str(reg.schedule.run.timezone), "America/Chicago")
                self.assertEqual(reg.schedule.misfire_grace, 300)
                self.assertEqual(reg.defer_policy.max_overdue, timedelta(days=10))
                self.assertIsNotNone(reg.capture_policy)


if __name__ == "__main__":
    unittest.main()
