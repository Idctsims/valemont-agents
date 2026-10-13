"""The one-off categorize pass (owner, 2026-10-12 23:38; after db/042).

Every kept, real Home story with no category gets one from Haiku: headline,
summary (when it has one) and outlet in, one category out, judged by the same
definition the summary call sends (feed/category.PROMPT) and checked against
the same enum. No keyword rule.

    * groups of GROUP_SIZE, one core.ai.complete() call each: Haiku,
      non-critical (the budget guard downgrades or refuses it like any other
      feed call), thinking off, a cached system prompt, structured JSON;
    * an answer that is not one of ours, or names a story not in the group,
      is dropped: the story stays uncategorized and the summary call or a
      re-run gives it one. Never a guess;
    * a spent budget (BudgetExceeded) stops the pass with what was written
      so far, reported;
    * writes only NULL categories (ledger.feed_set_categories), so a re-run
      changes nothing already set.

scripts/feed_categorize.py runs it once and prints counts, ten samples per
category for the owner's review, and the cost recorded in ai_usage.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Sequence

from core import ai, ledger
from feed import category

log = logging.getLogger("valemont.feed.categorize")

MODEL = "claude-haiku-5-5"
PURPOSE = "feed_categorize"
#: The re-run over an explicit id set (owner, 2026-10-13), costed apart.
RECAT_PURPOSE = "feed_recategorize"
GROUP_SIZE = 25

SYSTEM = f"""You sort news stories for one reader's private dashboard into categories. For each story you get its id, headline, outlet, sport (when it has one) and, when there is one, a short summary. Return one result per story.

{category.PROMPT}
{category.MONEY_PROMPT}

Judge by what the story is about, read through the business lens. Output only the JSON object."""


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["stories"],
        "properties": {
            "stories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "category", "money"],
                    "properties": {
                        "id": {"type": "integer"},
                        "category": {"type": "string", "enum": list(category.CATEGORIES)},
                        "money": {"type": "boolean"},
                    },
                },
            }
        },
    }


def user_message(group: Sequence[ledger.CategorizeJob]) -> str:
    return "STORIES:\n" + json.dumps([
        {"id": j.story_id, "headline": j.title, "outlet": j.source_name, "sport": j.sport or "none",
         "summary": j.summary or ""}
        for j in group], ensure_ascii=False)


def answers_for(group: Sequence[ledger.CategorizeJob], payload: Any) -> dict[int, str]:
    """story id -> category, for valid answers about stories in the group,
    first answer per story wins. The money answer decides a story with a
    sport, or one answered sports_business (category.decide); without a
    boolean answer such a story is dropped, never guessed."""
    sports = {j.story_id: j.sport for j in group}
    out: dict[int, str] = {}
    seen: set[int] = set()
    items = payload.get("stories") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        if sid not in sports or sid in seen:
            continue
        cat = category.decide(category.valid(item.get("category")), item.get("money"), sports[sid])
        if cat is not None:
            seen.add(sid)
            out[sid] = cat
    return out


@dataclass
class PassResult:
    asked: int = 0
    written: int = 0
    calls: int = 0
    cost_usd: Decimal = Decimal("0")
    stopped: str | None = None
    #: category -> (story id, headline) of every story written.
    by_category: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    #: A re-run only: (story id, old, new) for every story whose category
    #: changed, and how many kept theirs.
    moves: list[tuple[int, str, str]] = field(default_factory=list)
    unchanged: int = 0


def run(jobs: Sequence[ledger.CategorizeJob] | None = None, *, client: Any = None,
        current: Mapping[int, str] | None = None) -> PassResult:
    """The pass. With `current` (story id -> its category now), a RE-RUN over
    exactly those stories (owner, 2026-10-13: the tightened definitions): it
    overwrites, but only through ledger.feed_recategorize's compare-and-set
    (a category changed meanwhile is left alone), records every move, and is
    costed under its own purpose. Without it, NULL categories only."""
    jobs = list(jobs if jobs is not None else ledger.feed_uncategorized_home_stories())
    if current is not None and any(j.story_id not in current for j in jobs):
        raise ValueError("a re-run categorizes only the stories in its id set")
    purpose = RECAT_PURPOSE if current is not None else PURPOSE
    res = PassResult()
    titles = {j.story_id: j.title for j in jobs}
    for i in range(0, len(jobs), GROUP_SIZE):
        group = jobs[i:i + GROUP_SIZE]
        costs: list[Decimal] = []
        try:
            response = ai.complete(
                purpose=purpose, model=MODEL, critical=False, max_tokens=2000, client=client,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_message(group)}],
                thinking={"type": "disabled"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema()}},
                on_recorded=lambda _id, _model, cost: costs.append(Decimal(str(cost))),
            )
        except ai.BudgetExceeded as exc:
            res.stopped = f"AI budget: {exc}"
            log.warning("feed categorize: stopped by the budget guard after %d calls", res.calls)
            break
        res.calls += 1
        res.asked += len(group)
        res.cost_usd += sum(costs, Decimal("0"))
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            log.warning("feed categorize: a group's answer was not JSON; its stories stay uncategorized")
            continue
        answers = answers_for(group, payload)
        if current is None:
            res.written += ledger.feed_set_categories(sorted(answers.items()))
        else:
            moves = [(sid, current[sid], cat) for sid, cat in sorted(answers.items()) if cat != current[sid]]
            res.unchanged += len(answers) - len(moves)
            if moves:
                res.written += ledger.feed_recategorize(moves)
            for sid, old, new in moves:
                log.info("feed recategorize: story %d %s -> %s", sid, old, new)
            res.moves.extend(moves)
        for sid, cat in answers.items():
            res.by_category.setdefault(cat, []).append((sid, titles[sid]))
    return res
