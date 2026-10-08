"""An agent is scheduled only with BOTH a Registration AND agents.enabled.

Two gates, either of which can stop an agent and neither of which alone can
start one. The database flag exists so an agent can be parked by pasting a
migration, without a deploy; before this test it was read by nothing, and the
roster showed three "enabled" agents that no code consulted.
"""

from __future__ import annotations

from typing import ClassVar

from core.orchestrator import Orchestrator, Registration, Schedule, every
from tests.support import LedgerTestCase, ScriptedAgent


class FakeSlugAgent(ScriptedAgent):
    slug: ClassVar[str] = "_fake"


def registration(agent: ScriptedAgent, *, enabled: bool = True) -> Registration:
    return Registration(
        agent=agent,
        schedule=Schedule(run=every(seconds=5), sweep=every(seconds=10)),
        enabled=enabled,
    )


class EnabledGate(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.orchestrator = Orchestrator()

    def jobs(self) -> set[str]:
        """Job ids wired so far. APScheduler lists pending jobs before start()."""
        return {job.id for job in self.orchestrator._scheduler.get_jobs()}

    def test_registered_and_enabled_in_the_database_is_scheduled(self) -> None:
        self.orchestrator.register(registration(ScriptedAgent()))
        self.assertEqual(self.orchestrator._wire(), 1)
        self.assertEqual(self.jobs(), {"_test:run", "_test:sweep"})

    def test_disabled_in_the_database_is_not_scheduled(self) -> None:
        self.ledger.disabled.add("_test")
        self.orchestrator.register(registration(ScriptedAgent()))
        self.assertEqual(self.orchestrator._wire(), 0)
        self.assertEqual(self.jobs(), set())

    def test_disabled_in_the_registration_is_not_scheduled(self) -> None:
        self.orchestrator.register(registration(ScriptedAgent(), enabled=False))
        self.assertEqual(self.orchestrator._wire(), 0)
        self.assertEqual(self.jobs(), set())

    def test_the_database_flag_alone_schedules_nothing(self) -> None:
        """Enabled rows with no Registration — crypto, _fake here — never run."""
        self.orchestrator.register(registration(ScriptedAgent()))
        self.orchestrator._wire()
        self.assertEqual({job.split(":")[0] for job in self.jobs()}, {"_test"})

    def test_gates_apply_per_agent(self) -> None:
        self.ledger.disabled.add("_fake")
        self.orchestrator.register(registration(ScriptedAgent()))
        self.orchestrator.register(registration(FakeSlugAgent()))
        self.assertEqual(self.orchestrator._wire(), 1)
        self.assertEqual(self.jobs(), {"_test:run", "_test:sweep"})

    def test_start_refuses_when_every_agent_is_gated_off(self) -> None:
        """An up-but-idle worker looks healthy. It must refuse instead."""
        self.ledger.disabled.add("_test")
        self.orchestrator.register(registration(ScriptedAgent()))
        with self.assertRaises(RuntimeError) as caught:
            self.orchestrator.start()
        self.assertIn("_test", str(caught.exception))
        self.assertFalse(self.orchestrator._scheduler.running)
