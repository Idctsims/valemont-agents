"""The Railway canary: its interval, its refusal, and that it can boot at all.

`main.py` with CANARY=true registers `_fake` on one cadence, CANARY_INTERVAL_S
(default 60 s, minimum 5). Every canary tick leaves permanent rows, so the
default is part of the contract, and a bad value must refuse before the first
database call rather than run at some other rate.

Before this file, the canary path could not boot: `_fake` opts in to close
capture and the canary schedule had no capture trigger, which the orchestrator
refuses at register(). Nothing exercised that path, so nothing noticed.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from unittest import mock

from adapters import _fake
from adapters._fake import (
    CANARY_INTERVAL_DEFAULT_S, CanaryConfigError, FakeAgent, canary_interval_s, canary_schedule,
)
from core.orchestrator import Orchestrator, Registration
from tests.support import LedgerTestCase

ENV = "CANARY_INTERVAL_S"


class Interval(LedgerTestCase):
    def test_unset_or_blank_is_sixty_seconds(self) -> None:
        self.assertEqual(CANARY_INTERVAL_DEFAULT_S, 60)
        self.assertEqual(canary_interval_s({}), 60)
        self.assertEqual(canary_interval_s({ENV: ""}), 60)
        self.assertEqual(canary_interval_s({ENV: "   "}), 60)

    def test_a_whole_number_of_at_least_five_is_accepted(self) -> None:
        self.assertEqual(canary_interval_s({ENV: "5"}), 5)
        self.assertEqual(canary_interval_s({ENV: " 300 "}), 300)

    def test_anything_else_is_refused(self) -> None:
        for raw in ("4", "0", "-60", "abc", "60.5", "1e2", "+60", "1_0", "60s"):
            with self.subTest(raw=raw), self.assertRaises(CanaryConfigError):
                canary_interval_s({ENV: raw})

    def test_run_sweep_and_capture_share_the_interval(self) -> None:
        schedule = canary_schedule(60)
        for trigger in (schedule.run, schedule.sweep, schedule.capture):
            self.assertEqual(trigger.interval, timedelta(seconds=60))


class Boot(LedgerTestCase):
    def test_the_canary_schedule_registers(self) -> None:
        """Regression: without a capture trigger, register() raised for `_fake`."""
        Orchestrator().register(Registration(agent=FakeAgent(), schedule=canary_schedule(60)))

    def test_main_refuses_a_bad_interval_before_touching_the_database(self) -> None:
        env = {"CANARY": "true", ENV: "4", "DATABASE_URL": "postgresql://unused"}
        sys.modules.pop("main", None)
        with mock.patch("core.paths.load_env"):           # import must not read .env
            import main
        self.addCleanup(sys.modules.pop, "main", None)
        refusal = SystemExit(main.CONFIG_EXIT)
        with mock.patch.dict(os.environ, env), mock.patch.dict(os.environ, {"ROSTER": ""}), \
                mock.patch.object(main, "_refuse", side_effect=refusal) as refuse, \
                mock.patch.object(_fake, "build", side_effect=AssertionError("build() reached")):
            with self.assertRaises(SystemExit) as raised:
                main.main()
        self.assertEqual(raised.exception.code, main.CONFIG_EXIT)
        self.assertIn(ENV, refuse.call_args.args[1])
