"""db/017: pre-registrations are DB-stamped, append-only, and the runner guard
passes against the real stamp. Skips until pasted."""

from __future__ import annotations

import os
import unittest

import psycopg

from core.preregistration import require_registered

from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401

F12 = ("docs/preregistration_nfl.md",
       "## 8. Forward-only hypotheses F1, F2 — committed 2026-10-01, before any analysis")


class Preregistrations(LiveLedgerTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.preregistrations') IS NOT NULL")
            if not cur.fetchone()[0]:
                raise unittest.SkipTest("db/017 not pasted yet")

    def test_f1_f2_guard_passes_on_the_real_stamp(self) -> None:
        stamp = require_registered(*F12)
        self.assertLess(stamp["registered_at"], stamp["checked_at_db"])

    def test_a_registration_cannot_be_edited_or_removed(self) -> None:
        self.refuses("UPDATE preregistrations SET registered_at = now() - interval '1 year'",
                     containing="append-only")
        self.refuses("DELETE FROM preregistrations", containing="append-only")


if __name__ == "__main__":
    unittest.main()
