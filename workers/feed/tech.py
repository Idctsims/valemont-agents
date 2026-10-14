"""Tech Hub summaries, categories and the free-credits push (Chat 4 Phase 1;
owner, 2026-10-13; db/045).

feed_tech_summarize runs FIRST after every feed_ingest (core/system_jobs.py),
before the Home and Sports summarizer, so an official item gets its summary
in the same 30-minute cycle it was found in. One core.ai.complete() call per
group (direct, never the batch path): Haiku, non-critical, thinking off, a
cached system prompt, structured JSON. Its queue: kept, real, linkable TECH
stories with no summary and a try left, OFFICIAL FIRST, then newest
published. The Home/Sports queue leaves tech stories out
(ledger.FEED_SUMMARY_QUEUE_SQL). Until its summary lands a story shows its
headline (feed_for_you lists an unscored tech story; db/045).

Per story the model returns summary (the Home rules: at most 2 sentences and
35 words, nothing beyond the teaser, no 8-word run copied), entities, topics,
`category` and `claimable` (the owner's one yes/no question, CREDITS_QUESTION).

category (decide()): an official story takes the model's category; a
community story (Hacker News) is `community` whatever the model said, so the
Community chip is the community's and an HN post never triggers a push.

THE PUSH (owner): a story whose category is pricing_credits AND whose answer
to CREDITS_QUESTION is yes gets one push, "Free credits: <headline>", deep
link /tech/story/<id>, if its lead item is push-eligible (push_eligible,
db/046: not a first-fetch backfill, and published within 24 h of first
seen). At most 3 a Chicago day and never the same story
twice: the database decides (db/045 tech_credit_pushes; its trigger refuses
a 4th and a repeat, and ledger.tech_credit_push asks first under the same
lock). A failed push is not recorded and is logged as an error.

A story that fails validation keeps no summary and counts an attempt; an
official one is retried once in the same run (with the reason), anything
else in the next run, then left headline-only for good (MAX_ATTEMPTS).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Sequence

from core import ai, ledger, memory, push
from core.jobs import JobSkipped
from feed.summarize import (COPY_RUN, MAX_ATTEMPTS, MAX_ENTITIES, MAX_SENTENCES, MAX_TOPICS, MAX_WORDS,
                            SummaryInvalid, copied_run, sentence_count)

log = logging.getLogger("valemont.feed.tech")

MODEL = "claude-haiku-5-5"
PURPOSE = "feed_tech_summarize"
GROUP_SIZE = 10
RUN_CAP = 40

#: stories.category for pillar tech (db/045). `repos` is Phase 2's (GitHub)
#: and never the model's answer here; `other` is shared with Home.
CATEGORIES: tuple[str, ...] = ("models_features", "pricing_credits", "tooling", "agents", "stack", "community",
                               "other")
CHIP_CATEGORIES: tuple[str, ...] = ("models_features", "pricing_credits", "tooling", "agents", "stack",
                                    "community")
LABELS: dict[str, str] = {
    "models_features": "Models & features",
    "pricing_credits": "Pricing & credits",
    "tooling": "Tooling",
    "agents": "Agents",
    "stack": "Stack",
    "community": "Community",
    "repos": "Repos",
    "other": "Other",
}

CATEGORY_PROMPT = (
    "category: the one that fits best: models_features (a new or updated AI model, or a new feature in an AI "
    "product: Claude, ChatGPT, the Claude or OpenAI API), pricing_credits (prices, price cuts, plans, usage "
    "tiers, rate limits, free credits, free-tier changes, promotions), tooling (developer tools: Claude Code, "
    "Cursor, MCP servers and the protocol, SDKs, CLIs, editors), agents (agent frameworks, agent SDKs, managed "
    "or multi-agent systems), stack (Next.js, Vercel, Supabase, Postgres, the web platform the reader builds "
    "on), community (people discussing, sharing or showing their own work), other (anything else).")

#: The owner's question (2026-10-13), asked of every tech story.
CREDITS_QUESTION = ("Does this offer free credits, a free tier increase, or a promo the reader can claim?")
CREDITS_PROMPT = (f"claimable: true or false: {CREDITS_QUESTION} True only when the story itself offers "
                  "something the reader can sign up for or claim now; a price cut alone is false.")

SYSTEM = f"""You summarize AI and developer-platform news for one reader's private dashboard. For each story you get its id, headline, outlet, whether the outlet is official (the company's own release notes, blog or changelog) and teaser. Return one result per story.

summary
- At most {MAX_SENTENCES} sentences and at most {MAX_WORDS} words. Plain and direct: what changed, and the one fact that makes it matter to a developer.
- Use ONLY what the headline and teaser say. Never add a fact, number, name, date or claim that is not in them. No speculation.
- Write in your own words. Never copy {COPY_RUN} or more consecutive words from the teaser or the headline.
- If the teaser is missing or too thin to say more, write ONE sentence that restates the headline in new words.
- No opinions, no hype words, no emoji, no markdown.

entities: the companies, products and models the story is about (as named; at most {MAX_ENTITIES}).
topics: 1 to {MAX_TOPICS} short lowercase snake_case topics, e.g. model_launch, api_pricing, rate_limits, free_credits, claude_code, mcp, nextjs.

{CATEGORY_PROMPT}
{CREDITS_PROMPT}

Output only the JSON object."""

PUSH_KIND = "tech_free_credits"


def schema() -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False, "required": ["stories"],
        "properties": {"stories": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "summary", "entities", "topics", "category", "claimable"],
            "properties": {
                "id": {"type": "integer"},
                "summary": {"type": "string"},
                "entities": {"type": "array", "items": {"type": "string"}},
                "topics": {"type": "array", "items": {"type": "string"}},
                "category": {"type": "string", "enum": list(CATEGORIES)},
                "claimable": {"type": "boolean"},
            },
        }}},
    }


# ------------------------------------------------------------------ the rules

def decide(model_category: object, official: bool) -> str | None:
    """The category a tech story gets: `community` for a community source
    (Hacker News), whatever the model said; the model's answer for an
    official one; None (never a guess) when the answer is not ours."""
    if not official:
        return "community"
    return model_category if isinstance(model_category, str) and model_category in CATEGORIES else None


def wants_push(category: str | None, claimable: object) -> bool:
    """The owner's rule: pricing_credits AND a yes to CREDITS_QUESTION. The
    cap and the never-twice rule are the database's (db/045)."""
    return category == "pricing_credits" and claimable is True


#: An item first seen more than this after it was published is old news.
PUSH_FRESH = timedelta(hours=24)


def push_eligible(backfill: bool, published_at: datetime | None, first_seen_at: datetime | None) -> bool:
    """Owner, 2026-10-14 (after notification 323 pushed a backfill item): only
    an item first seen on a cycle after its source's first successful fetch
    (not a first-fetch backfill) AND published within 24 h of first seen.
    The twin of db/046 tech_push_eligible, which the database enforces."""
    if backfill or published_at is None or first_seen_at is None:
        return False
    return first_seen_at - published_at <= PUSH_FRESH


def push_message(story_id: int, title: str, summary: str | None, *, test: bool = False) -> push.Message:
    head = f"Free credits{' (test)' if test else ''}: {title}"
    return push.Message(kind=PUSH_KIND, title=head[:120], body=(summary or "")[:240] or None,
                        deep_link=f"/tech/story/{story_id}", tag=f"tech-credits-{story_id}")


# ------------------------------------------------------------------ validator

@dataclass(frozen=True)
class Valid:
    summary: str
    entities: tuple[str, ...]
    topics: tuple[str, ...]
    category: str | None
    claimable: bool | None


def validate(item: dict[str, Any], job: ledger.TechSummaryJob) -> Valid:
    summary = " ".join(str(item.get("summary") or "").split())
    if not summary:
        raise SummaryInvalid("empty summary")
    n = sentence_count(summary)
    if n > MAX_SENTENCES:
        raise SummaryInvalid(f"{n} sentences; the cap is {MAX_SENTENCES}")
    w = len(summary.split())
    if w > MAX_WORDS:
        raise SummaryInvalid(f"{w} words; the cap is {MAX_WORDS}")
    for name, text in (("teaser", job.teaser), ("headline", job.title)):
        run = copied_run(summary, text)
        if run:
            raise SummaryInvalid(f"copies {COPY_RUN} words from the {name}: {run!r}")
    entities = tuple(dict.fromkeys(str(e).strip()[:80] for e in item.get("entities") or [] if str(e).strip()))
    topics = tuple(dict.fromkeys(t for t in ("_".join(str(x).lower().split())[:40] for x in item.get("topics") or [])
                                 if t))
    claimable = item.get("claimable")
    return Valid(summary, entities[:MAX_ENTITIES], topics[:MAX_TOPICS], decide(item.get("category"), job.official),
                 claimable if isinstance(claimable, bool) else None)


def results_for(group: Sequence[ledger.TechSummaryJob], payload: Any, *, attempts: int = 1
                ) -> tuple[list[ledger.SummaryOut], dict[int, str]]:
    """Every story in the group gets a SummaryOut (accepted, or summary None
    with `attempts` counted), and the reasons the rejected ones failed."""
    by_id = {j.story_id: j for j in group}
    accepted: dict[int, ledger.SummaryOut] = {}
    reasons: dict[int, str] = {}
    items = payload.get("stories") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        sid = item.get("id") if isinstance(item, dict) else None
        if sid not in by_id or sid in accepted:
            continue
        try:
            v = validate(item, by_id[sid])
        except SummaryInvalid as exc:
            reasons[sid] = str(exc)
            continue
        accepted[sid] = ledger.SummaryOut(story_id=sid, summary=v.summary, entities=v.entities, topics=v.topics,
                                          category=v.category, claimable=v.claimable, attempts=attempts)
    for sid in by_id:
        if sid not in accepted:
            reasons.setdefault(sid, "no valid result")
    return [accepted.get(sid) or ledger.SummaryOut(sid, None, attempts=attempts) for sid in by_id], reasons


def user_message(group: Sequence[ledger.TechSummaryJob], reasons: dict[int, str] | None = None) -> str:
    rows = []
    for j in group:
        row: dict[str, Any] = {"id": j.story_id, "headline": j.title, "outlet": j.source_name,
                               "official": j.official, "teaser": j.teaser or ""}
        if reasons and j.story_id in reasons:
            row["your_last_answer_was_rejected_because"] = reasons[j.story_id]
        rows.append(row)
    lead = ("REWRITE: your last answer for these stories broke a rule (given per story). Follow every rule "
            "above.\n" if reasons else "")
    return lead + "STORIES:\n" + json.dumps(rows, ensure_ascii=False)


def _ask(content: str, client: Any, recorded: list[int]) -> Any:
    response = ai.complete(
        purpose=PURPOSE, model=MODEL, critical=False, max_tokens=4000, client=client,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        thinking={"type": "disabled"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema()}},
        on_recorded=lambda usage_id, _model, _cost: recorded.append(usage_id),
    )
    if getattr(response, "stop_reason", None) == "refusal":
        log.warning("feed tech: the model declined a group")
        return None
    text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("feed tech: an answer was not JSON")
        return None


# ------------------------------------------------------------------ the job

@dataclass
class TechRun:
    accepted: int = 0
    rejected: int = 0
    retried: int = 0
    pushes: int = 0
    #: Story ids whose push was wanted but refused by the cap or a repeat.
    capped: list[int] = field(default_factory=list)
    push_errors: list[str] = field(default_factory=list)
    #: Free-credits stories not pushed: a backfill, or old news (db/046).
    ineligible: list[int] = field(default_factory=list)

    @property
    def job_note(self) -> str | None:
        parts = []
        if self.pushes:
            parts.append(f"{self.pushes} free-credits push(es)")
        if self.ineligible:
            parts.append(f"{len(self.ineligible)} not push-eligible (backfill or old)")
        if self.capped:
            parts.append(f"{len(self.capped)} over the cap or already pushed")
        if self.push_errors:
            parts.append(f"push failed: {self.push_errors[-1]}")
        return "; ".join(parts) or None


Send = Callable[[push.Message], Any]


def maybe_push(out: ledger.SummaryOut, job: ledger.TechSummaryJob, run: TechRun, *, send: Send = push.send) -> None:
    if not wants_push(out.category, out.claimable):
        return
    if not push_eligible(job.backfill, job.published_at, job.first_seen_at):
        run.ineligible.append(job.story_id)
        log.info("feed tech: story %d is a free-credits story but not push-eligible (backfill or old)", job.story_id)
        return
    msg = push_message(job.story_id, job.title, out.summary)
    try:
        sent = ledger.tech_credit_push(job.story_id, lambda: _notification_id(send(msg)))
    except push.PushError as exc:
        run.push_errors.append(str(exc))
        log.error("feed tech: free-credits push for story %d failed: %s", job.story_id, exc)
        return
    if sent is None:
        run.capped.append(job.story_id)
        log.info("feed tech: story %d wanted a free-credits push; the cap or a repeat refused it", job.story_id)
    else:
        run.pushes += 1
        log.info("feed tech: free-credits push for story %d (notification %s)", job.story_id, sent)


def _notification_id(results: Any) -> int | None:
    for r in results or []:
        nid = getattr(r, "notification_id", None)
        if nid is not None:
            return int(nid)
    return None


def summarize_group(group: Sequence[ledger.TechSummaryJob], run: TechRun, *, client: Any = None,
                    send: Send = push.send) -> None:
    first: list[int] = []
    results, reasons = results_for(group, _ask(user_message(group), client, first))
    retry_ids = {j.story_id for j in group
                 if j.official and j.story_id in reasons and MAX_ATTEMPTS - j.attempts - 1 > 0}
    retried: list[ledger.SummaryOut] = []
    second: list[int] = []
    for sid, why in reasons.items():
        log.warning("feed tech: story %d: %s", sid, why)
    if retry_ids:
        again = [j for j in group if j.story_id in retry_ids]
        retried, _ = results_for(again, _ask(user_message(again, {s: reasons[s] for s in retry_ids}), client, second),
                                 attempts=2)
        results = [r for r in results if r.story_id not in retry_ids]
        run.retried += len(again)
    ledger.feed_save_summaries(results, first[-1] if first else None)
    if retried:
        ledger.feed_save_summaries(retried, second[-1] if second else None)
    by_id = {j.story_id: j for j in group}
    for out in results + retried:
        if out.summary is None:
            run.rejected += 1
            continue
        run.accepted += 1
        maybe_push(out, by_id[out.story_id], run, send=send)


def tech_summarize_job(*, client: Any = None, send: Send = push.send) -> TechRun:
    """The feed_tech_summarize system job."""
    queue = ledger.feed_tech_summary_queue(RUN_CAP, MAX_ATTEMPTS)
    run = TechRun()
    try:
        for i in range(0, len(queue), GROUP_SIZE):
            try:
                summarize_group(queue[i:i + GROUP_SIZE], run, client=client, send=send)
            except ai.BudgetExceeded as exc:
                raise JobSkipped(f"AI budget spent; tech headlines only ({run.accepted} summarized first)") from exc
    finally:
        memory.release()
    if queue:
        log.info("feed tech: %d accepted, %d rejected of %d (%d official first); %d retried; %s",
                 run.accepted, run.rejected, len(queue), sum(j.official for j in queue), run.retried,
                 run.job_note or "no push")
    return run
