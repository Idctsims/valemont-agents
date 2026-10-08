"""db/020: the owner reads the ledger and nothing else can; app tables obey
their policies; commitments carry mode/origin; TRUNCATE is refused.

Every attempt runs in a transaction that is rolled back (helpers from
test_push_rls). The one exception is `ModeDefaults`, which writes a `_test`
commitment through the ledger exactly like the rest of tests_live, so it is
quarantined by `is_test` and parked a century out.

Skips until db/020 is pasted.
"""

from __future__ import annotations

import os
import unittest
import uuid

import psycopg

from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401
from .test_push_rls import _act_as, _rolled_back

LEDGER = (
    "commitments", "legs", "resolutions", "selections", "commitment_factors",
    "closing_snapshots", "events", "runs", "agents", "resolution_attempts", "briefs",
)
APPEND_ONLY = LEDGER + (
    "model_versions", "preregistrations", "migration_log",
    "kalshi_markets", "kalshi_candles", "kalshi_trades", "kalshi_settlements", "ai_usage",
)


def _owner() -> uuid.UUID | None:
    with _rolled_back() as cur:
        cur.execute("SELECT to_regclass('public.app_settings') IS NOT NULL")
        if not cur.fetchone()[0]:
            return None
        cur.execute("SELECT owner_id FROM app_settings")
        row = cur.fetchone()
        return row[0] if row else None


class Shared020(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            owner = _owner()
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")
        if owner is None:
            raise unittest.SkipTest("db/020 not pasted")
        cls.owner = owner

    # -- the owner check -------------------------------------------------------

    def test_app_settings_holds_exactly_one_row(self) -> None:
        with _rolled_back() as cur:
            cur.execute("SELECT count(*) FROM app_settings")
            self.assertEqual(cur.fetchone()[0], 1)
            with self.assertRaises(psycopg.errors.UniqueViolation):
                cur.execute("INSERT INTO app_settings (owner_id) SELECT owner_id FROM app_settings")

    def test_is_owner_is_true_only_for_the_owner(self) -> None:
        for sub, expected in ((self.owner, True), (uuid.uuid4(), False)):
            with self.subTest(owner=expected), _rolled_back() as cur:
                _act_as(cur, "authenticated", sub)
                cur.execute("SELECT public.is_owner()")
                self.assertEqual(cur.fetchone()[0], expected)

    def test_owner_cannot_change_the_owner_id_or_delete_settings(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("UPDATE app_settings SET owner_id = owner_id")
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("DELETE FROM app_settings")
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            cur.execute("UPDATE app_settings SET timezone = 'America/New_York'")
            self.assertEqual(cur.rowcount, 1)

    # -- the ledger --------------------------------------------------------------

    def test_owner_reads_every_ledger_table_in_full(self) -> None:
        for table in LEDGER:
            with self.subTest(table=table), _rolled_back() as cur:
                cur.execute(f"SELECT count(*) FROM {table}")
                everything = cur.fetchone()[0]
                _act_as(cur, "authenticated", self.owner)
                cur.execute(f"SELECT count(*) FROM {table}")
                self.assertEqual(cur.fetchone()[0], everything)

    def test_another_user_reads_no_ledger_row(self) -> None:
        for table in LEDGER:
            with self.subTest(table=table), _rolled_back() as cur:
                _act_as(cur, "authenticated", uuid.uuid4())
                cur.execute(f"SELECT count(*) FROM {table}")
                self.assertEqual(cur.fetchone()[0], 0)

    def test_anon_cannot_read_any_public_table(self) -> None:
        with _rolled_back() as cur:
            cur.execute("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = 'public' AND c.relkind = 'r'""")
            tables = [r[0] for r in cur.fetchall()]
        for table in tables:
            with self.subTest(table=table), _rolled_back() as cur:
                _act_as(cur, "anon")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(f"SELECT 1 FROM {table} LIMIT 1")

    def test_owner_cannot_write_any_ledger_table(self) -> None:
        statements = {
            "INSERT": "INSERT INTO {t} DEFAULT VALUES",
            "UPDATE": "UPDATE {t} SET id = id",
            "DELETE": "DELETE FROM {t}",
            "TRUNCATE": "TRUNCATE {t} CASCADE",
        }
        for table in LEDGER:
            for verb, sql in statements.items():
                with self.subTest(table=table, verb=verb), _rolled_back() as cur:
                    _act_as(cur, "authenticated", self.owner)
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        cur.execute(sql.format(t=table))

    # -- the hardening -----------------------------------------------------------

    def test_truncate_is_refused_even_for_postgres(self) -> None:
        for table in APPEND_ONLY:
            with self.subTest(table=table), _rolled_back() as cur:
                with self.assertRaises(psycopg.errors.RaiseException) as ctx:
                    cur.execute(f"TRUNCATE {table} CASCADE")
                self.assertIn("append-only", str(ctx.exception))

    # -- the app tables ----------------------------------------------------------

    def test_owner_enqueues_only_fresh_jobs(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            cur.execute("INSERT INTO job_queue (kind) VALUES ('noop') RETURNING status, attempts")
            self.assertEqual(cur.fetchone(), ("queued", 0))
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("INSERT INTO job_queue (kind, attempts) VALUES ('noop', 3)")
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("UPDATE job_queue SET status = 'done'")

    def test_ai_usage_is_append_only_for_everyone(self) -> None:
        with _rolled_back() as cur:
            cur.execute("""INSERT INTO ai_usage (purpose, model, tokens_in, tokens_out, cost_usd)
                           VALUES ('tests_live', 'claude-haiku-5-5', 1, 1, 0.000006) RETURNING id""")
            row_id = cur.fetchone()[0]
            with self.assertRaises(psycopg.errors.RaiseException):
                cur.execute("UPDATE ai_usage SET cost_usd = 0 WHERE id = %s", (row_id,))
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("DELETE FROM ai_usage")

    def test_job_health_is_read_only_for_the_owner(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            cur.execute("SELECT count(*) FROM job_health")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                cur.execute("INSERT INTO job_health (job, expected_interval_s) VALUES ('probe', 60)")


class ModeDefaults(LiveLedgerTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if _owner() is None:
            raise unittest.SkipTest("db/020 not pasted")
        super().setUpClass()

    def test_every_existing_commitment_reads_paper_agent(self) -> None:
        self.assertEqual(
            self.scalar("SELECT count(*) FROM commitments WHERE mode <> 'paper' OR origin <> 'agent'"),
            0,
        )

    def test_a_new_commitment_defaults_to_paper_agent_and_is_frozen(self) -> None:
        c = self.commitment()
        self.assertEqual(
            self.scalar("SELECT mode || '/' || origin FROM commitments WHERE id = %s", c.id),
            "paper/agent",
        )
        self.refuses("UPDATE commitments SET mode = 'live' WHERE id = %s", c.id,
                     containing="append-only")

    def test_mode_rejects_anything_but_paper_or_live(self) -> None:
        # Checked on the constraint itself: an INSERT that violates it would
        # need a full commitment fixture just to fail.
        definition = self.scalar("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                 "WHERE conname = 'commitments_mode_check'")
        self.assertIn("'paper'", definition)
        self.assertIn("'live'", definition)


if __name__ == "__main__":
    unittest.main()
