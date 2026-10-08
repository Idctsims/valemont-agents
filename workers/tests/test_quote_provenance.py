"""Quote provenance and the per-tick clock offset: worker time is stored as
worker time, with a separately labelled database-clock estimate."""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timedelta, timezone

from core.agent import with_quote_provenance
from tests.support import LedgerTestCase, ScriptedAgent, proposal

W = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


class Provenance(LedgerTestCase):
    def committed_payload(self, p) -> dict:
        ScriptedAgent(proposals=[p]).run_once()
        [call] = self.ledger.named("commit")
        return call.kwargs["payload"]

    def test_worker_time_and_db_estimate_are_stored_side_by_side(self) -> None:
        q = self.committed_payload(dataclasses.replace(proposal(), quote_fetched_at=W))["quote_provenance"]
        self.assertEqual(q["fetched_at_worker"], W.isoformat())
        self.assertEqual(q["fetched_at_db_estimate"], (W + timedelta(milliseconds=250)).isoformat())
        self.assertEqual((q["clock_offset_ms"], q["clock_rtt_ms"]), ("250", "40"))

    def test_the_adapters_payload_is_kept(self) -> None:
        payload = self.committed_payload(proposal())
        self.assertEqual(payload["stake"], "1")

    def test_a_commitment_without_a_fetch_time_is_refused(self) -> None:
        outcome = ScriptedAgent(proposals=[dataclasses.replace(proposal(), quote_fetched_at=None)]).run_once()
        self.assertEqual(outcome.status, "error")
        self.assertEqual(self.ledger.named("commit", ok=None), [])

    def test_naive_and_reserved_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            with_quote_provenance(dataclasses.replace(proposal(), quote_fetched_at=W.replace(tzinfo=None)), None)
        p = dataclasses.replace(proposal(), payload={"quote_provenance": {}})
        with self.assertRaises(ValueError):
            with_quote_provenance(p, None)

    def test_every_tick_measures_and_stores_its_clock(self) -> None:
        ScriptedAgent(proposals=[proposal()]).run_once()
        [run] = self.ledger.named("start_run")
        self.assertEqual(run.kwargs["clock"], self.ledger.clock)
        self.assertEqual(len(self.ledger.named("measure_clock")), 1)

    def test_a_failed_measurement_costs_the_estimate_not_the_tick(self) -> None:
        self.ledger.fail_on("measure_clock", RuntimeError("timeout"))
        outcome = ScriptedAgent(proposals=[proposal()]).run_once()
        self.assertEqual(outcome.status, "ok")
        q = self.ledger.named("commit")[0].kwargs["payload"]["quote_provenance"]
        self.assertIsNone(q["fetched_at_db_estimate"])
        self.assertIn("no clock sample", q["note"])
        self.assertIsNotNone(q["fetched_at_worker"])


if __name__ == "__main__":
    unittest.main()
