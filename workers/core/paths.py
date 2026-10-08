"""Where things live, resolved from this file rather than the working directory.

The Python tree sits under `workers/`; `.env`, `docs/`, `db/` and `.cache/`
stay at the repository root, shared with `apps/web`. A bare `Path("docs/...")`
resolves against whatever directory the process happened to start in, so it
works from one place and silently reads or writes somewhere else from another.
Everything that names a repo-root file goes through here instead.

    REPO_ROOT / "docs/preregistration_nfl.md"

Pre-registration `document` strings stay repo-relative (`docs/...`): they are
the key `preregistrations` rows are stored under, and the file is read as
`REPO_ROOT / document`.

**`.env` has exactly one location: `REPO_ROOT / ".env"`.** `load_env()` loads
that file and no other, and never overrides a variable already set, so a
Railway container (no `.env`, variables injected) is unaffected. In the image
this module sits at `/app/core/paths.py`, so `REPO_ROOT` points above `/app`
and holds none of these files. Nothing the worker runs reads `docs/` or `db/`.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

__all__ = ["REPO_ROOT", "WORKERS_ROOT", "ENV_FILE", "load_env"]

WORKERS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKERS_ROOT.parent
ENV_FILE = REPO_ROOT / ".env"


def load_env() -> None:
    """Load the repository's one `.env`, if present. Existing variables win."""
    load_dotenv(ENV_FILE, override=False)
