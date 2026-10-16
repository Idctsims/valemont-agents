"""The Arsenal's cards: what the model is asked for and what is kept.

Pure (no network, no database): the categories and rooms (db/054's CHECKs),
the prompt (the voice file, the rooms, the seeds as "taste, never repeat",
the last 60 keys), the structured-output schemas, the per-category split of
a batch by the current weights, and the validation every card passes before
it is inserted. A card that fails validation is counted and logged, never an
error; a quote over 15 words is dropped, not the card.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core import ledger
from core.voice import load_arsenal_voice

CATEGORIES: tuple[str, ...] = ("finance_business", "history_military", "film_tv", "sports", "music_culture",
                               "philosophy", "literature_myth")
ROOMS: tuple[str, ...] = ("boardroom", "pitch", "negotiation", "homies")
SOURCE_KINDS: tuple[str, ...] = ("wikipedia", "wikiquote", "other")

#: What each category means to the model (the owner's decisions, Oct 15).
CATEGORY_NOTES: Mapping[str, str] = {
    "finance_business": "deals, investors, operators, companies, markets",
    "history_military": "battles, generals, empires, turning points",
    "film_tv": "films and series the room has seen (power, money, crime, deals)",
    "sports": "coaches, athletes, dynasties, comebacks",
    "music_culture": "artists, labels, moguls, moments in culture",
    "philosophy": "Stoics and strategists, applied only, never a seminar",
    "literature_myth": "only when instantly recognizable",
}

#: The weekly pool (docs/arsenal.md §4).
POOL_REFS = 30
POOL_WORDS = 10
#: The drop's synchronous top-up.
TOPUP_REFS = 6
TOPUP_WORDS = 2

MAX_QUOTE_WORDS = 15
KEY = re.compile(r"^[a-z0-9][a-z0-9_]{1,79}$")
LEMMA = re.compile(r"^[a-z][a-z' -]*[a-z]$")

#: Field length bounds, the same as db/054's CHECKs.
BOUNDS: Mapping[str, tuple[int, int]] = {
    "title": (2, 120), "origin": (10, 280), "meaning": (5, 300), "deploy_line": (5, 220),
    "source_work": (2, 160), "sentence": (5, 240),
}


# ------------------------------------------------------------------ split

def allocate(total: int, weights: Mapping[str, float]) -> dict[str, int]:
    """`total` references across the categories by weight (largest
    remainder), at least one each. Deterministic: ties go in CATEGORIES order."""
    w = {c: max(0.0, float(weights.get(c, 0.0))) for c in CATEGORIES}
    if total < len(CATEGORIES) or not sum(w.values()):
        raise ValueError(f"allocate: {total} references cannot cover {len(CATEGORIES)} categories")
    out = {c: 1 for c in CATEGORIES}
    rest = total - len(CATEGORIES)
    s = sum(w.values())
    shares = {c: rest * w[c] / s for c in CATEGORIES}
    for c in CATEGORIES:
        out[c] += int(shares[c])
    left = total - sum(out.values())
    for c in sorted(CATEGORIES, key=lambda c: (-(shares[c] - int(shares[c])), CATEGORIES.index(c)))[:left]:
        out[c] += 1
    return out


def category_weights(weights: Mapping[tuple[str, str], float]) -> dict[str, float]:
    """The category slice of ledger.arsenal_weights()."""
    return {k: v for (dim, k), v in weights.items() if dim == "category"}


# ---------------------------------------------------------------- prompt

@dataclass(frozen=True)
class PromptContext:
    """What every request carries in its cached system block."""
    seeds: Sequence[tuple[str, str, str]]
    recent_keys: Sequence[str]
    recent_lemmas: Sequence[str]
    #: The owner's known words (db/055 word seeds): never a Power Word.
    known_lemmas: Sequence[str] = ()


RULES = """\
OUTPUT
Answer with the JSON the schema asks for, nothing else.

FOR EACH REFERENCE
- canonical_key: snake_case, lowercase letters, digits and underscores, naming the reference itself (crossing_the_rubicon), never a key in the lists below.
- category: one of {categories}.
- source_kind: "wikiquote" when the card carries a quote from a film, series or person with a Wikiquote page; "wikipedia" otherwise; "other" only when neither has a page.
- source_page: the exact English Wikipedia or Wikiquote page title that supports the origin (for a film, the film's page: "Heat (1995 film)").
- quote and quote_by: null unless you are certain of the exact words; at most 15 words.
- A film or TV quote credits the character AND the work, never the real person alone: quote_by "Sean Parker (as written in The Social Network)".
- Check the ending: if the reference's real outcome undercuts the deploy_line (Thermopylae was a total defeat, so it is never a pitch line about winning), reframe the line to fit what really happened, or choose another reference.
- Never repeat a seed or a recent key, and never write the same reference under a different name.

FOR EACH POWER WORD
- lemma: one English word, lowercase.
- meaning: what it means, in plain words.
- sentence: one sentence Tsims would say in the room, with the word in it.
- Never a known word or a recent word below.
"""


def system_blocks(ctx: PromptContext) -> list[dict[str, Any]]:
    """The voice, the rules, then the seeds and recent keys (cached: the two
    requests of a batch, and the top-up, share it)."""
    voice = load_arsenal_voice()
    taste = "\n".join(f"- {title} ({work}) [{key}]" for key, title, work in ctx.seeds)
    recent = ", ".join(ctx.recent_keys) or "(none yet)"
    lemmas = ", ".join(ctx.recent_lemmas) or "(none yet)"
    known = ", ".join(ctx.known_lemmas) or "(none)"
    rules = RULES.format(categories=", ".join(f"{c} ({CATEGORY_NOTES[c]})" for c in CATEGORIES))
    lists = ("THE OWNER'S CANON: taste to match, NEVER to repeat\n" + taste
             + "\n\nRECENT KEYS: never repeat\n" + recent
             + "\n\nKNOWN POWER WORDS: the owner already uses these; the register to match, never a Power Word\n"
             + known + "\n\nRECENT POWER WORDS: never repeat\n" + lemmas)
    return [
        {"type": "text", "text": voice.text},
        {"type": "text", "text": rules},
        {"type": "text", "text": lists, "cache_control": {"type": "ephemeral"}},
    ]


def refs_message(split: Mapping[str, int]) -> str:
    parts = ", ".join(f"{c} {n}" for c, n in split.items() if n)
    return (f"Write {sum(split.values())} references for the Arsenal pool, exactly this many per category: {parts}. "
            "Spread the rooms. Each must pass the footnote test.")


def words_message(n: int) -> str:
    return f"Write {n} Power Words for the Arsenal pool, spread across the rooms."


def _nullable() -> dict[str, Any]:
    return {"anyOf": [{"type": "string"}, {"type": "null"}]}


def ref_item_schema() -> dict[str, Any]:
    s = {"type": "string"}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["canonical_key", "category", "title", "origin", "meaning", "deploy_line", "room",
                     "source_work", "quote", "quote_by", "source_kind", "source_page"],
        "properties": {
            "canonical_key": s, "category": {"type": "string", "enum": list(CATEGORIES)}, "title": s, "origin": s,
            "meaning": s, "deploy_line": s, "room": {"type": "string", "enum": list(ROOMS)}, "source_work": s,
            "quote": _nullable(), "quote_by": _nullable(),
            "source_kind": {"type": "string", "enum": list(SOURCE_KINDS)}, "source_page": s,
        },
    }


def word_item_schema() -> dict[str, Any]:
    s = {"type": "string"}
    return {
        "type": "object", "additionalProperties": False, "required": ["lemma", "meaning", "room", "sentence"],
        "properties": {"lemma": s, "meaning": s, "room": {"type": "string", "enum": list(ROOMS)}, "sentence": s},
    }


def refs_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": ["refs"],
            "properties": {"refs": {"type": "array", "items": ref_item_schema()}}}


def words_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": ["words"],
            "properties": {"words": {"type": "array", "items": word_item_schema()}}}


def topup_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": ["refs", "words"],
            "properties": {"refs": {"type": "array", "items": ref_item_schema()},
                           "words": {"type": "array", "items": word_item_schema()}}}


def response_json(message: Any) -> Any:
    """The JSON a structured answer carries, or None (a refusal, bad JSON)."""
    if getattr(message, "stop_reason", None) == "refusal":
        return None
    text = "".join(getattr(b, "text", "") for b in getattr(message, "content", []) or []
                   if getattr(b, "type", "") == "text")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


# ------------------------------------------------------------- validation

def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _bounded(field: str, value: Any) -> str | None:
    text = _clean(value)
    lo, hi = BOUNDS[field]
    return text if lo <= len(text) <= hi else None


def normalize_key(raw: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(raw or "").lower()).strip("_")
    return key[:80].rstrip("_")


def quote_words(quote: str) -> int:
    return len(quote.split())


def clean_quote(raw: Any) -> str | None:
    """The quote without wrapping quotation marks; None when absent or over
    MAX_QUOTE_WORDS (the card stays, as a described move)."""
    q = _clean(raw).strip('"“”\'‘’ ')
    if not q or quote_words(q) > MAX_QUOTE_WORDS or len(q) > 160:
        return None
    return q


_YEAR_PARENS = re.compile(r"\s*\([^()]*\)\s*$")
CREDIT = "as written in"


def credit_film_quote(quote_by: str | None, source_work: str) -> str | None:
    """A film or TV quote credits the character AND the work, never the real
    person alone (owner, 2026-10-16): "Sean Parker" from "The Social Network
    (2010)" becomes "Sean Parker (as written in The Social Network)". None
    when there is no speaker to credit, or the credit will not fit
    quote_by's 80 characters: the quote then goes, the card stays."""
    speaker = _clean(quote_by)
    work = source_work.strip()
    while (m := _YEAR_PARENS.search(work)) and m.start() > 0:
        work = work[:m.start()].strip()
    if not speaker or not work:
        return None
    if CREDIT in speaker.lower() or work.lower() in speaker.lower():
        credited = speaker
    else:
        credited = f"{speaker} ({CREDIT} {work})"
    return credited if len(credited) <= 80 else None


def validate_ref(item: Any, blocked: set[str]) -> tuple[ledger.ArsenalCardIn | None, str | None]:
    """(card, None) when kept, else (None, why)."""
    if not isinstance(item, Mapping):
        return None, "not an object"
    key = normalize_key(item.get("canonical_key")) or normalize_key(item.get("title"))
    if not KEY.match(key):
        return None, f"bad canonical_key {item.get('canonical_key')!r}"
    if key in blocked:
        return None, f"repeats {key}"
    category, room, kind = item.get("category"), item.get("room"), item.get("source_kind")
    if category not in CATEGORIES:
        return None, f"unknown category {category!r}"
    if room not in ROOMS:
        return None, f"unknown room {room!r}"
    if kind not in SOURCE_KINDS:
        return None, f"unknown source_kind {kind!r}"
    fields = {f: _bounded(f, item.get(f)) for f in ("title", "origin", "meaning", "deploy_line", "source_work")}
    bad = [f for f, v in fields.items() if v is None]
    if bad:
        return None, f"{', '.join(bad)} missing or out of bounds"
    quote = clean_quote(item.get("quote"))
    quote_by = _clean(item.get("quote_by"))[:80] or None if quote else None
    if quote and category == "film_tv":
        quote_by = credit_film_quote(item.get("quote_by"), fields["source_work"])
        if quote_by is None:
            quote = None
    page = _clean(item.get("source_page"))[:200] or None
    return ledger.ArsenalCardIn(
        canonical_key=key, category=category, title=fields["title"], origin=fields["origin"],
        meaning=fields["meaning"], deploy_line=fields["deploy_line"], room=room, source_work=fields["source_work"],
        quote=quote, quote_by=quote_by, source_kind=kind, source_page=page), None


def _stem(lemma: str) -> str:
    """Enough of the lemma to find it in a sentence through inflection
    (asymmetric/asymmetry, provenance/provenances)."""
    return lemma[:max(4, len(lemma) - 2)]


def validate_word(item: Any, blocked: set[str]) -> tuple[ledger.ArsenalWordIn | None, str | None]:
    if not isinstance(item, Mapping):
        return None, "not an object"
    lemma = _clean(item.get("lemma")).lower()
    if not (2 <= len(lemma) <= 60 and LEMMA.match(lemma)):
        return None, f"bad lemma {item.get('lemma')!r}"
    if lemma in blocked:
        return None, f"repeats {lemma}"
    room = item.get("room")
    if room not in ROOMS:
        return None, f"unknown room {room!r}"
    meaning, sentence = _bounded("meaning", item.get("meaning")), _bounded("sentence", item.get("sentence"))
    if meaning is None or sentence is None:
        return None, "meaning or sentence missing or out of bounds"
    if _stem(lemma) not in sentence.lower():
        return None, f"the sentence does not use {lemma!r}"
    return ledger.ArsenalWordIn(lemma=lemma, meaning=meaning, room=room, sentence=sentence), None


def embed_text(card: ledger.ArsenalCardIn) -> str:
    """What the near-duplicate check compares: the reference and its work."""
    return f"{card.title} — {card.source_work}"
