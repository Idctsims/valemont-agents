"""db/018: valemont_readonly reads everything and writes nothing.

Skips until db/018 is pasted, the owner has enabled the role's login, and
DATABASE_URL_READONLY is set. Every write attempt is rolled back, and is
expected to be refused before it gets that far.
"""

from __future__ import annotations

import os
import unittest

import psycopg

from .support import TEST_SLUG, tearDownModule  # noqa: F401

RO = "DATABASE_URL_READONLY"


def _count(url: str, table: str) -> int:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        n = cur.fetchone()[0]
        conn.rollback()
        return n


class ReadonlyRole(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv(RO):
            raise unittest.SkipTest(f"{RO} not set (db/018 + ALTER ROLE ... LOGIN)")
        try:
            with psycopg.connect(os.environ[RO]) as conn:
                conn.rollback()
        except Exception as exc:
            raise unittest.SkipTest(f"{RO} cannot connect: {type(exc).__name__}")
        cls.url = os.environ[RO]

    def refused(self, sql: str) -> None:
        with psycopg.connect(self.url) as conn, conn.cursor() as cur:
            try:
                cur.execute(sql)
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                return
            conn.rollback()
            self.fail(f"valemont_readonly was ALLOWED: {sql}")

    def test_it_connects_as_the_readonly_role(self) -> None:
        with psycopg.connect(self.url) as conn, conn.cursor() as cur:
            cur.execute("SELECT current_user")
            self.assertEqual(cur.fetchone()[0], "valemont_readonly")

    def test_it_sees_the_same_rows_as_the_worker(self) -> None:
        # RLS is on with no policies: without BYPASSRLS this would be 0 vs N.
        for table in ("agents", "runs", "commitments"):
            with self.subTest(table=table):
                self.assertEqual(_count(self.url, table),
                                 _count(os.environ["DATABASE_URL"], table))
        self.assertGreater(_count(self.url, "agents"), 0)

    def test_insert_as_test_agent_is_refused(self) -> None:
        self.refused(f"INSERT INTO runs (agent_id) SELECT id FROM agents WHERE slug = '{TEST_SLUG}'")

    def test_update_and_delete_are_refused(self) -> None:
        self.refused("UPDATE agents SET display_name = display_name WHERE slug = '_test'")
        self.refused("DELETE FROM events WHERE false")

    def test_ddl_is_refused(self) -> None:
        self.refused("CREATE TABLE public.readonly_probe (id int)")


if __name__ == "__main__":
    unittest.main()
