"""scripts/tests_live_metered.py, the merge gate's wrapper: it reads the
egress total as a property (the Nov 5 db/068 gate lost its byte count to
`.total()`), and it meters both psycopg.connect and Connection.connect."""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

import psycopg

from core import egress
from scripts import tests_live_metered as gate


class Wrapper(unittest.TestCase):
    def test_the_total_is_read_as_a_property(self) -> None:
        source = inspect.getsource(gate)
        self.assertNotIn("snapshot().total()", source)
        self.assertIn("egress.snapshot().total", source)
        self.assertIsInstance(egress.snapshot().total, int)

    def test_both_connect_paths_get_the_metered_cursor(self) -> None:
        seen = []
        with mock.patch.object(gate, "_connect", lambda cls, *a, **kw: seen.append(kw.get("cursor_factory"))), \
             mock.patch.object(psycopg.Connection, "connect", psycopg.Connection.connect), \
             mock.patch.object(psycopg, "connect", psycopg.connect), \
             mock.patch.object(unittest.defaultTestLoader, "discover", lambda *a, **kw: unittest.TestSuite()), \
             mock.patch("builtins.print"):
            self.assertEqual(gate.main(), 0)
            psycopg.connect("postgresql://unused")
            psycopg.Connection.connect("postgresql://unused")
        self.assertEqual(seen, [egress.MeteredCursor, egress.MeteredCursor])


if __name__ == "__main__":
    unittest.main()
