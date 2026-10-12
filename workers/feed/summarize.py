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
check: no run of COPY_RUN consecutive words shared with the teaser or the
headline, counting ordinary words only. Number tokens and capitalized-name
tokens are left out of the measure (owner, 2026-10-11): on the first passes
the check rejected ~20% of summaries, many for restating "the Denver Broncos
(2-2) and Los Angeles Chargers (3-1)".

A story that fails ONLY the copy check is retried in the same run, once:
the retry names the copied phrase and asks for a rewrite in different words
(retry_message; owner, 2026-10-11). The first call and the retry are its two
attempts. Any other failure is retried in the next run. An invalid story keeps no summary and counts an attempt; it is retried
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


#: Capitalized function words are ordinary prose, not names: "The", "In"...
#: at the start of a sentence still count toward the overlap.
_FUNCTION_WORDS = frozenset(
    "a an the and or but nor so yet in on at to for of with by from as into onto over under after before "
    "about against between during without within it its this that these those he she they we you i his "
    "her their our your who which what when where why how is are was were be been has have had will would "
    "can could may might should not no".split())


def measured_words(text: str | None) -> list[str]:
    """The words the copy check measures (owner, 2026-10-11): every word
    except number tokens (any digit: "2-2", "35", "2027") and capitalized-name
    tokens (team, person, company names: "Denver", "Broncos", "LIV"), so a
    summary may repeat the facts and names its teaser states. Capitalized
    function words still count."""
    out = []
    for raw in _WORD.findall(text or ""):
        w = raw.strip("'")
        if not w or any(ch.isdigit() for ch in w):
            continue
        if w[0].isupper() and w.lower() not in _FUNCTION_WORDS:
            continue
        out.append(w.lower())
    return out


def copied_run(summary: str, source: str | None, run: int = COPY_RUN) -> str | None:
    """The first run of `run` consecutive MEASURED words the summary shares
    with `source`, or None. Names and numbers are left out of both texts
    before comparing (measured_words); the threshold is unchanged."""
    src = measured_words(source)
    if len(src) < run:
        return None
    grams = {tuple(src[i:i + run]) for i in range(len(src) - run + 1)}
    s = measured_words(summary)
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
            raise CopyInvalid(f"copies {COPY_RUN} words from the {name}: {run!r}", run, summary)
    drop = item.get("drop")
    if drop not in DROPS:
        raise SummaryInvalid(f"unknown drop {drop!r}")
    sport = item.get("sport")
    entities = tuple(dict.fromkeys(str(e).strip()[:80] for e in item.get("entities") or [] if str(e).strip()))
    topics = tuple(dict.fromkeys(t for t in (_TOPIC.sub("_", str(x).lower()).strip("_")[:40]
                                            for x in item.get("topics") or []) if t))
    return Valid(summary, entities[:MAX_ENTITIES], topics[:MAX_TOPICS],
                 sport if sport in SPORTS and sport != "none" else None, drop)


class CopyInvalid(SummaryInvalid):
    """The copy check failed: `phrase` is the shared run (measured words) and
    `summary` the text that copied it, for the targeted retry."""

    def __init__(self, message: str, phrase: str, summary: str) -> None:
        super().__init__(message)
        self.phrase, self.summary = phrase, summary


@dataclass(frozen=True)
class Copied:
    story_id: int
    summary: str
    phrase: str


def results_for(group: Sequence[ledger.SummaryJob], payload: Any, *,
                attempts: int = 1) -> tuple[list[ledger.SummaryOut], list[str], list[Copied]]:
    """Every story in the group gets a SummaryOut: accepted, or summary None
    (`attempts` counted). Returns (results, problems for the log, the stories
    that failed ONLY the copy check, for the targeted retry)."""
    by_id = {j.story_id: j for j in group}
    accepted: dict[int, ledger.SummaryOut] = {}
    problems: list[str] = []
    copied: dict[int, Copied] = {}
    items = payload.get("stories") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        sid = item.get("id") if isinstance(item, dict) else None
        if sid not in by_id:
            problems.append(f"unknown story id {sid!r}")
            continue
        if sid in accepted or sid in copied:
            problems.append(f"story {sid} returned twice")
            continue
        try:
            v = validate(item, by_id[sid])
        except CopyInvalid as exc:
            problems.append(f"story {sid}: {exc}")
            copied[sid] = Copied(sid, exc.summary, exc.phrase)
            continue
        except SummaryInvalid as exc:
            problems.append(f"story {sid}: {exc}")
            continue
        accepted[sid] = ledger.SummaryOut(
            story_id=sid, summary=v.summary, entities=v.entities, topics=v.topics, sport=v.sport,
            drop=v.drop, filtered_reason=f"drop:{v.drop}" if DROP_LIVE and v.drop != "none" else None,
            attempts=attempts)
    missing = [sid for sid in by_id if sid not in accepted]
    return ([accepted.get(sid) or ledger.SummaryOut(sid, None, attempts=attempts) for sid in by_id],
            problems + ([f"no valid result for {len(missing)} of {len(by_id)}"] if missing else []),
            list(copied.values()))


# ------------------------------------------------------------------ the job

def user_message(group: Sequence[ledger.SummaryJob]) -> str:
    return "STORIES:\n" + json.dumps([
        {"id": j.story_id, "headline": j.title, "outlet": j.source_name,
         "sport": j.sport, "teaser": j.teaser or ""} for j in group], ensure_ascii=False)


def retry_message(group: Sequence[ledger.SummaryJob], copied: Sequence[Copied]) -> str:
    """The targeted retry (owner, 2026-10-11): each story's rejected summary
    and the phrase it copied, with a request to rewrite in different words."""
    by_id = {j.story_id: j for j in group}
    return (
        "REWRITE: each summary below repeated wording from its teaser or headline. For each story, write a "
        "new summary in different words: keep the same facts and follow every rule above, and do not reuse "
        "the copied phrase (names and numbers may stay).\nSTORIES:\n"
        + json.dumps([{"id": c.story_id, "headline": by_id[c.story_id].title,
                       "outlet": by_id[c.story_id].source_name, "sport": by_id[c.story_id].sport,
                       "teaser": by_id[c.story_id].teaser or "", "rejected_summary": c.summary,
                       "copied_phrase": c.phrase} for c in copied], ensure_ascii=False))


def _ask(content: str, client: Any, recorded: list[int], label: str) -> Any:
    response = ai.complete(
        purpose=PURPOSE, model=MODEL, critical=False, max_tokens=4000, client=client,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        thinking={"type": "disabled"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema()}},
        on_recorded=lambda usage_id, _model, _cost: recorded.append(usage_id),
    )
    if getattr(response, "stop_reason", None) == "refusal":
        log.warning("feed summarize: the model declined a %s", label)
        return None
    text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("feed summarize: the %s answer was not JSON", label)
        return None


@dataclass
class SummaryRun:
    accepted: int = 0
    rejected: int = 0
    #: Stories sent back once, in the same run, for copying wording.
    retried: int = 0
    #: Of those, accepted on the retry.
    retry_passed: int = 0

    def add(self, other: "SummaryRun") -> None:
        self.accepted += other.accepted
        self.rejected += other.rejected
        self.retried += other.retried
        self.retry_passed += other.retry_passed


def summarize_group(group: Sequence[ledger.SummaryJob], *, client: Any = None) -> SummaryRun:
    """One call for up to GROUP_SIZE stories, then one targeted retry for the
    stories that failed only the copy check and have a try left: the retry
    names the copied phrase (retry_message). The first call and the retry are
    the story's two attempts; any other failure is retried in the next run."""
    first: list[int] = []
    results, problems, copied = results_for(group, _ask(user_message(group), client, first, "group"))
    run = SummaryRun()
    attempts_left = {j.story_id: MAX_ATTEMPTS - j.attempts - 1 for j in group}
    copied = [c for c in copied if attempts_left[c.story_id] > 0]
    retried: list[ledger.SummaryOut] = []
    second: list[int] = []
    if copied:
        retry_ids = {c.story_id for c in copied}
        retried, retry_problems, _ = results_for(
            [j for j in group if j.story_id in retry_ids],
            _ask(retry_message(group, copied), client, second, "retry"), attempts=2)
        problems += [f"retry: {p}" for p in retry_problems]
        results = [r for r in results if r.story_id not in retry_ids]
        run.retried = len(copied)
        run.retry_passed = sum(1 for r in retried if r.summary is not None)
    for p in problems:
        log.warning("feed summarize: %s", p)
    # Each story names the call its summary came from.
    ledger.feed_save_summaries(results, first[-1] if first else None)
    if retried:
        ledger.feed_save_summaries(retried, second[-1] if second else None)
    run.accepted = sum(1 for r in results + retried if r.summary is not None)
    run.rejected = len(results) + len(retried) - run.accepted
    return run


def summarize_job(*, client: Any = None) -> SummaryRun:
    """The feed_summarize system job."""
    total = SummaryRun()
    room = min(RUN_CAP, DAILY_CAP - ledger.feed_summarized_today())
    if room <= 0:
        log.info("feed summarize: daily cap of %d reached", DAILY_CAP)
        return total
    queue = ledger.feed_summary_queue(room, MAX_ATTEMPTS)
    for i in range(0, len(queue), GROUP_SIZE):
        try:
            total.add(summarize_group(queue[i:i + GROUP_SIZE], client=client))
        except ai.BudgetExceeded as exc:
            raise JobSkipped(f"AI budget spent; headlines only ({total.accepted} summarized first)") from exc
    if queue:
        log.info("feed summarize: %d accepted, %d rejected of %d; %d retried for copying, %d passed the retry",
                 total.accepted, total.rejected, len(queue), total.retried, total.retry_passed)
    return total
