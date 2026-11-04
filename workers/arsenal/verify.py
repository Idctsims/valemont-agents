"""Verification: one reference or one Power Word at a time.

A REFERENCE (docs/arsenal.md §4; retrieval reworked by the owner, 2026-10-16,
without loosening the judge):
  1. Locate (candidates()). The pages that could hold the passage, best
     first: the card's own source_page and title, then MediaWiki searches
     (list=search) for the specific WORK (the song, the film, the letter,
     with its person and year to pick the right page out of a
     disambiguation: Moneyball the book, not the film), and on Wikiquote for
     the quote's key phrase and the person. Each page is cut into sections;
     the lead plus the sections that match the card (and the quote) are what
     the judge reads (sources.excerpt), never the first 5,000 characters.
     Pages are ranked by their best section.
  2. Quote. Matched (normalized, fuzzy) against the Wikiquote lines of the
     allowed (sourced) sections, else the Wikipedia sentences, of every
     candidate page. No match: the quote is dropped and the card stays as a
     described move, 'corrected'.
  3. Judge. Haiku (non-critical, thinking off) reads the excerpt and the
     card: supported | corrected (with the fixed wording) | contradicted |
     insufficient. It also checks the OUTCOME: when the reference's real
     ending undercuts the meaning or the deploy line (Thermopylae was a total
     defeat), it reframes them (corrected) or the card is cut (unverified);
     either way the note starts "outcome_conflict".
  4. 'insufficient' tries ONE second source (the next candidate page) before
     landing unverified; 'contradicted' and an outcome conflict never retry.
  5. Map (outcome()): the worse of the quote step and the judge wins.
         supported                      -> verified (corrected if the quote went)
         corrected, with usable wording -> corrected
         contradicted / insufficient    -> unverified
         no page, or the source would not answer in 2 tries -> unverified
     Unverified never drops (db/054's drop guard).

A WORD: kaikki's etymology (two lines) and IPA, never the model's. No entry,
no etymology or no pronunciation: unverified, with the reason.

The queue handler is verify_job (job_queue kind 'arsenal_verify', payload
{'ref_id': n} or {'word_id': n}); core/system_jobs runs it through
run_tracked, so it has its own job_health row and RSS / KB-read line.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from arsenal import sources
from arsenal.cards import response_json
from core import ai, ledger

log = logging.getLogger("valemont.arsenal.verify")

PURPOSE = "arsenal_verify"
#: Haiku 5.5 takes no temperature: non-default sampling is a 400 on this
#: model, and the installed SDK's create() has no such argument (the owner
#: asked for temperature 0 on 2026-10-16; checked the same day). Verdict
#: drift is held by covers_every_claim / held() and, for the outcome check,
#: by a vote of OUTCOME_SAMPLES reads.
MODEL = "claude-haiku-5-5"
VERDICTS = ("supported", "corrected", "contradicted", "insufficient")

SYSTEM = """\
You check one reference card against a source excerpt, using ONLY the excerpt.

- supported: the origin and the source_work agree with the excerpt (a paraphrase is fine).
- corrected: the substance is right but a detail is wrong (a date, a name, a number, "burned" where the excerpt says "scuttled"), AND the excerpt itself states the right detail. Give fixed_origin (at most 280 characters, two short lines, the same voice) and/or fixed_source_work.
- contradicted: the excerpt is about the card's subject and says otherwise on the substance.
- insufficient: the excerpt does not cover it, or covers only part of it, or is about a different subject.

Never write a fix from your own knowledge; every word of a fix comes from the excerpt.

covers_every_claim: true only when the excerpt mentions every claim of the card's origin (who, what, when, the detail) and its source_work; false when any of them is missing from the excerpt.

The trim (the only way a silent excerpt can still clear a card). When the excerpt CONFIRMS THE CORE of the card (who did it, what they did, and when) but is silent on one or more other details (a flourish, a number, a scene detail):
- core_confirmed: true; dropped_details: each detail the excerpt does not mention, one short phrase each.
- verdict corrected, with fixed_origin REWRITTEN FROM THE EXCERPT'S OWN WORDING: the core only, none of the dropped details.
- deploy_depends_on_dropped: true when the deploy_line or meaning only works because of a dropped detail.
When the excerpt does not confirm the core, or says otherwise: core_confirmed false, and insufficient or contradicted as above. When nothing is dropped, dropped_details is empty.

The outcome check. When the excerpt shows how the story really ended and that ending undercuts the card's meaning or deploy_line (Thermopylae was a total defeat for the Greeks; it cannot be a pitch line about winning), set outcome_conflict to true. Then either reframe it honestly (verdict corrected, with fixed_meaning and/or fixed_deploy_line that fit the real ending, same voice, one breath) or, when no honest reframe works, answer contradicted. When the ending fits the card, outcome_conflict is false.

note: one short sentence saying why. Answer with the JSON the schema asks for."""


def schema() -> dict[str, Any]:
    nullable = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    fields = ("verdict", "covers_every_claim", "core_confirmed", "dropped_details", "deploy_depends_on_dropped",
              "outcome_conflict", "fixed_origin", "fixed_source_work", "fixed_meaning", "fixed_deploy_line", "note")
    return {"type": "object", "additionalProperties": False, "required": list(fields),
            "properties": {"verdict": {"type": "string", "enum": list(VERDICTS)},
                           "covers_every_claim": {"type": "boolean"}, "core_confirmed": {"type": "boolean"},
                           "dropped_details": {"type": "array", "items": {"type": "string"}},
                           "deploy_depends_on_dropped": {"type": "boolean"},
                           "outcome_conflict": {"type": "boolean"},
                           "fixed_origin": nullable, "fixed_source_work": nullable, "fixed_meaning": nullable,
                           "fixed_deploy_line": nullable, "note": {"type": "string"}}}


@dataclass(frozen=True)
class Judgement:
    verdict: str
    fixed_origin: str | None
    fixed_source_work: str | None
    note: str
    outcome_conflict: bool = False
    fixed_meaning: str | None = None
    fixed_deploy_line: str | None = None
    #: The judge's own answer to "does the excerpt mention every claim?".
    #: A model answer that omits it counts as False (parse_judgement).
    covers_every_claim: bool = True
    #: The guarded trim (owner, 2026-11-04): the excerpt confirms who, what
    #: and when; the details it is silent on; whether the deploy line or
    #: meaning leans on one of them. Omitted answers count against a trim.
    core_confirmed: bool = False
    dropped_details: tuple[str, ...] = ()
    deploy_depends_on_dropped: bool = True


def held(j: Judgement, ref: ledger.ArsenalRefToVerify | None = None) -> Judgement:
    """The judge held to its own coverage answer: 'supported' or 'corrected'
    on an excerpt that does not cover every claim is 'insufficient' (owner,
    2026-10-16: a silent excerpt never trims a card into 'corrected').
    An outcome reframe is held to it too (verifier yield, 2026-11-04: the
    frozen Vader misquote was reframed into 'corrected' on an excerpt that
    never showed its line); an uncovered conflict stays unverified, its
    outcome_conflict note kept."""
    if j.verdict in ("supported", "corrected") and not j.covers_every_claim:
        trim = trimmed(j) if ref is None or trim_allowed(ref, j) else None
        if trim is not None:
            return trim
        if j.outcome_conflict:
            return Judgement("insufficient", None, None,
                             f"the reframe's excerpt does not cover every claim ({j.note})".replace(" ()", ""),
                             outcome_conflict=True, covers_every_claim=False)
        return Judgement("insufficient", None, None, f"the excerpt does not cover every claim ({j.note})".replace(" ()", ""))
    return j


#: A trimmed card's note starts so (and lists every detail it dropped).
TRIMMED = "corrected by trimming"


def trim_allowed(ref: ledger.ArsenalRefToVerify, j: Judgement) -> bool:
    """The card's own subject is never trimmed: a dropped detail that names
    the card's title (The Grave Dancer's article, the frozen controls) means
    the core is NOT confirmed. The person's own names are not the subject's
    words for this test (a dropped "Jordan scored 38" still names Jordan)."""
    person, _, _ = split_work(ref.source_work)
    subject = sources.key_terms(ref.title) - sources.key_terms(person, _CREDIT.sub("", ref.quote_by or ""))
    if any(subject & sources.key_terms(d) for d in j.dropped_details):
        return False
    # The deploy line and meaning never lean on a dropped detail (owner,
    # 2026-11-04), checked here as well as asked of the judge: a dropped detail
    # sharing a distinctive word with them ("wins the room on substance"
    # against "still hold the room") means the card's point went with it.
    point = (sources.key_terms(ref.meaning, ref.deploy_line) - subject
             - sources.key_terms(person, _CREDIT.sub("", ref.quote_by or "")))
    if any(point & sources.key_terms(d) for d in j.dropped_details):
        return False
    # The core's WHEN: every year the card's origin names survives into the
    # rewritten origin. A page that gives another year (The Grave Dancer: 1982,
    # not 1976) does not confirm the core; that is never a trim.
    years = set(_YEAR.findall(ref.origin))
    return years <= set(_YEAR.findall(j.fixed_origin or ""))


def rests_on_unfound_quote(ref: ledger.ArsenalRefToVerify) -> bool:
    """The origin asserts the card's quote ("Vader reveals himself with the
    exact line 'Luke, I am your father'") while no source holds that quote:
    dropping the quote cannot leave a true card behind (the frozen Vader
    misquote, 2026-11-04)."""
    return bool(ref.quote) and sources.quote_found(ref.quote, [ref.origin])


def trimmed(j: Judgement) -> Judgement | None:
    """THE GUARDED TRIM (owner, 2026-11-04; it amends the Oct 16 rule that a
    silent excerpt never clears a card). An excerpt that confirms the core
    (who, what, when) but is silent on other details clears the card as
    'corrected' ONLY when all hold:
      * the judge says the core is confirmed and names what it dropped;
      * the card is rewritten from the page's wording (fixed_origin);
      * the deploy line and meaning do not lean on a dropped detail;
      * no outcome conflict rides on it (that path has its own reframe).
    The note lists every dropped detail. Otherwise None (insufficient)."""
    if (j.verdict != "corrected" or not j.core_confirmed or not j.dropped_details or not j.fixed_origin
            or j.deploy_depends_on_dropped or j.outcome_conflict):
        return None
    dropped = "; ".join(j.dropped_details)
    return Judgement("corrected", j.fixed_origin, j.fixed_source_work,
                     f"{TRIMMED} (dropped: {dropped}): {j.note}"[:300], outcome_conflict=False,
                     fixed_meaning=j.fixed_meaning, fixed_deploy_line=j.fixed_deploy_line, covers_every_claim=False,
                     core_confirmed=True, dropped_details=j.dropped_details, deploy_depends_on_dropped=False)


#: (card, excerpt) -> Judgement. Injected by tests; production calls Haiku.
Ask = Callable[[ledger.ArsenalRefToVerify, str], Judgement]


def ask_haiku(ref: ledger.ArsenalRefToVerify, excerpt: str, *, client: Any = None) -> Judgement:
    """The judge: Haiku, one read (owner, 2026-11-04: the Sonnet and escalation
    trials did not pass the frozen controls; docs/arsenal.md §11.5)."""
    card = {"title": ref.title, "origin": ref.origin, "meaning": ref.meaning, "deploy_line": ref.deploy_line,
            "source_work": ref.source_work, "quote": ref.quote, "quote_by": ref.quote_by}
    response = ai.complete(
        purpose=PURPOSE, model=MODEL, critical=False, max_tokens=600, client=client,
        system=SYSTEM,
        messages=[{"role": "user", "content": "CARD:\n" + json.dumps(card, ensure_ascii=False)
                   + "\n\nEXCERPT:\n" + excerpt[:sources.JUDGE_CHARS + 200]}],
        thinking={"type": "disabled"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema()}},
    )
    return parse_judgement(response_json(response))


def parse_judgement(data: Any) -> Judgement:
    """A judgement from the model's JSON; anything unreadable is insufficient."""
    if not isinstance(data, Mapping) or data.get("verdict") not in VERDICTS:
        return Judgement("insufficient", None, None, "the judge's answer was unreadable")

    def text(key: str, lo: int, hi: int) -> str | None:
        t = " ".join(str(data.get(key) or "").split())
        return t if lo <= len(t) <= hi else None

    return Judgement(str(data["verdict"]), text("fixed_origin", 10, 280), text("fixed_source_work", 2, 160),
                     " ".join(str(data.get("note") or "").split())[:300],
                     outcome_conflict=data.get("outcome_conflict") is True,
                     fixed_meaning=text("fixed_meaning", 5, 300), fixed_deploy_line=text("fixed_deploy_line", 5, 220),
                     covers_every_claim=data.get("covers_every_claim") is True,
                     core_confirmed=data.get("core_confirmed") is True,
                     dropped_details=tuple(" ".join(str(d).split())[:80] for d in (data.get("dropped_details") or [])
                                           if str(d).strip())[:6],
                     deploy_depends_on_dropped=data.get("deploy_depends_on_dropped") is not False)


# ------------------------------------------------------------------ mapping

RANK = {"verified": 0, "corrected": 1, "unverified": 2}
#: The note of every card whose real ending undercut it (owner, 2026-10-16).
OUTCOME_CONFLICT = "outcome_conflict"


def outcome(*, quote_dropped: bool, judgement: Judgement | None, unreachable: str | None) -> tuple[str, str]:
    """(verification, why) from the quote step and the judge; the worse wins.
    An outcome conflict is reframed (corrected, with new wording) or cut."""
    if unreachable:
        return "unverified", unreachable
    assert judgement is not None
    if judgement.outcome_conflict:
        if judgement.verdict == "corrected" and (judgement.fixed_meaning or judgement.fixed_deploy_line):
            return "corrected", f"{OUTCOME_CONFLICT}: reframed: {judgement.note}".rstrip(": ")
        return "unverified", f"{OUTCOME_CONFLICT}: {judgement.note}".rstrip(": ")
    if judgement.verdict in ("contradicted", "insufficient"):
        return "unverified", f"{judgement.verdict}: {judgement.note}".rstrip(": ")
    if judgement.verdict == "corrected":
        if judgement.fixed_origin is None and judgement.fixed_source_work is None:
            return "unverified", "the judge corrected it without usable wording"
        if judgement.note.startswith(TRIMMED):
            return "corrected", judgement.note
        return "corrected", f"corrected by the source: {judgement.note}".rstrip(": ")
    return ("corrected", "") if quote_dropped else ("verified", "")


@dataclass(frozen=True)
class RefVerdict:
    verification: str
    note: str | None
    source_url: str | None
    origin: str | None = None
    source_work: str | None = None
    drop_quote: bool = False
    meaning: str | None = None
    deploy_line: str | None = None
    #: The pages the judge read, in order (one, or two after 'insufficient').
    judged_pages: tuple[str, ...] = ()


# ---------------------------------------------------------------- retrieval

#: Pages fetched per wiki, at most (each Wikipedia page is two requests).
MAX_WIKIPEDIA_PAGES = 5
MAX_WIKIQUOTE_PAGES = 3
#: Claim terms added to the title search ("The Flu Game" + "Jordan fever 38").
CLAIM_TERMS = 4
#: For film and TV, these sections always make the excerpt (the story is there).
PLOT_HEADINGS = ("plot", "synopsis", "premise", "summary", "story", "episodes", "season")
#: A page about something other than the card's work (verifier yield, owner
#: 2026-11-04: no concept pages, soundtracks, unrelated people).
OFF_SUBJECT = re.compile(r"\((?:[^()]*\b)?(?:soundtrack|album|song|single|video game|disambiguation|band|"
                         r"musical|novel|play|board game)\)|^list of |discography|filmography", re.I)
#: The same kinds, read from Wikipedia's one-line description ("2021 song by
#: Bo Burnham", "2005 studio album by Teairra Mari"): titles alone miss them.
OFF_SUBJECT_DESCRIPTION = re.compile(r"\b(?:song|single|album|soundtrack|video game|board game|band|musical|"
                                     r"disambiguation|given name|surname|topics referred to)\b", re.I)
_QUALIFIER = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")
_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_CREDIT = re.compile(r"\s*\(as written in [^()]*\)\s*$", re.I)
#: The quote's key phrase for a phrase search: its first words.
PHRASE_WORDS = 6
_TRAILING_PARENS = re.compile(r"\s*\(([^()]*)\)\s*$")


def split_work(source_work: str) -> tuple[str | None, str, str]:
    """(person, work, context) from a source_work: "Jay-Z, Diamonds from
    Sierra Leone (Remix) (2005)" -> ("Jay-Z", "Diamonds from Sierra Leone",
    "Remix 2005"); "Moneyball (Michael Lewis, 2003)" -> (None, "Moneyball",
    "Michael Lewis, 2003")."""
    work, context = source_work.strip(), []
    while (m := _TRAILING_PARENS.search(work)) and m.start() > 0:
        context.insert(0, m.group(1))
        work = work[:m.start()]
    person = None
    head, sep, rest = work.partition(", ")
    if sep and rest and len(head.split()) <= 4:
        person, work = head, rest
    return person, work.strip(), " ".join(context)


@dataclass(frozen=True)
class Candidate:
    kind: str  # 'wikipedia' | 'wikiquote'
    title: str
    url: str
    excerpt: sources.Excerpt
    #: Quote lines (Wikiquote) or sentences (Wikipedia): the quote step's corpus.
    lines: tuple[str, ...]


def _unique(names: list[str | None]) -> list[str]:
    seen: set[str] = set()
    out = []
    for n in names:
        k = (n or "").strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


def _surname(name: str | None) -> str | None:
    """The last distinctive word of a name: "Sam Zell" -> "zell"; a film
    credit's "(as written in ...)" is dropped first."""
    words = sorted(sources.key_terms(_CREDIT.sub("", name or "")), key=(name or "").lower().find)
    return words[-1] if words else None


def on_subject(ref: ledger.ArsenalRefToVerify, page_title: str) -> tuple[bool, str]:
    """Is this page about the card's subject? (verdict, why). Verifier yield
    (owner, 2026-11-04): a second source whose title does not match the
    card's subject is rejected: no concept pages, soundtracks, unrelated
    people. The card's own named page always passes."""
    title = page_title.strip()
    own = (ref.source_page or "").strip()
    if own and title.lower() == own.lower():
        return True, "the card's own page"
    m = _QUALIFIER.match(own)
    person, work, _ = split_work(ref.source_work)
    # A film or show's episode list holds its plot: allowed when it names the work.
    episodes = (ref.category == "film_tv" and title.lower().startswith("list of ")
                and bool(sources.key_terms(work)) and sources.key_terms(work) <= sources.key_terms(title))
    if OFF_SUBJECT.search(title) and not OFF_SUBJECT.search(own) and not episodes:
        return False, "a page about something else (soundtrack, album, list, ...)"
    if m:
        base, qualifier = m.group(1).strip().lower(), m.group(2).strip().lower()
        t = _QUALIFIER.match(title)
        if title.lower() == base:
            return False, f"the bare title when the card's page is {own!r} (a concept or other page)"
        if t and t.group(1).strip().lower() == base and t.group(2).strip().lower() != qualifier:
            return False, f"another {base!r} ({t.group(2)}), not the card's ({qualifier})"
    if episodes:
        return True, "the work's episode list"
    words = sources.key_terms(title)
    names = {n for n in (_surname(person), _surname(ref.quote_by)) if n}
    if names & words:
        return True, "names the card's person"
    for terms in (sources.key_terms(work), sources.key_terms(ref.title),
                  sources.key_terms(m.group(1)) if m else sources.key_terms(own)):
        if terms and terms <= words:
            return True, "holds the work's or the card's title"
    return False, "names neither the card's person nor its work"


def episode_pages(ref: ledger.ArsenalRefToVerify) -> list[str]:
    """For a TV card, the pages that tell its episodes: "List of The Diplomat
    (American TV series) episodes" and "The Diplomat (American TV series)
    season 1". A series' own page rarely has the scene a card names; its
    episode list does (the Diplomat controls, 2026-11-04). A missing page
    costs one 404."""
    own = (ref.source_page or "").strip()
    if ref.category != "film_tv" or not re.search(r"\((?:[^()]*\s)?(?:TV series|miniseries)\)$", own, re.I):
        return []
    base = _QUALIFIER.match(own).group(1).strip()  # type: ignore[union-attr]
    return [f"List of {own} episodes", f"List of {base} episodes"]


def claim_query(ref: ledger.ArsenalRefToVerify) -> str:
    """The card's title plus its first CLAIM_TERMS distinctive origin words
    not already in the title: "The Flu Game Jordan 1997 fever ..."."""
    have = sources.key_terms(ref.title)
    extra = [w for w in sources.normalize(ref.origin).split()
             if w in sources.key_terms(ref.origin) and w not in have]
    return " ".join([ref.title, *list(dict.fromkeys(extra))[:CLAIM_TERMS]])


def candidates(ref: ledger.ArsenalRefToVerify, *, http: sources.Http | None = None) -> tuple[list[Candidate], list[str]]:
    """Every page that could hold the passage, ranked by its best-matching
    section (the work's own page wins ties: it is searched first). Returns
    (candidates, the sources that would not answer). A page that is not about
    the card's subject (on_subject) is never a candidate."""
    person, work, context = split_work(ref.source_work)
    strong = sources.key_terms(ref.title, work, context, person, ref.quote, _CREDIT.sub("", ref.quote_by or ""))
    weak = sources.key_terms(ref.origin)
    # A person's Wikiquote page holds their remarks ("the ovarian lottery").
    want_quotes = (ref.category == "film_tv" or ref.quote is not None or ref.source_kind == "wikiquote"
                   or person is not None)
    # The work's own section first ("Game 5" of the 1997 NBA Finals), and for
    # film and TV the plot.
    prefer = tuple(p for p in (work.strip().lower(),) if p) + (PLOT_HEADINGS if ref.category == "film_tv" else ())
    down: list[str] = []

    def searched(api: str, query: str) -> list[str]:
        try:
            return sources.search(api, query, http=http)
        except sources.SourceUnavailable as exc:
            down.append(str(exc))
            return []

    # The work with its person ("Enchiridion Epictetus"), else with its own
    # context ("Moneyball Michael Lewis, 2003": the book, not the film).
    # The specific event, work or person: the title with its key claim terms
    # first ("The Flu Game" + the origin's words), then the work with its
    # person or context ("Moneyball Michael Lewis, 2003": the book, not the
    # film), then the title with the person or work. Off-subject names are
    # dropped before anything is fetched.
    wp_names = _unique([ref.source_page, *episode_pages(ref), *searched(sources.WIKIPEDIA_API, claim_query(ref)),
                        *searched(sources.WIKIPEDIA_API, f"{work} {person or context}"),
                        ref.title, *searched(sources.WIKIPEDIA_API, f"{ref.title} {person or work}")])
    wp_names = [n for n in wp_names if on_subject(ref, n)[0]]
    wq_names: list[str] = []
    if want_quotes:
        phrase = " ".join((ref.quote or "").split()[:PHRASE_WORDS])
        character = _CREDIT.sub("", ref.quote_by or "").strip()
        # For film and TV: the film's own Wikiquote page, and a search for the
        # work with its character (the character's section holds their lines).
        wq_names = _unique([ref.source_page, *(searched(sources.WIKIQUOTE_API, f'"{phrase}"') if phrase else []),
                            *(searched(sources.WIKIQUOTE_API, f"{work} {character}") if character else []),
                            work, person, character])
        wq_names = [n for n in wq_names if on_subject(ref, n)[0]]
    out: list[Candidate] = []
    for name in wp_names[:MAX_WIKIPEDIA_PAGES]:
        try:
            page = sources.wikipedia_page(name, http=http)
        except sources.SourceUnavailable as exc:
            down.append(str(exc))
            continue
        # A redirect can land on another subject: check the page's own title,
        # and its description (a song, an album) unless the card is about one.
        if page is None or not on_subject(ref, page.title)[0] \
                or (OFF_SUBJECT_DESCRIPTION.search(page.description)
                    and not OFF_SUBJECT_DESCRIPTION.search(f"{ref.source_work} {ref.title} {ref.source_page or ''}")
                    and page.title.lower() != (ref.source_page or "").lower()) \
                or any(c.kind == "wikipedia" and c.title == page.title for c in out):
            continue
        sections = page.sections or (sources.Section("", page.text),)
        # A TV series' episode summaries live in tables the extract drops:
        # read them for the card's own series page.
        if ref.category == "film_tv" and episode_pages(ref) and page.title.lower() == (ref.source_page or "").lower():
            try:
                eps = sources.episode_summaries(page.title, http=http)
            except sources.SourceUnavailable as exc:
                down.append(str(exc))
                eps = None
            if eps is not None:
                sections = (*sections, eps)
        out.append(Candidate("wikipedia", page.title, page.url,
                             sources.excerpt(sections, strong=strong, weak=weak, quote=ref.quote, prefer=prefer),
                             tuple(ln for s in sections for ln in sources.sentences(s.text))))
    for name in wq_names[:MAX_WIKIQUOTE_PAGES]:
        try:
            q = sources.wikiquote_page(name, http=http)
        except sources.SourceUnavailable as exc:
            down.append(str(exc))
            continue
        if q is None or not q.lines or not on_subject(ref, q.title)[0] \
                or any(c.kind == "wikiquote" and c.title == q.title for c in out):
            continue
        # A person's page is often one long "Quotes" section: rank its LINES
        # (with their source lines) so "the ovarian lottery" is not cut off
        # by the first few thousand characters.
        line_sections = tuple(sources.Section(f"quote {i}", ln) for i, ln in enumerate(q.lines, 1))
        out.append(Candidate("wikiquote", q.title, q.url,
                             sources.excerpt((sources.Section("", ""), *line_sections),
                                             strong=strong, weak=weak, quote=ref.quote),
                             q.lines))
    # The card's own subject page first: a page whose title holds every key
    # word of the card's title ("Battle of Thermopylae" for "Thermopylae")
    # outranks one that merely shares terms (the Battle of Salamis shares
    # Xerxes and 480 BC). Then the best-matching section; search order
    # breaks ties (a stable sort).
    subject = sources.key_terms(ref.title)

    def own_page(c: Candidate) -> bool:
        return bool(subject) and subject <= set(sources.normalize(c.title).split())

    out.sort(key=lambda c: (not own_page(c), -c.excerpt.score))
    return out, down


def reattributed(ref: ledger.ArsenalRefToVerify, j: Judgement) -> Judgement:
    """A correction fixes a detail; it never changes WHO the card is about.
    A 'corrected' whose fixed source_work drops the card's named person (the
    Marie Antoinette misquote rewritten to Rousseau: the frozen controls,
    2026-11-04) is the excerpt saying otherwise on the substance:
    contradicted. The judge's prompt is unchanged."""
    person, _, _ = split_work(ref.source_work)
    name = _surname(person)
    if j.verdict != "corrected" or not j.fixed_source_work or not name:
        return j
    # Only a DIFFERENT named person re-attributes it ("Jean-Jacques Rousseau,
    # Confessions"); a work with no person ("Reasonable Doubt (1996)" for the
    # Roc-A-Fella card, whose "person" is the label) is a detail fix.
    new_person, _, _ = split_work(j.fixed_source_work)
    new_name = _surname(new_person)
    if new_name is None or name in sources.key_terms(j.fixed_source_work):
        return j
    return Judgement("contradicted", None, None,
                     f"the correction re-attributes it away from {person} ({j.fixed_source_work}): {j.note}"[:300],
                     outcome_conflict=False, covers_every_claim=j.covers_every_claim)


def verify_ref(ref: ledger.ArsenalRefToVerify, *, ask: Ask, http: sources.Http | None = None) -> RefVerdict:
    """Steps 1-5 for one reference. Never raises for a source problem: an
    unreachable or missing page is an 'unverified' verdict with its reason."""
    page_title = ref.source_page or ref.title
    found, down = candidates(ref, http=http)
    if not found:
        if down:
            return RefVerdict("unverified", f"source unreachable: {down[0]}", None)
        return RefVerdict("unverified", f"no Wikipedia or Wikiquote page for {page_title!r}", None)

    notes: list[str] = []
    drop_quote = False
    quote_page: Candidate | None = None
    matched_line = None
    if ref.quote:
        for c in found:
            if sources.quote_found(ref.quote, c.lines):
                quote_page = c
                matched_line = next((ln for ln in c.lines if sources.quote_found(ref.quote, [ln])), None)
                break
        if quote_page is None:
            drop_quote = True
            notes.append("quote not found in the sourced lines; kept as a described move")

    # The quote's page is read first; then the best-matching pages.
    order = [c for c in ([quote_page] if quote_page else []) + [c for c in found if c is not quote_page]
             if c.excerpt.text.strip()]
    judged: list[str] = []
    judgement = Judgement("insufficient", None, None, "an empty page")
    used: Candidate | None = None
    first: Candidate | None = None
    while order:
        # 'insufficient' earns ONE second source, from the OTHER wiki when it
        # has a page (Wikiquote holds a person's remarks; the next Wikipedia
        # lookalike rarely helps: "Lauren Sanchez Bezos" after "Jeff Bezos").
        c = order.pop(0) if first is None else next((x for x in order if x.kind != first.kind), order[0])
        if first is not None:
            order.remove(c)
        text = f"SOURCE: {c.title} ({c.kind})\n\n{c.excerpt.text}"
        if c is quote_page and matched_line:
            text = f"QUOTED LINE: {matched_line}\n\n{text}"
        judgement = reattributed(ref, held(ask(ref, text[:sources.JUDGE_CHARS + 200]), ref))
        judged.append(c.title)
        used = c
        first = first or c
        if judgement.verdict != "insufficient" or judgement.outcome_conflict or len(judged) == 2:
            break
    if len(judged) == 2:
        notes.append(f"first source insufficient ({judged[0]}); second source {judged[1]}")
    verification, why = outcome(quote_dropped=drop_quote, judgement=judgement, unreachable=None)
    if drop_quote and verification != "unverified" and rests_on_unfound_quote(ref):
        verification, why = "unverified", "the origin rests on a quote no source holds"
    if why:
        notes.append(why)
    # The card links where it was checked: the quote's page when its quote was
    # matched there, else the page the judge read last.
    url = (quote_page.url if quote_page else used.url if used else found[0].url)
    corrected = verification == "corrected" and judgement.verdict == "corrected"
    return RefVerdict(verification, "; ".join(notes) or None, url,
                      origin=judgement.fixed_origin if corrected else None,
                      source_work=judgement.fixed_source_work if corrected else None,
                      drop_quote=drop_quote,
                      meaning=judgement.fixed_meaning if corrected else None,
                      deploy_line=judgement.fixed_deploy_line if corrected else None,
                      judged_pages=tuple(judged))


# ------------------------------------------------------------ outcome check

OUTCOME_PURPOSE = "arsenal_outcome"
OUTCOME_SYSTEM = """\
You check ONE thing about one reference card: does the reference's real ending (defeat, death, ruin, scandal) contradict what the deploy_line asks the listener to believe?

Two steps, in order:
1. What does the deploy_line ask the listener to believe? Name the move it recommends and the result it promises.
2. How did the reference really end for whoever made that move? A conflict is ONLY when that ending is the opposite of the promised result: the line recommends a move, and the people who made that move were defeated, died, were ruined or disgraced.

A line that WARNS against the move, using the bad ending as its lesson, agrees with the ending: NOT a conflict. A line whose promised result is what really happened: NOT a conflict.

- Thermopylae: the Greeks held the narrow pass for three days, then were outflanked and wiped out, and the Persians took the pass. "I'm holding one narrow pass where our numbers don't matter" recommends the Greeks' move and promises it wins: CONFLICT.
- Enron collapsed in fraud: "clean-looking numbers can hide rot, so we do the diligence" warns, using the ending: NOT a conflict. A line praising Enron's growth model as the way to win: CONFLICT.
- The Fabian strategy wore Hannibal down and Rome won: "patient attrition wins" promises what happened: NOT a conflict.

Use the excerpt for the ending. When the excerpt does not show the ending at all, answer false.
outcome_conflict: true or false as above. It must agree with your note: if your note says the ending supports or fits the line, the answer is false.
note: one short sentence: the move, the promised result, the real ending. Answer with the JSON the schema asks for."""
#: Haiku 5.5 takes no temperature (non-default sampling is a 400), so the
#: check votes: this many independent reads, a hold on a strict majority.
OUTCOME_SAMPLES = 3


def outcome_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": ["outcome_conflict", "note"],
            "properties": {"outcome_conflict": {"type": "boolean"}, "note": {"type": "string"}}}


#: (card, excerpt) -> (conflict, note). Injected by tests.
AskOutcome = Callable[[ledger.ArsenalRefToVerify, str], tuple[bool, str]]


def ask_outcome_haiku(ref: ledger.ArsenalRefToVerify, excerpt: str, *, client: Any = None) -> tuple[bool, str]:
    card = {"title": ref.title, "origin": ref.origin, "meaning": ref.meaning, "deploy_line": ref.deploy_line,
            "source_work": ref.source_work}
    response = ai.complete(
        purpose=OUTCOME_PURPOSE, model=MODEL, critical=False, max_tokens=300, client=client,
        system=OUTCOME_SYSTEM,
        messages=[{"role": "user", "content": "CARD:\n" + json.dumps(card, ensure_ascii=False)
                   + "\n\nEXCERPT:\n" + excerpt[:sources.JUDGE_CHARS + 200]}],
        thinking={"type": "disabled"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": outcome_schema()}},
    )
    data = response_json(response)
    if not isinstance(data, Mapping) or not isinstance(data.get("outcome_conflict"), bool):
        return False, "unreadable answer"
    return bool(data["outcome_conflict"]), " ".join(str(data.get("note") or "").split())[:300]


@dataclass(frozen=True)
class OutcomeResult:
    conflict: bool
    #: Each read's answer, in order.
    votes: tuple[bool, ...]
    #: A conflicting read's note when held, else the first read's.
    note: str
    page: str | None


def outcome_check(ref: ledger.ArsenalRefToVerify, *, ask: AskOutcome, http: sources.Http | None = None,
                  samples: int = OUTCOME_SAMPLES, page_title: str | None = None) -> OutcomeResult:
    """The item 5 check on one checked card: the best page's excerpt, read
    `samples` times; a conflict on a strict majority. No page: no conflict
    (nothing shows the ending), said in the note.

    `page_title` pins the page (the controls, owner Nov 4: they test the
    judge, not retrieval; Custer's ranking picked the 1936 film serial). Only
    the candidate with exactly that title is read; if it is not there,
    nothing is read and the note says so, so a control fails loudly rather
    than reading another page."""
    found, down = candidates(ref, http=http)
    if page_title is not None:
        found = [c for c in found if c.title.lower() == page_title.lower()]
        if not any(c.excerpt.text.strip() for c in found):
            return OutcomeResult(False, (), f"pinned page {page_title!r} not read "
                                 f"({down[0] if down else 'not among the candidates'})", None)
    page = next((c for c in found if c.excerpt.text.strip()), None)
    if page is None:
        return OutcomeResult(False, (), f"no page to read ({down[0] if down else 'none found'})", None)
    text = f"SOURCE: {page.title} ({page.kind})\n\n{page.excerpt.text}"
    answers = [ask(ref, text) for _ in range(samples)]
    votes = tuple(a[0] for a in answers)
    conflict = sum(votes) * 2 > len(votes)
    note = next((n for c, n in answers if c), answers[0][1]) if conflict else answers[0][1]
    return OutcomeResult(conflict, votes, note, page.title)


@dataclass(frozen=True)
class WordVerdict:
    verification: str
    note: str | None
    pronunciation: str | None
    etymology: str | None
    etymology_url: str | None


def verify_word(lemma: str, *, http: sources.Http | None = None) -> WordVerdict:
    try:
        src = sources.word_source(lemma, http=http)
    except sources.SourceUnavailable as exc:
        return WordVerdict("unverified", f"source unreachable: {exc}", None, None, None)
    if src is None:
        return WordVerdict("unverified", "not in kaikki or Wiktionary", None, None, None)
    if not src.etymology:
        return WordVerdict("unverified", f"no etymology source ({src.source})", src.pronunciation, None, src.url)
    if not src.pronunciation:
        return WordVerdict("unverified", "no pronunciation in the source", None, src.etymology, src.url)
    return WordVerdict("verified", None, src.pronunciation, src.etymology, src.url)


# ---------------------------------------------------------------- the job

@dataclass(frozen=True)
class VerifyRun:
    job_note: str


def verify_item(target: str, item_id: int, *, client: Any = None, http: sources.Http | None = None,
                ask: Ask | None = None) -> str:
    """Verify and settle one pending item; close its run once it is the last.
    Returns the outcome ('verified', ..., or 'skipped: ...')."""
    if target == "ref":
        ref = ledger.arsenal_ref_to_verify(item_id)
        if ref is None or ref.verification != "pending":
            return "skipped: not pending"
        v = verify_ref(ref, ask=ask or (lambda r, e: ask_haiku(r, e, client=client)), http=http)
        ledger.arsenal_settle_ref(item_id, verification=v.verification, note=v.note, source_url=v.source_url,
                                  origin=v.origin, source_work=v.source_work, drop_quote=v.drop_quote,
                                  meaning=v.meaning, deploy_line=v.deploy_line)
        run_id = ref.pool_run_id
        log.info("arsenal verify ref %d %r: %s%s", item_id, ref.title, v.verification,
                 f" ({v.note})" if v.note else "")
        result = v.verification
    elif target == "word":
        word = ledger.arsenal_word_to_verify(item_id)
        if word is None or word.verification != "pending":
            return "skipped: not pending"
        w = verify_word(word.lemma, http=http)
        ledger.arsenal_settle_word(item_id, verification=w.verification, note=w.note, pronunciation=w.pronunciation,
                                   etymology=w.etymology, etymology_url=w.etymology_url)
        run_id = word.pool_run_id
        log.info("arsenal verify word %d %r: %s%s", item_id, word.lemma, w.verification,
                 f" ({w.note})" if w.note else "")
        result = w.verification
    else:
        raise ValueError(f"arsenal verify: unknown target {target!r}")
    if run_id is not None and ledger.arsenal_close_run_if_settled(run_id):
        # db/057: the run's references drop only once outcome-checked.
        job = ledger.arsenal_queue_outcome(f"run {run_id} settled")
        log.info("arsenal run %d: every item settled; outcome check queued (job %d)", run_id, job)
    return result


def target_of(payload: Mapping[str, Any]) -> tuple[str, int]:
    for key, target in (("ref_id", "ref"), ("word_id", "word")):
        if key in payload:
            return target, int(payload[key])
    raise ValueError(f"arsenal_verify payload names no ref_id or word_id: {sorted(payload)}")


def verify_job(payload: Mapping[str, Any]) -> VerifyRun:
    target, item_id = target_of(payload)
    return VerifyRun(f"{target} {item_id}: {verify_item(target, item_id)}")
