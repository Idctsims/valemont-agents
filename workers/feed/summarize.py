"""feed_summarize: run after every feed_ingest (core/system_jobs.py).

Picks kept, real, linkable stories with no summary and a try left, newest
first: at most RUN_CAP per run and DAILY_CAP per Chicago day. Groups of
GROUP_SIZE, one core.ai.complete() call per group: Haiku, non-critical,
thinking off at low effort, a cached system prompt, structured JSON.

Input is the title and the lead item's teaser ONLY. No article page is ever
fetched.

Per story the model returns:
    summary   at most 2 sentences and 45 words, plain and direct, nothing
              beyond the teaser, no run of COPY_RUN consecutive words copied
              from the teaser or the title; a thin teaser gets one sentence
              restating the headline in new words.
    entities  people, teams, companies.
    topics    a few short snake_case topics.
    sport     cfb | nfl | nba | f1 | golf | none (used only when the story has none).
    drop      gossip | partisan_politics | ai_hype | crypto_price |
              recap_no_storyline | listicle | none   (the owner's don't-cares).

DROP IS IN SHADOW (owner, 2026-10-11): stored on the story (db/033
drop_category), filters nothing. DROP_LIVE flips it after the owner approves
the 24-hour list; then a non-none drop also sets filtered_reason.

Validation (validate()): known id, once; sentence and word counts; the copy
check. An invalid story keeps no summary and counts an attempt; it is retried
in the next run, then left NULL for good (MAX_ATTEMPTS): its card shows the
headline only.

A spent AI budget is not a failure: the job raises JobSkipped, which
job_health records as a note, and the feed still renders headlines.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence

from core import ai, ledger
from core.jobs import JobSkipped

log = logging.getLogger("valemont.feed.summarize")

MODEL = "claude-haiku-5-5"
PURPOSE = "feed_summarize"
GROUP_SIZE = 10
RUN_CAP = 60
DAILY_CAP = 600  # owner, 2026-10-11 (was 250)
MAX_ATTEMPTS = 2
MAX_SENTENCES = 2
MAX_WORDS = 45
COPY_RUN = 8
MAX_ENTITIES = 10
MAX_TOPICS = 6
DROP_LIVE = False

DROPS = ("gossip", "partisan_politics", "ai_hype", "crypto_price", "recap_no_storyline", "listicle", "none")
SPORTS = ("cfb", "nfl", "nba", "f1", "golf", "none")

SYSTEM = f"""You summarize news stories for one reader's private dashboard. For each story you get its id, headline, outlet, sport (if known) and teaser. Return one result per story.

summary
- At most {MAX_SENTENCES} sentences and at most {MAX_WORDS} words. Plain and direct: what happened, and the one fact that makes it matter.
- Use ONLY what the headline and teaser say. Never add a fact, number, name, date or claim that is not in them. No speculation, no "this could mean".
- Write in your own words. Never copy {COPY_RUN} or more consecutive words from the teaser or the headline.
- If the teaser is missing or too thin to say more, write ONE sentence that restates the headline in new words.
- No opinions, no hype words, no emoji, no markdown.

entities: the people, teams, leagues and companies the story is about (as named; at most {MAX_ENTITIES}).
topics: 1 to {MAX_TOPICS} short lowercase snake_case topics, e.g. media_rights, stadium_financing, nil, coaching_change, funding_round, m_and_a, hotel_development, music_publishing.
sport: cfb, nfl, nba, f1 or golf when the story is about that sport, otherwise none.

drop: classify against the reader's don't-care list. Pick the first that fits, else none.
- gossip: celebrity gossip, dating, relationships, breakups, feuds between personalities, red-carpet looks. Business news about a celebrity's company, deal or earnings is NOT gossip.
- partisan_politics: horse-race politics (polls, who is up or down, campaign trail) and culture-war fights. Policy that moves money (taxes, tariffs, regulation, antitrust, rates, budgets, labor law affecting a business or league) is none.
- ai_hype: generic "AI will change everything" takes and vague predictions. A concrete AI product launch, funding round, deal, lawsuit or number is none.
- crypto_price: crypto price chatter (up, down, predictions, "why it moved"). Crypto company business news (funding, deals, regulation, enforcement) is none.
- recap_no_storyline: a plain game or event recap or result with no storyline beyond the score. A result that changes a playoff picture, a record, a firing, an injury, or a trade is none.
- listicle: SEO lists and rankings with no news ("10 best...", "every X ranked"), shopping and deal posts.
- none: everything else.
ALWAYS none: sports-betting regulation, sportsbook and prediction-market business, and anything about Kalshi. Sports business (media rights, valuations, ownership, stadiums, NIL) is none.

Output only the JSON object."""


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
                    "required": ["id", "summary", "entities", "topics", "sport", "drop"],
                    "properties": {
                        "id": {"type": "integer"},
                        "summary": {"type": "string"},
                        "entities": {"type": "array", "items": {"type": "string"}},
                        "topics": {"type": "array", "items": {"type": "string"}},
                        "sport": {"type": "string", "enum": list(SPORTS)},
                        "drop": {"type": "string", "enum": list(DROPS)},
                    },
                },
            }
        },
    }


# ------------------------------------------------------------------ validator

_SENTENCE_END = re.compile(r"[.!?](?:[\"'”’)\]]*)(?=\s|$)")
_WORD = re.compile(r"[A-Za-z0-9']+")
_TOPIC = re.compile(r"[^a-z0-9]+")


class SummaryInvalid(ValueError):
    """The rule the summary broke."""


#: Periods that do not end a sentence: initials and initialisms (U.S., J.J.)
#: and common abbreviations (Mr., Jr., vs., Inc., No. 1).
_NOT_AN_END = re.compile(
    # Two or more initials (U.S., J.J.) never end a sentence here; a single
    # initial only before a lowercase word or another initial.
    r"\b(?:[A-Z]\.){2,3}|\b[A-Z]\.(?=\s+[a-z0-9]|\s*[A-Z]\.)"
    r"|\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|Inc|Co|Corp|Ltd|No|Gov|Sen|Rep|Gen|Mt|Ft|approx)\.",
)


def sentence_count(text: str) -> int:
    t = text.strip()
    t = _NOT_AN_END.sub(lambda m: m.group(0).replace(".", ""), t)
    ends = len(_SENTENCE_END.findall(t))
    # Text that does not end in punctuation still has a last sentence.
    return ends + (0 if re.search(r"[.!?][\"'”’)\]]*\s*$", t) else 1)


def words(text: str | None) -> list[str]:
    return [w.lower().strip("'") for w in _WORD.findall(text or "") if w.strip("'")]


def copied_run(summary: str, source: str | None, run: int = COPY_RUN) -> str | None:
    """The first run of `run` consecutive words the summary shares with
    `source`, or None."""
    src = words(source)
    if len(src) < run:
        return None
    grams = {tuple(src[i:i + run]) for i in range(len(src) - run + 1)}
    s = words(summary)
    for i in range(len(s) - run + 1):
        if tuple(s[i:i + run]) in grams:
            return " ".join(s[i:i + run])
    return None


@dataclass(frozen=True)
class Valid:
    summary: str
    entities: tuple[str, ...]
    topics: tuple[str, ...]
    sport: str | None
    drop: str


def validate(item: dict[str, Any], job: ledger.SummaryJob) -> Valid:
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
    drop = item.get("drop")
    if drop not in DROPS:
        raise SummaryInvalid(f"unknown drop {drop!r}")
    sport = item.get("sport")
    entities = tuple(dict.fromkeys(str(e).strip()[:80] for e in item.get("entities") or [] if str(e).strip()))
    topics = tuple(dict.fromkeys(t for t in (_TOPIC.sub("_", str(x).lower()).strip("_")[:40]
                                            for x in item.get("topics") or []) if t))
    return Valid(summary, entities[:MAX_ENTITIES], topics[:MAX_TOPICS],
                 sport if sport in SPORTS and sport != "none" else None, drop)


def results_for(group: Sequence[ledger.SummaryJob], payload: Any) -> tuple[list[ledger.SummaryOut], list[str]]:
    """Every story in the group gets a SummaryOut: accepted, or summary None
    (an attempt counted). Returns (results, problems) for the log."""
    by_id = {j.story_id: j for j in group}
    accepted: dict[int, ledger.SummaryOut] = {}
    problems: list[str] = []
    items = payload.get("stories") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        sid = item.get("id") if isinstance(item, dict) else None
        if sid not in by_id:
            problems.append(f"unknown story id {sid!r}")
            continue
        if sid in accepted:
            problems.append(f"story {sid} returned twice")
            continue
        try:
            v = validate(item, by_id[sid])
        except SummaryInvalid as exc:
            problems.append(f"story {sid}: {exc}")
            continue
        accepted[sid] = ledger.SummaryOut(
            story_id=sid, summary=v.summary, entities=v.entities, topics=v.topics, sport=v.sport,
            drop=v.drop, filtered_reason=f"drop:{v.drop}" if DROP_LIVE and v.drop != "none" else None)
    missing = [sid for sid in by_id if sid not in accepted]
    return [accepted.get(sid) or ledger.SummaryOut(sid, None) for sid in by_id], problems + (
        [f"no valid result for {len(missing)} of {len(by_id)}"] if missing else [])


# ------------------------------------------------------------------ the job

def user_message(group: Sequence[ledger.SummaryJob]) -> str:
    return "STORIES:\n" + json.dumps([
        {"id": j.story_id, "headline": j.title, "outlet": j.source_name,
         "sport": j.sport, "teaser": j.teaser or ""} for j in group], ensure_ascii=False)


def summarize_group(group: Sequence[ledger.SummaryJob], *, client: Any = None) -> tuple[int, int]:
    """One call for up to GROUP_SIZE stories. Returns (accepted, rejected)."""
    recorded: list[int] = []
    response = ai.complete(
        purpose=PURPOSE, model=MODEL, critical=False, max_tokens=4000, client=client,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message(group)}],
        thinking={"type": "disabled"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema()}},
        on_recorded=lambda usage_id, _model, _cost: recorded.append(usage_id),
    )
    if getattr(response, "stop_reason", None) == "refusal":
        payload: Any = None
        log.warning("feed summarize: the model declined a group of %d", len(group))
    else:
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
            log.warning("feed summarize: the answer was not JSON")
    results, problems = results_for(group, payload)
    for p in problems:
        log.warning("feed summarize: %s", p)
    ledger.feed_save_summaries(results, recorded[-1] if recorded else None)
    ok = sum(1 for r in results if r.summary is not None)
    return ok, len(results) - ok


def summarize_job(*, client: Any = None) -> tuple[int, int]:
    """The feed_summarize system job. Returns (accepted, rejected)."""
    room = min(RUN_CAP, DAILY_CAP - ledger.feed_summarized_today())
    if room <= 0:
        log.info("feed summarize: daily cap of %d reached", DAILY_CAP)
        return 0, 0
    queue = ledger.feed_summary_queue(room, MAX_ATTEMPTS)
    accepted = rejected = 0
    for i in range(0, len(queue), GROUP_SIZE):
        try:
            ok, bad = summarize_group(queue[i:i + GROUP_SIZE], client=client)
        except ai.BudgetExceeded as exc:
            raise JobSkipped(f"AI budget spent; headlines only ({accepted} summarized first)") from exc
        accepted, rejected = accepted + ok, rejected + bad
    if queue:
        log.info("feed summarize: %d accepted, %d rejected of %d", accepted, rejected, len(queue))
    return accepted, rejected
