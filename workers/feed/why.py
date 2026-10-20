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
# feed_why: a concrete use for ONE target (a venture named exactly, the
# dashboard, the owner's dev workflow, or freelance client sites), or plainly
# "General interest." Unlike news, a repo's line is never NULL: "General
# interest." is the stored answer when the model names no use, or names one
# the rules still refuse after one same-run retry (logged with its reason).
#
# Repo rules (owner, 2026-10-14, after day one scored 0/8): "can" and "could"
# are allowed, because the line must name its target, so the use is stated,
# not guessed. "may", "might", "potentially", "possibly", "perhaps" stay
# refused. News why (above) keeps its own rules unchanged.

REPO_PURPOSE = "feed_repo_why"
REPO_GROUP_SIZE = 8
#: 8 repos a day is one call, two with the retry; the cap leaves room for a second run.
REPO_DAILY_CALLS = 4
GENERAL = "General interest."
DASHBOARD = "dashboard"
WORKFLOW = "dev workflow"
CLIENTS = "client sites"
#: The non-venture targets: how each is described to the model, and what the
#: line must say to name it.
#: Written to the reader in the second person (owner, 2026-10-14): the
#: model copies these words, so they say "your", never the owner's name.
REPO_TARGETS: dict[str, tuple[str, re.Pattern[str]]] = {
    DASHBOARD: ("Your dashboard, Valemont Command: a Next.js app on Vercel, Supabase Postgres, Python workers on "
                "Railway, Claude for summaries and the Wags assistant, a feed engine with embeddings, push "
                "notifications, and paper-trading and forecast-tracking agents.",
                re.compile(r"\bdashboard\b", re.IGNORECASE)),
    WORKFLOW: ("Your dev workflow: you build with Claude Code, Cursor and MCP servers, agent skills and the CLI "
               "tools around them.",
               re.compile(r"\b(workflow|claude code|cursor|mcp)\b", re.IGNORECASE)),
    CLIENTS: ("Your freelance client sites: Next.js, Supabase and Tailwind websites and web apps you build for "
              "clients.",
              re.compile(r"\b(client|clients|freelance)\b", re.IGNORECASE)),
}
#: What the model may send as a target and which one it means (2026-10-15:
#: told to speak in the second person, it sent "your dev workflow" and "your
#: Claude Code", and the bare-key lookup refused all 7 good lines).
TARGET_ALIASES: dict[str, str] = {
    "dashboard": DASHBOARD, "valemont command": DASHBOARD,
    "dev workflow": WORKFLOW, "workflow": WORKFLOW, "claude code": WORKFLOW, "cursor": WORKFLOW, "mcp": WORKFLOW,
    "claude code setup": WORKFLOW, "agent workflow": WORKFLOW,
    "client sites": CLIENTS, "client site": CLIENTS, "freelance client sites": CLIENTS, "client builds": CLIENTS,
}


def target_key(target: Any) -> str:
    """The target the model named, as a REPO_TARGETS key when it is one:
    case and a leading "your" / "the" / "my" ignored, and the dev workflow's
    tools (Claude Code, Cursor, MCP) mean the dev workflow. Anything else is
    returned as given (lowercased): a venture's name."""
    t = " ".join(str(target or "").split()).lower().rstrip(".")
    t = re.sub(r"^(your|the|my)\s+", "", t)
    return TARGET_ALIASES.get(t, t)


#: The repo hedge rule: "can"/"could" are allowed (the target is named).
REPO_HEDGE = re.compile(r"\b(may|might|potentially|possibly|perhaps)\b", re.IGNORECASE)
#: The dev-workflow rule (owner, 2026-10-20; feed review 5): the line names
#: what the repo lets the owner DO. A vague verb ("sharpen your workflow") or
#: a workflow as the only purpose ("for agent-driven workflows") is not that.
WORKFLOW_VAGUE = re.compile(
    r"\b(sharpen|sharpens|improve|improves|boost|boosts|enhance|enhances|streamline|streamlines|supercharge"
    r"|supercharges|level up|power up|elevate|elevates|optimi[sz]e|optimi[sz]es)\b"
    r"|\bfor (?:[\w-]+ ){0,2}workflows?\b",
    re.IGNORECASE)
#: Words that name the tool or the target, not a use: a workflow line needs at
#: least WORKFLOW_MIN_USE_WORDS words beyond these.
_WORKFLOW_FILLER = frozenset(
    "your you the a an and or this that these its it can could lets let give gives help helps use uses using "
    "with to for in on of into from by as setup dev workflow workflows claude code cursor codex mcp server "
    "servers skill skills agent agents plugin plugins tool tools repo repository".split())
#: Two: "Can add a reverse-engineering MCP server to your Claude Code." (the
#: owner's good line of 2026-10-14) keeps "add" and "reverse-engineering".
WORKFLOW_MIN_USE_WORDS = 2


def workflow_use_problem(why: str) -> str | None:
    """Why a dev-workflow line names no use, or None."""
    vague = WORKFLOW_VAGUE.search(why)
    if vague:
        return (f"says {vague.group(0)!r}, not what it lets you do: name the action "
                "(\"search and download 3D assets\", \"check MCP configs for leaked secrets\")")
    use = [w for w in re.findall(r"[a-z0-9][a-z0-9'-]*", why.lower()) if w not in _WORKFLOW_FILLER]
    if len(use) < WORKFLOW_MIN_USE_WORDS:
        return "names the tool, not what it lets you do: name the action"
    return None
#: Owner, 2026-10-14: a repo line speaks to the reader ("your Claude Code",
#: "your client sites"), never about them by name.
THIRD_PERSON = re.compile(r"\btsims\b|\bthe (reader|owner|user)\b", re.IGNORECASE)

REPO_SYSTEM = f"""You decide whether a GitHub repository is something the reader would actually use, and tell them for what in one line. Speak to the reader directly: "you", "your".

You get the reader's ventures (name, what it does, and for some the stage, next action and workstreams), three more targets described below, and a few repositories (id, owner/name, description, topics, summary).

For each repository answer with a target and a line:
- target = a venture's exact name, when the repository has a concrete use in that venture's work: it does a job the venture needs done (its product, its marketing, its operations, its data).
- target = "{DASHBOARD}", when it has a concrete use in your dashboard: a library, tool or pattern its stack would adopt.
- target = "{WORKFLOW}", when it has a concrete use in your dev workflow: Claude Code, Cursor, MCP, agent skills.
- target = "{CLIENTS}", when it has a concrete use in your freelance client sites (Next.js, Supabase, Tailwind).
- target = null, when there is no concrete use. Never stretch.

When the target is not null, write ONE sentence of at most {MAX_WORDS} words that names the target (the venture's exact name, or "your dashboard", "your dev workflow", "your Claude Code", "your client sites") and the concrete use, in the second person: "your Claude Code", "your client sites". Never the reader's name, never "the reader". "Can" is fine; never "may" or "might".
For your dev workflow, say what the repository lets you DO ("search and download 3D assets from 23 sites in one call"). Naming the tool is not a use, and neither is "sharpen", "improve" or "boost your workflow", or "for agent-driven workflows".

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
    key = target_key(target)
    if key in REPO_TARGETS:
        if not REPO_TARGETS[key][1].search(why):
            return GENERAL, f"does not name the {key} in the line"
        if key == WORKFLOW:
            problem = workflow_use_problem(why)
            if problem:
                return GENERAL, problem
    else:
        name = resolve_venture(target, names)
        if name is None:
            return GENERAL, f"names no known venture or target ({target!r})"
        if not any(_names_in(why, form) for form in name_forms(name)):
            return GENERAL, f"does not name {name!r} in the line"
    hedge = REPO_HEDGE.search(why)
    if hedge:
        return GENERAL, f"hedges ({hedge.group(0)!r}): say what it does for the target, plainly"
    third = THIRD_PERSON.search(why)
    if third:
        return GENERAL, f"names the reader ({third.group(0)!r}): speak to them as \"you\" / \"your\""
    n = len(why.split())
    if n > MAX_WORDS:
        return GENERAL, f"{n} words; the cap is {MAX_WORDS}"
    if sentence_count(why) != 1:
        return GENERAL, f"{sentence_count(why)} sentences; one only"
    if len(why) > MAX_CHARS:
        return GENERAL, f"{len(why)} characters; the cap is {MAX_CHARS}"
    return why, None


def repo_user_message(ctx: dict[str, Any], group: Sequence[ledger.RepoWhyJob],
                      reasons: dict[int, str] | None = None) -> str:
    """The repos, with the three non-venture targets described. On the
    retry, each refused repo carries why its last answer was rejected."""
    rows = []
    for j in group:
        row: dict[str, Any] = {"id": j.story_id, "repo": j.full_name, "description": j.description or "",
                               "topics": list(j.topics[:8]), "summary": j.summary or ""}
        if reasons and j.story_id in reasons:
            row["your_last_answer_was_rejected_because"] = reasons[j.story_id]
        rows.append(row)
    lead = ("REWRITE: your last answer for these repositories broke a rule (given per repository). Answer "
            "again, following every rule above; null is still fine when there is no concrete use.\n"
            if reasons else "")
    return (lead + "VENTURES:\n" + json.dumps(venture_context(ctx), ensure_ascii=False, indent=1)
            + "\nOTHER TARGETS:\n" + json.dumps({k: v[0] for k, v in REPO_TARGETS.items()}, ensure_ascii=False,
                                                indent=1)
            + "\nREPOSITORIES:\n" + json.dumps(rows, ensure_ascii=False))


def repo_check_all(group: Sequence[ledger.RepoWhyJob], payload: Any, names: Sequence[str]
                   ) -> tuple[dict[int, str], dict[int, str]]:
    """Every repo in the group gets a line (its validated one or GENERAL),
    and the reasons the refused ones failed (the model's own null is not a
    refusal and is not retried)."""
    out: dict[int, str] = {j.story_id: GENERAL for j in group}
    reasons: dict[int, str] = {}
    seen: set[int] = set()
    items = payload.get("repos") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        sid = item.get("id") if isinstance(item, dict) else None
        if sid in out and sid not in seen:
            seen.add(sid)
            line, refused = check_repo(item, names)
            out[sid] = line
            if refused:
                reasons[sid] = refused
                REFUSED.append((sid, str(item.get("why")), refused))
                log.warning("feed why: repo story %s line refused (%s): %r", sid, refused, item.get("why"))
    return out, reasons


def repo_answers_for(group: Sequence[ledger.RepoWhyJob], payload: Any, names: Sequence[str]) -> dict[int, str]:
    """Every repo in the group gets a line: its validated one or GENERAL."""
    return repo_check_all(group, payload, names)[0]


def _ask_repos(content: str, client: Any, recorded: list[int]) -> Any:
    response = ai.complete(
        purpose=REPO_PURPOSE, model=MODEL, critical=False, max_tokens=2000, client=client,
        system=[{"type": "text", "text": REPO_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        thinking={"type": "disabled"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": repo_schema()}},
        on_recorded=lambda usage_id, _model, _cost: recorded.append(usage_id),
    )
    if getattr(response, "stop_reason", None) == "refusal":
        return None
    text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("feed why: the repo answer was not JSON")
        return None


def ask_repo_group(group: Sequence[ledger.RepoWhyJob], ctx: dict[str, Any], names: Sequence[str], *,
                   client: Any = None, retry: bool = True) -> dict[int, str]:
    """One call for the group; then, when any line was REFUSED by the rules
    (not a null) and `retry`, ONE same-run retry for those repos with the
    reasons (owner, 2026-10-14, like official tech summaries). Each answer is
    saved with the call that produced it."""
    first: list[int] = []
    answers, reasons = repo_check_all(group, _ask_repos(repo_user_message(ctx, group), client, first), names)
    keep = {sid: line for sid, line in answers.items() if sid not in reasons or not retry}
    ledger.feed_save_repo_why(keep, first[-1] if first else None)
    if retry and reasons:
        again = [j for j in group if j.story_id in reasons]
        second: list[int] = []
        retried, _ = repo_check_all(again, _ask_repos(repo_user_message(ctx, again, reasons), client, second), names)
        ledger.feed_save_repo_why(retried, second[-1] if second else None)
        answers.update(retried)
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
    for i in range(0, len(queue), REPO_GROUP_SIZE):
        # A group takes one call, two with the retry: never past the cap.
        left = REPO_DAILY_CALLS - ledger.ai_calls_today(REPO_PURPOSE)
        if left <= 0:
            log.info("feed why: repo cap of %d calls reached; %d repo(s) wait", REPO_DAILY_CALLS, len(queue) - i)
            break
        try:
            answers = ask_repo_group(queue[i:i + REPO_GROUP_SIZE], ctx, names, client=client, retry=left >= 2)
        except ai.BudgetExceeded as exc:
            raise JobSkipped("AI budget spent; repos keep no why-it-matters line yet") from exc
        concrete += sum(1 for a in answers.values() if a != GENERAL)
    log.info("feed why: %d of %d repo(s) with a concrete use", concrete, len(queue))
    return concrete


def repo_ask_again(story_ids: Sequence[int], *, client: Any = None) -> dict[int, str]:
    """By hand only (scripts/feed_profile.py --repo-why --again): forget that
    these repo stories were asked and ask once more (with the same-run retry
    when the cap allows). The repo daily cap applies."""
    from feed.summarize import MAX_ATTEMPTS
    left = REPO_DAILY_CALLS - ledger.ai_calls_today(REPO_PURPOSE)
    if left <= 0:
        raise JobSkipped(f"repo cap of {REPO_DAILY_CALLS} calls reached")
    ledger.feed_reset_repo_why(story_ids)
    jobs = [j for j in ledger.feed_repo_why_queue(1000, MAX_ATTEMPTS) if j.story_id in set(story_ids)]
    ctx = ledger.ctx_ventures()
    return ask_repo_group(jobs[:REPO_GROUP_SIZE], ctx, venture_names(ctx), client=client, retry=left >= 2)


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
