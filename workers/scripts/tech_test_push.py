"""Force one TEST free-credits push (Chat 4 Phase 1; owner, 2026-10-13).

    ..\\venv\\Scripts\\python.exe -m scripts.tech_test_push              (from workers/)
    ..\\venv\\Scripts\\python.exe -m scripts.tech_test_push --story 12345

Pushes "Free credits (test): <headline>" for a real tech story (the newest
summarized one, a pricing_credits one first, unless --story names it), deep
link /tech/story/<id>, through the same path as the real push (core/push.py),
and records it in tech_credit_pushes with is_test = true: never counted
toward the 3-a-day cap, and never blocking a real push of the same story
(db/045). Needs a subscribed device (/onboarding). Prints ids only.
"""

from __future__ import annotations

import argparse
import logging
import sys

from core import ledger
from core.paths import load_env
from core.push import PushError, send
from feed.tech import _notification_id, push_message


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--story", type=int, help="a tech story id (default: the newest summarized one)")
    args = ap.parse_args(argv)
    load_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        story = ledger.tech_story_for_test_push(args.story)
        if story is None:
            print("No tech story to push (none summarized yet, or --story is not a tech story).", file=sys.stderr)
            return 1
        sid, title, summary = story
        try:
            row = ledger.tech_credit_test_push(sid, lambda: _notification_id(send(push_message(sid, title, summary,
                                                                                             test=True))))
        except PushError as exc:
            print(f"Not sent: {exc}", file=sys.stderr)
            return 1
        print(f"test push sent for story {sid}; tech_credit_pushes row {row} (is_test); "
              f"real pushes left today: {ledger.tech_credit_push_room()}")
        return 0
    finally:
        ledger.close_pool()


if __name__ == "__main__":
    sys.exit(main())
