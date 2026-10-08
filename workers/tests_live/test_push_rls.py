"""db/019: the first RLS policies. The owner reaches only their own rows,
another signed-in user reaches none of them, and anon reaches nothing at all.

Every test runs inside ONE transaction that is always rolled back. These are
app tables, not the ledger: a leftover row here is not quarantined by
`is_test`, it is a live push target the worker would try to deliver to. So
nothing is ever committed, and the `_test` agent is not involved.

PostgREST runs a request as role `authenticated` (or `anon`) with the JWT's
claims in `request.jwt.claims`, which is what `auth.uid()` reads. The tests do
the same from the `postgres` connection: SET LOCAL ROLE plus set_config(...,
true), both scoped to the transaction.

Skips until db/019 is pasted, and when auth.users is empty (the owner policy
needs a real user id for the foreign key).
"""

from __future__ import annotations

import json
import os
import unittest
import uuid
from contextlib import contextmanager
from typing import Iterator

import psycopg

from core.paths import load_env

load_env()

ENDPOINT = "https://push.example.invalid/tests_live/"


@contextmanager
def _rolled_back() -> Iterator[psycopg.Cursor]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        try:
            yield cur
        finally:
            conn.rollback()


def _act_as(cur: psycopg.Cursor, role: str, sub: uuid.UUID | None = None) -> None:
    claims = {"role": role} | ({"sub": str(sub)} if sub else {})
    cur.execute("SELECT set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(sub) if sub else "",))
    cur.execute(f"SET LOCAL ROLE {role}")


class PushRls(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            with _rolled_back() as cur:
                cur.execute("SELECT to_regclass('public.push_subscriptions'), "
                            "to_regclass('public.notifications')")
                if None in cur.fetchone():
                    raise unittest.SkipTest("db/019 not pasted")
                cur.execute("SELECT id FROM auth.users ORDER BY created_at LIMIT 1")
                row = cur.fetchone()
        except unittest.SkipTest:
            raise
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")
        if row is None:
            raise unittest.SkipTest("auth.users is empty")
        cls.owner = row[0]

    def _insert_subscription(self, cur: psycopg.Cursor, owner: uuid.UUID, tag: str) -> None:
        cur.execute(
            "INSERT INTO push_subscriptions (owner_id, endpoint, p256dh, auth, device_label) "
            "VALUES (%s, %s, 'p256dh-test', 'auth-test', 'tests_live')",
            (owner, ENDPOINT + tag),
        )

    # -- the owner -----------------------------------------------------------

    def test_owner_writes_and_reads_their_own_subscription(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            self._insert_subscription(cur, self.owner, "own")
            cur.execute("UPDATE push_subscriptions SET active = false WHERE endpoint = %s",
                        (ENDPOINT + "own",))
            self.assertEqual(cur.rowcount, 1)
            cur.execute("SELECT active FROM push_subscriptions WHERE endpoint = %s",
                        (ENDPOINT + "own",))
            self.assertEqual(cur.fetchall(), [(False,)])
            cur.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (ENDPOINT + "own",))
            self.assertEqual(cur.rowcount, 1)

    def test_owner_logs_a_notification(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            cur.execute(
                "INSERT INTO notifications (owner_id, kind, title, deep_link) "
                "VALUES (%s, 'test', 'tests_live', '/onboarding') RETURNING id",
                (self.owner,),
            )
            nid = cur.fetchone()[0]
            cur.execute("UPDATE notifications SET status = 'sent', sent_at = now() WHERE id = %s",
                        (nid,))
            self.assertEqual(cur.rowcount, 1)

    def test_owner_cannot_write_a_row_for_someone_else(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege) as ctx:
                self._insert_subscription(cur, uuid.uuid4(), "foreign")
            self.assertIn("row-level security", str(ctx.exception))

    # -- another signed-in user ---------------------------------------------

    def test_another_user_sees_none_of_the_owners_rows(self) -> None:
        with _rolled_back() as cur:
            self._insert_subscription(cur, self.owner, "hidden")  # as postgres
            _act_as(cur, "authenticated", uuid.uuid4())
            cur.execute("SELECT count(*) FROM push_subscriptions WHERE endpoint = %s",
                        (ENDPOINT + "hidden",))
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("UPDATE push_subscriptions SET active = false WHERE endpoint = %s",
                        (ENDPOINT + "hidden",))
            self.assertEqual(cur.rowcount, 0)
            cur.execute("DELETE FROM push_subscriptions WHERE endpoint = %s",
                        (ENDPOINT + "hidden",))
            self.assertEqual(cur.rowcount, 0)

    # -- anon ---------------------------------------------------------------

    def test_anon_cannot_read_either_table(self) -> None:
        for table in ("push_subscriptions", "notifications"):
            with self.subTest(table=table), _rolled_back() as cur:
                _act_as(cur, "anon")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(f"SELECT 1 FROM {table} LIMIT 1")

    def test_anon_cannot_write(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "anon")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self._insert_subscription(cur, self.owner, "anon")

    # -- the shape the app relies on ----------------------------------------

    def test_rls_is_on_with_exactly_one_policy_per_table(self) -> None:
        with _rolled_back() as cur:
            cur.execute("""
                SELECT c.relname, c.relrowsecurity,
                       (SELECT array_agg(p.policyname || ':' || array_to_string(p.roles, ','))
                          FROM pg_policies p WHERE p.tablename = c.relname)
                  FROM pg_class c
                 WHERE c.relname IN ('push_subscriptions', 'notifications')
                 ORDER BY c.relname""")
            self.assertEqual(cur.fetchall(), [
                ("notifications", True, ["notifications_owner:authenticated"]),
                ("push_subscriptions", True, ["push_subscriptions_owner:authenticated"]),
            ])

    def test_deep_link_must_be_a_same_origin_path(self) -> None:
        with _rolled_back() as cur:
            with self.assertRaises(psycopg.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO notifications (owner_id, kind, title, deep_link) "
                    "VALUES (%s, 'test', 't', '//evil.example')",
                    (self.owner,),
                )


if __name__ == "__main__":
    unittest.main()
