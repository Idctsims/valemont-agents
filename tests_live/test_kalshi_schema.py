"""db/008 and db/010 against the database: the `settled` leg outcome and
append-only, walk-forward `model_versions`.

Skips, rather than fails, until those migrations are pasted — an unpasted
migration is not a broken invariant (tests_live/README.md).
"""

from __future__ import annotations

import typing
import unittest
from datetime import timedelta

import psycopg

from core import ledger

from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401


class SettledLegOutcome(LiveLedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        definition = self.scalar(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'legs'::regclass AND conname = 'legs_outcome_check'"
        )
        if definition is None or "'settled'" not in definition:
            self.skipTest("db/008 not pasted")

    def test_ledger_literal_matches_the_schema(self) -> None:
        definition = self.scalar(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'legs'::regclass AND conname = 'legs_outcome_check'"
        )
        for value in typing.get_args(ledger.LegResult):
            self.assertIn(f"'{value}'", definition)

    def test_a_fair_value_settlement_can_be_recorded(self) -> None:
        committed = self.commit_due()
        ledger.add_resolution(
            commitment_id=committed.id, outcome="partial",
            leg_outcomes=[ledger.LegOutcome(0, "settled", "0.19")], pnl="-0.62",
            detail={"settlement": "fair_value", "sealed_by": "tests_live"},
        )
        self.assertEqual(
            self.scalar("SELECT outcome FROM legs WHERE commitment_id = %s", committed.id),
            "settled",
        )


class ModelVersions(LiveLedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        if self.scalar("SELECT to_regclass('public.model_versions')") is None:
            self.skipTest("db/010 not pasted")

    def test_round_trip_and_the_read_time_fence(self) -> None:
        now = self.server_now()
        through = now - timedelta(days=1)
        version = ledger.record_model_version(
            agent_id=self.agent_id, model="tests_live_model", data_through=through,
            params={"betas": {"injury": "0.1"}},
        )
        got = ledger.latest_model_version(self.agent_id, "tests_live_model", before=now)
        self.assertIsNotNone(got)
        self.assertGreaterEqual(got.id, version)
        # A thesis at `through` itself must not see a fit whose data reaches it.
        self.assertNotEqual(
            getattr(ledger.latest_model_version(self.agent_id, "tests_live_model",
                                                before=through), "id", None),
            version,
        )

    def test_a_fit_cannot_claim_data_from_the_future(self) -> None:
        with self.assertRaises(psycopg.errors.CheckViolation):
            ledger.record_model_version(
                agent_id=self.agent_id, model="tests_live_model",
                data_through=self.server_now() + timedelta(days=1), params={},
            )

    def test_fits_are_append_only(self) -> None:
        version = ledger.record_model_version(
            agent_id=self.agent_id, model="tests_live_model",
            data_through=self.server_now() - timedelta(days=1), params={},
        )
        self.refuses("UPDATE model_versions SET usable = false, reason = 'x' WHERE id = %s",
                     version, containing="append-only")

    def test_an_unusable_fit_must_say_why(self) -> None:
        with self.assertRaises(psycopg.errors.CheckViolation):
            ledger.record_model_version(
                agent_id=self.agent_id, model="tests_live_model",
                data_through=self.server_now() - timedelta(days=1), params={},
                usable=False,
            )


if __name__ == "__main__":
    unittest.main()
