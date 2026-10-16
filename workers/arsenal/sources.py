"""The Arsenal's free sources (no keys; probed 2026-10-15, docs/arsenal.md §2).

    Wikipedia  REST page summary (the canonical URL and the lead) and the
               MediaWiki plain-text extract (up to EXTRACT_CHARS, what the
               judge reads: the summary's ~450-character lead alone leaves
               most origins "insufficient").
    Wikiquote  MediaWiki parse (sections + wikitext). Quote lines are taken
               only from sections that are not Disputed, Misattributed,
               About, Quotes about, Cast, See also, External links or
               References.
    kaikki     per-word JSONL (Wiktionary's data): the etymology and the IPA.
               Wiktionary's REST definition is the fallback that can only say
               "the word exists" (it carries no etymology or IPA).

Every fetch: the User-Agent below, a 10 s timeout, at most 2 tries; a 429
waits Retry-After (at most RETRY_WAIT_MAX_S) before the second. A page that
is not there is None; a source that will not answer raises SourceUnavailable.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

from rapidfuzz import fuzz

log = logging.getLogger("valemont.arsenal.sources")

USER_AGENT = "ValemontCommand/1.0 (+https://valemont-command.vercel.app/privacy)"
TIMEOUT_S = 10.0
TRIES = 2
RETRY_WAIT_MAX_S = 10.0
EXTRACT_CHARS = 5000
#: What the judge reads, at most (~1,500 tokens).
JUDGE_CHARS = 6000
ETYMOLOGY_CHARS = 200
#: A quote matches a sourced line at or above this (rapidfuzz partial_ratio).
QUOTE_MATCH = 90

WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIQUOTE_API = "https://en.wikiquote.org/w/api.php"
KAIKKI = "https://kaikki.org/dictionary/English/meaning/"
WIKTIONARY_DEFINITION = "https://en.wiktionary.org/api/rest_v1/page/definition/"


class SourceUnavailable(RuntimeError):
    """The source did not answer (network, 5xx, 429 twice) after TRIES."""


#: (url, params) -> (status, body text, Retry-After seconds or None).
#: Injected by tests; production uses httpx.
Http = Callable[[str, Mapping[str, str]], tuple[int, str, float | None]]

_CLIENT: Any = None


def _httpx(url: str, params: Mapping[str, str]) -> tuple[int, str, float | None]:
    global _CLIENT
    if _CLIENT is None:
        import httpx  # deferred: tests inject `http` and never import it

        _CLIENT = httpx.Client(timeout=TIMEOUT_S, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    r = _CLIENT.get(url, params=dict(params) or None)
    retry = r.headers.get("retry-after")
    try:
        wait = float(retry) if retry is not None else None
    except ValueError:
        wait = None
    return r.status_code, r.text, wait


def close_http() -> None:
    global _CLIENT
    if _CLIENT is not None:
        _CLIENT.close()
        _CLIENT = None


def fetch(url: str, params: Mapping[str, str] | None = None, *, http: Http | None = None,
          sleep: Callable[[float], None] = time.sleep) -> tuple[int, str]:
    """(status, text) for any answer below 500 but 429 (a 404 is an answer:
    no such page); SourceUnavailable for anything that would not answer in
    TRIES."""
    get = http or _httpx
    last = "no attempt"
    for attempt in range(TRIES):
        try:
            status, text, wait = get(url, params or {})
        except Exception as exc:  # network, timeout
            last = type(exc).__name__
            if attempt + 1 < TRIES:
                sleep(1.0)
            continue
        if status == 429:
            last = "HTTP 429"
            if attempt + 1 < TRIES:
                sleep(min(wait if wait is not None else 2.0, RETRY_WAIT_MAX_S))
            continue
        if status >= 500:
            last = f"HTTP {status}"
            if attempt + 1 < TRIES:
                sleep(1.0)
            continue
        return status, text
    raise SourceUnavailable(f"{url.split('?')[0]}: {last} after {TRIES} tries")


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def page_path(title: str) -> str:
    return quote(title.strip().replace(" ", "_"), safe="()_,'!:-.")


# --------------------------------------------------------------- wikipedia

@dataclass(frozen=True)
class WikiPage:
    title: str
    url: str
    #: The plain-text extract (else the summary's lead), at most JUDGE_CHARS.
    text: str


def wikipedia_page(title: str, *, http: Http | None = None) -> WikiPage | None:
    """The page's summary and extract; None when there is no such article
    (or only a disambiguation page)."""
    status, body = fetch(WIKIPEDIA_SUMMARY + page_path(title), {"redirect": "true"}, http=http)
    summary = _json(body) if status == 200 else None
    if not isinstance(summary, dict) or summary.get("type") == "disambiguation":
        return None
    canonical = str(summary.get("title") or title)
    url = ((summary.get("content_urls") or {}).get("desktop") or {}).get("page") \
        or f"https://en.wikipedia.org/wiki/{page_path(canonical)}"
    lead = str(summary.get("extract") or "")
    text = lead
    status, body = fetch(WIKIPEDIA_API, {"action": "query", "prop": "extracts", "explaintext": "1",
                                         "exchars": str(EXTRACT_CHARS), "titles": canonical, "redirects": "1",
                                         "format": "json", "formatversion": "2"}, http=http)
    data = _json(body) if status == 200 else None
    pages = ((data or {}).get("query") or {}).get("pages") or []
    if pages and isinstance(pages[0], dict) and pages[0].get("extract"):
        text = str(pages[0]["extract"])
    return WikiPage(canonical, str(url), text[:JUDGE_CHARS])


# --------------------------------------------------------------- wikiquote

#: Section headings whose quotes are not the subject's own, sourced words.
EXCLUDED_SECTION = re.compile(r"disputed|misattributed|^about\b|quotes? about|^cast\b|see also|external links"
                              r"|^references?\b|^sources?\b|^notes?\b", re.I)
_HEADING = re.compile(r"^(={2,6})\s*(.*?)\s*\1\s*$")


@dataclass(frozen=True)
class QuotePage:
    title: str
    url: str
    #: Cleaned quote lines from the allowed sections.
    lines: tuple[str, ...]


def allowed_wikitext(wikitext: str) -> str:
    """The wikitext without the excluded sections (and their subsections)."""
    keep: list[str] = []
    skip_level: int | None = None
    for line in wikitext.splitlines():
        m = _HEADING.match(line.strip())
        if m:
            level = len(m.group(1))
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and EXCLUDED_SECTION.search(strip_markup(m.group(2))):
                skip_level = level
            continue
        if skip_level is None:
            keep.append(line)
    return "\n".join(keep)


_REF = re.compile(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", re.I | re.S)
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_LINK = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]")
_EXTERNAL = re.compile(r"\[https?://\S+\s*([^\]]*)\]")
_TAG = re.compile(r"<[^>]+>")


def strip_markup(text: str) -> str:
    t = _REF.sub("", text)
    for _ in range(4):  # nested templates, innermost first
        t, n = _TEMPLATE.subn("", t)
        if not n:
            break
    t = _LINK.sub(r"\1", t)
    t = _EXTERNAL.sub(r"\1", t)
    t = _TAG.sub(" ", t)
    t = t.replace("'''", "").replace("''", "")
    return " ".join(t.split())


def quote_lines(wikitext: str) -> list[str]:
    """Every quote or dialogue line (`*`, `:` and `#` lines) of the allowed
    sections, markup stripped."""
    out = []
    for line in allowed_wikitext(wikitext).splitlines():
        s = line.strip()
        if not s or s[0] not in "*:#":
            continue
        cleaned = strip_markup(s.lstrip("*:# "))
        if len(cleaned) >= 4:
            out.append(cleaned)
    return out


def wikiquote_page(title: str, *, http: Http | None = None) -> QuotePage | None:
    status, body = fetch(WIKIQUOTE_API, {"action": "parse", "page": title, "prop": "wikitext", "format": "json",
                                         "formatversion": "2", "redirects": "1"}, http=http)
    data = _json(body) if status == 200 else None
    parsed = (data or {}).get("parse") if isinstance(data, dict) else None
    if not isinstance(parsed, dict):
        return None  # {"error": {"code": "missingtitle"}} or a 404
    canonical = str(parsed.get("title") or title)
    return QuotePage(canonical, f"https://en.wikiquote.org/wiki/{page_path(canonical)}",
                     tuple(quote_lines(str(parsed.get("wikitext") or ""))))


# ------------------------------------------------------------------ quotes

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def normalize(text: str) -> str:
    """Lowercase, accents and punctuation gone, spaces collapsed: "can't" and
    "can’t" and "Can't!" are the same."""
    t = unicodedata.normalize("NFKD", text.translate(_QUOTES))
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    t = re.sub(r"[^a-z0-9 ]+", "", t.replace("-", " "))
    return " ".join(t.split())


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def quote_found(quote: str, lines: Sequence[str]) -> bool:
    """The quote, normalized, in a sourced line: exactly as a substring, or
    fuzzily (partial_ratio >= QUOTE_MATCH) against a line at least as long."""
    q = normalize(quote)
    if not q:
        return False
    for line in lines:
        n = normalize(line)
        if q in n:
            return True
        if len(q) >= 12 and len(n) >= len(q) and fuzz.partial_ratio(q, n) >= QUOTE_MATCH:
            return True
    return False


# ------------------------------------------------------------------- words

@dataclass(frozen=True)
class WordSource:
    #: None when the source has the word but not this field.
    pronunciation: str | None
    etymology: str | None
    url: str
    source: str  # 'kaikki' | 'wiktionary'


def two_lines(text: str, limit: int = ETYMOLOGY_CHARS) -> str:
    """At most `limit` characters, cut at a sentence end when one fits,
    otherwise at a word with an ellipsis."""
    t = " ".join(text.split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    if end >= limit // 2:
        return cut[:end + 1]
    return cut[:cut.rfind(" ")].rstrip(",;:") + "…"


def _ipa(entries: Sequence[Mapping[str, Any]]) -> str | None:
    first = None
    for e in entries:
        for s in e.get("sounds") or []:
            ipa = s.get("ipa") if isinstance(s, Mapping) else None
            if not ipa:
                continue
            tags = {str(t) for t in s.get("tags") or []}
            if tags & {"US", "General-American"}:
                return str(ipa)[:80]
            first = first or str(ipa)[:80]
    return first


def word_source(lemma: str, *, http: Http | None = None) -> WordSource | None:
    """kaikki first; Wiktionary's definition only says the word exists. None
    when neither knows it."""
    wiki_url = f"https://en.wiktionary.org/wiki/{quote(lemma.replace(' ', '_'))}#Etymology"
    status, body = fetch(f"{KAIKKI}{quote(lemma[0])}/{quote(lemma[:2])}/{quote(lemma)}.jsonl", http=http)
    if status == 200:
        entries = [e for e in (_json(line) for line in body.splitlines() if line.strip()) if isinstance(e, dict)]
        etym = next((str(e["etymology_text"]) for e in entries if e.get("etymology_text")), None)
        if entries:
            return WordSource(_ipa(entries), two_lines(etym) if etym else None, wiki_url, "kaikki")
    status, body = fetch(WIKTIONARY_DEFINITION + quote(lemma), http=http)
    if status == 200 and isinstance(_json(body), dict) and (_json(body) or {}).get("en"):
        return WordSource(None, None, wiki_url, "wiktionary")
    return None
