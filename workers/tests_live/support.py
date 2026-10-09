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

import psycopg

from core import ledger
from core.paths import load_env

load_env()

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

    #: Upper bound on waiting for the server's clock to pass a fixture deadline.
    #: Generous: the wait is a poll on the server, so this only bounds a hang.
    SERVER_WAIT_TIMEOUT = timedelta(seconds=30)

    #: Every row that belongs to something other than a test agent, per table.
    #: `briefs` has no agent at all, so any row there counts. An event with a
    #: NULL agent is not quarantined by anything, so it counts too.
    NON_TEST_ROWS = """
        SELECT
          (SELECT count(*) FROM runs r JOIN agents a ON a.id = r.agent_id
            WHERE NOT a.is_test),
          (SELECT count(*) FROM events e LEFT JOIN agents a ON a.id = e.agent_id
            WHERE a.is_test IS NOT TRUE),
          (SELECT count(*) FROM commitments c JOIN agents a ON a.id = c.agent_id
            WHERE NOT a.is_test),
          (SELECT count(*) FROM legs x JOIN commitments c ON c.id = x.commitment_id
            JOIN agents a ON a.id = c.agent_id WHERE NOT a.is_test),
          (SELECT count(*) FROM resolutions x JOIN commitments c ON c.id = x.commitment_id
            JOIN agents a ON a.id = c.agent_id WHERE NOT a.is_test),
          (SELECT count(*) FROM resolution_attempts x
            JOIN commitments c ON c.id = x.commitment_id
            JOIN agents a ON a.id = c.agent_id WHERE NOT a.is_test),
          (SELECT count(*) FROM closing_snapshots x
            JOIN commitments c ON c.id = x.commitment_id
            JOIN agents a ON a.id = c.agent_id WHERE NOT a.is_test),
          (SELECT count(*) FROM commitment_factors x
            JOIN commitments c ON c.id = x.commitment_id
            JOIN agents a ON a.id = c.agent_id WHERE NOT a.is_test),
          (SELECT count(*) FROM selections x JOIN commitments c ON c.id = x.commitment_id
            JOIN agents a ON a.id = c.agent_id WHERE NOT a.is_test),
          (SELECT count(*) FROM briefs)
    """
    NON_TEST_TABLES = (
        "runs", "events", "commitments", "legs", "resolutions",
        "resolution_attempts", "closing_snapshots", "commitment_factors",
        "selections", "briefs",
    )

    def setUp(self) -> None:
        #: Commitment ids this test wrote, checked for due-set leaks on the way out.
        self._created: list[int] = []
        self._non_test_before = self._non_test_rows()
        self.run_id = ledger.start_run(self.agent_id, notes="tests_live")
        self.addCleanup(self._close_run)
        self.addCleanup(self._assert_quarantined)

    def _non_test_rows(self) -> dict[str, int]:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(self.NON_TEST_ROWS)
            return dict(zip(self.NON_TEST_TABLES, cur.fetchone()))

    def _close_run(self) -> None:
        try:
            ledger.end_run(self.run_id, "ok", notes="tests_live")
        except Exception:
            pass

    def _assert_quarantined(self) -> None:
        """Nothing this test wrote may belong to an agent in the track record.

        Two halves. `_test` must still carry its flag, or everything it wrote
        just joined the track record. And the count of non-test rows in every
        ledger table must be unchanged, which is the direct check: a test that
        wrote as a real agent, or wrote a brief, moves a number here.

        The count is global, so a real agent writing concurrently would trip it.
        None has ever written (see CLAUDE.md, Current State); once one runs, a
        failure here names the table, and a production write shows up as rows
        belonging to that agent rather than to this test's run.
        """
        self.assertTrue(
            ledger.agent_is_test(TEST_SLUG),
            f"{TEST_SLUG} lost its is_test flag mid-run",
        )
        after = self._non_test_rows()
        changed = {
            table: (self._non_test_before[table], after[table])
            for table in self.NON_TEST_TABLES
            if after[table] != self._non_test_before[table]
        }
        self.assertEqual(
            changed, {},
            f"non-test rows changed during this test (table: before, after): "
            f"{changed}",
        )
        self._assert_left_nothing_due()

    def _assert_left_nothing_due(self) -> None:
        """No fixture may be left sitting in either sweep's due set.

        This is what keeps the suite's growth bounded where it matters. Rows are
        permanent, so archive growth is unavoidable and fine — but an unresolved
        commitment whose deadline has passed is returned by `due_for_resolution`
        FOREVER, because no scheduled agent will ever resolve `_test`. Leak one
        per run and the global due-set query accumulates corpses without limit.

        Runs after the seals (cleanups are LIFO and seals are registered during
        the test), so a properly sealed fixture passes and a new test that
        invents a due fixture some other way fails here rather than silently
        adding to the backlog.
        """
        if not self._created:
            return
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id FROM commitments c
                LEFT JOIN resolutions r ON r.commitment_id = c.id
                WHERE c.id = ANY(%s) AND r.id IS NULL
                  AND c.resolves_after <= now()
                """,
                (self._created,),
            )
            unresolved = [r[0] for r in cur.fetchall()]
            cur.execute(
                """
                SELECT c.id FROM commitments c
                LEFT JOIN closing_snapshots s ON s.commitment_id = c.id
                WHERE c.id = ANY(%s) AND s.id IS NULL
                  AND c.closes_at IS NOT NULL AND c.closes_at <= now()
                """,
                (self._created,),
            )
            uncaptured = [r[0] for r in cur.fetchall()]
        self.assertEqual(
            (unresolved, uncaptured), ([], []),
            f"left commitments in a due set forever — unresolved={unresolved}, "
            f"uncaptured={uncaptured}. Seal them (see support.seal) or park them "
            f"with due=False.",
        )

    # -- fixtures -----------------------------------------------------------
    #
    # Two kinds, and the difference is about growth rather than convenience.
    #
    # Rows here are permanent. Disk is not the concern — what would actually
    # degrade is the DUE SET: an unresolved commitment whose `resolves_after`
    # has passed is returned by `due_for_resolution` forever, because no
    # scheduled agent will ever resolve `_test`. Run the suite a thousand times
    # and the global due-set query drags a thousand corpses.
    #
    # So a fixture that does not need to BE due gets `resolves_after` a century
    # out. It is permanently invisible to both sweeps, needs no cleanup, and
    # cannot clog anything. Only the few tests that genuinely exercise a sweep
    # use `due=True`, and those are sealed on the way out.

    FAR_FUTURE = timedelta(days=365 * 100)

    def commitment(
        self,
        *,
        due: bool = False,
        with_close: bool = False,
        kind: str = "event_contract",
        line: str = "0.40",
        thesis: str = "tests_live fixture",
        confidence: Any = None,
        factors: Any = (),
    ) -> ledger.Commitment:
        """Write a `_test` commitment.

        `due=False` (the default) parks `resolves_after` a century out, so the
        row never enters either sweep's due set. Use it for anything that just
        needs a row to exist — which is most immutability and constraint tests.

        `due=True` uses a short horizon and waits it out, then seals the row in
        cleanup so it leaves the due set again. `closes_at` has to sit strictly
        after `committed_at` and at or before `resolves_after` (db/004's CHECK),
        so the close always lands first.

        Deadlines are computed from the SERVER's `now()`, never this machine's.
        The triggers and due-set queries compare against the server clock, and
        a worker clock running ahead of it (measured at ~1.8s on the dev box)
        pushes a local-clock deadline further out than the test believes.
        """
        now = self.server_now()
        if due:
            resolves_after = now + self.HORIZON
            closes_at = now + (self.HORIZON / 2) if with_close else None
        else:
            resolves_after = now + self.FAR_FUTURE
            # Parked means parked for BOTH sweeps. This used to be now + 1 day:
            # invisible when the test ended, then due for capture forever the
            # next day, with nothing to seal it. That leaked the 109 rows that
            # pushed fresh fixtures off due_for_capture's page (2026-10-09).
            closes_at = now + self.FAR_FUTURE - timedelta(days=1) if with_close else None

        committed = ledger.commit(
            agent_id=self.agent_id,
            run_id=self.run_id,
            kind=kind,
            thesis=thesis,
            payload={"invalidation": "0", "fixture": True},
            resolves_after=resolves_after,
            closes_at=closes_at,
            confidence=confidence,
            factors=factors,
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
        self._created.append(committed.id)
        if due:
            self.addCleanup(self.seal, committed.id, with_close)
            self.wait_for_server(resolves_after)
        return committed

    def server_now(self) -> datetime:
        return self.scalar("SELECT now()")

    def wait_for_server(self, deadline: datetime, timeout: timedelta | None = None) -> None:
        """Block until the SERVER confirms `now() >= deadline`.

        The exit condition is the server's own answer, not an elapsed duration,
        so no amount of clock skew between here and Postgres can end the wait
        early. The short pause between polls only paces the queries; it has no
        bearing on correctness.
        """
        give_up = time.monotonic() + (timeout or self.SERVER_WAIT_TIMEOUT).total_seconds()
        while not self.scalar("SELECT now() >= %s", deadline):
            if time.monotonic() > give_up:
                self.fail(
                    f"server clock never reached {deadline.isoformat()} within "
                    f"{self.SERVER_WAIT_TIMEOUT}"
                )
            time.sleep(0.1)

    def commit_due(self, **kwargs: Any) -> ledger.Commitment:
        """`commitment(due=True)`. Kept for readability at call sites."""
        return self.commitment(due=True, **kwargs)

    def commit_many_due(self, n: int, *, with_close: bool = True) -> list[ledger.Commitment]:
        """`n` due fixtures sharing one deadline, with ONE wait, all sealed in
        cleanup. commit_due() n times would wait n x HORIZON."""
        # Every row's close must land strictly after ITS commit (db/004's
        # closes_between_commit_and_resolve), so the shared close is budgeted
        # past the time it takes to write them all: 0.5 s a row. A first
        # version gave the close a flat 2 s, and row 17 of 70 was committed
        # after it.
        #
        # Each row gets its OWN deadline, a millisecond apart, oldest first:
        # real rows do not share a deadline, and with ties the old
        # deadline-only ORDER BY broke them arbitrarily, which can look fair
        # by accident.
        now = self.server_now()
        closes_base = now + self.HORIZON + timedelta(seconds=n * 0.5)
        resolves_after = closes_base + timedelta(seconds=1)
        made = []
        try:
            for i in range(n):
                step = timedelta(milliseconds=i)
                committed = ledger.commit(
                    agent_id=self.agent_id, run_id=self.run_id, kind="event_contract",
                    thesis=f"tests_live sweep-order fixture {i}",
                    payload={"invalidation": "0", "fixture": True},
                    resolves_after=resolves_after + step,
                    closes_at=closes_base + step if with_close else None,
                    legs=[ledger.Leg(subject="TEST-MKT", market="event_contract",
                                     line=Decimal("0.40"), direction="yes", size=Decimal("1"))],
                )
                self._created.append(committed.id)
                self.addCleanup(self.seal, committed.id, with_close)
                made.append(committed)
        finally:
            # Even if a commit failed midway: the seals of the rows already
            # written can only run once they are due. Waiting here means they
            # do, instead of leaking into both due sets for good. The deadline
            # is deliberately far (0.5 s a row), so the wait is sized to it.
            last = resolves_after + timedelta(milliseconds=n)
            self.wait_for_server(last, timeout=last - now + self.SERVER_WAIT_TIMEOUT)
        return made

    def due_ids(self, sweep: str) -> list[int]:
        """Every `_test` commitment in a sweep's due set right now."""
        pred = (
            "c.closes_at IS NOT NULL AND c.closes_at <= now() AND NOT EXISTS "
            "(SELECT 1 FROM closing_snapshots s WHERE s.commitment_id = c.id)"
            if sweep == "capture" else
            "c.resolves_after <= now() AND NOT EXISTS "
            "(SELECT 1 FROM resolutions r WHERE r.commitment_id = c.id)"
        )
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT c.id FROM commitments c WHERE c.agent_id = %s AND {pred} ORDER BY c.id",
                        (self.agent_id,))
            return [r[0] for r in cur.fetchall()]

    def due_count(self, sweep: str) -> int:
        """How many of `_test`'s rows are in a sweep's due set right now: the
        whole set, so a test can read it in one page however many permanent
        `_test` rows have built up. Same predicates as the ledger queries."""
        if sweep == "capture":
            sql = """SELECT count(*) FROM commitments c
                      WHERE c.agent_id = %s AND c.closes_at IS NOT NULL AND c.closes_at <= now()
                        AND NOT EXISTS (SELECT 1 FROM closing_snapshots s WHERE s.commitment_id = c.id)"""
        else:
            sql = """SELECT count(*) FROM commitments c
                      WHERE c.agent_id = %s AND c.resolves_after <= now()
                        AND NOT EXISTS (SELECT 1 FROM resolutions r WHERE r.commitment_id = c.id)"""
        return int(self.scalar(sql, self.agent_id))

    def seal(self, commitment_id: int, with_close: bool = False) -> None:
        """Take a due fixture back out of the sweeps' due sets.

        Writes a void resolution — and a missed snapshot when the fixture had a
        close — so the row is finished rather than forever pending. This is the
        only cleanup the append-only rules permit, and it is enough: the due set
        stays bounded no matter how often the suite runs, and what accumulates
        is finished rows, which is exactly what the real agents accumulate too.

        Tolerates exactly one thing: a fixture the test already finished. A
        resolved commitment refuses a second resolution in one of two ways —
        `legs_frozen` ("already resolved") when the first wrote leg outcomes,
        the UNIQUE on `resolutions.commitment_id` when it did not — and a
        captured one refuses a second snapshot by UNIQUE. Anything else
        propagates. Swallowing every exception here once hid a "not due yet"
        refusal and left 32 fixtures in the due set for good.
        """
        try:
            ledger.add_resolution(
                commitment_id=commitment_id,
                outcome="void",
                leg_outcomes=[ledger.LegOutcome(0, "void", None)],
                pnl=None,
                detail={"sealed_by": "tests_live", "abandoned": True},
            )
        except psycopg.errors.UniqueViolation:
            pass
        except psycopg.errors.RaiseException as exc:
            if "already resolved" not in str(exc):
                raise
        if with_close:
            try:
                ledger.add_closing_snapshot(
                    commitment_id=commitment_id,
                    status="missed",
                    reason="tests_live fixture sealed, never captured",
                )
            except psycopg.errors.UniqueViolation:
                pass

    def find(
        self, rows: list[ledger.PendingCommitment], commitment_id: int
    ) -> ledger.PendingCommitment:
        matching = [r for r in rows if r.id == commitment_id]
        self.assertEqual(
            len(matching), 1,
            f"commitment {commitment_id} appeared {len(matching)} times",
        )
        return matching[0]

    # -- attempting what the application cannot express ---------------------

    def refuses(
        self,
        sql: str,
        *params: Any,
        error: type[Exception] = psycopg.errors.RaiseException,
        containing: str = "",
    ) -> Exception:
        """Assert the DATABASE refuses a statement, and return the exception.

        RAW SQL, DELIBERATELY. CLAUDE.md §6 puts all database access behind
        `core/ledger.py`, and that rule is about production code: if `ledger.py`
        exposed `update_commitment()` the guarantee under test would already be
        broken. A test that proves the database refuses an illegal write has to
        be able to issue one, and the only way to express `UPDATE commitments`
        is to write it out. Nothing here is a path production code can reach.

        Each attempt gets its own connection, because a failed statement aborts
        its transaction and would poison anything sharing it.
        """
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            try:
                cur.execute(sql, params or None)
            except error as exc:
                conn.rollback()
                if containing:
                    self.assertIn(containing.lower(), str(exc).lower())
                return exc
            except Exception as exc:  # wrong error type — report which
                conn.rollback()
                self.fail(
                    f"expected {error.__name__}, got {type(exc).__name__}: {exc}"
                )
            conn.rollback()
            self.fail(f"the database ACCEPTED a statement it must refuse:\n  {sql}")

    def allows(self, sql: str, *params: Any) -> None:
        """Assert a statement is accepted, then roll it back.

        The mirror of `refuses`. A guard that rejects everything is not a guard,
        so several tests below prove the legal neighbour of an illegal write
        still goes through.
        """
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            try:
                cur.execute(sql, params or None)
            except Exception as exc:
                conn.rollback()
                self.fail(f"the database refused a LEGAL statement: {exc}")
            conn.rollback()

    def returning(self, sql: str, *params: Any) -> Any:
        """Run a write, read its RETURNING value, then roll it back.

        For asserting what the database DID to a value on the way in — a
        trigger-assigned column, a default — without leaving the row behind.
        """
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(sql, params or None)
            row = cur.fetchone()
            value = None if row is None else row[0]
            conn.rollback()
            return value

    def scalar(self, sql: str, *params: Any) -> Any:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(sql, params or None)
            row = cur.fetchone()
            return None if row is None else row[0]


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
