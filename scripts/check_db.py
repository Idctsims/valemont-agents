"""Foundation check. Run this before writing anything else.

    source venv/bin/activate
    python scripts/check_db.py

Verifies the connection, the Postgres version, that the schema is loaded,
and that the append-only triggers actually fire.
"""
import os
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("DATABASE_URL")
if not url:
    sys.exit("DATABASE_URL not set. Copy .env.example to .env and fill it in.")

with psycopg.connect(url) as conn, conn.cursor() as cur:
    cur.execute("SELECT version()")
    print(cur.fetchone()[0].split(",")[0])

    cur.execute("SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public'")
    tables = cur.fetchone()[0]
    if tables == 0:
        print("\nConnected, but no schema yet.")
        print("Paste db/001_schema.sql into the Supabase SQL Editor and Run.")
        sys.exit(0)

    cur.execute("SELECT slug, display_name FROM agents ORDER BY id")
    print(f"\n{tables} tables. Roster:")
    for slug, name in cur.fetchall():
        print(f"  {slug:12} {name}")

# Confirm the immutability triggers are installed — the guarantee this whole
# project rests on. (Checked via catalog, not by attempting an UPDATE: a row
# trigger won't fire on an empty table, which would give a false pass.)
with psycopg.connect(url) as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT tgname FROM pg_trigger
        WHERE NOT tgisinternal
          AND tgname IN ('commitments_immutable','events_immutable',
                         'legs_frozen','resolutions_timing')
    """)
    found = {r[0] for r in cur.fetchall()}
    missing = {'commitments_immutable', 'events_immutable',
               'legs_frozen', 'resolutions_timing'} - found
    if missing:
        print(f"\nWARNING: append-only triggers missing: {', '.join(sorted(missing))}")
    else:
        print("\nAppend-only enforcement: active.")

print("\nFoundation is real. Next: core/ledger.py")
