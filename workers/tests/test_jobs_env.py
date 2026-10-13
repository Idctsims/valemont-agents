"""The recurring jobs run by hand load the repo's one .env before main().

`python -m jobs.archive_nfl_props --week 5` was first run on 2026-10-13 and
failed at its first write: "DATABASE_URL is not set". Every script under
scripts/ and main.py call core.paths.load_env(); the jobs did not.

Each job is run as __main__ with load_env replaced by a sentinel that
raises, so main() (network, database) never starts: the sentinel firing is
the proof that load_env came first.
"""

from __future__ import annotations

import runpy
import sys
import unittest
from unittest import mock

RECURRING = ("jobs.archive_nfl_props", "jobs.fit_nfl_ml")


class _Loaded(Exception):
    pass


def _sentinel() -> None:
    raise _Loaded


class RecurringJobsLoadEnv(unittest.TestCase):
    def test_load_env_runs_before_main(self) -> None:
        for name in RECURRING:
            with self.subTest(job=name), \
                    mock.patch("core.paths.load_env", _sentinel), \
                    mock.patch.object(sys, "argv", [name]):
                sys.modules.pop(name, None)  # runpy warns if it is already imported
                with self.assertRaises(_Loaded):
                    runpy.run_module(name, run_name="__main__")


if __name__ == "__main__":
    unittest.main()
