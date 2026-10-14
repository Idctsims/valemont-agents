"""Feed bytes -> items, and URL canonicalization. Pure: no network, no database.

feedparser does the parsing (RSS 0.9x/1.0/2.0, Atom, and the character-set
detection that makes a non-UTF-8 feed readable). A feed that parses to no
entries AND reports a well-formedness error is a broken feed (ParseFailed,
which counts against the source); a well-formed feed with no entries is
simply empty.
"""

from __future__ import annotations

import calendar
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

TEASER_MAX = 500
TITLE_MAX = 500
MAX_ITEMS = 50
#: Tracking parameters stripped from every URL (owner: utm_* and fbclid; the
#: rest are the same thing under other names).
_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid)$", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


class ParseFailed(ValueError):
    """The bytes are not a feed."""


@dataclass(frozen=True)
class RawItem:
    external_id: str
    link: str | None
    title: str
    teaser: str | None
    #: As the feed states it, UTC; None for an undated item.
    published_at: datetime | None
    categories: tuple[str, ...]
    #: 'date' when the source states only a day (a listing page, or a feed
    #: stamping every entry at midnight), else 'time' (db/045).
    precision: str = "time"


def strip_html(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    out = _WS.sub(" ", html.unescape(_TAG.sub(" ", text))).strip()
    if not out:
        return None
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


def canonical_url(url: str | None, *, keep_fragment: bool = False) -> str | None:
    """utm_*/fbclid (and kin) stripped, fragment dropped, scheme and host
    lowercased, default port and trailing host dot removed. The path and the
    remaining query are kept as they are: they can be case-sensitive.
    `keep_fragment`: a release-notes page's entries differ only by fragment
    (feed/watch.py sections and blocks), so a page source keeps it."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname.lower().rstrip(".")
    port = parts.port
    netloc = host if port is None or (scheme, port) in (("http", 80), ("https", 443)) else f"{host}:{port}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not _TRACKING.match(k)])
    return urlunsplit((scheme, netloc, parts.path or "/", query, parts.fragment if keep_fragment else ""))


_CLOSE_ITEM = re.compile(rb"</(?:item|entry)\s*>", re.I)
_ATOM = re.compile(rb"<feed[\s>]", re.I)


def head_items(content: bytes, n: int = MAX_ITEMS) -> bytes | None:
    """The feed cut after its n-th entry and closed again, or None when it
    has fewer: a huge feed (Vercel's Atom is 3.7 MB) is read only as far as
    the ingest keeps (parse_feed takes the first MAX_ITEMS anyway)."""
    ends = 0
    for m in _CLOSE_ITEM.finditer(content):
        ends += 1
        if ends == n:
            tail = b"\n</feed>\n" if _ATOM.search(content[:4096]) else b"\n</channel>\n</rss>\n"
            return content[:m.end()] + tail
    return None


#: A feed that stamps entries at midnight states a day, not a time.
_MIDNIGHT = re.compile(r"(T|\s)00:00(:00(\.0+)?)?(\s|Z|[+-]\d|$)")


def _when(entry: dict) -> datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    except (OverflowError, ValueError):
        return None


def _gnews_title(title: str) -> str:
    """Google News titles end in ' - Outlet'; the outlet is on the card already."""
    head, sep, _tail = title.rpartition(" - ")
    return head.strip() if sep and head.strip() else title


def parse_feed(content: bytes, *, gnews: bool = False) -> list[RawItem]:
    """Every usable entry (a title and an id or link), newest first as the
    feed orders them, at most MAX_ITEMS."""
    d = feedparser.parse(content)
    entries = list(d.entries)
    if not entries:
        # A well-formed feed with no items has a version ('rss20', 'atom10'...);
        # an HTML error page or truncated XML has none.
        if not d.get("version"):
            raise ParseFailed(f"not a feed ({type(d.get('bozo_exception')).__name__ if d.bozo else 'no version'})")
        return []
    out: list[RawItem] = []
    for e in entries:
        title = strip_html(e.get("title"), TITLE_MAX)
        link = e.get("link") or None
        ext = (e.get("id") or e.get("guid") or link or "").strip()
        if not title or not ext:
            continue
        if gnews:
            title = _gnews_title(title)
        # A Google News description is only the title and the outlet again.
        teaser = None if gnews else strip_html(e.get("summary") or e.get("description"), TEASER_MAX)
        cats = tuple(t.get("term", "").strip() for t in e.get("tags", []) or [] if t.get("term"))
        stamp = str(e.get("published") or e.get("updated") or "")
        out.append(RawItem(ext[:2000], link, title, teaser, _when(e), cats,
                           "date" if stamp and _MIDNIGHT.search(stamp) else "time"))
        if len(out) >= MAX_ITEMS:
            break
    return out


def keeps_category(item: RawItem, include: tuple[str, ...] | None) -> bool:
    if not include:
        return True
    wanted = {c.lower() for c in include}
    return any(c.lower() in wanted for c in item.categories)
