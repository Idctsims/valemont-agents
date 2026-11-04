"""Re-run verification on real UNVERIFIED references: a dry run, or --write.

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_reverify            (from workers/; a dry run)
    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_reverify --ids 190 206
    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_reverify --write --by "owner, 2026-11-02: ..."

Runs today's verifier (search-located sources, the matched section, one
second source on 'insufficient', the outcome check; arsenal/verify.py) on
each item and prints before and after. Each judge call is a real Haiku
call, costed in ai_usage like any other.

Without --write it NEVER settles anything. With --write, a reference whose
verdict CHANGES (unverified -> verified or corrected) is settled through
db/055's one re-verification (ledger.arsenal_reverify_ref: recorded in
arsenal_reverifications, append-only, with --by as who). A reference that
comes back unverified again is NOT written: the database allows one
re-verification per card, ever, and an unchanged verdict would spend it for
nothing (2026-11-02). After the writes, every reference that cleared is
outcome-checked (arsenal/outcome.py, the controls first; nothing recorded if
a control fails). Prints the before -> after counts.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from arsenal import outcome, sources, verify
from core import ledger
from core.paths import load_env


#: The groups the owner reports by (Nov 4), from origin_kind.
GROUPS = {"pool": "pool", "title_hook": "hook", "arm_me": "arm", "seed": "seed"}


def unverified_ids() -> list[int]:
    """Every real unverified pool, title-hook, Arm Me and seed reference
    (db/063 opened the one re-verification to seeds and arm cards)."""
    with ledger._brief_conn(None) as c:  # noqa: SLF001 - a script's one read
        return [int(r[0]) for r in c.execute(
            "SELECT id FROM arsenal_refs WHERE NOT is_test AND status IN ('pool', 'seed', 'arm') "
            "AND verification = 'unverified' ORDER BY id").fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ids", type=int, nargs="*", help="reference ids (default: every real unverified one)")
    parser.add_argument("--write", action="store_true",
                        help="settle each CHANGED verdict through db/055's one re-verification, then outcome-check")
    parser.add_argument("--by", help="who, for arsenal_reverifications.by_whom (required with --write)")
    args = parser.parse_args(argv)
    if args.write and not (args.by or "").strip():
        parser.error("--write needs --by (who re-verified, as recorded)")
    load_env()
    after: Counter[str] = Counter()
    by_group: dict[str, Counter[str]] = {}
    written = refused = 0
    try:
        ids = args.ids or unverified_ids()
        print(f"{len(ids)} references ({'changed verdicts written' if args.write else 'dry run: nothing is settled'})")
        for ref_id in ids:
            r = ledger.arsenal_ref_to_verify(ref_id)
            if r is None:
                print(f"\n{ref_id}: not found")
                continue
            with ledger._brief_conn(None) as c:  # noqa: SLF001
                was, note, origin_kind = c.execute(
                    "SELECT verification, verification_note, origin_kind FROM arsenal_refs WHERE id = %s",
                    (ref_id,)).fetchone()
            v = verify.verify_ref(r, ask=lambda card, e: verify.ask_haiku(card, e))
            after[v.verification] += 1
            by_group.setdefault(GROUPS.get(origin_kind, origin_kind), Counter())[v.verification] += 1
            print(f"\n{ref_id} {r.category} {r.title!r} ({r.source_work})")
            print(f"  before: {was}: {note}")
            print(f"  after:  {v.verification}: {v.note or '(no note)'}")
            print(f"  read:   {', '.join(v.judged_pages) or '(no page)'}; links {v.source_url}")
            for field, value in (("origin", v.origin), ("source_work", v.source_work), ("meaning", v.meaning),
                                 ("deploy_line", v.deploy_line)):
                if value:
                    print(f"  fixed {field}: {value}")
            if v.drop_quote:
                print("  quote: dropped")
            if args.write and was == "unverified" and v.verification in ("verified", "corrected"):
                try:
                    rec = ledger.arsenal_reverify_ref(
                        ref_id, verification=v.verification, note=v.note, by=args.by.strip(),
                        source_url=v.source_url, origin=v.origin, source_work=v.source_work, meaning=v.meaning,
                        deploy_line=v.deploy_line, drop_quote=v.drop_quote)
                    written += 1
                    print(f"  WRITTEN: arsenal_reverifications {rec}")
                except Exception as exc:  # the database's refusal (already re-verified once, ...)
                    refused += 1
                    print(f"  REFUSED by the database: {exc}")
            elif args.write:
                print("  not written (verdict unchanged: its one re-verification is kept)")
        print("\nbefore: " + f"unverified {len(ids)}" + "; after: "
              + ", ".join(f"{k} {n}" for k, n in sorted(after.items())))
        for group in ("pool", "hook", "arm", "seed"):
            if group in by_group:
                g = by_group[group]
                cleared = g["verified"] + g["corrected"]
                print(f"  {group}: unverified {sum(g.values())} -> verified {g['verified']}, corrected "
                      f"{g['corrected']}, unverified {g['unverified']} ({cleared}/{sum(g.values())} clear)")
        if args.write:
            print(f"written {written}, refused {refused}")
            try:
                print("outcome: " + outcome.check_unchecked().job_note)
            except outcome.ControlsFailed as exc:
                print(f"outcome: REFUSED, nothing recorded: {exc}")
    finally:
        sources.close_http()
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
