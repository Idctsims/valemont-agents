"""The SQL For you order against feed/rank.order_for_you, up to exact ties.

Two stories can rank EXACTLY equal in arithmetic: feeds publish on round
times and boosts are whole hours, so a golf story at 11:30 inside a 6 h
window ranks with a 17:30 story of the same score (2026-10-13, ids 4943 and
5425). SQL computes the rank with a numeric ln(2) and age, Python with floats,
so each side breaks such a tie by its own last-bit noise, and no input can
make them agree. What must agree, and does:

  * every story's rank, within RANK_TOL (catches a missing boost or a wrong
    term outright: a 12 h boost is ~0.46);
  * the floored flags, and page one as a set up to tied stories;
  * the order, except that two stories whose SQL ranks are within RANK_TOL
    may swap.

Read REAL columns as float8 in the query that feeds this: a float4 as text
arrives as its shortest decimal, up to ~6e-8 off as a float64.
"""

from __future__ import annotations

import unittest
from typing import Sequence

from feed import rank

RANK_TOL = 1e-9


def assert_same_order(tc: unittest.TestCase, twin: Sequence[rank.Placed],
                      sql: Sequence[tuple[int, int, float, bool]]) -> None:
    """`sql`: (id, pos, rank, floored) in SQL order."""
    tc.assertEqual(sorted(p.id for p in twin), sorted(r[0] for r in sql), "different stories")
    sql_rank = {r[0]: float(r[2]) for r in sql}
    off = [(p.id, p.rank, sql_rank[p.id]) for p in twin if abs(p.rank - sql_rank[p.id]) > RANK_TOL]
    tc.assertEqual(off[:5], [], f"{len(off)} stories rank differently in the twin (id, twin, sql)")
    tc.assertEqual({p.id: p.floored for p in twin}, {r[0]: r[3] for r in sql}, "floored flags")
    tc.assertEqual([r[1] for r in sql], list(range(1, len(sql) + 1)), "SQL positions are 1..n")
    sql_pos = {r[0]: r[1] for r in sql}
    # Every inversion between the two orders must be a tie in the SQL ranks.
    bad = []
    for j, b in enumerate(twin):
        for a in twin[:j]:
            if sql_pos[a.id] > sql_pos[b.id] and abs(sql_rank[a.id] - sql_rank[b.id]) > RANK_TOL:
                bad.append((a.id, b.id, sql_rank[a.id], sql_rank[b.id]))
        if len(bad) >= 5:
            break
    tc.assertEqual(bad, [], "the twin orders untied stories differently (a, b, sql rank a, sql rank b)")
