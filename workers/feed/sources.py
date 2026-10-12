"""workers/feed/sources.toml, loaded and checked. The worker syncs it into the
`sources` table at boot (ledger.feed_sync_sources)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

SOURCES_FILE = Path(__file__).with_name("sources.toml")
GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
PILLARS = frozenset({"home", "sports"})
SPORTS = frozenset({"cfb", "nfl", "nba", "f1", "golf"})
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class SourcesInvalid(ValueError):
    """sources.toml breaks a rule; the message names the source and the rule."""


@dataclass(frozen=True)
class SourceSpec:
    slug: str
    name: str
    pillars: tuple[str, ...]
    sport: str | None
    kind: str
    url: str
    priority: int
    active: bool
    include_categories: tuple[str, ...] | None
    window_days: int
    notes: str | None
    #: "source": the source's sport is the story's (the summarizer's only when
    #: the source has none). "model": a multi-sport source; the summarizer's
    #: sport wins and the source's is the fallback when it says none (owner
    #: D8, 2026-10-12).
    sport_from: str = "source"


SPORT_FROM = frozenset({"source", "model"})


def gnews_url(query: str, window_days: int) -> str:
    return GNEWS.format(q=quote_plus(f"{query} when:{window_days}d"))


def _spec(raw: dict) -> SourceSpec:
    slug = raw.get("slug", "")
    where = f"source {slug or '?'}"
    if not _SLUG.match(slug):
        raise SourcesInvalid(f"{where}: slug must be lowercase letters, digits and hyphens")
    pillars = tuple(raw.get("pillars") or ())
    if not pillars or not set(pillars) <= PILLARS:
        raise SourcesInvalid(f"{where}: pillars must be a non-empty subset of {sorted(PILLARS)}")
    sport = raw.get("sport")
    if sport is not None and sport not in SPORTS:
        raise SourcesInvalid(f"{where}: unknown sport {sport!r}")
    kind = raw.get("kind")
    window = int(raw.get("window_days", 2))
    if not 1 <= window <= 30:
        raise SourcesInvalid(f"{where}: window_days must be 1-30")
    if kind == "rss":
        url = raw.get("url", "")
        if not url.startswith("https://") or "query" in raw:
            raise SourcesInvalid(f"{where}: an rss source has an https url and no query")
    elif kind == "gnews":
        if not raw.get("query") or "url" in raw:
            raise SourcesInvalid(f"{where}: a gnews source has a query and no url")
        url = gnews_url(raw["query"], window)
    else:
        raise SourcesInvalid(f"{where}: kind must be rss or gnews")
    cats = raw.get("include_categories")
    sport_from = raw.get("sport_from", "source")
    if sport_from not in SPORT_FROM:
        raise SourcesInvalid(f"{where}: sport_from must be source or model")
    return SourceSpec(
        slug=slug, name=str(raw.get("name") or ""), pillars=pillars, sport=sport, kind=kind,
        url=url, priority=int(raw.get("priority", 100)), active=bool(raw.get("active", True)),
        include_categories=tuple(cats) if cats else None, window_days=window,
        notes=raw.get("notes"), sport_from=sport_from,
    )


def load_sources(path: Path = SOURCES_FILE) -> list[SourceSpec]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    specs = [_spec(raw) for raw in data.get("source", [])]
    slugs = [s.slug for s in specs]
    dupes = sorted({s for s in slugs if slugs.count(s) > 1})
    if dupes:
        raise SourcesInvalid(f"duplicate slugs: {', '.join(dupes)}")
    for s in specs:
        if not s.name:
            raise SourcesInvalid(f"source {s.slug}: name is required")
    return specs


def model_sport_slugs(path: Path = SOURCES_FILE) -> frozenset[str]:
    """The multi-sport sources, whose stories take the summarizer's sport."""
    return frozenset(s.slug for s in load_sources(path) if s.sport_from == "model")
