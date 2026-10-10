"""db/026 capital: the append-only guards, the sign rules, capital_value_as_of
at past instants, snapshot idempotency (the worker's exact SQL), paper and
live never mixed in v_capital_today, RLS, and the seed's shape.

Every test runs inside ONE transaction that is always rolled back: these are
money tables and append-only, so a committed test row could never be removed
and would sit in the owner's real bankroll. Nothing is committed.

Assertions are relative to what is already there. Past instants use 2020
(no real entry is that old), and the "today" checks use LIVE mode, which has
no real rows (CLAUDE.md §1: nothing is live). Rows dated in the past are
inserted as the worker's role, which may set created_at; the web's role may
not (column grants), and a test below proves that.

Skips until db/026 is pasted.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator

import psycopg

from core.ledger import CAPITAL_SNAPSHOT_SQL
from core.paths import load_env

load_env()

D = Decimal
JAN = datetime(2020, 1, 1, 18, 0, tzinfo=timezone.utc)   # noon in Chicago


@contextmanager
def _rolled_back() -> Iterator[tuple[psycopg.Connection, psycopg.Cursor]]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        try:
            yield conn, cur
        finally:
            conn.rollback()


def _act_as(cur: psycopg.Cursor, role: str, sub: uuid.UUID | str | None = None) -> None:
    claims = {"role": role} | ({"sub": str(sub)} if sub else {})
    cur.execute("SELECT set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(sub) if sub else "",))
    cur.execute(f"SET LOCAL ROLE {role}")


class CapitalTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            with _rolled_back() as (_, cur):
                cur.execute("SELECT to_regclass('public.bankroll_entries')")
                if cur.fetchone()[0] is None:
                    raise unittest.SkipTest("db/026 not pasted")
                cur.execute("SELECT owner_id FROM app_settings")
                row = cur.fetchone()
        except unittest.SkipTest:
            raise
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")
        if row is None:
            raise unittest.SkipTest("app_settings is empty")
        cls.owner = row[0]

    def entry(self, cur: psycopg.Cursor, kind: str, amount: str, *, mode: str = "paper",
              at: datetime | None = None, note: str = "tests_live") -> int:
        cur.execute(
            """
            INSERT INTO bankroll_entries (owner_id, mode, kind, amount, note, created_at)
            VALUES (%s, %s, %s, %s, %s, coalesce(%s, now())) RETURNING id
            """,
            (self.owner, mode, kind, D(amount), note, at),
        )
        return cur.fetchone()[0]

    def value(self, cur: psycopg.Cursor, mode: str, at: datetime) -> Decimal:
        cur.execute("SELECT source, value FROM capital_value_as_of(%s, %s)", (mode, at))
        rows = dict(cur.fetchall())
        self.assertEqual(set(rows), {"bankroll"}, "one source today: bankroll")
        return rows["bankroll"]

    def today(self, cur: psycopg.Cursor) -> date:
        cur.execute("SELECT goal_local_today()")
        return cur.fetchone()[0]

    def view(self, cur: psycopg.Cursor) -> dict[tuple[str, bool, str], tuple]:
        cur.execute("SELECT mode, is_total, source, value, prior_date, prior_value, change FROM v_capital_today")
        return {(r[0], r[1], r[2]): r[3:] for r in cur.fetchall()}

    def refused(self, conn: psycopg.Connection, cur: psycopg.Cursor, sql: str, args=(),
                containing: str | None = None) -> None:
        with self.assertRaises(psycopg.Error) as caught:
            with conn.transaction():   # a savepoint: the outer transaction survives
                cur.execute(sql, args)
        if containing:
            self.assertIn(containing, str(caught.exception))


# ------------------------------------------------------------- append-only

class AppendOnly(CapitalTestCase):
    def test_update_delete_and_truncate_are_refused_on_all_three(self) -> None:
        with _rolled_back() as (conn, cur):
            eid = self.entry(cur, "deposit", "10.00", at=JAN)
            cur.execute("INSERT INTO crypto_holdings (owner_id, asset, qty) VALUES (%s, 'BTC', 1) RETURNING id",
                        (self.owner,))
            hid = cur.fetchone()[0]
            cur.execute("INSERT INTO capital_snapshots (owner_id, snap_date, source, value) "
                        "VALUES (%s, '2020-01-01', 'tests_live', 1) RETURNING id", (self.owner,))
            sid = cur.fetchone()[0]
            for table, rid, column in (("bankroll_entries", eid, "amount = 99"),
                                       ("crypto_holdings", hid, "qty = 99"),
                                       ("capital_snapshots", sid, "value = 99")):
                with self.subTest(table=table):
                    self.refused(conn, cur, f"UPDATE {table} SET {column} WHERE id = %s", (rid,), "append-only")
                    self.refused(conn, cur, f"DELETE FROM {table} WHERE id = %s", (rid,), "append-only")
                    self.refused(conn, cur, f"TRUNCATE {table}", containing="TRUNCATE is refused")


# -------------------------------------------------------------- sign rules

class SignRules(CapitalTestCase):
    def test_deposits_and_withdrawals_are_positive_adjustments_nonzero(self) -> None:
        with _rolled_back() as (conn, cur):
            for kind, amount in (("deposit", "0"), ("deposit", "-5"), ("withdrawal", "0"),
                                 ("withdrawal", "-5"), ("adjustment", "0")):
                with self.subTest(kind=kind, amount=amount):
                    self.refused(conn, cur,
                                 "INSERT INTO bankroll_entries (owner_id, kind, amount) VALUES (%s, %s, %s)",
                                 (self.owner, kind, D(amount)), "bankroll_entries_amount_sign")
            for kind, amount in (("deposit", "5"), ("withdrawal", "5"), ("adjustment", "5"), ("adjustment", "-5")):
                self.entry(cur, kind, amount, at=JAN)

    def test_mode_is_paper_or_live_and_defaults_to_paper(self) -> None:
        with _rolled_back() as (conn, cur):
            self.refused(conn, cur, "INSERT INTO bankroll_entries (owner_id, mode, kind, amount) "
                                    "VALUES (%s, 'blended', 'deposit', 1)", (self.owner,), "mode_check")
            cur.execute("INSERT INTO bankroll_entries (owner_id, kind, amount, created_at) "
                        "VALUES (%s, 'deposit', 1, %s) RETURNING mode", (self.owner, JAN))
            self.assertEqual(cur.fetchone()[0], "paper")


# ------------------------------------------------------ value as of a past instant

class ValueAsOf(CapitalTestCase):
    def test_the_signed_sum_at_each_past_instant(self) -> None:
        with _rolled_back() as (_, cur):
            t0 = JAN
            base = self.value(cur, "paper", t0 + timedelta(days=30))   # real entries are newer: 0
            self.entry(cur, "deposit", "500.00", at=t0)
            self.entry(cur, "withdrawal", "120.00", at=t0 + timedelta(days=1))
            self.entry(cur, "adjustment", "-30.50", at=t0 + timedelta(days=2))
            self.entry(cur, "adjustment", "10.00", at=t0 + timedelta(days=3))
            self.assertEqual(self.value(cur, "paper", t0 - timedelta(seconds=1)), base)
            self.assertEqual(self.value(cur, "paper", t0), base + D("500.00"))          # <= at, inclusive
            self.assertEqual(self.value(cur, "paper", t0 + timedelta(days=1)), base + D("380.00"))
            self.assertEqual(self.value(cur, "paper", t0 + timedelta(days=2, hours=1)), base + D("349.50"))
            self.assertEqual(self.value(cur, "paper", t0 + timedelta(days=30)), base + D("359.50"))

    def test_modes_are_separate(self) -> None:
        with _rolled_back() as (_, cur):
            later = JAN + timedelta(days=5)
            paper, live = self.value(cur, "paper", later), self.value(cur, "live", later)
            self.entry(cur, "deposit", "70.00", mode="live", at=JAN)
            self.assertEqual(self.value(cur, "paper", later), paper)
            self.assertEqual(self.value(cur, "live", later), live + D("70.00"))

    def test_an_unknown_mode_is_refused(self) -> None:
        with _rolled_back() as (conn, cur):
            self.refused(conn, cur, "SELECT * FROM capital_value_as_of('blended', now())", containing="paper or live")

    def test_day_end_is_the_last_instant_of_the_chicago_day(self) -> None:
        with _rolled_back() as (_, cur):
            cur.execute("SELECT capital_day_end('2020-01-01')")
            self.assertEqual(cur.fetchone()[0],
                             datetime(2020, 1, 2, 5, 59, 59, 999999, tzinfo=timezone.utc))  # CST, UTC-6


# ------------------------------------------------------------- snapshots

class Snapshots(CapitalTestCase):
    def snap(self, cur: psycopg.Cursor, mode: str, first: date, last: date) -> int:
        cur.execute(CAPITAL_SNAPSHOT_SQL, {"mode": mode, "first": first, "last": last})
        return cur.rowcount

    def test_idempotent_and_valued_at_each_day_end(self) -> None:
        with _rolled_back() as (_, cur):
            day1, day3 = date(2020, 1, 1), date(2020, 1, 3)
            # Live: no real rows, and none of these dates has a snapshot.
            self.entry(cur, "deposit", "100.00", mode="live", at=JAN)                     # Jan 1, noon
            self.entry(cur, "deposit", "25.00", mode="live",
                       at=datetime(2020, 1, 3, 5, 30, tzinfo=timezone.utc))               # Jan 2, 23:30 local
            self.assertEqual(self.snap(cur, "live", day1, day3), 3)
            self.assertEqual(self.snap(cur, "live", day1, day3), 0)                        # again: nothing
            cur.execute("SELECT snap_date, source, value, owner_id FROM capital_snapshots "
                        "WHERE mode = 'live' AND snap_date BETWEEN %s AND %s ORDER BY snap_date", (day1, day3))
            rows = cur.fetchall()
            self.assertEqual([(r[0], r[1], r[2]) for r in rows], [
                (date(2020, 1, 1), "bankroll", D("100.00")),
                (date(2020, 1, 2), "bankroll", D("125.00")),
                (date(2020, 1, 3), "bankroll", D("125.00")),
            ])
            self.assertTrue(all(r[3] == self.owner for r in rows))

    def test_a_day_that_has_not_ended_is_refused(self) -> None:
        with _rolled_back() as (conn, cur):
            today = self.today(cur)
            self.entry(cur, "deposit", "1.00", mode="live")
            self.refused(conn, cur, CAPITAL_SNAPSHOT_SQL, {"mode": "live", "first": today, "last": today},
                         "has not ended")
            self.refused(conn, cur, "INSERT INTO capital_snapshots (owner_id, snap_date, source, value) "
                                    "VALUES (%s, %s, 'bankroll', 1)", (self.owner, today + timedelta(days=1)),
                         "has not ended")


# ---------------------------------------------------------- the view, per mode

class TodayView(CapitalTestCase):
    def test_live_appears_separately_and_paper_is_untouched(self) -> None:
        with _rolled_back() as (_, cur):
            before = self.view(cur)
            self.assertFalse([k for k in before if k[0] == "live"], "no real live rows exist today")
            yesterday = self.today(cur) - timedelta(days=1)
            three_days = datetime.now(timezone.utc) - timedelta(days=3)
            self.entry(cur, "deposit", "100.00", mode="live", at=three_days)
            cur.execute(CAPITAL_SNAPSHOT_SQL, {"mode": "live", "first": yesterday, "last": yesterday})
            self.entry(cur, "deposit", "50.00", mode="live")

            after = self.view(cur)
            for key, row in before.items():
                self.assertEqual(after[key], row, f"{key} changed when a live entry was added")
            self.assertEqual(after[("live", False, "bankroll")],
                             (D("150.00"), yesterday, D("100.00"), D("50.00")))
            self.assertEqual(after[("live", True, "total")],
                             (D("150.00"), yesterday, D("100.00"), D("50.00")))
            self.assertEqual({k[0] for k in after}, {"paper", "live"})
            self.assertTrue(all(k[0] in ("paper", "live") for k in after), "no row spans modes")

    def test_no_prior_snapshot_means_no_change(self) -> None:
        with _rolled_back() as (_, cur):
            self.entry(cur, "deposit", "40.00", mode="live")
            row = self.view(cur)[("live", True, "total")]
            self.assertEqual(row[0], D("40.00"))
            self.assertEqual(row[1:], (None, None, None))


# -------------------------------------------------------------------- RLS

class Rls(CapitalTestCase):
    def test_the_owner_reads_and_appends_but_never_edits_or_backdates(self) -> None:
        with _rolled_back() as (conn, cur):
            _act_as(cur, "authenticated", self.owner)
            cur.execute("INSERT INTO bankroll_entries (mode, kind, amount, note) "
                        "VALUES ('paper', 'deposit', 5, 'tests_live rls') RETURNING id, owner_id")
            eid, owner = cur.fetchone()
            self.assertEqual(owner, self.owner)
            cur.execute("SELECT count(*) FROM bankroll_entries WHERE id = %s", (eid,))
            self.assertEqual(cur.fetchone()[0], 1)
            self.refused(conn, cur, "INSERT INTO bankroll_entries (kind, amount, created_at) "
                                    "VALUES ('deposit', 5, '2020-01-01')", containing="permission denied")
            self.refused(conn, cur, "UPDATE bankroll_entries SET amount = 6 WHERE id = %s", (eid,),
                         "permission denied")
            self.refused(conn, cur, "DELETE FROM bankroll_entries WHERE id = %s", (eid,), "permission denied")
            self.refused(conn, cur, "INSERT INTO capital_snapshots (owner_id, snap_date, source, value) "
                                    "VALUES (%s, '2020-01-01', 'bankroll', 1)", (self.owner,), "permission denied")
            cur.execute("SELECT count(*) FROM v_capital_today WHERE mode = 'paper' AND is_total")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_a_stranger_sees_nothing(self) -> None:
        with _rolled_back() as (_, cur):
            _act_as(cur, "authenticated", uuid.uuid4())
            for table in ("bankroll_entries", "crypto_holdings", "capital_snapshots", "v_capital_today"):
                cur.execute(f"SELECT count(*) FROM {table}")
                self.assertEqual(cur.fetchone()[0], 0, table)

    def test_anon_has_no_access(self) -> None:
        for table in ("bankroll_entries", "crypto_holdings", "capital_snapshots", "v_capital_today"):
            with self.subTest(table=table), _rolled_back() as (conn, cur):
                _act_as(cur, "anon")
                self.refused(conn, cur, f"SELECT 1 FROM {table}", containing="permission denied")


# --------------------------------------------------------------- the seed

class Seed(CapitalTestCase):
    def test_the_first_entry_is_the_paper_seed(self) -> None:
        with _rolled_back() as (_, cur):
            cur.execute("SELECT owner_id, mode, kind, amount, note FROM bankroll_entries ORDER BY id LIMIT 1")
            self.assertEqual(cur.fetchone(), (self.owner, "paper", "deposit", D("1000.00"), "Paper bankroll seed"))
            cur.execute("SELECT count(*) FROM bankroll_entries WHERE note = 'Paper bankroll seed'")
            self.assertEqual(cur.fetchone()[0], 1)

# ------------------------------------------------- db/027: test entries

class TestEntriesTestCase(CapitalTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as (_, cur):
            cur.execute("SELECT to_regclass('public.e2e_markers')")
            if cur.fetchone()[0] is None:
                raise unittest.SkipTest("db/027 not pasted")

    def flagged(self, cur: psycopg.Cursor, kind: str, amount: str, *, mode: str = "paper",
                   at: datetime | None = None) -> int:
        cur.execute(
            """
            INSERT INTO bankroll_entries (owner_id, mode, kind, amount, note, created_at, is_test)
            VALUES (%s, %s, %s, %s, 'tests_live is_test', coalesce(%s, now()), true) RETURNING id
            """,
            (self.owner, mode, kind, D(amount), at),
        )
        return cur.fetchone()[0]

    def own_marker(self, cur: psycopg.Cursor) -> str:
        """A marker only this transaction knows: its hash becomes the newest
        e2e_markers row until the rollback."""
        marker = f"tests_live-{uuid.uuid4()}"
        cur.execute("INSERT INTO e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(%s, 'UTF8')), 'hex'))",
                    (marker,))
        return marker


class TestEntriesExcluded(TestEntriesTestCase):
    def test_value_as_of_ignores_them(self) -> None:
        with _rolled_back() as (_, cur):
            later = JAN + timedelta(days=5)
            before = self.value(cur, "paper", later), self.value(cur, "paper", datetime.now(timezone.utc))
            self.flagged(cur, "deposit", "300.00", at=JAN)
            self.flagged(cur, "adjustment", "-12.00")
            self.assertEqual((self.value(cur, "paper", later),
                              self.value(cur, "paper", datetime.now(timezone.utc))), before)

    def test_a_mode_with_only_test_rows_has_no_data(self) -> None:
        with _rolled_back() as (_, cur):
            self.flagged(cur, "deposit", "50.00", mode="live", at=JAN)
            cur.execute("SELECT capital_first_at('live')")
            self.assertIsNone(cur.fetchone()[0])
            self.assertFalse([k for k in self.view(cur) if k[0] == "live"])

    def test_the_view_is_unchanged_by_them(self) -> None:
        with _rolled_back() as (_, cur):
            before = self.view(cur)
            self.flagged(cur, "deposit", "999.00")
            self.flagged(cur, "withdrawal", "1.00")
            self.assertEqual(self.view(cur), before)

    def test_snapshots_ignore_them(self) -> None:
        with _rolled_back() as (_, cur):
            day1, day2 = date(2020, 1, 1), date(2020, 1, 2)
            self.entry(cur, "deposit", "100.00", mode="live", at=JAN)
            self.flagged(cur, "deposit", "5000.00", mode="live", at=JAN)
            cur.execute(CAPITAL_SNAPSHOT_SQL, {"mode": "live", "first": day1, "last": day2})
            cur.execute("SELECT snap_date, value FROM capital_snapshots WHERE mode = 'live' "
                        "AND snap_date BETWEEN %s AND %s ORDER BY 1", (day1, day2))
            self.assertEqual(cur.fetchall(), [(day1, D("100.00")), (day2, D("100.00"))])

    def test_existing_rows_are_all_real(self) -> None:
        # The flag came with a false default: nothing written before db/027
        # was reclassified (append-only, no exceptions).
        with _rolled_back() as (_, cur):
            cur.execute("SELECT count(*) FROM bankroll_entries WHERE is_test AND note = 'Paper bankroll seed'")
            self.assertEqual(cur.fetchone()[0], 0)


class TestEntriesWriter(TestEntriesTestCase):
    def test_the_web_role_cannot_set_is_test(self) -> None:
        with _rolled_back() as (conn, cur):
            _act_as(cur, "authenticated", self.owner)
            self.refused(conn, cur, "INSERT INTO bankroll_entries (kind, amount, is_test) VALUES ('deposit', 1, true)",
                         containing="permission denied")

    def test_the_marker_function_writes_a_paper_test_row_for_the_owner(self) -> None:
        with _rolled_back() as (_, cur):
            marker = self.own_marker(cur)
            paper_now = self.view(cur)[("paper", True, "total")]
            _act_as(cur, "authenticated", self.owner)
            cur.execute("SELECT add_test_bankroll_entry(%s, 'deposit', 25, 'tests_live marker')", (marker,))
            eid = cur.fetchone()[0]
            cur.execute("SELECT owner_id, mode, is_test, amount FROM bankroll_entries WHERE id = %s", (eid,))
            self.assertEqual(cur.fetchone(), (self.owner, "paper", True, D("25.00")))
            self.assertEqual(self.view(cur)[("paper", True, "total")], paper_now, "a test row moves no number")

    def test_absent_or_wrong_markers_are_refused(self) -> None:
        with _rolled_back() as (conn, cur):
            marker = self.own_marker(cur)
            _act_as(cur, "authenticated", self.owner)
            for bad in (None, "", "wrong", marker + "x"):
                with self.subTest(marker=bad):
                    self.refused(conn, cur, "SELECT add_test_bankroll_entry(%s, 'deposit', 1, 'x')", (bad,),
                                 "test marker refused")

    def test_a_stranger_is_refused_even_with_the_marker(self) -> None:
        with _rolled_back() as (conn, cur):
            marker = self.own_marker(cur)
            _act_as(cur, "authenticated", uuid.uuid4())
            self.refused(conn, cur, "SELECT add_test_bankroll_entry(%s, 'deposit', 1, 'x')", (marker,), "owner only")

    def test_anon_cannot_call_it_or_read_the_hashes(self) -> None:
        with _rolled_back() as (conn, cur):
            _act_as(cur, "anon")
            self.refused(conn, cur, "SELECT add_test_bankroll_entry('x', 'deposit', 1, 'x')",
                         containing="permission denied")
            self.refused(conn, cur, "SELECT 1 FROM e2e_markers", containing="permission denied")

    def test_the_owner_cannot_read_the_hashes(self) -> None:
        with _rolled_back() as (conn, cur):
            _act_as(cur, "authenticated", self.owner)
            self.refused(conn, cur, "SELECT 1 FROM e2e_markers", containing="permission denied")

    def test_the_marker_hashes_are_append_only(self) -> None:
        with _rolled_back() as (conn, cur):
            self.refused(conn, cur, "UPDATE e2e_markers SET marker_sha256 = repeat('0', 64)", containing="append-only")
            self.refused(conn, cur, "DELETE FROM e2e_markers", containing="append-only")
            self.refused(conn, cur, "TRUNCATE e2e_markers", containing="TRUNCATE is refused")


if __name__ == "__main__":
    unittest.main()
