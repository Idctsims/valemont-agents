"""Input tokens of the Arsenal's two big prompts, as production builds them
(owner, 2026-11-04: cap the never-repeat list; report tokens before/after).

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_prompt_tokens [--title 469]   (from workers/)

Uses the API's free token count (core/ai.count_tokens): no model call, no
ai_usage row. Reads the live pool context, the weights and one title.
"""

from __future__ import annotations

import argparse
import sys

from arsenal import cards, hook, pool
from core import ai, ledger
from core.paths import load_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--title", type=int, default=469, help="titles.id for the hook prompt (469: The Diplomat)")
    args = parser.parse_args(argv)
    load_env()
    try:
        ctx = pool.context()
        weights = ledger.arsenal_weights()
        title = ledger.arsenal_title_for_hook(args.title)
        if title is None:
            print(f"no title {args.title}")
            return 1
        hook_ctx = hook.context_for(title) if hasattr(hook, "context_for") else ctx
        hook_tokens = ai.count_tokens(model=hook.MODEL, system=hook.system_blocks(hook_ctx),
                                      messages=[{"role": "user", "content": hook.hook_message(title, weights)}])
        split = cards.quota_split(cards.POOL_REFS, pool.category_have(ledger.arsenal_category_counts()),
                                  cards.category_weights(weights))
        pool_ctx = pool.context_for(split) if hasattr(pool, "context_for") else ctx
        pool_tokens = ai.count_tokens(model=pool.MODEL, system=cards.system_blocks(pool_ctx),
                                      messages=[{"role": "user", "content": cards.refs_message(split)}])
        print(f"never-repeat list: {len(ledger.arsenal_proposed())} references proposed in all; "
              f"hook prompt lists {len(hook_ctx.proposed)}, pool prompt lists {len(pool_ctx.proposed)}")
        print(f"hook prompt ({title.name}): {hook_tokens} input tokens")
        print(f"pool reference request: {pool_tokens} input tokens")
    finally:
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
