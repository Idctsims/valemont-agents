"""The merge gate: full `tests_live -v` with every connection metered.

core/egress.py's MeteredCursor is set on BOTH psycopg.connect and
psycopg.Connection.connect (a run that patched only the second missed the
pre-bound psycopg.connect and under-read ~0.13 MB; docs/arsenal.md). At the
end it prints the counts, every skip by name, and the bytes read.

Run from workers/:
    ..\\venv\\Scripts\\python.exe -m scripts.tests_live_metered

History: on Nov 5 the db/068 gate (502 OK, 0 skipped, 1,132 s) lost its
byte count because this script, then a scratchpad copy, called the
snapshot's `total` property as if it were a method. Fixed here; first proven on
db/069's gate run.
"""

from __future__ import annotations

import sys
import time
import unittest

import psycopg

from core import egress

_connect = psycopg.Connection.connect.__func__


def _metered(cls, *args, **kwargs):  # type: ignore[no-untyped-def]
    kwargs.setdefault("cursor_factory", egress.MeteredCursor)
    return _connect(cls, *args, **kwargs)


def main() -> int:
    psycopg.Connection.connect = classmethod(_metered)  # type: ignore[method-assign]
    psycopg.connect = psycopg.Connection.connect  # type: ignore[assignment]
    started = time.time()
    suite = unittest.defaultTestLoader.discover("tests_live", top_level_dir=".")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    read = egress.snapshot().total  # a property, not a method
    print(f"\nMETERED: {result.testsRun} tests, {len(result.failures)} failures, {len(result.errors)} errors, "
          f"{len(result.skipped)} skipped; {read:,} bytes read ({read / 1024:,.0f} KB); "
          f"{time.time() - started:,.0f} s")
    for test, reason in result.skipped:
        print(f"SKIP {test.id()}: {reason}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
