"""The Morning Brief: generated at 06:15, pushed at 09:45 (America/Chicago).

THE SECTION CONTRACT (read this before adding a pillar)
    The brief's sections ARE the context-provider registry (db/029):
    every row of context_providers with enabled AND brief_section, in sort
    order, is one section, titled by its label, fed by its provider's slice of
    context_snapshot(). Today: goals, ventures, capital. A later chat that
    ships a pillar adds its provider function and registry row in its own
    migration and sets brief_section = true; its section appears in the next
    brief with NO change here. ctx_brief (the brief itself, for Wags) is
    registered with brief_section = false, so a brief never feeds on itself.
    The reserved `calendar` key follows the same rule: until a calendar
    provider is registered, there is no calendar section and nothing renders.

    A section with no data is omitted, never padded: the prompt says so and
    the fallback template does it by construction. The fallback knows how to
    word the three sections above; a later pillar's section appears in AI
    briefs as soon as it registers, and in fallback briefs once a template
    for it is added to FALLBACK_SECTIONS (until then the fallback omits it).

THE BRIEF
    {headline, sections: [{key, title, lines[]}], one_move}, validated:
    every key is a registered brief section (each at most once), headline and
    one_move are present, every line is text, and the whole is at most
    MAX_WORDS words (the target is 120 to 220: readable in under a minute).

    Claude via core/ai.complete(): Sonnet, critical=True (never downgraded at
    80% of the monthly budget; still under the console's hard cap), the ONE
    shared voice (prompts/wags_voice.md, core/voice.py) plus the brief's
    rules, structured JSON output. Thinking is at Sonnet's lowest setting
    (between_tools; with no tools, no thinking).

FALLBACK
    A brief always exists. When the AI call is refused, or fails validation
    twice, or keeps failing until FALLBACK_AT, the deterministic template
    builds one from the same context (fallback = true): same shape, plainer
    words.

SCHEDULE (owner's local time, by the database clock)
    morning_brief       06:15, then every 15 min until 09:30, and at boot.
                        No-op once today's brief exists. Before FALLBACK_AT
                        an AI failure raises (job_health records it; the next
                        quarter-hour retries); from FALLBACK_AT it falls back.
    morning_brief_push  09:45, and at boot: pushes "Morning brief" (the
                        headline), linking /brief, once per date
                        (ledger.deliver_brief_once). At boot after 09:45 the
                        body is prefixed "Late ·"; after 18:00 the push is
                        skipped as stale (the brief is still generated).

    Both run through core/jobs.run_tracked, so both appear in job_health.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Callable, Literal, Mapping, Sequence

from core import ai, ledger, push
from core.voice import load_voice

log = logging.getLogger("valemont.brief")

KIND = "morning"
MODEL = "claude-sonnet-5-5"
LINK = "/brief"

GENERATE_AT = time(6, 15)
RETRY_EVERY_MIN = 15
FALLBACK_AT = time(9, 30)
PUSH_AT = time(9, 45)
#: A push this long after PUSH_AT is a catch-up, sent as "Late ·".
LATE_AFTER = time(9, 50)
STALE_AFTER = time(18, 0)

MAX_WORDS = 260
TARGET_WORDS = (120, 220)
RESERVED_SECTIONS = ("calendar",)


class BriefInvalid(ValueError):
    """The model's brief broke a rule; the message says which."""


class BriefRefused(RuntimeError):
    """The model declined (stop_reason 'refusal'): fall back now, never retry."""


# --------------------------------------------------------------- the schedule

GenerateDecision = Literal["wait", "skip", "generate"]
PushDecision = Literal["wait", "done", "stale", "push", "late"]


def generate_decision(now: time, has_brief: bool) -> GenerateDecision:
    """Before 06:15 nothing; once today's brief exists nothing; else generate.
    After 18:00 a missing brief is still generated (only the push is stale)."""
    if now < GENERATE_AT:
        return "wait"
    return "skip" if has_brief else "generate"


def may_fall_back(now: time) -> bool:
    """From 09:30 an AI failure becomes a fallback brief instead of a retry."""
    return now >= FALLBACK_AT


def push_decision(now: time, delivered: bool) -> PushDecision:
    if now < PUSH_AT:
        return "wait"
    if delivered:
        return "done"
    if now >= STALE_AFTER:
        return "stale"
    return "late" if now >= LATE_AFTER else "push"


def retry_times() -> list[time]:
    """06:15, 06:30 ... 09:30: every quarter-hour attempt the cron makes."""
    out, minutes = [], GENERATE_AT.hour * 60 + GENERATE_AT.minute
    while minutes <= FALLBACK_AT.hour * 60 + FALLBACK_AT.minute:
        out.append(time(minutes // 60, minutes % 60))
        minutes += RETRY_EVERY_MIN
    return out


# -------------------------------------------------------------- the content

_WORD = re.compile(r"\S+")


def word_count(brief: Mapping[str, Any]) -> int:
    """Words a reader reads: the headline, every line and the one move."""
    texts = [brief.get("headline", ""), brief.get("one_move", "")]
    for s in brief.get("sections", []) or []:
        texts.extend(s.get("lines", []) or [])
    return sum(len(_WORD.findall(str(t))) for t in texts)


def validate(brief: Any, allowed: Sequence[str]) -> dict[str, Any]:
    """The brief, checked. Raises BriefInvalid naming the first broken rule."""
    if not isinstance(brief, dict):
        raise BriefInvalid("the brief is not an object")
    for field in ("headline", "one_move"):
        if not isinstance(brief.get(field), str) or not brief[field].strip():
            raise BriefInvalid(f"missing {field}")
    sections = brief.get("sections")
    if not isinstance(sections, list):
        raise BriefInvalid("sections is not a list")
    seen: set[str] = set()
    for s in sections:
        if not isinstance(s, dict) or not isinstance(s.get("key"), str):
            raise BriefInvalid("a section has no key")
        if s["key"] not in allowed:
            raise BriefInvalid(f"unknown section {s['key']!r} (not a registered brief section)")
        if s["key"] in seen:
            raise BriefInvalid(f"section {s['key']!r} appears twice")
        seen.add(s["key"])
        lines = s.get("lines")
        if not isinstance(lines, list) or not lines or not all(isinstance(x, str) and x.strip() for x in lines):
            raise BriefInvalid(f"section {s['key']!r} has no lines")
        if not isinstance(s.get("title"), str) or not s["title"].strip():
            raise BriefInvalid(f"section {s['key']!r} has no title")
    words = word_count(brief)
    if words > MAX_WORDS:
        raise BriefInvalid(f"{words} words; the cap is {MAX_WORDS}")
    return {"headline": brief["headline"].strip(), "one_move": brief["one_move"].strip(),
            "sections": [{"key": s["key"], "title": s["title"].strip(),
                          "lines": [x.strip() for x in s["lines"]]} for s in sections]}


def schema(keys: Sequence[str]) -> dict[str, Any]:
    """The structured-output schema: the brief's shape, keys limited to the registry."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["headline", "sections", "one_move"],
        "properties": {
            "headline": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["key", "title", "lines"],
                    "properties": {
                        "key": {"type": "string", "enum": list(keys)},
                        "title": {"type": "string"},
                        "lines": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "one_move": {"type": "string"},
        },
    }


RULES = f"""You are writing this morning's Morning Brief for Tsims, in the voice above.

The brief
- A headline (one line, at most 14 words): the shape of today.
- Sections, only from SECTIONS, in that order, keys exactly as given. Each section: 1 to 4 short lines, each at most 25 words.
- one_move: the single most important thing to do today, as one sentence. It must be doable today and grounded in the data.
- {TARGET_WORDS[0]} to {TARGET_WORDS[1]} words in all; never over {MAX_WORDS}. Readable in under a minute.

Rules
- Every line comes from the CONTEXT. Cite the pillar and the number ("Goals: 2 of 8 done"). No line that the data does not support.
- A section with no data is OMITTED. Never pad, never write "nothing to report".
- Ventures: what is due today or overdue comes first, overdue first, then next actions that matter today.
- Capital: say paper for paper money, every time. Never add paper and live together.
- Plain text in every string: no markdown, no emoji.
- Output only the JSON object."""


def _clock(payload: Mapping[str, Any]) -> str:
    return f"{payload.get('weekday')}, {payload.get('local_date')} · ISO week {payload.get('iso_week')}"


def prompt(payload: Mapping[str, Any], sections: Sequence[ledger.BriefSection]) -> tuple[list[dict], str]:
    """(system blocks, user message). The voice and the rules are stable and
    cached; the day's data goes in the user turn."""
    voice = load_voice()
    system = [
        {"type": "text", "text": voice.text},
        {"type": "text", "text": RULES, "cache_control": {"type": "ephemeral"}},
    ]
    data = {
        "today": _clock(payload),
        "sections": [{"key": s.key, "title": s.title} for s in sections],
        "context": {s.key: payload.get(s.key) for s in sections},
    }
    return system, f"Write today's brief.\n\nCONTEXT:\n{json.dumps(data, default=str)}"


@dataclass(frozen=True)
class Generated:
    body: dict[str, Any]
    model: str | None
    ai_usage_id: int | None
    fallback: bool
    words: int


def generate_ai(payload: Mapping[str, Any], sections: Sequence[ledger.BriefSection], *,
                client: Any = None) -> Generated:
    """Up to two AI attempts; a brief that fails validation twice, or a
    refusal, raises for the caller to fall back. API errors propagate."""
    keys = [s.key for s in sections]
    titles = {s.key: s.title for s in sections}
    system, user = prompt(payload, sections)
    last_error: Exception | None = None
    for attempt in (1, 2):
        recorded: list[tuple[int, str]] = []
        response = ai.complete(
            purpose="morning_brief", model=MODEL, critical=True, max_tokens=2000, client=client,
            system=system, messages=[{"role": "user", "content": user}],
            thinking={"type": "between_tools"},
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema(keys)}},
            on_recorded=lambda usage_id, model, _cost: recorded.append((usage_id, model)),
        )
        if getattr(response, "stop_reason", None) == "refusal":
            raise BriefRefused("the model declined to write the brief")
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        try:
            body = validate(json.loads(text), keys)
        except (BriefInvalid, json.JSONDecodeError) as exc:
            last_error = exc
            log.warning("brief attempt %d invalid: %s", attempt, exc)
            continue
        # The registry's label is the title, whatever the model wrote.
        for s in body["sections"]:
            s["title"] = titles[s["key"]]
        usage_id, model = recorded[-1] if recorded else (None, MODEL)
        return Generated(body, model, usage_id, False, word_count(body))
    raise BriefInvalid(f"invalid twice: {last_error}")


# ---------------------------------------------------------------- fallback

def _money(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "?"
    return f"{'-' if n < 0 else ''}${abs(n):,.2f}"


def _goals_lines(g: Mapping[str, Any]) -> list[str]:
    week = g.get("week") or {}
    held, done = week.get("held") or 0, week.get("done") or 0
    if not held and not week.get("goals"):
        return []
    lines = [f"Goals: {done} of {held} done this week."]
    open_goals = [x for x in week.get("goals") or [] if x.get("state") == "open"]
    carried = [x for x in open_goals if (x.get("carried") or 0) >= 2]
    for x in carried[:2]:
        lines.append(f"Carried {x['carried']} times: {x['title']}.")
    for x in open_goals:
        if len(lines) >= 4:
            break
        if x not in carried:
            lines.append(f"Open: {x['title']}.")
    return lines


def _day(iso: str) -> str:
    """'Oct 7' for '2026-10-07' (no %-d: it does not exist on Windows)."""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%b} {d.day}"


def _due_items(v: Mapping[str, Any], today: str) -> list[tuple[bool, str]]:
    """(overdue, line) for every open date due today or before, overdue first."""
    items = []
    for venture in v.get("active") or []:
        for d in venture.get("dates") or []:
            due = str(d.get("due"))
            if d.get("overdue") or due == today:
                items.append((bool(d.get("overdue")), due, f"{venture['name']}: {d['label']}"))
    items.sort(key=lambda x: (not x[0], x[1]))
    return [(o, f"{label} ({'overdue since ' + _day(due) if o else 'due today'}).") for o, due, label in items]


def _ventures_lines(v: Mapping[str, Any], today: str) -> list[str]:
    lines = [line for _, line in _due_items(v, today)][:3]
    for venture in v.get("active") or []:
        if len(lines) >= 4:
            break
        if venture.get("next_action"):
            lines.append(f"{venture['name']} next: {venture['next_action']}.")
    return lines[:4]


def _capital_lines(c: Mapping[str, Any]) -> list[str]:
    lines = []
    for mode, m in (c.get("modes") or {}).items():
        label = "Paper" if mode == "paper" else "Live"
        change = m.get("change_today")
        lines.append(f"{label}: {_money(m.get('total'))}" + (f" ({'+' if float(change) >= 0 else ''}{_money(change)} today)."
                                                              if change is not None else "."))
    return lines


#: How the fallback words each section it knows. A later pillar adds one.
FALLBACK_SECTIONS: dict[str, Callable[[Mapping[str, Any], str], list[str]]] = {
    "goals": lambda p, _today: _goals_lines(p.get("goals") or {}),
    "ventures": lambda p, today: _ventures_lines(p.get("ventures") or {}, today),
    "capital": lambda p, _today: _capital_lines(p.get("capital") or {}),
}


def fallback_brief(payload: Mapping[str, Any], sections: Sequence[ledger.BriefSection]) -> Generated:
    """The deterministic template: same shape, plainer words, data only."""
    today = str(payload.get("local_date"))
    out = []
    for s in sections:
        render = FALLBACK_SECTIONS.get(s.key)
        data = payload.get(s.key)
        if render is None or not isinstance(data, dict) or "error" in data:
            continue
        lines = render(payload, today)
        if lines:
            out.append({"key": s.key, "title": s.title, "lines": lines})

    due = _due_items(payload.get("ventures") or {}, today)
    goals = (payload.get("goals") or {}).get("week") or {}
    open_goals = [x for x in goals.get("goals") or [] if x.get("state") == "open"]
    if due:
        one_move = f"Clear {due[0][1].rstrip('.')}"
    elif open_goals:
        one_move = f"Move one goal forward: {open_goals[0]['title']}"
    else:
        one_move = "Set this week's goals"
    overdue = sum(1 for o, _ in due if o)
    weekday = str(payload.get("weekday") or "Today")
    headline = (f"{weekday}: {overdue} overdue, {len(due) - overdue} due today"
                if due else f"{weekday}: a clear day to move the week")
    body = {"headline": headline, "sections": out, "one_move": one_move + "."}
    # Trim from the end until it fits: the cap holds for the template too.
    while word_count(body) > MAX_WORDS and any(s["lines"] for s in out):
        longest = max(out, key=lambda s: len(s["lines"]))
        longest["lines"].pop()
        out[:] = [s for s in out if s["lines"]]
    body = validate(body, [s.key for s in sections])
    return Generated(body, None, None, True, word_count(body))


# ------------------------------------------------------------------ the jobs

def build_today(*, allow_fallback: bool, client: Any = None) -> int:
    """Generate and store today's brief; returns the new row id.
    Refusal or two invalid briefs fall back at once; an API failure falls
    back only when `allow_fallback`, else it raises for a later retry."""
    payload = ledger.context_snapshot()
    # Registered brief sections that have a slice in the snapshot. A reserved
    # key (calendar) appears only once its provider is registered and enabled.
    sections = [s for s in ledger.brief_sections() if s.key in payload]
    try:
        made = generate_ai(payload, sections, client=client)
    except (BriefRefused, BriefInvalid) as exc:
        log.warning("brief: falling back (%s)", exc)
        made = fallback_brief(payload, sections)
    except Exception:
        if not allow_fallback:
            raise
        log.exception("brief: AI failed at the deadline; falling back")
        made = fallback_brief(payload, sections)
    day = date.fromisoformat(str(payload["local_date"]))
    brief_id = ledger.insert_brief(
        kind=KIND, day=day, body=made.body, model=made.model, ai_usage_id=made.ai_usage_id,
        fallback=made.fallback, voice_version=load_voice().version, word_count=made.words,
    )
    log.info("brief %d for %s: %d words, %s", brief_id, day, made.words,
             "fallback" if made.fallback else made.model)
    return brief_id


def morning_brief_job() -> None:
    """06:15 to 09:30 every 15 min, and at boot."""
    now = ledger.owner_local_now()
    decision = generate_decision(now.time(), ledger.latest_brief(KIND, now.date()) is not None)
    if decision != "generate":
        return
    build_today(allow_fallback=may_fall_back(now.time()))


def push_message(headline: str, *, late: bool, test: bool = False) -> push.Message:
    return push.Message(
        kind="morning_brief_test" if test else "morning_brief",
        title="Morning brief (test)" if test else "Morning brief",
        body=f"Late · {headline}" if late else headline,
        deep_link=LINK,
        tag="morning-brief",
    )


def _notification_id(results: Sequence[Any]) -> int | None:
    ids = [getattr(r, "notification_id", None) for r in results]
    return next((i for i in ids if i is not None), None)


def morning_brief_push_job() -> None:
    """09:45, and at boot: once per date, late after 09:50, never after 18:00."""
    now = ledger.owner_local_now()
    today = now.date()
    decision = push_decision(now.time(), ledger.brief_delivered(KIND, today))
    if decision in ("wait", "done"):
        return
    if decision == "stale":
        log.info("brief push for %s skipped: past %s", today, STALE_AFTER)
        return
    brief = ledger.latest_brief(KIND, today)
    if brief is None:  # a brief always exists by push time
        build_today(allow_fallback=True)
        brief = ledger.latest_brief(KIND, today)
    message = push_message(brief.body["headline"], late=decision == "late")
    ledger.deliver_brief_once(kind=KIND, day=today, brief_id=brief.id, late=decision == "late",
                              send=lambda: _notification_id(push.send(message)))
