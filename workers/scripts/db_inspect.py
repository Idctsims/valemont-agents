"""Read-only database inspection. The only way a session looks at the database.

    ..\\venv\\Scripts\\python.exe scripts\\db_inspect.py "SELECT slug, enabled FROM agents"    (from workers/)

Connects with DATABASE_URL_READONLY, the `valemont_readonly` role from db/018,
and with nothing else. It never reads DATABASE_URL: the .env file is parsed
for the one key it needs instead of being loaded into the environment, so the
write-capable string never enters this process.

The guarantee is the role's grants, not this script. It runs whatever it is
given, so a write attempt reaches the database and is refused there with a
permission error, which is the proof db/018 works. As a second layer every
transaction is rolled back, never committed.

Refuses a URL whose user is not `valemont_readonly.<ref>`, so pasting the
`postgres` string into DATABASE_URL_READONLY by mistake fails loudly instead of
quietly inspecting with write access.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from dotenv import dotenv_values

from core.paths import ENV_FILE  # the path only; nothing is loaded

KEY = "DATABASE_URL_READONLY"
ROLE = "valemont_readonly"
STATEMENT_TIMEOUT_MS = 30_000


def readonly_url() -> str:
    url = (dotenv_values(ENV_FILE).get(KEY) if ENV_FILE.is_file() else None) or os.environ.get(KEY)
    if not url:
        sys.exit(f"{KEY} is not set in {ENV_FILE} or the environment. See .env.example.")
    user = unquote(urlsplit(url).username or "")
    if user != ROLE and not user.startswith(ROLE + "."):
        sys.exit(f"{KEY} connects as {user.split('.')[0]!r}, not {ROLE!r}. Refusing.")
    return url


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].strip():
        sys.exit('usage: db_inspect.py "<SQL>"')
    with psycopg.connect(readonly_url()) as conn, conn.cursor() as cur:
        try:
            cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
            cur.execute(argv[1])
            if cur.description is None:
                print(cur.statusmessage)
            else:
                print("\t".join(col.name for col in cur.description))
                rows = cur.fetchall()
                for row in rows:
                    print("\t".join("NULL" if v is None else str(v) for v in row))
                print(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
        except psycopg.Error as exc:
            print(f"ERROR {exc.sqlstate}: {exc.diag.message_primary or exc}", file=sys.stderr)
            return 1
        finally:
            conn.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
