"""Trailers from YouTube channel feeds (Chat 4 Phase 3; owner, 2026-10-14).

KEEP (is_trailer): a watch-page video (never a Short: the feed links those
to /shorts/) whose title says "Trailer" or "Teaser" ("Official Trailer",
"Official Teaser", "Trailer 2", "Teaser Trailer"), unless it is about a
trailer rather than one: reaction, breakdown, recap, explained, featurette,
clip, behind the scenes, review, reacts. Everything else (Sneak Peek, first
look, podcasts, promos, full episodes) is dropped.

MATCH (match): the headline is cleaned (clean_title) to the show or film's
name, searched on TMDB (search/multi), and accepted only on an EXACT match
of the normalized name:
  * a movie also needs its US release year within one year of the trailer's
    post (a 2026 trailer for "City of God" is not the 2003 film);
  * a "Season N" / "S2" / "Limited Series" headline wants TV first;
  * no exact name: once more without a trailing sequel number ("Cyberpunk:
    Edgerunners 2" is the series "Cyberpunk: Edgerunners"), TV only.
No exact match is UNMATCHED: the trailer still shows, flagged.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

KEEP = re.compile(r"\b(trailers?|teasers?)\b", re.IGNORECASE)
DROP = re.compile(r"\b(reactions?|reacts?|breakdowns?|recaps?|explained|featurettes?|clips?|behind the scenes|"
                  r"reviews?|first reaction|easter eggs?)\b", re.IGNORECASE)


def is_short(link: str | None) -> bool:
    return bool(link) and "/shorts/" in str(link)


def is_trailer(title: str, link: str | None) -> bool:
    return not is_short(link) and bool(KEEP.search(title or "")) and not DROP.search(title or "")


#: Segments that are not the title: the trailer label, the channel name, a
#: "Stream now on ..." line, a rating, a resolution.
_LABEL = re.compile(r"^\s*(official\s+)?(final\s+|main\s+|teaser\s+|first\s+|international\s+)?"
                    r"(trailers?|teasers?)(\s*#?\d+)?(\s+hd|\s+4k)?\s*$", re.IGNORECASE)
_TAIL_LABEL = re.compile(r"\s*[-–—:]?\s*(official\s+)?(final\s+|main\s+|teaser\s+|first\s+)?(trailers?|teasers?)"
                         r"(\s*#?\d+)?(\s+hd|\s+4k)?\s*$", re.IGNORECASE)
_SEASON = re.compile(r"\s*[-–—:,]?\s*(\bseason\s+\d+\b|\bs\d{1,2}\b|\blimited series\b|\bseries\b|\bpart\s+\d+\b)"
                     r".*$", re.IGNORECASE)
_YEAR = re.compile(r"\((19|20)\d{2}\)")
_SPLIT = re.compile(r"\s+[|]\s+|\s+[-–—]\s+(?=(official\s+)?(trailer|teaser))", re.IGNORECASE)


@dataclass(frozen=True)
class Cleaned:
    query: str
    tv_hint: bool
    year: int | None


def clean_title(headline: str) -> Cleaned:
    """The show or film name a trailer headline is about.
    "Guarding Stars | Leighton Meester & ... | Official Trailer | Netflix" ->
    "Guarding Stars"; "The Gilded Age Season 4 | Official Trailer" -> "The
    Gilded Age" (TV); "The Greatest - Official Trailer | Prime Video" -> "The
    Greatest"."""
    text = " ".join(str(headline or "").split())
    year_m = _YEAR.search(text)
    parts = [p for p in _SPLIT.split(text) if p and not re.fullmatch(r"(official\s+)?(trailer|teaser)", p, re.I)]
    first = next((p for p in parts if not _LABEL.match(p)), parts[0] if parts else text)
    tv_hint = bool(_SEASON.search(first))
    name = _SEASON.sub("", first)
    name = _TAIL_LABEL.sub("", name)
    name = _YEAR.sub("", name).strip(" -–—:|,")
    return Cleaned(name or first.strip(), tv_hint, int(year_m.group(0)[1:5]) if year_m else None)


def norm(name: str) -> str:
    """Lowercase, no accents, no punctuation, single spaces; "&" is "and"."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _year(r: dict[str, Any]) -> int | None:
    raw = r.get("release_date") or r.get("first_air_date") or ""
    return int(raw[:4]) if len(raw) >= 4 and raw[:4].isdigit() else None


@dataclass(frozen=True)
class Match:
    tmdb_id: int
    kind: str
    name: str


def pick(results: Sequence[dict[str, Any]], c: Cleaned, posted: datetime | None, *, tv_only: bool = False) -> Match | None:
    want = norm(c.query)
    ref_year = c.year or (posted.year if posted else None)
    exact = [r for r in results if norm(r.get("title") or r.get("name") or "") == want
             and (not tv_only or r.get("media_type") == "tv")]
    ok = []
    for r in exact:
        if r.get("media_type") == "movie":
            y = _year(r)
            if ref_year is not None and (y is None or abs(y - ref_year) > 1):
                continue
        ok.append(r)
    if not ok:
        return None
    if c.tv_hint:
        ok.sort(key=lambda r: r.get("media_type") != "tv")
    r = ok[0]
    return Match(int(r["id"]), str(r["media_type"]), str(r.get("title") or r.get("name")))


_SEQUEL = re.compile(r"\s+\d{1,2}$")


def match(search: Any, headline: str, posted: datetime | None) -> tuple[Match | None, str]:
    """(the TMDB title, or None for unmatched; the query searched).
    `search(query) -> results` is Tmdb.search_multi."""
    c = clean_title(headline)
    if not c.query:
        return None, ""
    hit = pick(search(c.query), c, posted)
    if hit is None and _SEQUEL.search(c.query):
        base = Cleaned(_SEQUEL.sub("", c.query), True, c.year)
        hit = pick(search(base.query), base, posted, tv_only=True)
    return hit, c.query
