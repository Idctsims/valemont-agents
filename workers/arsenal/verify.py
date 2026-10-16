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
MODEL = "claude-haiku-5-5"
VERDICTS = ("supported", "corrected", "contradicted", "insufficient")

SYSTEM = """\
You check one reference card against a source excerpt, using ONLY the excerpt.

- supported: the origin and the source_work agree with the excerpt (a paraphrase is fine).
- corrected: the substance is right but a detail is wrong (a date, a name, a number, "burned" where the excerpt says "scuttled"), AND the excerpt itself states the right detail. Give fixed_origin (at most 280 characters, two short lines, the same voice) and/or fixed_source_work.
- contradicted: the excerpt is about the card's subject and says otherwise on the substance.
- insufficient: the excerpt does not cover it, or covers only part of it, or is about a different subject.

Never use corrected to delete, soften or rewrite a claim the excerpt does not mention: a claim the excerpt is silent on makes the card insufficient, not corrected. Never write a fix from your own knowledge; every word of a fix comes from the excerpt.

covers_every_claim: true only when the excerpt mentions every claim of the card's origin (who, what, when, the detail) and its source_work; false when any of them is missing from the excerpt.

The outcome check. When the excerpt shows how the story really ended and that ending undercuts the card's meaning or deploy_line (Thermopylae was a total defeat for the Greeks; it cannot be a pitch line about winning), set outcome_conflict to true. Then either reframe it honestly (verdict corrected, with fixed_meaning and/or fixed_deploy_line that fit the real ending, same voice, one breath) or, when no honest reframe works, answer contradicted. When the ending fits the card, outcome_conflict is false.

note: one short sentence saying why. Answer with the JSON the schema asks for."""


def schema() -> dict[str, Any]:
    nullable = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    fields = ("verdict", "covers_every_claim", "outcome_conflict", "fixed_origin", "fixed_source_work", "fixed_meaning",
              "fixed_deploy_line", "note")
    return {"type": "object", "additionalProperties": False, "required": list(fields),
            "properties": {"verdict": {"type": "string", "enum": list(VERDICTS)},
                           "covers_every_claim": {"type": "boolean"}, "outcome_conflict": {"type": "boolean"},
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


def held(j: Judgement) -> Judgement:
    """The judge held to its own coverage answer: 'supported' or 'corrected'
    on an excerpt that does not cover every claim is 'insufficient' (owner,
    2026-10-16: a silent excerpt never trims a card into 'corrected')."""
    if j.verdict in ("supported", "corrected") and not j.covers_every_claim and not j.outcome_conflict:
        return Judgement("insufficient", None, None, f"the excerpt does not cover every claim ({j.note})".replace(" ()", ""))
    return j


#: (card, excerpt) -> Judgement. Injected by tests; production calls Haiku.
Ask = Callable[[ledger.ArsenalRefToVerify, str], Judgement]


def ask_haiku(ref: ledger.ArsenalRefToVerify, excerpt: str, *, client: Any = None) -> Judgement:
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
                     covers_every_claim=data.get("covers_every_claim") is True)


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
MAX_WIKIPEDIA_PAGES = 4
MAX_WIKIQUOTE_PAGES = 3
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


def candidates(ref: ledger.ArsenalRefToVerify, *, http: sources.Http | None = None) -> tuple[list[Candidate], list[str]]:
    """Every page that could hold the passage, ranked by its best-matching
    section (the work's own page wins ties: it is searched first). Returns
    (candidates, the sources that would not answer)."""
    person, work, context = split_work(ref.source_work)
    strong = sources.key_terms(ref.title, work, context, person, ref.quote)
    weak = sources.key_terms(ref.origin)
    want_quotes = ref.category == "film_tv" or ref.quote is not None or ref.source_kind == "wikiquote"
    down: list[str] = []

    def searched(api: str, query: str) -> list[str]:
        try:
            return sources.search(api, query, http=http)
        except sources.SourceUnavailable as exc:
            down.append(str(exc))
            return []

    # The work with its person ("Enchiridion Epictetus"), else with its own
    # context ("Moneyball Michael Lewis, 2003": the book, not the film).
    wp_names = _unique([ref.source_page, *searched(sources.WIKIPEDIA_API, f"{work} {person or context}"),
                        ref.title, *searched(sources.WIKIPEDIA_API, f"{ref.title} {person or work}")])
    wq_names: list[str] = []
    if want_quotes:
        phrase = " ".join((ref.quote or "").split()[:PHRASE_WORDS])
        wq_names = _unique([ref.source_page, *(searched(sources.WIKIQUOTE_API, f'"{phrase}"') if phrase else []),
                            work, person, ref.quote_by])
    out: list[Candidate] = []
    for name in wp_names[:MAX_WIKIPEDIA_PAGES]:
        try:
            page = sources.wikipedia_page(name, http=http)
        except sources.SourceUnavailable as exc:
            down.append(str(exc))
            continue
        if page is None or any(c.kind == "wikipedia" and c.title == page.title for c in out):
            continue
        sections = page.sections or (sources.Section("", page.text),)
        out.append(Candidate("wikipedia", page.title, page.url,
                             sources.excerpt(sections, strong=strong, weak=weak, quote=ref.quote),
                             tuple(ln for s in sections for ln in sources.sentences(s.text))))
    for name in wq_names[:MAX_WIKIQUOTE_PAGES]:
        try:
            q = sources.wikiquote_page(name, http=http)
        except sources.SourceUnavailable as exc:
            down.append(str(exc))
            continue
        if q is None or not q.lines or any(c.kind == "wikiquote" and c.title == q.title for c in out):
            continue
        out.append(Candidate("wikiquote", q.title, q.url,
                             sources.excerpt(q.sections or (sources.Section("", "\n".join(q.lines)),),
                                             strong=strong, weak=weak, quote=ref.quote),
                             q.lines))
    out.sort(key=lambda c: -c.excerpt.score)  # stable: search order breaks ties
    return out, down


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
    order = ([quote_page] if quote_page else []) + [c for c in found if c is not quote_page]
    judged: list[str] = []
    judgement = Judgement("insufficient", None, None, "an empty page")
    used: Candidate | None = None
    for c in order:
        if not c.excerpt.text.strip():
            continue
        text = f"SOURCE: {c.title} ({c.kind})\n\n{c.excerpt.text}"
        if c is quote_page and matched_line:
            text = f"QUOTED LINE: {matched_line}\n\n{text}"
        judgement = held(ask(ref, text[:sources.JUDGE_CHARS + 200]))
        judged.append(c.title)
        used = c
        # 'insufficient' earns ONE second source; nothing else retries.
        if judgement.verdict != "insufficient" or judgement.outcome_conflict or len(judged) == 2:
            break
    if len(judged) == 2:
        notes.append(f"first source insufficient ({judged[0]}); second source {judged[1]}")
    verification, why = outcome(quote_dropped=drop_quote, judgement=judgement, unreachable=None)
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
        log.info("arsenal run %d: every item settled", run_id)
    return result


def target_of(payload: Mapping[str, Any]) -> tuple[str, int]:
    for key, target in (("ref_id", "ref"), ("word_id", "word")):
        if key in payload:
            return target, int(payload[key])
    raise ValueError(f"arsenal_verify payload names no ref_id or word_id: {sorted(payload)}")


def verify_job(payload: Mapping[str, Any]) -> VerifyRun:
    target, item_id = target_of(payload)
    return VerifyRun(f"{target} {item_id}: {verify_item(target, item_id)}")
