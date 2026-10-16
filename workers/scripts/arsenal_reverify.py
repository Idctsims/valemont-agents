"""Re-run verification on real UNVERIFIED references, as a dry run.

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_reverify            (from workers/)
    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_reverify --ids 190 206

Runs today's verifier (search-located sources, the matched section, one
second source on 'insufficient', the outcome check; arsenal/verify.py) on
each item and prints before and after. It NEVER settles anything: db/054's
guard lets verification settle once, so writing a new verdict over an
'unverified' row needs its own owner-approved path. Each judge call is a
real Haiku call, costed in ai_usage like any other.
"""

from __future__ import annotations

import argparse
import sys

from arsenal import sources, verify
from core import ledger
from core.paths import load_env


def unverified_ids() -> list[int]:
    with ledger._brief_conn(None) as c:  # noqa: SLF001 - a script's one read
        return [int(r[0]) for r in c.execute(
            "SELECT id FROM arsenal_refs WHERE NOT is_test AND status = 'pool' AND verification = 'unverified' "
            "ORDER BY id").fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ids", type=int, nargs="*", help="reference ids (default: every real unverified one)")
    args = parser.parse_args(argv)
    load_env()
    try:
        ids = args.ids or unverified_ids()
        print(f"{len(ids)} references (dry run: nothing is settled)")
        for ref_id in ids:
            r = ledger.arsenal_ref_to_verify(ref_id)
            if r is None:
                print(f"\n{ref_id}: not found")
                continue
            with ledger._brief_conn(None) as c:  # noqa: SLF001
                was, note = c.execute("SELECT verification, verification_note FROM arsenal_refs WHERE id = %s",
                                      (ref_id,)).fetchone()
            v = verify.verify_ref(r, ask=lambda card, e: verify.ask_haiku(card, e))
            print(f"\n{ref_id} {r.title!r} ({r.source_work})")
            print(f"  before: {was}: {note}")
            print(f"  after:  {v.verification}: {v.note or '(no note)'}")
            print(f"  read:   {', '.join(v.judged_pages) or '(no page)'}; links {v.source_url}")
            for field, value in (("origin", v.origin), ("source_work", v.source_work), ("meaning", v.meaning),
                                 ("deploy_line", v.deploy_line)):
                if value:
                    print(f"  fixed {field}: {value}")
            if v.drop_quote:
                print("  quote: dropped")
    finally:
        sources.close_http()
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
