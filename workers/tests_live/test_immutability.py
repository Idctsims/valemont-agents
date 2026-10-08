"""The guarantees this project exists for, tested against the database.

CLAUDE.md §2 says a commitment "cannot be updated or deleted — the database
enforces this with triggers, not just convention." That sentence has never been
checked by anything that survives a session. Every previous verification of it
lived in a throwaway script and exists now only in a chat transcript.

Order here follows the load-bearing order:

  1. committed_at is the server's, not application code's  (the §2 guarantee)
  2. reject_mutation on every append-only table, UPDATE and DELETE
  3. resolutions.commitment_id UNIQUE  (what makes a void permanent)
  4. legs_freeze_commit_fields, resolutions_timing, snapshot_timing,
     selection_before_close

Raw SQL appears throughout, deliberately — see `support.refuses`.
"""

from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timedelta, timezone

import psycopg

from core import ledger

from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401

#: Every table carrying reject_mutation. SIX, not five: commitments and events
#: from db/001, resolution_attempts from db/003, and three more from db/004.
APPEND_ONLY_TABLES = (
    "commitments",
    "events",
    "resolution_attempts",
    "closing_snapshots",
    "commitment_factors",
    "selections",
)


# ---------------------------------------------------------------------------
# 1. committed_at
# ---------------------------------------------------------------------------

class CommittedAtBelongsToTheServer(LiveLedgerTestCase):
    """§2: "committed_at is set by the database, never by application code.\""""

    def test_ledger_commit_has_no_committed_at_parameter(self) -> None:
        """The enforcement is the absence of a parameter. Prove it is absent."""
        params = inspect.signature(ledger.commit).parameters
        self.assertNotIn("committed_at", params)
        self.assertNotIn("committed", params)

    def test_committed_at_comes_back_on_the_servers_clock(self) -> None:
        before = self.scalar("SELECT now()")
        committed = self.commitment()
        after = self.scalar("SELECT now()")

        self.assertIsNotNone(committed.committed_at.tzinfo, "must be tz-aware")
        self.assertLessEqual(before, committed.committed_at)
        self.assertLessEqual(committed.committed_at, after)

    def test_committed_at_is_not_the_workers_clock(self) -> None:
        """A skewed container must not be able to move the record.

        Not a strict inequality against local time — the two clocks agree here —
        but the value must come from the row the server wrote, so it round-trips
        identically out of a fresh read.
        """
        committed = self.commitment()
        stored = self.scalar(
            "SELECT committed_at FROM commitments WHERE id = %s", committed.id
        )
        self.assertEqual(stored, committed.committed_at)

    def test_committed_at_cannot_be_moved_after_the_fact(self) -> None:
        committed = self.commitment()
        self.refuses(
            "UPDATE commitments SET committed_at = now() - interval '30 days' "
            "WHERE id = %s",
            committed.id,
            containing="append-only",
        )

    def test_where_the_committed_at_guarantee_actually_ends(self) -> None:
        """Honest boundary: the COLUMN has a DEFAULT, not a prohibition.

        `committed_at TIMESTAMPTZ NOT NULL DEFAULT now()` means a raw INSERT
        supplying an explicit value is not refused by the database — only
        `resolves_in_future` constrains it, and only relative to resolves_after.
        The guarantee in §2 is therefore enforced by `ledger.commit()` having no
        such parameter, NOT by the schema.

        This test records that boundary so it is a known limit rather than a
        surprise. It asserts what constrains the column, without writing a
        backdated row to find out.
        """
        constraints = self.scalar(
            """
            SELECT coalesce(array_agg(conname ORDER BY conname), '{}')
              FROM pg_constraint
             WHERE conrelid = 'commitments'::regclass
               AND contype = 'c'
               AND pg_get_constraintdef(oid) LIKE '%committed_at%'
            """
        )
        self.assertEqual(
            list(constraints),
            ["closes_between_commit_and_resolve", "resolves_in_future"],
            "if a CHECK on committed_at was added or removed, §2's enforcement "
            "story changed and this test should say so",
        )
        # Both only constrain it RELATIVE to other columns. Neither forbids an
        # explicit value, so the guarantee is ledger.commit()'s signature.
        for name in ("closes_between_commit_and_resolve", "resolves_in_future"):
            definition = self.scalar(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = %s", name,
            )
            self.assertIn("committed_at", definition)
            self.assertIn(">", definition)


# ---------------------------------------------------------------------------
# 2. reject_mutation
# ---------------------------------------------------------------------------

class AppendOnlyTablesRefuseMutation(LiveLedgerTestCase):
    """Every append-only table, both verbs. The core structural guarantee."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.ids: dict[str, int] = {}

    def setUp(self) -> None:
        super().setUp()
        if not self.ids:
            self._seed()

    def _seed(self) -> None:
        """One row in each append-only table, parked out of every due set."""
        committed = self.commitment(with_close=False)
        self.ids["commitments"] = committed.id

        ledger.emit_event("idle", "tests_live seed", agent_id=self.agent_id,
                          run_id=self.run_id)
        self.ids["events"] = self.scalar(
            "SELECT max(id) FROM events WHERE agent_id = %s", self.agent_id
        )

        ledger.record_resolution_attempt(committed.id, "deferred", "seed")
        self.ids["resolution_attempts"] = self.scalar(
            "SELECT max(id) FROM resolution_attempts WHERE commitment_id = %s",
            committed.id,
        )

        # A snapshot needs closes_at in the past, so this one gets its own row.
        closing = self.commitment(due=True, with_close=True)
        ledger.add_closing_snapshot(
            commitment_id=closing.id, entry_price="0.40", close_price="0.55",
            clv="0.15", clv_pct="0.375",
        )
        self.ids["closing_snapshots"] = self.scalar(
            "SELECT id FROM closing_snapshots WHERE commitment_id = %s", closing.id
        )

        factored = self.commitment(factors=[ledger.Factor("seed_factor", "-0.01")])
        self.ids["commitment_factors"] = self.scalar(
            "SELECT id FROM commitment_factors WHERE commitment_id = %s", factored.id
        )

        # A selection must land before the deadline, which is a century out here.
        ledger.record_selection(factored.id, True, "tests_live seed")
        self.ids["selections"] = self.scalar(
            "SELECT id FROM selections WHERE commitment_id = %s", factored.id
        )

    def test_update_is_refused_on_every_append_only_table(self) -> None:
        columns = {
            "commitments": "thesis = 'rewritten'",
            "events": "message = 'rewritten'",
            "resolution_attempts": "reason = 'rewritten'",
            "closing_snapshots": "clv = 99",
            "commitment_factors": "value = 99",
            "selections": "selected = false",
        }
        for table in APPEND_ONLY_TABLES:
            with self.subTest(table=table):
                exc = self.refuses(
                    f"UPDATE {table} SET {columns[table]} WHERE id = %s",
                    self.ids[table],
                    containing="append-only",
                )
                self.assertIn(table, str(exc))

    def test_delete_is_refused_on_every_append_only_table(self) -> None:
        for table in APPEND_ONLY_TABLES:
            with self.subTest(table=table):
                self.refuses(
                    f"DELETE FROM {table} WHERE id = %s",
                    self.ids[table],
                    containing="append-only",
                )

    def test_a_mutation_hitting_no_rows_is_still_refused(self) -> None:
        """Row triggers only fire on matched rows, so an empty UPDATE succeeds.

        Recorded rather than asserted as protection: this is why
        `scripts/check_db.py` checks the catalog instead of attempting an UPDATE
        against an empty table, and why that decision was right.
        """
        self.allows("UPDATE commitments SET thesis = 'x' WHERE id = -1")

    def test_runs_is_deliberately_mutable(self) -> None:
        """`runs` is bookkeeping, not a commitment. It must stay updatable."""
        self.allows(
            "UPDATE runs SET notes = 'tests_live mutability check' WHERE id = %s",
            self.run_id,
        )


# ---------------------------------------------------------------------------
# 3. resolutions.commitment_id UNIQUE
# ---------------------------------------------------------------------------

class AVoidIsPermanent(LiveLedgerTestCase):
    """UNIQUE on resolutions.commitment_id is what makes abandonment final.

    CLAUDE.md §9.3 leans on this: "once abandoned, a commitment can never be
    resolved, even if the answer arrives ten minutes later." That is a claim
    about a database constraint, so it belongs here.
    """

    def test_a_second_resolution_is_refused(self) -> None:
        """Through the real write path, TWO guards stand in the way.

        Writing this test found which one fires. `add_resolution` updates the
        legs before inserting the resolution, so `legs_frozen` rejects the
        second attempt ("already resolved") before it ever reaches the UNIQUE
        constraint. Defence in depth rather than a single point — but it means
        the UNIQUE needs its own test below, or it is never exercised.
        """
        committed = self.commit_due()
        ledger.add_resolution(
            commitment_id=committed.id, outcome="hit",
            leg_outcomes=[ledger.LegOutcome(0, "hit", "1")], pnl="1",
        )
        with self.assertRaises(psycopg.errors.RaiseException) as caught:
            ledger.add_resolution(
                commitment_id=committed.id, outcome="miss",
                leg_outcomes=[ledger.LegOutcome(0, "miss", "0")], pnl="-1",
            )
        self.assertIn("already resolved", str(caught.exception))

    def test_resolutions_commitment_id_is_unique(self) -> None:
        """The constraint §9.3's permanence claim actually rests on.

        Reached with raw SQL because the leg freeze shields it on the normal
        path. Without this, a resolution written with no leg outcomes — which
        `add_resolution` permits — would have nothing stopping a second one.
        """
        committed = self.commit_due()
        ledger.add_resolution(
            commitment_id=committed.id, outcome="hit",
            leg_outcomes=[], pnl="1",
        )
        self.refuses(
            "INSERT INTO resolutions (commitment_id, outcome) VALUES (%s, 'miss')",
            committed.id,
            error=psycopg.errors.UniqueViolation,
        )

    def test_a_voided_commitment_cannot_later_be_resolved(self) -> None:
        """The §9.3 sentence, as a test: the answer arriving later changes nothing."""
        committed = self.commit_due()
        ledger.add_resolution(
            commitment_id=committed.id, outcome="void",
            leg_outcomes=[ledger.LegOutcome(0, "void", None)], pnl=None,
            detail={"abandoned": True, "reason": "tests_live"},
        )
        with self.assertRaises(psycopg.errors.Error):
            ledger.add_resolution(
                commitment_id=committed.id, outcome="hit",
                leg_outcomes=[ledger.LegOutcome(0, "hit", "1")], pnl="1",
            )
        self.refuses(
            "INSERT INTO resolutions (commitment_id, outcome) VALUES (%s, 'hit')",
            committed.id,
            error=psycopg.errors.UniqueViolation,
        )
        outcome = self.scalar(
            "SELECT outcome FROM resolutions WHERE commitment_id = %s", committed.id
        )
        self.assertEqual(outcome, "void", "the void must still stand")

    def test_one_snapshot_per_commitment(self) -> None:
        committed = self.commit_due(with_close=True)
        ledger.add_closing_snapshot(
            commitment_id=committed.id, entry_price="0.40",
            close_price="0.55", clv="0.15", clv_pct="0.375",
        )
        with self.assertRaises(psycopg.errors.UniqueViolation):
            ledger.add_closing_snapshot(
                commitment_id=committed.id, entry_price="0.40",
                close_price="0.60", clv="0.20", clv_pct="0.5",
            )


# ---------------------------------------------------------------------------
# 4. The four timing / freezing triggers
# ---------------------------------------------------------------------------

class LegsFreezeAtCommitTime(LiveLedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.committed = self.commitment()
        self.leg_id = self.scalar(
            "SELECT id FROM legs WHERE commitment_id = %s", self.committed.id
        )

    def test_the_line_cannot_move(self) -> None:
        self.refuses(
            "UPDATE legs SET line = 999 WHERE id = %s", self.leg_id,
            containing="frozen",
        )

    def test_no_commit_time_field_can_move(self) -> None:
        for column, value in [
            ("subject", "'ELSEWHERE'"), ("market", "'other'"),
            ("line", "999"), ("direction", "'no'"), ("size", "999"),
        ]:
            with self.subTest(column=column):
                self.refuses(
                    f"UPDATE legs SET {column} = {value} WHERE id = %s",
                    self.leg_id, containing="frozen",
                )

    def test_the_outcome_may_be_written_once(self) -> None:
        self.allows(
            "UPDATE legs SET actual = 1, outcome = 'hit' WHERE id = %s", self.leg_id
        )

    def test_the_outcome_cannot_be_rewritten(self) -> None:
        committed = self.commit_due()
        ledger.add_resolution(
            commitment_id=committed.id, outcome="hit",
            leg_outcomes=[ledger.LegOutcome(0, "hit", "1")], pnl="1",
        )
        leg_id = self.scalar(
            "SELECT id FROM legs WHERE commitment_id = %s", committed.id
        )
        self.refuses(
            "UPDATE legs SET actual = 0, outcome = 'miss' WHERE id = %s", leg_id,
            containing="already resolved",
        )

    def test_legs_cannot_be_deleted(self) -> None:
        """Before db/015, `legs` accepted a raw DELETE (the 2026-10-01 audit
        confirmed it empirically). db/015 adds `legs_no_delete`. The trigger
        list is pinned to whichever state the database is in; the refusal
        itself is tested in test_mutation_gaps.py."""
        triggers = list(self.scalar(
            """
            SELECT coalesce(array_agg(tgname ORDER BY tgname), '{}')
              FROM pg_trigger WHERE NOT tgisinternal
               AND tgrelid = 'legs'::regclass
            """
        ))
        self.assertIn(triggers, (["legs_frozen"], ["legs_frozen", "legs_no_delete"]))


class ResolutionsCannotLandEarly(LiveLedgerTestCase):
    def test_a_resolution_before_resolves_after_is_refused(self) -> None:
        committed = self.commitment()  # resolves a century out
        self.refuses(
            "INSERT INTO resolutions (commitment_id, outcome) VALUES (%s, 'hit')",
            committed.id, containing="cannot resolve",
        )

    def test_the_ledger_path_hits_the_same_wall(self) -> None:
        """Not just raw SQL — the real write path is fenced too."""
        committed = self.commitment()
        with self.assertRaises(psycopg.errors.RaiseException):
            ledger.add_resolution(
                commitment_id=committed.id, outcome="hit",
                leg_outcomes=[ledger.LegOutcome(0, "hit", "1")], pnl="1",
            )

    def test_a_resolution_at_or_after_the_deadline_is_accepted(self) -> None:
        committed = self.commit_due()
        resolution_id = ledger.add_resolution(
            commitment_id=committed.id, outcome="hit",
            leg_outcomes=[ledger.LegOutcome(0, "hit", "1")], pnl="1",
        )
        self.assertGreater(resolution_id, 0)


class SnapshotsCannotLandEarlyOrWithoutAClose(LiveLedgerTestCase):
    def test_a_snapshot_before_closes_at_is_refused(self) -> None:
        committed = self.commitment(with_close=True)  # closes tomorrow
        self.refuses(
            "INSERT INTO closing_snapshots (commitment_id, entry_price, "
            "close_price, clv) VALUES (%s, 0.4, 0.55, 0.15)",
            committed.id, containing="before",
        )

    def test_a_snapshot_on_a_commitment_with_no_close_is_refused(self) -> None:
        committed = self.commitment(with_close=False)
        self.refuses(
            "INSERT INTO closing_snapshots (commitment_id, entry_price, "
            "close_price, clv) VALUES (%s, 0.4, 0.55, 0.15)",
            committed.id, containing="no closes_at",
        )

    def test_capture_lag_is_computed_by_the_database(self) -> None:
        """db/005: the lag is the trigger's to set, never the caller's."""
        committed = self.commit_due(with_close=True)
        snapshot = ledger.add_closing_snapshot(
            commitment_id=committed.id, entry_price="0.40",
            close_price="0.55", clv="0.15", clv_pct="0.375",
        )
        self.assertIsNotNone(snapshot.capture_lag_seconds)
        self.assertGreater(snapshot.capture_lag_seconds, 0)
        self.assertNotIn(
            "capture_lag_seconds", inspect.signature(ledger.add_closing_snapshot).parameters
        )

    def test_a_caller_supplied_lag_is_overwritten_not_trusted(self) -> None:
        """The trigger assigns unconditionally, so a lie does not survive."""
        committed = self.commit_due(with_close=True)
        stored = self.returning(
            "INSERT INTO closing_snapshots (commitment_id, entry_price, "
            "close_price, clv, capture_lag_seconds) "
            "VALUES (%s, 0.4, 0.55, 0.15, -9999) RETURNING capture_lag_seconds",
            committed.id,
        )
        self.assertNotEqual(stored, -9999, "a caller's lag was trusted")
        self.assertGreaterEqual(stored, 0)


class SelectionsCannotLandLate(LiveLedgerTestCase):
    """§10.2 inverted from resolutions_timing: too LATE rather than too early."""

    def test_a_selection_before_the_close_is_accepted(self) -> None:
        committed = self.commitment(with_close=True)  # closes tomorrow
        self.assertGreater(ledger.record_selection(committed.id, True, "in time"), 0)

    def test_a_selection_after_the_close_is_refused(self) -> None:
        committed = self.commit_due(with_close=True)  # close already passed
        self.refuses(
            "INSERT INTO selections (commitment_id, selected) VALUES (%s, true)",
            committed.id, containing="hindsight",
        )

    def test_the_ledger_path_hits_the_same_wall(self) -> None:
        committed = self.commit_due(with_close=True)
        with self.assertRaises(psycopg.errors.RaiseException):
            ledger.record_selection(committed.id, True, "too late")

    def test_a_domain_with_no_close_falls_back_to_resolves_after(self) -> None:
        """No closes_at must not mean no deadline."""
        committed = self.commit_due(with_close=False)
        self.refuses(
            "INSERT INTO selections (commitment_id, selected) VALUES (%s, true)",
            committed.id, containing="hindsight",
        )

    def test_one_selection_per_commitment(self) -> None:
        committed = self.commitment(with_close=True)
        ledger.record_selection(committed.id, True, "first")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            ledger.record_selection(committed.id, False, "second")

    def test_declining_is_recorded_not_absent(self) -> None:
        committed = self.commitment(with_close=True)
        ledger.record_selection(committed.id, False, "looked and passed")
        self.assertIs(
            self.scalar(
                "SELECT selected FROM selections WHERE commitment_id = %s",
                committed.id,
            ),
            False,
        )


if __name__ == "__main__":
    unittest.main()
