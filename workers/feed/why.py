"""feed_why: the "Why it matters" line (Chat 3 Phase 2; docs/feed_ranking.md §3).

Chained after every feed_score (so after each ingest) and after every
feed_profile rebuild. Its queue is Home's top TOP stories in For you
(db/039 feed_for_you) that were never asked; a story is asked once, whatever
the answer (stories.why_generated_at).

One core.ai.complete() call per group of up to GROUP_SIZE stories: Haiku,
non-critical, thinking off, a cached system prompt, structured JSON. At most
DAILY_CALLS calls per local day (ai_usage rows for this purpose). Venture
context is ctx_ventures(), the same text Wags and the brief read.

NULL IS THE DEFAULT (owner, 2026-10-11: "only when there's a real
connection; otherwise none. Never force it"). The validator keeps a line
only when it
    * names a venture exactly as ctx_ventures spells it, in the line itself,
    * is one sentence of at most MAX_WORDS words.
Anything else is stored as NULL, and the card shows no line.

A spent AI budget is not a failure: JobSkipped, as feed_summarize does.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from core import ai, ledger
from core.jobs import JobSkipped
from feed.summarize import sentence_count

log = logging.getLogger("valemont.feed.why")

MODEL = "claude-haiku-5-5"
PURPOSE = "feed_why"
TOP = 8
GROUP_SIZE = 8
DAILY_CALLS = 20
MAX_WORDS = 20
MAX_CHARS = 200

SYSTEM = f"""You decide whether a news story matters to one reader's own ventures, and say why in one line when it truly does.

You get the reader's ventures (name, stage, next action, workstreams) and a few stories (id, headline, outlet, summary).

For each story, the answer is null UNLESS the story has a concrete, specific connection to one venture: it changes what that venture should do next, or it is about the venture's market, a direct competitor, a likely partner, customer or investor, a deal, a regulation or a permit that bears on it.
Sharing a broad topic is NOT a connection. "This is about hotels and the venture is hospitality" is not enough. If you have to stretch, answer null. Most stories get null; that is expected and correct.

When there is a real connection, write ONE sentence of at most {MAX_WORDS} words that names the venture exactly as written in the list and says why it matters to it. Plain words, no hype, no "could", no "might".

Return one result per story: its id, the venture's exact name (or null) and the line (or null). Output only the JSON object."""


def schema() -> dict[str, Any]:
    nullable = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {
        "type": "object", "additionalProperties": False, "required": ["stories"],
        "properties": {"stories": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "venture", "why"],
            "properties": {"id": {"type": "integer"}, "venture": nullable, "why": nullable},
        }}},
    }


def venture_names(ctx: dict[str, Any]) -> list[str]:
    """Live ventures (set up first, then the rest); never archived ones."""
    names = [str(v["name"]) for v in ctx.get("active") or [] if v.get("name")]
    names += [str(n) for n in ctx.get("not_set_up") or [] if n]
    return list(dict.fromkeys(names))


def validate(item: dict[str, Any], names: Sequence[str]) -> str | None:
    """The line, or None. None is the default and the common answer."""
    why = " ".join(str(item.get("why") or "").split())
    venture = item.get("venture")
    if not why or not venture or venture not in names:
        return None
    if venture.lower() not in why.lower():
        return None
    if len(why.split()) > MAX_WORDS or sentence_count(why) != 1 or len(why) > MAX_CHARS:
        return None
    return why


def user_message(ctx: dict[str, Any], group: Sequence[ledger.WhyJob]) -> str:
    return ("VENTURES:\n" + json.dumps({"active": ctx.get("active") or [],
                                        "not_set_up": ctx.get("not_set_up") or []}, ensure_ascii=False)
            + "\nSTORIES:\n" + json.dumps([{"id": j.story_id, "headline": j.title, "outlet": j.source_name,
                                            "summary": j.summary or ""} for j in group], ensure_ascii=False))


def answers_for(group: Sequence[ledger.WhyJob], payload: Any, names: Sequence[str]) -> dict[int, str | None]:
    """Every story in the group gets an answer: its validated line or None."""
    out: dict[int, str | None] = {j.story_id: None for j in group}
    items = payload.get("stories") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        sid = item.get("id") if isinstance(item, dict) else None
        if sid in out and out[sid] is None:
            out[sid] = validate(item, names)
    return out


def ask_group(group: Sequence[ledger.WhyJob], ctx: dict[str, Any], names: Sequence[str], *,
              client: Any = None) -> dict[int, str | None]:
    recorded: list[int] = []
    response = ai.complete(
        purpose=PURPOSE, model=MODEL, critical=False, max_tokens=2000, client=client,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message(ctx, group)}],
        thinking={"type": "disabled"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema()}},
        on_recorded=lambda usage_id, _model, _cost: recorded.append(usage_id),
    )
    payload = None
    if getattr(response, "stop_reason", None) != "refusal":
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            log.warning("feed why: the answer was not JSON")
    answers = answers_for(group, payload, names)
    ledger.feed_save_why(answers, recorded[-1] if recorded else None)
    return answers


def why_job(*, client: Any = None) -> int:
    """The feed_why system job. Returns lines kept."""
    queue = ledger.feed_why_queue(TOP)
    if not queue:
        return 0
    room = DAILY_CALLS - ledger.ai_calls_today(PURPOSE)
    if room <= 0:
        raise JobSkipped(f"daily cap of {DAILY_CALLS} calls reached; {len(queue)} waiting")
    ctx = ledger.ctx_ventures()
    names = venture_names(ctx)
    if not names:
        raise JobSkipped("no live ventures to tie a story to")
    kept = 0
    for i in range(0, min(len(queue), room * GROUP_SIZE), GROUP_SIZE):
        try:
            answers = ask_group(queue[i:i + GROUP_SIZE], ctx, names, client=client)
        except ai.BudgetExceeded as exc:
            raise JobSkipped("AI budget spent; no why-it-matters lines") from exc
        kept += sum(1 for a in answers.values() if a)
    log.info("feed why: %d line(s) kept for %d story(ies)", kept, len(queue))
    return kept
