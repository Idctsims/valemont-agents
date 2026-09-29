"""Resolution and capture must never spend each other's attempt budget.

This is a STRUCTURAL test. The bug it guards lived in SQL, and the stub ledger
in `tests/support.py` deliberately never reaches a database, so no amount of
behavioural stubbing here can catch it — see `tests_live/` for the test that
actually executes the query.

What this can do offline is assert the invariant is spelled in the SQL at all:
every place either query counts `resolution_attempts`, it scopes that count to
its own purpose. That is a weaker claim than "the query returns the right
number", and it is not nothing: the bug was a missing four-word predicate, and
this fails the moment one goes missing again.

The original defect: `due_for_resolution` counted EVERY attempt while
`due_for_capture` filtered to `purpose = 'capture'`. Resolution and capture
have separate `DeferPolicy` budgets, so every capture wait also drew down the
resolution budget. A commitment whose market legitimately sat open past its
expected close burned 12 resolution attempts while capture was still correctly
waiting, then voided — permanently, because `resolutions.commitment_id` is
UNIQUE. It died of bookkeeping with every component working as designed.
"""

from __future__ import annotations

import re
import unittest

from core import ledger

#: Matches an attempt-counting subquery and captures everything up to its close,
#: so the assertions below can look for a purpose predicate inside it.
_ATTEMPT_COUNT = re.compile(
    r"FROM\s+resolution_attempts\s+ra(?P<body>.*?)\)\s*(?:AS\s+)?attempts",
    re.IGNORECASE | re.DOTALL,
)


class AttemptCountsAreScopedByPurpose(unittest.TestCase):
    """Neither sweep may count the other's attempts."""

    QUERIES = {
        "due_for_resolution": (ledger._DUE_SQL, "resolve"),
        "due_for_capture": (ledger._CAPTURE_SQL, "capture"),
    }

    def test_each_query_counts_only_its_own_purpose(self) -> None:
        for name, (sql, purpose) in self.QUERIES.items():
            with self.subTest(query=name):
                match = _ATTEMPT_COUNT.search(sql)
                self.assertIsNotNone(
                    match, f"{name} has no recognisable attempts subquery"
                )
                body = match.group("body")
                self.assertIn(
                    f"purpose = '{purpose}'", body,
                    f"{name} counts attempts without scoping to "
                    f"purpose = '{purpose}' — it is spending the other sweep's "
                    f"budget",
                )

    def test_neither_query_counts_the_other_purpose(self) -> None:
        for name, (sql, purpose) in self.QUERIES.items():
            other = "capture" if purpose == "resolve" else "resolve"
            with self.subTest(query=name):
                body = _ATTEMPT_COUNT.search(sql).group("body")
                self.assertNotIn(f"purpose = '{other}'", body)

    def test_no_unscoped_reference_to_the_attempts_table(self) -> None:
        """Belt and braces: every mention of the table carries a purpose.

        Catches a future second subquery added without a filter, which the
        per-query checks above would miss if it did not alias to `attempts`.
        """
        for name, (sql, _) in self.QUERIES.items():
            with self.subTest(query=name):
                mentions = sql.lower().count("resolution_attempts")
                filters = sql.lower().count("ra.purpose =")
                self.assertEqual(
                    mentions, filters,
                    f"{name} mentions resolution_attempts {mentions} time(s) "
                    f"but carries {filters} purpose filter(s)",
                )


class AttemptRecordingPassesPurposeThrough(unittest.TestCase):
    """The write side must be able to distinguish the two, or the read cannot."""

    def test_ledger_records_purpose(self) -> None:
        import inspect
        sig = inspect.signature(ledger.record_resolution_attempt)
        self.assertIn("purpose", sig.parameters)
        self.assertEqual(sig.parameters["purpose"].default, "resolve")

    def test_purpose_vocabulary_matches_the_schema_check(self) -> None:
        """`AttemptPurpose` must mirror db/004's CHECK, or a write fails at 3am."""
        import typing
        self.assertEqual(
            set(typing.get_args(ledger.AttemptPurpose)), {"resolve", "capture"}
        )


if __name__ == "__main__":
    unittest.main()
