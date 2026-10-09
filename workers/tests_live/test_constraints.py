"""CHECK constraints and transaction atomicity.

Priorities 5 and 6. These are the guards that keep a malformed row out rather
than an edit out, and they are invisible to `tests/` for the same reason the
triggers are: the stub ledger never reaches a database.

The snake_case factor-name regex gets the most attention here because its
failure mode is the quietest in the schema. `injury`, `injuries` and `Injury`
would fragment into three factors and every §10.1 attribution average would be
computed over a third of the evidence — silently, and in the direction of
looking more significant than it is.
"""

from __future__ import annotations

import unittest

import psycopg

from core import ledger

from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401

CHECK = psycopg.errors.CheckViolation


class FactorNamesMustBeSnakeCase(LiveLedgerTestCase):
    """db/004: CHECK (name ~ '^[a-z][a-z0-9_]*$')."""

    ACCEPTED = ("injury", "short_week", "rest_days_3", "a", "x9_y")
    REFUSED = (
        "Injury",           # capital — the fragmentation case
        "INJURY",
        "short-week",       # hyphen
        "short week",       # space
        "3_day_rest",       # leading digit
        "_leading",         # leading underscore
        "trailing ",        # trailing space
        "injury.severity",  # dot
        "",                 # empty
    )

    def test_snake_case_names_are_accepted(self) -> None:
        for name in self.ACCEPTED:
            with self.subTest(name=name):
                committed = self.commitment(factors=[ledger.Factor(name, "-0.01")])
                stored = self.scalar(
                    "SELECT name FROM commitment_factors WHERE commitment_id = %s",
                    committed.id,
                )
                self.assertEqual(stored, name)

    def test_anything_else_is_refused(self) -> None:
        for name in self.REFUSED:
            with self.subTest(name=name):
                with self.assertRaises(CHECK):
                    self.commitment(factors=[ledger.Factor(name, "-0.01")])

    def test_the_two_spellings_that_would_fragment_attribution(self) -> None:
        """`injury` and `Injury` must not be able to coexist as two factors."""
        committed = self.commitment(factors=[ledger.Factor("injury", "-0.04")])
        self.refuses(
            "INSERT INTO commitment_factors (commitment_id, name, value) "
            "VALUES (%s, 'Injury', -0.04)",
            committed.id, error=CHECK,
        )

    def test_one_factor_name_per_commitment(self) -> None:
        """UNIQUE NULLS NOT DISTINCT — a plain UNIQUE would allow two NULL legs."""
        committed = self.commitment(factors=[ledger.Factor("injury", "-0.04")])
        self.refuses(
            "INSERT INTO commitment_factors (commitment_id, name, value) "
            "VALUES (%s, 'injury', -0.09)",
            committed.id, error=psycopg.errors.UniqueViolation,
        )

    def test_the_same_name_on_a_different_leg_is_allowed(self) -> None:
        """leg_index distinguishes them: a six-leg slip can have two hurt players."""
        committed = self.commitment(
            factors=[ledger.Factor("injury", "-0.04", leg_index=0)]
        )
        self.allows(
            "INSERT INTO commitment_factors (commitment_id, leg_index, name, value) "
            "VALUES (%s, 1, 'injury', -0.02)",
            committed.id,
        )


class TimingChecksOnCommitments(LiveLedgerTestCase):
    def test_resolves_after_must_be_in_the_future(self) -> None:
        self.refuses(
            "INSERT INTO commitments (agent_id, run_id, kind, thesis, payload, "
            "resolves_after) VALUES (%s, %s, 'event_contract', 't', '{}'::jsonb, "
            "now() - interval '1 hour')",
            self.agent_id, self.run_id, error=CHECK,
        )

    def test_the_ledger_path_hits_it_too(self) -> None:
        from datetime import timedelta
        with self.assertRaises(CHECK):
            ledger.commit(
                agent_id=self.agent_id, run_id=self.run_id, kind="event_contract",
                thesis="backdated", payload={},
                resolves_after=self.server_now() - timedelta(hours=1),
                legs=[ledger.Leg("X", "event_contract", "0.4", "yes", "1")],
            )

    def test_closes_at_must_sit_between_commit_and_resolution(self) -> None:
        cases = {
            "before the commit": "now() - interval '1 hour'",
            "after resolution": "now() + interval '400 days'",
        }
        for label, expression in cases.items():
            with self.subTest(case=label):
                self.refuses(
                    "INSERT INTO commitments (agent_id, run_id, kind, thesis, "
                    f"payload, resolves_after, closes_at) VALUES (%s, %s, "
                    f"'event_contract', 't', '{{}}'::jsonb, "
                    f"now() + interval '365 days', {expression})",
                    self.agent_id, self.run_id, error=CHECK,
                )

    def test_closes_at_equal_to_resolves_after_is_allowed(self) -> None:
        """A contract can settle the moment it closes. `<=`, not `<`."""
        self.allows(
            "INSERT INTO commitments (agent_id, run_id, kind, thesis, payload, "
            "resolves_after, closes_at) VALUES (%s, %s, 'event_contract', 't', "
            "'{}'::jsonb, now() + interval '365 days', now() + interval '365 days')",
            self.agent_id, self.run_id,
        )


class VocabularyChecks(LiveLedgerTestCase):
    """Every CHECK-constrained enum, and its matching Literal in ledger.py.

    A drift between the two is a 3am write failure, so both are asserted.
    """

    CASES = (
        ("commitments", "kind", "paper_position", "not_a_kind"),
        ("legs", "direction", "yes", "sideways"),
        ("resolutions", "outcome", "hit", "sort_of"),
        ("runs", "status", "ok", "fine"),
        ("resolution_attempts", "result", "deferred", "shrug"),
        ("resolution_attempts", "purpose", "capture", "something_else"),
        ("closing_snapshots", "status", "missed", "maybe"),
    )

    def test_each_enum_refuses_a_value_outside_its_check(self) -> None:
        for table, column, _good, bad in self.CASES:
            with self.subTest(table=table, column=column):
                definition = self.scalar(
                    """
                    SELECT pg_get_constraintdef(oid) FROM pg_constraint
                     WHERE conrelid = %s::regclass AND contype = 'c'
                       AND pg_get_constraintdef(oid) LIKE %s
                     LIMIT 1
                    """,
                    table, f"%{column}%",
                )
                self.assertIsNotNone(
                    definition, f"{table}.{column} has no CHECK constraint"
                )
                self.assertNotIn(f"'{bad}'", definition)

    def test_ledger_literals_match_the_schema(self) -> None:
        import typing
        pairs = [
            (ledger.Kind, "commitments", "kind"),
            (ledger.Direction, "legs", "direction"),
            (ledger.Outcome, "resolutions", "outcome"),
            (ledger.AttemptPurpose, "resolution_attempts", "purpose"),
            (ledger.AttemptResult, "resolution_attempts", "result"),
        ]
        for literal, table, column in pairs:
            with self.subTest(table=table, column=column):
                definition = self.scalar(
                    """
                    SELECT pg_get_constraintdef(oid) FROM pg_constraint
                     WHERE conrelid = %s::regclass AND contype = 'c'
                       AND pg_get_constraintdef(oid) LIKE %s
                     LIMIT 1
                    """,
                    table, f"%{column} =%",
                )
                for value in typing.get_args(literal):
                    self.assertIn(
                        f"'{value}'", definition,
                        f"ledger.py allows {value!r} but the {table}.{column} "
                        f"CHECK does not — a write would fail at 3am",
                    )

    def test_confidence_must_be_a_probability(self) -> None:
        for bad in ("-0.1", "1.5"):
            with self.subTest(confidence=bad):
                with self.assertRaises(CHECK):
                    self.commitment(confidence=bad)

    def test_confidence_may_be_null_or_either_bound(self) -> None:
        for value in (None, "0", "1", "0.5"):
            with self.subTest(confidence=value):
                self.assertGreater(self.commitment(confidence=value).id, 0)


class SnapshotShapeChecks(LiveLedgerTestCase):
    def test_a_captured_snapshot_must_carry_prices(self) -> None:
        committed = self.commit_due(with_close=True)
        self.refuses(
            "INSERT INTO closing_snapshots (commitment_id, status) "
            "VALUES (%s, 'captured')",
            committed.id, error=CHECK,
        )

    def test_a_missed_snapshot_must_carry_a_reason(self) -> None:
        committed = self.commit_due(with_close=True)
        # An attempt on record, so db/024's trigger lets the row through and
        # it is the reason CHECK that refuses it, which is what this tests.
        ledger.record_resolution_attempt(committed.id, "deferred", "tests_live", purpose="capture")
        self.refuses(
            "INSERT INTO closing_snapshots (commitment_id, status) "
            "VALUES (%s, 'missed')",
            committed.id, error=CHECK,
        )

    def test_the_ledger_refuses_a_reasonless_miss_before_the_database_does(self) -> None:
        """Caught in Python with a better message than the CHECK would give."""
        committed = self.commitment(with_close=True)
        with self.assertRaises(ledger.LedgerError):
            ledger.add_closing_snapshot(
                commitment_id=committed.id, status="missed", reason=None
            )


class CommitIsAtomic(LiveLedgerTestCase):
    """Priority 6: commitment, legs and factors land together or not at all.

    A half-written commitment is worse than none: a thesis with no legs, or legs
    with no parent, is a corrupt record that cannot be deleted.
    """

    def _exists(self, thesis: str) -> bool:
        return bool(self.scalar(
            "SELECT count(*) FROM commitments WHERE thesis = %s", thesis
        ))

    def test_a_bad_factor_rolls_back_the_whole_commitment(self) -> None:
        marker = "atomicity: bad factor name"
        with self.assertRaises(CHECK):
            self.commitment(
                thesis=marker,
                factors=[
                    ledger.Factor("good_factor", "-0.01"),
                    ledger.Factor("Bad Factor", "-0.02"),   # violates the regex
                ],
            )
        self.assertFalse(
            self._exists(marker),
            "the commitment survived a failed factor insert — the transaction "
            "did not roll back, and the ledger now holds a commitment whose "
            "attribution is missing",
        )

    def test_no_orphan_legs_or_factors_survive_the_rollback(self) -> None:
        marker = "atomicity: orphan check"
        with self.assertRaises(CHECK):
            self.commitment(thesis=marker, factors=[ledger.Factor("Nope", "0")])
        orphan_legs = self.scalar(
            "SELECT count(*) FROM legs l LEFT JOIN commitments c "
            "ON c.id = l.commitment_id WHERE c.id IS NULL"
        )
        orphan_factors = self.scalar(
            "SELECT count(*) FROM commitment_factors f LEFT JOIN commitments c "
            "ON c.id = f.commitment_id WHERE c.id IS NULL"
        )
        self.assertEqual(orphan_legs, 0)
        self.assertEqual(orphan_factors, 0)

    def test_a_bad_resolves_after_rolls_back_before_any_leg_is_written(self) -> None:
        from datetime import timedelta
        marker = "atomicity: backdated"
        before = self.scalar("SELECT count(*) FROM legs")
        with self.assertRaises(CHECK):
            ledger.commit(
                agent_id=self.agent_id, run_id=self.run_id, kind="event_contract",
                thesis=marker, payload={},
                resolves_after=self.server_now() - timedelta(hours=1),
                legs=[ledger.Leg("X", "event_contract", "0.4", "yes", "1")],
            )
        self.assertFalse(self._exists(marker))
        self.assertEqual(self.scalar("SELECT count(*) FROM legs"), before)

    def test_a_successful_commit_writes_all_three_together(self) -> None:
        committed = self.commitment(
            thesis="atomicity: the happy case",
            factors=[ledger.Factor("injury", "-0.04"),
                     ledger.Factor("short_week", "0.09")],
        )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM legs WHERE commitment_id = %s",
                        committed.id), 1)
        self.assertEqual(
            self.scalar("SELECT count(*) FROM commitment_factors "
                        "WHERE commitment_id = %s", committed.id), 2)

    def test_the_ledger_refuses_a_legless_commitment_before_the_database(self) -> None:
        from datetime import timedelta
        with self.assertRaises(ledger.LedgerError):
            ledger.commit(
                agent_id=self.agent_id, run_id=self.run_id, kind="event_contract",
                thesis="no legs", payload={},
                resolves_after=self.server_now() + timedelta(days=1),
                legs=[],
            )


if __name__ == "__main__":
    unittest.main()
