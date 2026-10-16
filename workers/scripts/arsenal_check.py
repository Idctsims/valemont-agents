"""The Arsenal's invariants, as counts (real rows only; db/054).

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_check      (from workers/)

Prints: dropped references that repeat (must be 0), Power Words that repeat
(must be 0), seeds ever dropped (must be 0), verification counts by status
for references and words, and today's drop ids. Exits 1 if an invariant
fails.
"""

from __future__ import annotations

import sys

from core import ledger
from core.paths import load_env


def main() -> int:
    load_env()
    try:
        c = ledger.arsenal_check()
    finally:
        ledger.close_pool()
    print(f"dropped refs with repeats: {c.repeated_refs}")
    print(f"dropped words with repeats: {c.repeated_words}")
    print(f"seeds ever dropped: {c.seeds_dropped}")
    print("refs by verification: " + (", ".join(f"{k} {v}" for k, v in c.refs_by_verification.items()) or "none"))
    print("words by verification: " + (", ".join(f"{k} {v}" for k, v in c.words_by_verification.items()) or "none"))
    if c.today_drop is None:
        print(f"today ({c.today}): no drop")
    else:
        drop_id, refs, word = c.today_drop
        print(f"today ({c.today}): drop {drop_id}, refs {list(refs)}, word {word}")
    return 1 if (c.repeated_refs or c.repeated_words or c.seeds_dropped) else 0


if __name__ == "__main__":
    sys.exit(main())
