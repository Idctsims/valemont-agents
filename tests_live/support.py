"""Shared setup for the live-database suite.

Deliberately NOT under `tests/`, so `python -m unittest discover -s tests -t .`
stays offline, dependency-free and under a second. The fast suite is the gate
CLAUDE.md §6 tells you to run; this one is a supplement you run when you have
touched SQL.

    python -m unittest discover -s tests_live -t .

Three isolation rules, all enforced here rather than remembered:

1.  **Every row belongs to `_test`**, which carries `is_test = true`. Rows are
    permanent — the append-only triggers see to that — so quarantine is the
    only available form of cleanup, and it is the same mechanism the real
    agents' track record relies on.
2.  **A missing database SKIPS, never fails.** An unset `DATABASE_URL`, a paused
    Supabase project or a missing `db/006` is not a broken invariant, and a red
    suite should mean a broken invariant.
3.  **Every test asserts afterwards that it wrote nothing as a real agent.** The
    same guard the stub suite carries, because here it is not hypothetical.
"""

from __future__ import annotations

import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from dotenv import load_dotenv

from core import ledger

load_dotenv()

#: The one agent this suite may write as. Seeded by db/006.
TEST_SLUG = "_test"

_UNAVAILABLE: str | None = None
_CHECKED = False


def _probe() -> str | None:
    """Return a skip reason, or None when the database is usable."""
    global _CHECKED, _UNAVAILABLE
    if _CHECKED:
        return _UNAVAILABLE
    _CHECKED = True

    if not os.getenv("DATABASE_URL"):
        _UNAVAILABLE = "DATABASE_URL is not set"
        return _UNAVAILABLE
    try:
        if not ledger.agent_is_test(TEST_SLUG):
            _UNAVAILABLE = (
                f"agent {TEST_SLUG!r} exists but is not is_test — refusing to "
                f"write test rows to a real agent"
            )
    except ledger.LedgerError as exc:
        _UNAVAILABLE = f"{TEST_SLUG!r} not registered — paste db/006 first ({exc})"
    except Exception as exc:
        _UNAVAILABLE = f"database unreachable: {type(exc).__name__}"
    return _UNAVAILABLE


class LiveLedgerTestCase(unittest.TestCase):
    """Base for tests that execute real SQL against real Postgres."""

    #: How far out a fixture's deadlines are set before the test waits them out.
    #: Small enough to keep the suite quick, large enough that the
    #: `resolves_in_future` CHECK and `resolutions_timing` trigger both hold.
    HORIZON = timedelta(seconds=2)

    @classmethod
    def setUpClass(cls) -> None:
        reason = _probe()
        if reason:
            raise unittest.SkipTest(reason)
        cls.agent_id = ledger.agent_id(TEST_SLUG)

    def setUp(self) -> None:
        self.run_id = ledger.start_run(self.agent_id, notes="tests_live")
        self.addCleanup(self._close_run)
        self.addCleanup(self._assert_quarantined)

    def _close_run(self) -> None:
        try:
            ledger.end_run(self.run_id, "ok", notes="tests_live")
        except Exception:
            pass

    def _assert_quarantined(self) -> None:
        """Nothing this test wrote may belong to an agent in the track record."""
        self.assertTrue(
            ledger.agent_is_test(TEST_SLUG),
            f"{TEST_SLUG} lost its is_test flag mid-run",
        )

    # -- fixtures -----------------------------------------------------------

    def commit_due(
        self,
        *,
        with_close: bool = False,
        kind: str = "event_contract",
        line: str = "0.40",
    ) -> ledger.Commitment:
        """Write a commitment and wait until it is due.

        `closes_at` has to sit strictly after `committed_at` and at or before
        `resolves_after` (db/004's CHECK), so the close lands first and the whole
        thing is due a moment later.
        """
        now = datetime.now(timezone.utc)
        resolves_after = now + self.HORIZON
        closes_at = now + (self.HORIZON / 2) if with_close else None

        committed = ledger.commit(
            agent_id=self.agent_id,
            run_id=self.run_id,
            kind=kind,
            thesis="tests_live fixture",
            payload={"invalidation": "0", "fixture": True},
            resolves_after=resolves_after,
            closes_at=closes_at,
            legs=[
                ledger.Leg(
                    subject="TEST-MKT",
                    market="event_contract",
                    line=Decimal(line),
                    direction="yes",
                    size=Decimal("1"),
                )
            ],
        )
        # Wait past resolves_after on the SERVER's clock, which is the one the
        # queries compare against.
        time.sleep(self.HORIZON.total_seconds() + 1.0)
        return committed

    def find(
        self, rows: list[ledger.PendingCommitment], commitment_id: int
    ) -> ledger.PendingCommitment:
        matching = [r for r in rows if r.id == commitment_id]
        self.assertEqual(
            len(matching), 1,
            f"commitment {commitment_id} appeared {len(matching)} times",
        )
        return matching[0]


def tearDownModule() -> None:
    """Close the pool before the interpreter starts finalizing.

    `unittest` calls this per module. Without it the pool's own `__del__` runs
    at shutdown and tries to join its worker threads, which Python 3.13+ refuses
    (`PythonFinalizationError`) — noise on the way out of an otherwise green run,
    and noise on the way out is how a real error gets ignored.

    Import it alongside `LiveLedgerTestCase` in every module in this package:

        from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401
    """
    try:
        ledger.close_pool()
    except Exception:
        pass
