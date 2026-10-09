"""No due row can be starved past sweep_limit.

Each sweep reads one page of `sweep_limit` rows. Ordered by deadline alone, a
due set bigger than the page kept handing back the same oldest rows while
they deferred, and the rest were never asked. Past `max_overdue` core then
voided, or tombstoned the close of, rows it had never once tried: permanent
loss from bookkeeping (CLAUDE.md §8).

The order is now: never-attempted first, then oldest last attempt, then
oldest deadline. This drives the REAL sweeps (BaseAgent.capture_due and
resolve_due: real SQL, real ordering, real expiry) with an agent that always
defers, over sweep_limit + 20 fresh rows plus whatever `_test` rows are
already due. However many there are, every row due at the start must be
attempted within ceil(n / limit) sweeps. And none may be given up on at zero
attempts (the first-look invariant, here on real rows).

Red then green: on 2026-10-09 this failed against the old ORDER BY (the fresh
rows were never reached within the bound) and passes with the new one.

Rows written are `_test` rows; the fresh ones are sealed afterwards. Already
due `_test` rows get attempts recorded here too, and old ones may be
tombstoned or voided by core's normal expiry once they have an attempt on
record. That is the sweeps doing their job on quarantined rows; nothing is
deleted.
"""

from __future__ import annotations

import math
import os

import psycopg

from core.agent import BaseAgent
from core.ledger import PendingCommitment

from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401

EXTRA = 20


class Deferring(BaseAgent[None, None]):
    """`_test`, asked about everything, answering nothing yet."""

    slug = "_test"
    captures_close = True

    def observe(self) -> None:
        return None

    def form_thesis(self, observation: None) -> None:
        return None

    def build_commitment(self, thesis: None) -> None:
        return None

    def resolve(self, pending: PendingCommitment) -> None:
        return None

    def capture_close(self, pending: PendingCommitment) -> None:
        return None


class SweepOrder(LiveLedgerTestCase):
    def unattempted(self, ids: list[int], purpose: str) -> list[int]:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(
                # u.cid, qualified: a bare `id` inside the subquery binds to
                # resolution_attempts.id, which made the first draft of this
                # check vacuous (it passed against the old ORDER BY).
                """SELECT u.cid FROM unnest(%s::bigint[]) AS u(cid)
                    WHERE NOT EXISTS (SELECT 1 FROM resolution_attempts ra
                                       WHERE ra.commitment_id = u.cid AND ra.purpose = %s)
                    ORDER BY u.cid""",
                (ids, purpose),
            )
            return [r[0] for r in cur.fetchall()]

    def given_up_unattempted(self, ids: list[int]) -> tuple[int, int]:
        """(missed tombstones, abandonment voids) among `ids` with no attempt
        of the matching purpose on record. The invariant says both are 0."""
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT
                     (SELECT count(*) FROM closing_snapshots s
                       WHERE s.commitment_id = ANY(%(ids)s) AND s.status = 'missed'
                         AND NOT EXISTS (SELECT 1 FROM resolution_attempts ra
                                          WHERE ra.commitment_id = s.commitment_id
                                            AND ra.purpose = 'capture')),
                     (SELECT count(*) FROM resolutions r
                       WHERE r.commitment_id = ANY(%(ids)s) AND r.outcome = 'void'
                         AND (r.detail ->> 'abandoned')::boolean IS TRUE
                         AND r.detail ->> 'sealed_by' IS NULL
                         AND NOT EXISTS (SELECT 1 FROM resolution_attempts ra
                                          WHERE ra.commitment_id = r.commitment_id
                                            AND ra.purpose = 'resolve'))""",
                {"ids": ids},
            )
            return tuple(cur.fetchone())

    def test_every_due_row_is_attempted_within_ceil_n_over_limit_sweeps(self) -> None:
        agent = Deferring()
        limit = agent.sweep_limit
        self.commit_many_due(limit + EXTRA)

        for sweep, run in (("capture", agent.capture_due), ("resolve", agent.resolve_due)):
            with self.subTest(sweep=sweep):
                due = self.due_ids(sweep)
                self.assertGreaterEqual(len(due), limit + EXTRA)
                bound = math.ceil(len(due) / limit)
                for _ in range(bound):
                    run()
                self.assertEqual(
                    self.unattempted(due, sweep), [],
                    f"{sweep}: rows never attempted after {bound} sweeps of {limit} "
                    f"over {len(due)} due: starved",
                )
                self.assertEqual(self.given_up_unattempted(due), (0, 0))
