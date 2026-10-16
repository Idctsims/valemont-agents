"""Verification: one reference or one Power Word at a time.

A REFERENCE (docs/arsenal.md §4):
  1. Fetch. Wikipedia for the origin (every card), plus Wikiquote when the
     card is film_tv, carries a quote, or names Wikiquote as its source.
  2. Quote. Matched (normalized, fuzzy) against the Wikiquote lines from the
     allowed sections, else against the Wikipedia text's sentences. No match:
     the quote is dropped and the card stays as a described move, 'corrected'.
  3. Judge. Haiku (non-critical, thinking off) reads the trimmed source text
     and the card's origin and source_work: supported | corrected (with the
     fixed wording) | contradicted | insufficient.
  4. Map (outcome()): the worse of the quote step and the judge wins.
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
- corrected: the substance is right but a detail is wrong (a date, a name, a number, "burned" where the excerpt says "scuttled"). Give fixed_origin (at most 280 characters, two short lines, the same voice) and/or fixed_source_work.
- contradicted: the excerpt says otherwise on the substance.
- insufficient: the excerpt does not cover it.

note: one short sentence saying why. Answer with the JSON the schema asks for."""


def schema() -> dict[str, Any]:
    nullable = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {"type": "object", "additionalProperties": False,
            "required": ["verdict", "fixed_origin", "fixed_source_work", "note"],
            "properties": {"verdict": {"type": "string", "enum": list(VERDICTS)}, "fixed_origin": nullable,
                           "fixed_source_work": nullable, "note": {"type": "string"}}}


@dataclass(frozen=True)
class Judgement:
    verdict: str
    fixed_origin: str | None
    fixed_source_work: str | None
    note: str


#: (card, excerpt) -> Judgement. Injected by tests; production calls Haiku.
Ask = Callable[[ledger.ArsenalRefToVerify, str], Judgement]


def ask_haiku(ref: ledger.ArsenalRefToVerify, excerpt: str, *, client: Any = None) -> Judgement:
    card = {"title": ref.title, "origin": ref.origin, "source_work": ref.source_work}
    response = ai.complete(
        purpose=PURPOSE, model=MODEL, critical=False, max_tokens=600, client=client,
        system=SYSTEM,
        messages=[{"role": "user", "content": "CARD:\n" + json.dumps(card, ensure_ascii=False)
                   + "\n\nEXCERPT:\n" + excerpt[:sources.JUDGE_CHARS]}],
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
                     " ".join(str(data.get("note") or "").split())[:300])


# ------------------------------------------------------------------ mapping

RANK = {"verified": 0, "corrected": 1, "unverified": 2}


def outcome(*, quote_dropped: bool, judgement: Judgement | None, unreachable: str | None) -> tuple[str, str]:
    """(verification, why) from the quote step and the judge; the worse wins."""
    if unreachable:
        return "unverified", unreachable
    assert judgement is not None
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


def verify_ref(ref: ledger.ArsenalRefToVerify, *, ask: Ask, http: sources.Http | None = None) -> RefVerdict:
    """Steps 1-4 for one reference. Never raises for a source problem: an
    unreachable or missing page is an 'unverified' verdict with its reason."""
    page_title = ref.source_page or ref.title
    want_quotes = ref.category == "film_tv" or ref.quote is not None or ref.source_kind == "wikiquote"
    try:
        wiki = sources.wikipedia_page(page_title, http=http)
        wq = sources.wikiquote_page(page_title, http=http) if want_quotes else None
    except sources.SourceUnavailable as exc:
        return RefVerdict("unverified", f"source unreachable: {exc}", None)
    if wiki is None and wq is None:
        return RefVerdict("unverified", f"no Wikipedia or Wikiquote page for {page_title!r}", None)

    notes: list[str] = []
    drop_quote = False
    matched_line = None
    if ref.quote:
        lines = list(wq.lines) if wq else []
        lines += sources.sentences(wiki.text) if wiki else []
        if sources.quote_found(ref.quote, lines):
            matched_line = next((ln for ln in lines if sources.quote_found(ref.quote, [ln])), None)
        else:
            drop_quote = True
            notes.append("quote not found in the sourced lines; kept as a described move")

    excerpt = wiki.text if wiki else ""
    if matched_line:
        excerpt = f"QUOTED LINE: {matched_line}\n\n{excerpt}"
    if not excerpt.strip() and wq:
        excerpt = "\n".join(wq.lines)[:sources.JUDGE_CHARS]
    judgement = ask(ref, excerpt) if excerpt.strip() else Judgement("insufficient", None, None, "an empty page")
    verification, why = outcome(quote_dropped=drop_quote, judgement=judgement, unreachable=None)
    if why:
        notes.append(why)
    # The card links where it was checked: Wikiquote when its quote was
    # matched there, else Wikipedia.
    url = wq.url if (wq and matched_line and matched_line in wq.lines) else (wiki.url if wiki else wq.url if wq else None)
    corrected = verification == "corrected" and judgement.verdict == "corrected"
    return RefVerdict(verification, "; ".join(notes) or None, url,
                      origin=judgement.fixed_origin if corrected else None,
                      source_work=judgement.fixed_source_work if corrected else None,
                      drop_quote=drop_quote)


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
                                  origin=v.origin, source_work=v.source_work, drop_quote=v.drop_quote)
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
