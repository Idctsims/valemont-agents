"""workers/feed/sources.toml, loaded and checked. The worker syncs it into the
`sources` table at boot (ledger.feed_sync_sources).

Tech Hub (db/045): pillar "tech" is exclusive (a tech source is tech only);
kind "page" is a listing-page watcher and kind "hn" a Hacker News query, both
with a `config` table checked here by feed/watch.py; `official` marks the
first-party sources (the "Official" tag, summarized first)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from feed import watch

SOURCES_FILE = Path(__file__).with_name("sources.toml")
GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
PILLARS = frozenset({"home", "sports", "tech"})
SPORTS = frozenset({"cfb", "nfl", "nba", "f1", "golf"})
KINDS = frozenset({"rss", "gnews", "page", "hn", "github"})
HN_API = "https://hn.algolia.com/api/v1/"
#: The repos source (db/049): tech only, never official, fetched by the daily
#: repos job (feed/repos.py), never by the 30-minute ingest.
GITHUB = "https://github.com/"
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
    #: A first-party source (db/045): the "Official" tag, summarized first.
    official: bool = False
    #: A page or hn source's checked config (feed/watch.py), else None.
    config: dict[str, Any] | None = None


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
    if "tech" in pillars and pillars != ("tech",):
        raise SourcesInvalid(f"{where}: a tech source is tech only (db/045: no cross-in)")
    sport = raw.get("sport")
    if sport is not None and sport not in SPORTS:
        raise SourcesInvalid(f"{where}: unknown sport {sport!r}")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise SourcesInvalid(f"{where}: kind must be one of {', '.join(sorted(KINDS))}")
    window = int(raw.get("window_days", 2))
    if not 1 <= window <= 30:
        raise SourcesInvalid(f"{where}: window_days must be 1-30")
    config = raw.get("config")
    if kind in ("page", "hn"):
        if not isinstance(config, dict):
            raise SourcesInvalid(f"{where}: a {kind} source needs a config table")
        try:
            watch.page_config(config) if kind == "page" else watch.hn_config(config)
        except watch.ConfigInvalid as exc:
            raise SourcesInvalid(f"{where}: config: {exc}") from exc
    elif config is not None:
        raise SourcesInvalid(f"{where}: only page and hn sources take a config")
    if kind == "gnews":
        if not raw.get("query") or "url" in raw:
            raise SourcesInvalid(f"{where}: a gnews source has a query and no url")
        url = gnews_url(raw["query"], window)
    else:
        url = raw.get("url", "")
        if not url.startswith("https://") or "query" in raw:
            raise SourcesInvalid(f"{where}: an {kind} source has an https url and no query")
        if kind == "hn" and not url.startswith(HN_API):
            raise SourcesInvalid(f"{where}: an hn source queries {HN_API}")
        if kind == "github" and not url.startswith(GITHUB):
            raise SourcesInvalid(f"{where}: a github source is on {GITHUB}")
    official = raw.get("official", False)
    if not isinstance(official, bool) or (official and pillars != ("tech",)):
        raise SourcesInvalid(f"{where}: official is true or false, and only on a tech source")
    if kind == "github" and (pillars != ("tech",) or official):
        raise SourcesInvalid(f"{where}: a github source is tech only and never official (db/049)")
    cats = raw.get("include_categories")
    sport_from = raw.get("sport_from", "source")
    if sport_from not in SPORT_FROM:
        raise SourcesInvalid(f"{where}: sport_from must be source or model")
    return SourceSpec(
        slug=slug, name=str(raw.get("name") or ""), pillars=pillars, sport=sport, kind=kind,
        url=url, priority=int(raw.get("priority", 100)), active=bool(raw.get("active", True)),
        include_categories=tuple(cats) if cats else None, window_days=window,
        notes=raw.get("notes"), sport_from=sport_from, official=official,
        config=dict(config) if config is not None else None,
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
