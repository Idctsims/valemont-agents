"""feed_why: the "Why it matters" line (Chat 3 Phase 2; docs/feed_ranking.md §3).

Chained after every feed_score (so after each ingest) and after every
feed_profile rebuild. Its queue is Home's and Tech's top TOP stories in For
you (db/039 feed_for_you, page one) that were never asked, in rank order:
position 1 of each, then position 2, and so on (owner, 2026-10-14). A story
is asked once, whatever the answer (stories.why_generated_at). Repo stories
are asked by repo_why_job instead.

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
import re
from typing import Any, Sequence

from core import ai, ledger
from core.jobs import JobSkipped
from feed.summarize import sentence_count

log = logging.getLogger("valemont.feed.why")

MODEL = "claude-haiku-5-5"
PURPOSE = "feed_why"
#: Home's and Tech's For you page one (rank.PAGE_SIZE), asked in rank order,
#: position by position across the two (owner, 2026-10-14: "rank-ordered like
#: summaries, page-one stories first"). It was the top 8 each.
TOP = 30
GROUP_SIZE = 8
#: Owner, 2026-10-14: 20 a day ran out at 14:55 Chicago on Oct 14 (19 of 20
#: used), so the evening feed got no lines. ~$0.0002 a call: ~$0.02 a day at
#: the cap (docs/feed_ranking.md). Repo stories keep their own cap.
DAILY_CALLS = 100
MAX_WORDS = 20
MAX_CHARS = 200
#: The prompt forbids hedging; a line that hedges is guessing at a link, so it
#: is refused (owner's bar, 2026-10-12: "could matter to Excursion" was one).
HEDGE = re.compile(r"\b(could|might|may|potentially|possibly|perhaps)\b", re.IGNORECASE)

SYSTEM = f"""You decide whether a news story matters to one reader's own ventures, and say why in one line when it truly does.

You get the reader's ventures (name, what it does, and for some the stage, next action and workstreams) and a few stories (id, headline, outlet, summary).

For each story, the answer is null UNLESS the story has a concrete, specific connection to one venture: it changes what that venture should do next, or it is about the venture's market, a direct competitor, a likely partner, customer or investor, a deal, a regulation or a permit that bears on it.
THE BAR. A connection must be one of these three, and nothing else:
1. Same industry: the story is about the venture's own industry (a creator-business deal for a venture on the business side of cultural figures; a members' club or beach club opening for a beach-club venture).
2. Same market segment: the story is about the specific segment the venture sells into or competes in.
3. A named counterparty or person: the story names a company, investor, partner, competitor or person the venture deals with or could deal with directly.
Geography alone NEVER qualifies. Two things being in the same city is not a connection. Counter-example, answer null: "Record Miami retail construction and rents signal a hot local development market relevant to Sail Beach Club's Biscayne Bay site." Retail construction is not hospitality; sharing Miami is not enough.
Sharing a broad topic is not a connection either. If you have to stretch, answer null. Most stories get null; that is expected and correct.

When there is a real connection, write ONE sentence of at most {MAX_WORDS} words (count them; a longer line is thrown away) that names the venture exactly as written in the list and says why it matters to it. Plain words, no hype, no "could", no "might".

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


def venture_context(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per live venture: its exact name, what it does (its Ventures
    HQ tagline, db/040; omitted when it has none) and, for a set-up venture,
    its stage, role, next action and active workstreams, all from
    ctx_ventures. The owner writes the taglines in Ventures HQ; nothing about
    a venture is kept in code."""
    active = {str(v.get("name")): v for v in ctx.get("active") or [] if v.get("name")}
    out = []
    for name in venture_names(ctx):
        entry: dict[str, Any] = {"name": name}
        v = active.get(name)
        if v and v.get("tagline"):
            entry["what_it_does"] = v["tagline"]
        if v:
            for key in ("stage", "role", "next_action"):
                if v.get(key):
                    entry[key] = v[key]
            if v.get("workstreams"):
                entry["workstreams"] = [w.get("name") for w in v["workstreams"] if w.get("name")]
        out.append(entry)
    return out


#: One trailing corporate word a line may leave off a venture's name (owner,
#: 2026-10-12): "Sims & Vale" names Sims & Vale Capital.
CORPORATE_WORDS = ("capital", "management", "group", "holdings", "inc", "llc")


def name_forms(name: str) -> list[str]:
    """A venture's exact name, plus the name with ONE trailing corporate word
    removed when it ends in one. Nothing shorter: "Vale" is not a form."""
    forms = [name]
    words = name.split()
    if len(words) > 1 and words[-1].lower().rstrip(".,") in CORPORATE_WORDS:
        forms.append(" ".join(words[:-1]))
    return forms


def _names_in(text: str, form: str) -> bool:
    return re.search(r"(?<![\w&])" + re.escape(form) + r"(?![\w&])", text, re.IGNORECASE) is not None


def resolve_venture(venture: Any, names: Sequence[str]) -> str | None:
    """The venture the model named, exactly or by a short form; else None."""
    if not venture:
        return None
    v = str(venture).strip().lower()
    for name in names:
        if v in (f.lower() for f in name_forms(name)):
            return name
    return None


def check(item: dict[str, Any], names: Sequence[str]) -> tuple[str | None, str | None]:
    """(line, None) when kept; (None, None) for the model's own null; (None,
    reason) when the model wrote a line the rules refuse. A refused line is
    logged with its reason (owner, 2026-10-12: a 24-word Excursion line was
    dropped silently and looked exactly like a null)."""
    why = " ".join(str(item.get("why") or "").split())
    venture = item.get("venture")
    if not why:
        return None, None
    name = resolve_venture(venture, names)
    if name is None:
        return None, f"names no known venture ({venture!r})"
    if not any(_names_in(why, form) for form in name_forms(name)):
        return None, f"does not name {name!r} in the line"
    hedge = HEDGE.search(why)
    if hedge:
        return None, f"hedges ({hedge.group(0)!r}): a real link is stated, not guessed"
    n = len(why.split())
    if n > MAX_WORDS:
        return None, f"{n} words; the cap is {MAX_WORDS}"
    if sentence_count(why) != 1:
        return None, f"{sentence_count(why)} sentences; one only"
    if len(why) > MAX_CHARS:
        return None, f"{len(why)} characters; the cap is {MAX_CHARS}"
    return why, None


def validate(item: dict[str, Any], names: Sequence[str]) -> str | None:
    """The line, or None. None is the default and the common answer."""
    return check(item, names)[0]


def user_message(ctx: dict[str, Any], group: Sequence[ledger.WhyJob]) -> str:
    return ("VENTURES:\n" + json.dumps(venture_context(ctx), ensure_ascii=False, indent=1)
            + "\nSTORIES:\n" + json.dumps([{"id": j.story_id, "headline": j.title, "outlet": j.source_name,
                                            "summary": j.summary or ""} for j in group], ensure_ascii=False))


def answers_for(group: Sequence[ledger.WhyJob], payload: Any, names: Sequence[str]) -> dict[int, str | None]:
    """Every story in the group gets an answer: its validated line or None."""
    out: dict[int, str | None] = {j.story_id: None for j in group}
    items = payload.get("stories") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        sid = item.get("id") if isinstance(item, dict) else None
        if sid in out and out[sid] is None:
            line, refused = check(item, names)
            out[sid] = line
            if refused:
                REFUSED.append((sid, str(item.get("why")), refused))
                log.warning("feed why: story %s line refused (%s): %r", sid, refused, item.get("why"))
    return out


#: Lines refused in this process, for the script to print: (story, line, reason).
REFUSED: list[tuple[int, str, str]] = []


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


def ask_again(story_ids: Sequence[int], *, client: Any = None) -> dict[int, str | None]:
    """By hand only (scripts/feed_profile.py --why --again): forget that these
    stories were asked and ask once more, in one call. The daily cap applies."""
    if DAILY_CALLS - ledger.ai_calls_today(PURPOSE) <= 0:
        raise JobSkipped(f"daily cap of {DAILY_CALLS} calls reached")
    jobs = ledger.feed_why_jobs(story_ids)[:GROUP_SIZE]
    ledger.feed_reset_why([j.story_id for j in jobs])
    ctx = ledger.ctx_ventures()
    return ask_group(jobs, ctx, venture_names(ctx), client=client)


# ------------------------------------------------------------------ repos
#
# Repo stories (db/049, feed/repos.py) get their own question, chained after
# feed_why: a concrete use for ONE venture (named exactly, as above) or for
# the dashboard itself, or plainly "General interest." Unlike news, a repo's
# line is never NULL: "General interest." is the stored answer when the model
# names no use, or names one the rules refuse (logged with its reason).

REPO_PURPOSE = "feed_repo_why"
REPO_GROUP_SIZE = 8
#: 8 repos a day is one call; the cap leaves room for a retry or a second run.
REPO_DAILY_CALLS = 4
GENERAL = "General interest."
DASHBOARD = "dashboard"
#: The owner's own dashboard, described for the model (no secrets, no hosts).
DASHBOARD_ABOUT = ("Valemont Command, the reader's private dashboard: a Next.js app on Vercel, Supabase "
                   "Postgres, Python workers on Railway, Claude for summaries and the Wags assistant, a feed "
                   "engine with embeddings, push notifications, and paper-trading and forecast-tracking agents.")

REPO_SYSTEM = f"""You decide whether a GitHub repository is something one reader would actually use, and say for what in one line.

You get the reader's ventures (name, what it does, and for some the stage, next action and workstreams), a description of the reader's own dashboard, and a few repositories (id, owner/name, description, topics, summary).

For each repository answer with a target and a line:
- target = a venture's exact name, when the repository has a concrete use in that venture's work: it does a job the venture needs done (its product, its marketing, its operations, its data).
- target = "{DASHBOARD}", when the repository has a concrete use in the reader's dashboard: a library, tool or pattern the dashboard's stack (Next.js, Vercel, Supabase, Python workers, Claude, agents, feeds, push) would adopt.
- target = null, when there is no concrete use. That is the common answer, and it is correct. Never stretch.

When the target is not null, write ONE sentence of at most {MAX_WORDS} words that names the target (the venture's exact name, or the word "dashboard") and the concrete use. Plain words, no hype, no "could", no "might".

Return one result per repository: its id, the target (or null) and the line (or null). Output only the JSON object."""


def repo_schema() -> dict[str, Any]:
    nullable = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {
        "type": "object", "additionalProperties": False, "required": ["repos"],
        "properties": {"repos": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "target", "why"],
            "properties": {"id": {"type": "integer"}, "target": nullable, "why": nullable},
        }}},
    }


def check_repo(item: dict[str, Any], names: Sequence[str]) -> tuple[str, str | None]:
    """(line, None) when kept; (GENERAL, None) for the model's own null;
    (GENERAL, reason) when the model wrote a line the rules refuse."""
    why = " ".join(str(item.get("why") or "").split())
    target = item.get("target")
    if not why or not target:
        return GENERAL, None
    if str(target).strip().lower() == DASHBOARD:
        if not re.search(r"\bdashboard\b", why, re.IGNORECASE):
            return GENERAL, "does not name the dashboard in the line"
    else:
        name = resolve_venture(target, names)
        if name is None:
            return GENERAL, f"names no known venture or the dashboard ({target!r})"
        if not any(_names_in(why, form) for form in name_forms(name)):
            return GENERAL, f"does not name {name!r} in the line"
    hedge = HEDGE.search(why)
    if hedge:
        return GENERAL, f"hedges ({hedge.group(0)!r}): a real use is stated, not guessed"
    n = len(why.split())
    if n > MAX_WORDS:
        return GENERAL, f"{n} words; the cap is {MAX_WORDS}"
    if sentence_count(why) != 1:
        return GENERAL, f"{sentence_count(why)} sentences; one only"
    if len(why) > MAX_CHARS:
        return GENERAL, f"{len(why)} characters; the cap is {MAX_CHARS}"
    return why, None


def repo_user_message(ctx: dict[str, Any], group: Sequence[ledger.RepoWhyJob]) -> str:
    return ("VENTURES:\n" + json.dumps(venture_context(ctx), ensure_ascii=False, indent=1)
            + "\nDASHBOARD:\n" + DASHBOARD_ABOUT
            + "\nREPOSITORIES:\n" + json.dumps([{"id": j.story_id, "repo": j.full_name,
                                                 "description": j.description or "",
                                                 "topics": list(j.topics[:8]), "summary": j.summary or ""}
                                                for j in group], ensure_ascii=False))


def repo_answers_for(group: Sequence[ledger.RepoWhyJob], payload: Any, names: Sequence[str]) -> dict[int, str]:
    """Every repo in the group gets a line: its validated one or GENERAL."""
    out: dict[int, str] = {j.story_id: GENERAL for j in group}
    seen: set[int] = set()
    items = payload.get("repos") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        sid = item.get("id") if isinstance(item, dict) else None
        if sid in out and sid not in seen:
            seen.add(sid)
            line, refused = check_repo(item, names)
            out[sid] = line
            if refused:
                REFUSED.append((sid, str(item.get("why")), refused))
                log.warning("feed why: repo story %s line refused (%s): %r", sid, refused, item.get("why"))
    return out


def ask_repo_group(group: Sequence[ledger.RepoWhyJob], ctx: dict[str, Any], names: Sequence[str], *,
                   client: Any = None) -> dict[int, str]:
    recorded: list[int] = []
    response = ai.complete(
        purpose=REPO_PURPOSE, model=MODEL, critical=False, max_tokens=2000, client=client,
        system=[{"type": "text", "text": REPO_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": repo_user_message(ctx, group)}],
        thinking={"type": "disabled"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": repo_schema()}},
        on_recorded=lambda usage_id, _model, _cost: recorded.append(usage_id),
    )
    payload = None
    if getattr(response, "stop_reason", None) != "refusal":
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            log.warning("feed why: the repo answer was not JSON")
    answers = repo_answers_for(group, payload, names)
    ledger.feed_save_repo_why(answers, recorded[-1] if recorded else None)
    return answers


def repo_why_job(*, client: Any = None) -> int:
    """The feed_repo_why system job, chained after feed_why. Returns the
    repo stories that got a concrete line (not "General interest.")."""
    from feed.summarize import MAX_ATTEMPTS
    queue = ledger.feed_repo_why_queue(REPO_GROUP_SIZE * REPO_DAILY_CALLS, MAX_ATTEMPTS)
    if not queue:
        return 0
    room = REPO_DAILY_CALLS - ledger.ai_calls_today(REPO_PURPOSE)
    if room <= 0:
        raise JobSkipped(f"daily cap of {REPO_DAILY_CALLS} calls reached; {len(queue)} repos waiting")
    ctx = ledger.ctx_ventures()
    names = venture_names(ctx)
    concrete = 0
    for i in range(0, min(len(queue), room * REPO_GROUP_SIZE), REPO_GROUP_SIZE):
        try:
            answers = ask_repo_group(queue[i:i + REPO_GROUP_SIZE], ctx, names, client=client)
        except ai.BudgetExceeded as exc:
            raise JobSkipped("AI budget spent; repos keep no why-it-matters line yet") from exc
        concrete += sum(1 for a in answers.values() if a != GENERAL)
    log.info("feed why: %d of %d repo(s) with a concrete use", concrete, len(queue))
    return concrete


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
