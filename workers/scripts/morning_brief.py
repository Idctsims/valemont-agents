r"""The Morning Brief by hand (core/brief.py).

    ..\venv\Scripts\python.exe -m scripts.morning_brief --generate          (from workers/)
    ..\venv\Scripts\python.exe -m scripts.morning_brief --push
    ..\venv\Scripts\python.exe -m scripts.morning_brief --force-test-push
    ..\venv\Scripts\python.exe -m scripts.morning_brief --fallback

--generate         makes today's brief now, as a NEW row (the latest wins).
--push             sends today's brief now, under the one-delivery-per-date
                   rule: if today was already delivered, nothing is sent.
--force-test-push  sends today's brief as a TEST push ("Morning brief
                   (test)"), never recorded as the delivery: the 09:45 push
                   still happens.
--fallback         builds the template brief from live data and prints it.
                   Nothing is written.

Prints ids, word counts and cost only (and, for --fallback, the dry-run text).
"""

from __future__ import annotations

import argparse
import json
import sys

from core import brief, ledger, push
from core.paths import load_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", action="store_true")
    group.add_argument("--push", action="store_true")
    group.add_argument("--force-test-push", action="store_true")
    group.add_argument("--fallback", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    try:
        now = ledger.owner_local_now()
        today = now.date()
        if args.generate:
            previous = ledger.latest_brief(brief.KIND, today)
            brief_id = brief.build_today(allow_fallback=True)
            row = ledger.latest_brief(brief.KIND, today)
            if previous is not None:
                # The guard (owner, 2026-10-20): a regenerated brief is on the record.
                ledger.record_regeneration("morning_brief", [ledger.Regenerated(
                    brief_id, f"brief {previous.id} ({'fallback' if previous.fallback else 'ai'})",
                    f"brief {brief_id} ({'fallback' if row.fallback else 'ai'})")],
                    how="scripts.morning_brief --generate")
            cost = _cost(row)
            print(f"brief {brief_id} for {today}: {row.word_count} words, "
                  f"{'fallback' if row.fallback else row.model}, cost_usd {cost}")
        elif args.fallback:
            made = brief.fallback_brief(ledger.context_snapshot(), ledger.brief_sections())
            print(f"fallback dry run for {today}: {made.words} words (not written)")
            print(json.dumps(made.body, indent=2, ensure_ascii=False))
        else:
            row = ledger.latest_brief(brief.KIND, today)
            if row is None:
                print(f"no brief for {today}: run --generate first")
                return 1
            if args.force_test_push:
                results = push.send(brief.push_message(row.body["headline"], late=False, test=True))
                print(f"test push for brief {row.id}: notification "
                      f"{[r.notification_id for r in results]} (not a delivery)")
            else:
                late = brief.push_decision(now.time(), False) == "late"
                delivery = ledger.deliver_brief_once(
                    kind=brief.KIND, day=today, brief_id=row.id, late=late,
                    send=lambda: brief._notification_id(push.send(brief.push_message(row.body["headline"], late=late))))
                print(f"brief {row.id}: " + (f"delivery {delivery}" if delivery else f"already delivered for {today}; nothing sent"))
    finally:
        ledger.close_pool()
    return 0


def _cost(row: ledger.BriefRow) -> str:
    if row.fallback:
        return "0 (no AI call)"
    return str(ledger.brief_cost(row.id))


if __name__ == "__main__":
    sys.exit(main())
