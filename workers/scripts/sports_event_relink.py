"""Repair one Kalshi link by hand, on the record (db/066; owner, 2026-11-04).

    ..\\venv\\Scripts\\python.exe -m scripts.sports_event_relink --event 123 --ticker KXNFLGAME-26NOV09PHIDET --reason "..."
    ..\\venv\\Scripts\\python.exe -m scripts.sports_event_relink --event 123 --unlink --reason "..."
    ...  --swapped / --not-swapped   the new event's home/away order (relink only; default: not stated)
    ...  --dry-run                   shows the event and what would change, writes nothing
                                                                                       (from workers/)

HAND USE ONLY. kalshi_map never relinks (docs/kalshi_mapping.md); this is
the one path, and nothing schedules it. db/066's sports_event_relink writes
an append-only sports_event_relinks row (old ticker, new ticker, reason,
role, time) and then the link, in one transaction; the database's guard
refuses a linked row's change without that row. A relink is method
'manual'. An unlink is for good: the row becomes out_of_scope ("unlinked by
hand: ...") and is never linked again. Prints the event before and after.
"""

from __future__ import annotations

import argparse
import sys

from core import ledger
from core.paths import load_env


def _show(e: ledger.EventLink) -> str:
    return (f"event {e.id} ({e.league}, {e.event_name}, {e.starts_at:%Y-%m-%d %H:%M}Z): {e.link_state or 'unevaluated'}"
            f", {e.ticker or 'no ticker'}, method {e.link_method or '-'}"
            + (f", conflict {e.link_conflict}" if e.link_conflict else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event", type=int, required=True, help="sports_events id")
    to = parser.add_mutually_exclusive_group(required=True)
    to.add_argument("--ticker", help="the Kalshi event ticker to relink it to")
    to.add_argument("--unlink", action="store_true", help="unlink it for good")
    swap = parser.add_mutually_exclusive_group()
    swap.add_argument("--swapped", dest="swapped", action="store_const", const=True)
    swap.add_argument("--not-swapped", dest="swapped", action="store_const", const=False)
    parser.add_argument("--reason", required=True, help="why (kept on the log row)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.unlink and args.swapped is not None:
        parser.error("--unlink takes no home/away flag")
    load_env()
    try:
        before = ledger.sports_event_link(args.event)
        if before is None:
            print(f"refused, nothing written: no sports_events row {args.event}")
            return 1
        print("before: " + _show(before))
        if before.ticker is None:
            print("refused, nothing written: the event is not linked (kalshi_map links it)")
            return 1
        target = "unlinked for good (out_of_scope)" if args.unlink else f"relinked to {args.ticker} (method manual)"
        if args.dry_run:
            print(f"would be {target}; nothing written")
            return 0
        log_id = ledger.sports_event_relink(args.event, None if args.unlink else args.ticker, args.reason,
                                            swapped=args.swapped)
        print(f"{target}: sports_event_relinks {log_id}")
        print("after:  " + _show(ledger.sports_event_link(args.event)))
    finally:
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
