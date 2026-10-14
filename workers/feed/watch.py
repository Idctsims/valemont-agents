"""The listing-page watcher (Chat 4 Phase 1, sources kind = "page"), and the
Hacker News reader (kind = "hn"). Pure: bytes in, feed/parse.RawItem out; no
network, no database. feed/ingest.py fetches (streamed, capped) and stores.

A page source carries its own `config` (sources.toml, then sources.config,
jsonb). Only the listing container is parsed: the text between `start` and
`end` (plain substrings, the first after the first), at most `max_bytes` of
the response. Three modes, one per page shape seen on 2026-10-13:

  links     a list of article links (anthropic.com/news, claude.com/blog,
            supabase.com/changelog). Every <a> whose href matches `href`
            (a regex) is one item, once per href:
              title   the anchor's longest text run that is not a date;
              date    the first text run inside the anchor that parses as a
                      date, else the first in the `date_after` text runs
                      that follow it (Supabase prints it just after);
              teaser  the anchor's second-longest run, when it is a sentence.
  sections  dated headings (the Claude API and Claude apps release notes):
            every `heading` element (h2, h3, ...) whose whole text is a date
            is one item, its content running to the next h1-h3:
              url     the page with #<the heading's id> (the page's own
                      anchor, so each entry has its own link);
              title   the first content run (a bold lead line, or the first
                      sentence of the first paragraph);
              teaser  the content, at most 500 characters.
  blocks    entries in repeated containers with a year-less date badge (the
            OpenAI API changelog): a block starts at each element whose class
            contains `block_class`; its date is the first run that parses as
            a date, its content is inside the element whose class contains
            `content_class`:
              url     the page with #<a slug of the date and title> (stable
                      across fetches; the page has no anchor per entry);
              title   the content's first sentence.

Dates: "Oct 8, 2026", "October 7, 2026", "2026-10-08", "8 October 2026",
and year-less "Oct 8" (the latest such date not after the fetch day plus
one). A page states only a date, so its items carry published_precision
'date' (db/045) and published_at at 00:00 UTC that day, capped at the fetch
time by the ingest. An entry with no date is skipped: "first seen" would
date an old entry to today.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urljoin

from feed.parse import TEASER_MAX, TITLE_MAX, ParseFailed, RawItem, strip_html

MODES = ("links", "sections", "blocks")
#: Default response cap for a page, in bytes: the listings we watch carry
#: their newest entries in the first ~300 KB (measured 2026-10-13).
DEFAULT_MAX_BYTES = 1_500_000
MAX_ITEMS = 30
TITLE_WORDS = 24


class ConfigInvalid(ValueError):
    """A source's config breaks a rule; the message says which."""


@dataclass(frozen=True)
class PageConfig:
    mode: str
    start: str | None = None
    end: str | None = None
    max_bytes: int = DEFAULT_MAX_BYTES
    max_items: int = MAX_ITEMS
    # links
    href: str | None = None
    date_after: int = 0
    skip: tuple[str, ...] = ()
    # sections
    heading: tuple[str, ...] = ("h3",)
    # blocks
    block_class: str | None = None
    content_class: str | None = None


def page_config(raw: Mapping[str, Any]) -> PageConfig:
    """Checked config. Raises ConfigInvalid naming the rule."""
    mode = raw.get("mode")
    if mode not in MODES:
        raise ConfigInvalid(f"mode must be one of {', '.join(MODES)}")
    known = {"mode", "start", "end", "max_bytes", "max_items", "href", "date_after", "skip", "heading",
             "block_class", "content_class"}
    extra = sorted(set(raw) - known)
    if extra:
        raise ConfigInvalid(f"unknown key(s): {', '.join(extra)}")
    href = raw.get("href")
    if mode == "links":
        if not href:
            raise ConfigInvalid("links mode needs href (a regex)")
        try:
            re.compile(href)
        except re.error as exc:
            raise ConfigInvalid(f"href is not a regex: {exc}") from exc
    if mode == "blocks" and not (raw.get("block_class") and raw.get("content_class")):
        raise ConfigInvalid("blocks mode needs block_class and content_class")
    heading = tuple(raw.get("heading") or ("h3",))
    if not all(re.fullmatch(r"h[1-6]", h) for h in heading):
        raise ConfigInvalid("heading must list h1-h6")
    max_bytes = int(raw.get("max_bytes", DEFAULT_MAX_BYTES))
    if not 10_000 <= max_bytes <= 4_000_000:
        raise ConfigInvalid("max_bytes must be 10,000 to 4,000,000")
    max_items = int(raw.get("max_items", MAX_ITEMS))
    if not 1 <= max_items <= 50:
        raise ConfigInvalid("max_items must be 1 to 50")
    return PageConfig(
        mode=mode, start=raw.get("start"), end=raw.get("end"), max_bytes=max_bytes, max_items=max_items,
        href=href, date_after=int(raw.get("date_after", 0)), skip=tuple(s.lower() for s in raw.get("skip") or ()),
        heading=heading, block_class=raw.get("block_class"), content_class=raw.get("content_class"),
    )


# ------------------------------------------------------------------- dates

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MDY = re.compile(r"^(?P<m>[A-Za-z]{3,9})\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<y>\d{4}))?$")
_DMY = re.compile(r"^(?P<d>\d{1,2})\s+(?P<m>[A-Za-z]{3,9})\.?,?\s+(?P<y>\d{4})$")
_ISO = re.compile(r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})$")


def parse_date(text: str, today: date) -> date | None:
    """A whole text run as a date, else None. A year-less date takes the
    latest year that puts it no later than `today` plus one day (a listing
    in US time can be a day ahead of UTC)."""
    t = " ".join(text.replace(" ", " ").split()).strip(" ·|")
    if not t or len(t) > 24:
        return None
    for rx in (_MDY, _DMY, _ISO):
        m = rx.match(t)
        if not m:
            continue
        try:
            month = int(m["m"]) if rx is _ISO else _MONTHS.get(m["m"].lower())
            if not month:
                return None
            day = int(m["d"])
            if m["y"]:
                return date(int(m["y"]), month, day)
            for year in (today.year, today.year - 1):
                d = date(year, month, day)
                if d.toordinal() <= today.toordinal() + 1:
                    return d
            return None
        except ValueError:
            return None
    return None


def day_start(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=timezone.utc)


# ------------------------------------------------------------------- tokens

@dataclass(frozen=True)
class _Tok:
    kind: str           # "start", "end", "text"
    tag: str = ""
    attrs: tuple[tuple[str, str], ...] = ()
    text: str = ""

    def attr(self, name: str) -> str:
        for k, v in self.attrs:
            if k == name:
                return v or ""
        return ""


_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_SKIP_TEXT = frozenset({"script", "style", "svg", "noscript", "template"})


class _Tokens(HTMLParser):
    """A flat token list; text inside script/style/svg is dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[_Tok] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TEXT:
            self._skip += 1
        if tag in _VOID:
            return
        self.out.append(_Tok("start", tag, tuple((k, v or "") for k, v in attrs)))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TEXT and self._skip:
            self._skip -= 1
        if tag in _VOID:
            return
        self.out.append(_Tok("end", tag))

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        t = " ".join(data.split())
        if t:
            self.out.append(_Tok("text", text=t))


def tokens(markup: str) -> list[_Tok]:
    p = _Tokens()
    p.feed(markup)
    p.close()
    return p.out


def container(markup: str, start: str | None, end: str | None) -> str:
    """The listing container: from `start` (inclusive) to the first `end`
    after it (exclusive). A configured marker that is missing is a broken
    page: ParseFailed, which counts against the source."""
    if start:
        i = markup.find(start)
        if i < 0:
            raise ParseFailed(f"start marker {start[:40]!r} not found")
        markup = markup[i:]
    if end:
        j = markup.find(end, len(start or ""))
        if j < 0:
            raise ParseFailed(f"end marker {end[:40]!r} not found")
        markup = markup[:j]
    return markup


def _runs_until_close(toks: list[_Tok], i: int) -> tuple[list[str], int]:
    """Text runs inside the element opened at toks[i], and the index after it."""
    tag = toks[i].tag
    depth, runs, j = 0, [], i
    while j < len(toks):
        t = toks[j]
        if t.kind == "start" and t.tag == tag:
            depth += 1
        elif t.kind == "end" and t.tag == tag:
            depth -= 1
            if depth == 0:
                return runs, j + 1
        elif t.kind == "text":
            runs.append(t.text)
        j += 1
    return runs, j


_BLOCK = frozenset("p li ul ol div h4 h5 h6 blockquote pre table tr section article br".split())


def _paragraphs(toks: list[_Tok]) -> list[str]:
    """Text runs grouped into paragraphs at block-level tags, so inline
    links and code never split a sentence."""
    out: list[str] = []
    cur: list[str] = []
    for t in toks:
        if t.kind == "text":
            cur.append(t.text)
        elif t.tag in _BLOCK and cur:
            out.append(_join(cur))
            cur = []
    if cur:
        out.append(_join(cur))
    return [p for p in out if p]


def _join(runs: list[str]) -> str:
    """Text runs as prose: no space before punctuation ("gpt-6-luna ." from
    "<code>gpt-6-luna</code>.")."""
    return re.sub(r"\s+([.,;:!?)])", r"\1", " ".join(runs)).strip()


def _first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    words = s.split()
    return " ".join(words[:TITLE_WORDS]) + ("…" if len(words) > TITLE_WORDS else "")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _ext(prefix: str, value: str) -> str:
    return f"{prefix}:{value}"[:2000]


def parse_page(content: bytes, page_url: str, cfg: PageConfig, today: date) -> list[RawItem]:
    """Every dated entry of the listing, in page order, at most cfg.max_items."""
    markup = container(content[:cfg.max_bytes].decode("utf-8", errors="replace"), cfg.start, cfg.end)
    toks = tokens(markup)
    del markup
    if cfg.mode == "links":
        out = _links(toks, page_url, cfg, today)
    elif cfg.mode == "sections":
        out = _sections(toks, page_url, cfg, today)
    else:
        out = _blocks(toks, page_url, cfg, today)
    return out[:cfg.max_items]


def _item(ext: str, url: str, title: str, teaser: str | None, d: date) -> RawItem:
    """A listing states a day, not a time: precision 'date'."""
    return RawItem(ext, url, strip_html(title, TITLE_MAX) or title[:TITLE_MAX],
                   strip_html(teaser, TEASER_MAX) if teaser else None, day_start(d), (), "date")


def _links(toks: list[_Tok], page_url: str, cfg: PageConfig, today: date) -> list[RawItem]:
    rx = re.compile(cfg.href or "")
    seen: set[str] = set()
    out: list[RawItem] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if not (t.kind == "start" and t.tag == "a" and rx.search(t.attr("href"))):
            i += 1
            continue
        url = urljoin(page_url, html.unescape(t.attr("href")))
        runs, nxt = _runs_until_close(toks, i)
        i = nxt
        if url in seen:
            continue
        when = next((d for r in runs if (d := parse_date(r, today))), None)
        if when is None and cfg.date_after:
            after = [x.text for x in toks[nxt:nxt + cfg.date_after * 4] if x.kind == "text"][:cfg.date_after]
            when = next((d for r in after if (d := parse_date(r, today))), None)
        words = [r for r in runs if parse_date(r, today) is None and r.lower() not in cfg.skip]
        if when is None or not words:
            continue
        seen.add(url)
        # The headline is the first run of three or more words (a card puts
        # its headline before its description); the description, the longest
        # other run when it is a sentence, is the teaser.
        title = next((r for r in words if len(r.split()) >= 3), max(words, key=len))
        rest = [r for r in words if r != title]
        longest = max(rest, key=len) if rest else None
        out.append(_item(_ext("page", url), url, title, longest if longest and len(longest) >= 40 else None, when))
    return out


def _sections(toks: list[_Tok], page_url: str, cfg: PageConfig, today: date) -> list[RawItem]:
    base = page_url.split("#", 1)[0]
    out: list[RawItem] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if not (t.kind == "start" and t.tag in cfg.heading):
            i += 1
            continue
        runs, nxt = _runs_until_close(toks, i)
        # The heading's date run (a heading may also hold a "copy link" label).
        when = parse_date(runs[0], today) if runs else None
        anchor = t.attr("id")
        if when is None:
            i = nxt
            continue
        # The content: paragraphs up to the next h1-h3.
        j = nxt
        while j < len(toks) and not (toks[j].kind == "start" and toks[j].tag in ("h1", "h2", "h3")):
            j += 1
        content = _paragraphs(toks[nxt:j])
        i = j
        if not content:
            continue
        lead = _first_sentence(content[0]) if len(content[0].split()) > TITLE_WORDS else content[0]
        body = " ".join(content[1:] if lead == content[0] and len(content) > 1 else content)
        url = f"{base}#{anchor}" if anchor else f"{base}#{when.isoformat()}"
        out.append(_item(_ext("page", url), url, lead, body, when))
    return out


def _has_class(t: _Tok, needle: str | None) -> bool:
    return bool(needle) and needle in t.attr("class")


def _blocks(toks: list[_Tok], page_url: str, cfg: PageConfig, today: date) -> list[RawItem]:
    base = page_url.split("#", 1)[0]
    out: list[RawItem] = []
    seen: set[str] = set()
    i = 0
    while i < len(toks):
        t = toks[i]
        if not (t.kind == "start" and _has_class(t, cfg.block_class)):
            i += 1
            continue
        _, end = _runs_until_close(toks, i)
        when: date | None = None
        content: list[str] = []
        j = i + 1
        while j < end:
            x = toks[j]
            if when is None and x.kind == "text":
                when = parse_date(x.text, today)
            if x.kind == "start" and _has_class(x, cfg.content_class):
                runs, j = _runs_until_close(toks, j)
                content.extend(runs)
                continue
            j += 1
        i = end
        text = _join(content)
        if when is None or not text:
            continue
        title = _first_sentence(text)
        key = f"{when.isoformat()}-{_slug(title)}"
        if key in seen:
            continue
        seen.add(key)
        url = f"{base}#{key}"
        # The id hashes date and title: the slug alone could collide.
        ext = _ext("page", f"{base}#{when.isoformat()}-{hashlib.sha1(title.encode()).hexdigest()[:12]}")
        out.append(_item(ext, url, title, text, when))
    return out


# --------------------------------------------------------------- Hacker News

@dataclass(frozen=True)
class HnConfig:
    min_points: int


def hn_config(raw: Mapping[str, Any]) -> HnConfig:
    extra = sorted(set(raw) - {"min_points"})
    if extra:
        raise ConfigInvalid(f"unknown key(s): {', '.join(extra)}")
    pts = raw.get("min_points")
    if not isinstance(pts, int) or not 1 <= pts <= 10_000:
        raise ConfigInvalid("min_points must be a whole number, 1 to 10,000")
    return HnConfig(pts)


HN_ITEM = "https://news.ycombinator.com/item?id={id}"


def parse_hn(content: bytes, cfg: HnConfig) -> list[RawItem]:
    """Algolia search hits at or above the points threshold. The link is the
    story's URL, or its HN page for a text post (Ask HN, some Show HN)."""
    try:
        data = json.loads(content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ParseFailed(f"not JSON ({type(exc).__name__})") from exc
    hits = data.get("hits") if isinstance(data, dict) else None
    if not isinstance(hits, list):
        raise ParseFailed("no hits list")
    out: list[RawItem] = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        oid, title = h.get("objectID"), strip_html(h.get("title"), TITLE_MAX)
        points, created = h.get("points"), h.get("created_at_i")
        if not oid or not title or not isinstance(points, int) or points < cfg.min_points:
            continue
        link = h.get("url") or HN_ITEM.format(id=oid)
        comments = h.get("num_comments") or 0
        when = datetime.fromtimestamp(created, tz=timezone.utc) if isinstance(created, int) else None
        out.append(RawItem(f"hn:{oid}", link, title, f"{points} points and {comments} comments on Hacker News.",
                           when, ()))
    return out
