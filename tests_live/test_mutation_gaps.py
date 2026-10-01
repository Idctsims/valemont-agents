"""db/015: the mutations the 2026-10-01 audit found the database accepted.

Each guard is tested both ways: the illegal mutation is refused, and the legal
neighbour the application needs (closing an open run, writing a leg's outcome
once, flipping `enabled`) still goes through. Everything is rolled back.
Skips until db/015 is pasted.
"""

from __future__ import annotations

import os
import unittest

import psycopg

from core import ledger

from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401


def _applied() -> bool:
    try:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.migration_log') IS NOT NULL")
            if not cur.fetchone()[0]:
                return False
            cur.execute("SELECT 1 FROM migration_log WHERE name = '015_close_mutation_gaps'")
            return cur.fetchone() is not None
    except Exception:
        return False


class MutationGapsClosed(LiveLedgerTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if not _applied():
            raise unittest.SkipTest("db/015 not pasted yet")

    def resolved(self) -> int:
        committed = self.commit_due()
        ledger.add_resolution(commitment_id=committed.id, outcome="hit",
                              leg_outcomes=[ledger.LegOutcome(0, "hit", "1")], pnl="1")
        return committed.id

    # -- resolutions ----------------------------------------------------------

    def test_a_resolution_cannot_be_rewritten(self) -> None:
        cid = self.resolved()
        self.refuses("UPDATE resolutions SET pnl = 99, outcome = 'miss' WHERE commitment_id = %s",
                     cid, containing="append-only")

    def test_a_resolution_cannot_be_deleted(self) -> None:
        cid = self.resolved()
        self.refuses("DELETE FROM resolutions WHERE commitment_id = %s", cid, containing="append-only")

    # -- legs -----------------------------------------------------------------

    def test_a_leg_cannot_be_deleted(self) -> None:
        committed = self.commitment()
        self.refuses("DELETE FROM legs WHERE commitment_id = %s", committed.id, containing="append-only")

    def test_an_unresolved_legs_outcome_can_still_be_written_once(self) -> None:
        committed = self.commitment()
        self.allows("UPDATE legs SET actual = 1, outcome = 'hit' WHERE commitment_id = %s", committed.id)

    # -- runs -----------------------------------------------------------------

    def test_an_open_run_can_be_closed(self) -> None:
        self.allows("UPDATE runs SET status = 'ok', ended_at = now() WHERE id = %s", self.run_id)

    def test_a_closed_run_is_final(self) -> None:
        run_id = ledger.start_run(self.agent_id, notes="tests_live 015")
        ledger.end_run(run_id, "ok")
        self.refuses("UPDATE runs SET status = 'error' WHERE id = %s", run_id, containing="already ended")

    def test_a_runs_identity_is_frozen(self) -> None:
        self.refuses("UPDATE runs SET started_at = started_at - interval '1 day' WHERE id = %s",
                     self.run_id, containing="frozen")

    def test_a_run_cannot_be_deleted(self) -> None:
        run_id = ledger.start_run(self.agent_id, notes="tests_live 015")
        ledger.end_run(run_id, "ok")
        self.refuses("DELETE FROM runs WHERE id = %s", run_id, containing="append-only")

    # -- agents ---------------------------------------------------------------

    def test_enabled_can_still_be_switched(self) -> None:
        self.allows("UPDATE agents SET enabled = NOT enabled WHERE id = %s", self.agent_id)

    def test_is_test_cannot_be_flipped(self) -> None:
        self.refuses("UPDATE agents SET is_test = false WHERE id = %s", self.agent_id, containing="frozen")

    def test_slug_cannot_be_renamed(self) -> None:
        self.refuses("UPDATE agents SET slug = '_renamed' WHERE id = %s", self.agent_id, containing="frozen")

    def test_an_agent_cannot_be_deleted(self) -> None:
        self.refuses("DELETE FROM agents WHERE id = %s", self.agent_id, containing="append-only")

    # -- briefs, migration_log --------------------------------------------------

    def _insert_then(self, insert: str, mutate: str) -> None:
        """Insert and mutate in ONE rolled-back transaction: the probe row never
        commits, so no non-test row is left behind."""
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(insert)
            rid = cur.fetchone()[0]
            with self.assertRaises(psycopg.errors.RaiseException) as caught:
                cur.execute(mutate, (rid,))
            self.assertIn("append-only", str(caught.exception))
            conn.rollback()

    def test_a_brief_cannot_be_rewritten_or_deleted(self) -> None:
        insert = ("INSERT INTO briefs (period_start, period_end, body, stats) "
                  "VALUES (now() - interval '1 day', now(), 'tests_live', '{}') RETURNING id")
        self._insert_then(insert, "UPDATE briefs SET body = 'rewritten' WHERE id = %s")
        self._insert_then(insert, "DELETE FROM briefs WHERE id = %s")

    def test_the_migration_stamp_cannot_be_moved(self) -> None:
        self.refuses("UPDATE migration_log SET applied_at = now() - interval '1 year' "
                     "WHERE name = '015_close_mutation_gaps'", containing="append-only")


if __name__ == "__main__":
    unittest.main()
