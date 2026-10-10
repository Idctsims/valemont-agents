"""One tiny Haiku call through core/ai.complete(), to prove the key, the budget
guard and the ai_usage accounting end to end.

    ..\\venv\\Scripts\\python.exe -m scripts.ai_smoke          (from workers/)

Prints only the model that served it, the token counts, cost_usd and the
ai_usage row id. Never the key, never the prompt or the reply. Costs a
fraction of a cent: a few dozen tokens at Haiku 5.5's rates.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from core import ai, ledger
from core.paths import load_env


def main() -> int:
    load_env()
    recorded: list[tuple[int, str, Decimal]] = []
    try:
        response = ai.complete(
            purpose="ai_smoke",
            model=ai.CHEAPEST_MODEL,
            max_tokens=64,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            on_recorded=lambda usage_id, model, cost: recorded.append((usage_id, model, cost)),
        )
    finally:
        ledger.close_pool()
    usage = ai.Usage.from_response(response.usage)
    [(usage_id, model, cost)] = recorded
    print(f"model {model}")
    print(f"tokens_in {usage.tokens_in} tokens_out {usage.tokens_out} "
          f"cache_read {usage.cache_read} cache_write {usage.cache_write}")
    print(f"cost_usd {cost}")
    print(f"ai_usage id {usage_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
