"""tech_repos: GitHub repos in Tech Hub (Chat 4 Phase 2; owner, 2026-10-14;
db/049; docs/tech_hub.md §7).

Daily at 07:00 America/Chicago, and at boot if today's run has not happened
(today has no star snapshot yet: JobSkipped otherwise).

  1. DISCOVER. github.com/trending, daily and weekly, parsed from the HTML
     (parse_trending; the selectors are johnku2011/github-trending-api's and
     lisniuse/github-trending-skill's, on stdlib html.parser). A parse that
     finds 0 repos, or a repo card missing its name or stars, RAISES: the
     job fails in job_health; it is never an empty day. Then the Search API,
     one query per interest (INTERESTS): repos created in the last 30 days,
     by stars, at most SEARCH_PER_MIN calls a minute (a 2.5 s gap).
  2. ENRICH. Search results are REST repository objects already (topics,
     stars, pushed_at, license, archived, fork). A trending-only repo gets
     GET /repos/{full_name}. Every candidate that passes the metadata
     filters gets the first README_BYTES of its README, streamed and
     dropped once read.
  3. SNAPSHOT. One repo_star_snapshots row per candidate per owner's day
     (append-only; the day is the database's). Repos tracked in the last
     TRACK_DAYS that were not candidates today are re-read and snapshotted
     too, at most TRACK_MAX, so stars_7d can be measured for them later.
  4. STARS_7D (stars_7d): today's snapshot minus the one 7 days ago,
     'measured'. Until a repo has that, an ESTIMATE, always labelled:
     trending's stars in its period ('trending_weekly', then
     'trending_daily'); for a search-only repo, every star when it is under
     7 days old ('since_created') or the age average ('age_average'). The
     card says "trending est." / "since created" / "avg est."; an estimate
     is never shown as measured. A trending count over half a 30-day-old
     repo's total is flagged (trending_suspect), and one over its total is a
     parse failure.
  5. SELECT, at most DAILY_MAX (8) a day. Hard filters (hard_filter):
     archived, fork, no README, not pushed in 90 days. Rank: the tech
     interest profile (feed/rank.score_story: embedding against the loves
     and not-interesteds, seeds included, plus learned topic weights), plus
     W_VELOCITY × log10(1 + stars_7d), plus W_INTEREST for a repo found by
     an interest search, less W_LOW_TRACTION under LOW_TRACTION stars in 7
     days (rank_score; weights from the 2026-10-14 dry run). A repo surfaces once; again only by
     db/049 repo_may_surface (stars_7d at least double its first-shown
     stars_7d, and never within 14 days). The day-one run is an ordinary
     day: 8, not a backfill dump.
  6. SURFACE. Each pick becomes a tech story (category repos, topics = its
     GitHub topics in snake_case, title owner/name) with one item from the
     `github-repos` source (published_at = the day it surfaced, precision
     'date'; teaser = description + README head) and a repo_surfacings row,
     in one transaction. The database's trigger is the rule: a 9th, or a
     resurfacing the rule refuses, rolls the pick back.

The 30-minute feed chain then summarizes them (feed/tech.py: Haiku,
counted under Tech's community cap; repos are not official and never push)
and writes why-it-matters (feed/why.py repo_why_job).

THE TOKEN: GITHUB_PUBLIC_READ_TOKEN (a fine-grained token, public repos,
read-only, no permissions). NEVER GITHUB_TOKEN, which the gh CLI reads. A
missing token, or one GitHub rejects (401, or 403 that is not a rate limit),
raises RepoTokenError naming the variable, never its value.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Mapping, Sequence

from core import embed as embedder
from core import ledger, memory
from core.jobs import JobSkipped
from feed import rank

log = logging.getLogger("valemont.feed.repos")

JOB_NAME = "tech_repos"
TOKEN_ENV = "GITHUB_PUBLIC_READ_TOKEN"
SOURCE_SLUG = "github-repos"
API = "https://api.github.com"
TRENDING_URL = "https://github.com/trending?since={period}"
PERIODS: tuple[str, ...] = ("daily", "weekly")
USER_AGENT = "ValemontCommand/1.0 (+https://valemont-command.vercel.app/privacy)"
TIMEOUT_S = 20.0

#: The owner's interests (2026-10-14), one Search API query each.
INTERESTS: tuple[tuple[str, str], ...] = (
    ("AI agents", "ai agent"),
    ("MCP servers", "mcp server"),
    ("Claude/Anthropic tooling", "claude OR anthropic"),
    ("trading bots", "trading bot"),
    ("Next.js", "nextjs"),
    ("Supabase", "supabase"),
    ("Vercel", "vercel"),
    ("automation", "automation"),
    ("marketing tools", "marketing tool"),
)
SEARCH_PER_PAGE = 10
SEARCH_CREATED_DAYS = 30
#: GitHub allows 30 authenticated search calls a minute; stay under it.
SEARCH_PER_MIN = 24
TRENDING_MAX_BYTES = 1_500_000
README_BYTES = 2048

DAILY_MAX = 8
STALE_PUSH_DAYS = 90
TRACK_DAYS = 30
TRACK_MAX = 150
#: The rank's weights, set from the dry run of 2026-10-14 (103 real
#: candidates, the real tech profile, nothing written): at 0.6 for velocity
#: alone, viral off-interest repos (a PS5 porting tool, an Xcode replacement)
#: took the 8; with the profile alone, 8-star repos did. Velocity 0.35, a
#: bonus for matching one of the owner's INTERESTS (the repo came from that
#: interest's search), and a penalty below LOW_TRACTION stars in 7 days kept
#: the agent, Claude Code and Next.js repos on top. A ranking term, not a hard
#: filter: a strong profile match can still beat it. The owner judges day one.
W_VELOCITY = 0.35
W_INTEREST = 0.4
LOW_TRACTION = 50
W_LOW_TRACTION = 0.5

TEASER_MAX = 500
TOPICS_MAX = 30


class RepoTokenError(RuntimeError):
    """The token is missing or GitHub rejected it. Never carries its value."""


class RateLimited(RuntimeError):
    """GitHub's rate limit (403/429 with no calls left)."""


class TrendingParseFailed(RuntimeError):
    """github.com/trending did not parse: 0 repos, or a card missing its name or stars."""


# ------------------------------------------------------------------ trending

@dataclass(frozen=True)
class TrendingRepo:
    full_name: str
    description: str | None
    language: str | None
    stars: int
    #: Stars gained in the page's period ("41,187 stars today").
    stars_period: int
    period: str


_NUM = re.compile(r"([\d.,]+)\s*([kKmM]?)")
_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def parse_count(text: str) -> int | None:
    """'238,274' -> 238274; '1.2k' -> 1200; no number -> None."""
    m = _NUM.search(text or "")
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    return int(round(value * {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1)))


class _TrendingParser(HTMLParser):
    """article.Box-row cards: the h2 link (owner/name), p.col-9 (description),
    span[itemprop=programmingLanguage], the /stargazers link (stars) and
    span.float-sm-right ("N stars today|this week")."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, Any]] = []
        self.card: dict[str, Any] | None = None
        self.in_h2 = False
        #: (field, tag, depth): text goes to `field` until `tag` closes at `depth`.
        self.capture: tuple[str, str, int] | None = None
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        classes = set(a.get("class", "").split())
        if tag == "article" and "Box-row" in classes:
            self.card = {"text": {}}
            self.depth = 0
            return
        if self.card is None:
            return
        self.depth += 1
        if tag == "h2":
            self.in_h2 = True
        elif tag == "a" and self.in_h2 and "name" not in self.card:
            self.card["name"] = a.get("href", "").strip("/")
        elif self.capture is None:
            if tag == "p" and "col-9" in classes:
                self.capture = ("description", tag, self.depth)
            elif tag == "span" and a.get("itemprop") == "programmingLanguage":
                self.capture = ("language", tag, self.depth)
            elif tag == "a" and a.get("href", "").endswith("/stargazers"):
                self.capture = ("stars", tag, self.depth)
            elif tag == "span" and "float-sm-right" in classes:
                self.capture = ("period", tag, self.depth)

    def handle_endtag(self, tag: str) -> None:
        if self.card is None:
            return
        if tag == "article":
            self.cards.append(self.card)
            self.card, self.capture, self.in_h2 = None, None, False
            return
        if self.capture and tag == self.capture[1] and self.depth == self.capture[2]:
            self.capture = None
        if tag == "h2":
            self.in_h2 = False
        self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.card is not None and self.capture is not None:
            parts = self.card["text"].setdefault(self.capture[0], [])
            parts.append(data)


def parse_trending(content: bytes | str, period: str) -> list[TrendingRepo]:
    """Every repo card on a github.com/trending page. Raises
    TrendingParseFailed when there are none, or when a card has no
    owner/name or no star count: the markup changed, and a quiet empty day
    would hide it."""
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    p = _TrendingParser()
    p.feed(text)
    p.close()
    if not p.cards:
        raise TrendingParseFailed(f"trending ({period}): 0 repos parsed; the page markup changed")
    out: list[TrendingRepo] = []
    for i, card in enumerate(p.cards, 1):
        name = card.get("name", "")
        t = {k: " ".join("".join(v).split()) for k, v in card["text"].items()}
        stars = parse_count(t.get("stars", ""))
        period_stars = parse_count(t.get("period", ""))
        if not _NAME.match(name) or stars is None or period_stars is None:
            raise TrendingParseFailed(
                f"trending ({period}): card {i} has no owner/name, stars or stars-in-period "
                f"(name {name!r}); the page markup changed")
        # Impossible: more stars in the period than in total means the two
        # numbers were read from the wrong places (owner, 2026-10-14).
        if period_stars > stars:
            raise TrendingParseFailed(
                f"trending ({period}): card {i} ({name}) has {period_stars} stars in the period but {stars} "
                f"in total; the page markup changed")
        out.append(TrendingRepo(name, t.get("description") or None, t.get("language") or None, stars,
                                period_stars, period))
    return out


# ------------------------------------------------------------------ GitHub

@dataclass(frozen=True)
class HttpResponse:
    status: int
    content: bytes
    headers: Mapping[str, str]

    def json(self) -> Any:
        import json
        return json.loads(self.content or b"null")


#: (url, params, headers, max_bytes) -> HttpResponse. Tests inject one.
Http = Callable[[str, Mapping[str, Any] | None, Mapping[str, str], int | None], HttpResponse]


def httpx_get() -> tuple[Http, Callable[[], None]]:
    """One httpx client for the run; a streamed read stops at max_bytes and
    closes the response, so the rest of a body is never held."""
    import httpx

    client = httpx.Client(timeout=TIMEOUT_S, follow_redirects=True)

    def get(url: str, params: Mapping[str, Any] | None, headers: Mapping[str, str],
            max_bytes: int | None) -> HttpResponse:
        if max_bytes is None:
            r = client.get(url, params=params, headers=dict(headers))
            return HttpResponse(r.status_code, r.content, {k.lower(): v for k, v in r.headers.items()})
        with client.stream("GET", url, params=params, headers=dict(headers)) as r:
            buf = bytearray()
            if r.status_code == 200:
                for chunk in r.iter_bytes(16 * 1024):
                    buf += chunk
                    if len(buf) >= max_bytes:
                        del buf[max_bytes:]
                        break
            return HttpResponse(r.status_code, bytes(buf), {k.lower(): v for k, v in r.headers.items()})

    return get, client.close


def read_token(env: Mapping[str, str] = os.environ) -> str:
    token = (env.get(TOKEN_ENV) or "").strip()
    if not token:
        raise RepoTokenError(f"{TOKEN_ENV} is not set (Railway and the root .env only; never GITHUB_TOKEN)")
    return token


class GitHub:
    """The REST and Search API with the public-read token, and the trending
    page (no token). Counts every call it makes."""

    def __init__(self, token: str, http: Http, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._auth = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28", "User-Agent": USER_AGENT}
        self.http, self.sleep, self.clock = http, sleep, clock
        #: search and core are the API's two limits; rate_limit calls are free.
        self.calls: dict[str, int] = {"search": 0, "core": 0, "trending": 0, "rate_limit": 0}
        self._last_search: float | None = None
        self.gap = 60.0 / SEARCH_PER_MIN

    def _check(self, r: HttpResponse, what: str) -> None:
        if r.status == 401:
            raise RepoTokenError(f"GitHub rejected {TOKEN_ENV} (HTTP 401 on {what})")
        if r.status in (403, 429):
            if r.headers.get("x-ratelimit-remaining") == "0" or r.status == 429:
                raise RateLimited(f"GitHub rate limit on {what} (HTTP {r.status}, "
                                  f"resets {r.headers.get('x-ratelimit-reset', '?')})")
            raise RepoTokenError(f"GitHub refused {TOKEN_ENV} (HTTP 403 on {what})")

    def _api(self, path: str, params: Mapping[str, Any] | None = None, *, kind: str = "core",
             accept: str | None = None, max_bytes: int | None = None) -> HttpResponse:
        headers = dict(self._auth)
        if accept:
            headers["Accept"] = accept
        self.calls[kind] += 1
        r = self.http(API + path, params, headers, max_bytes)
        self._check(r, path.split("?")[0])
        return r

    def check_token(self) -> Mapping[str, Any]:
        """GET /rate_limit (not counted against the limit): proves the token
        is accepted before anything else runs."""
        r = self._api("/rate_limit", kind="rate_limit")
        if r.status != 200:
            raise RuntimeError(f"GitHub /rate_limit answered HTTP {r.status}")
        return r.json() or {}

    def trending(self, period: str) -> list[TrendingRepo]:
        self.calls["trending"] += 1
        r = self.http(TRENDING_URL.format(period=period), None, {"User-Agent": USER_AGENT}, TRENDING_MAX_BYTES)
        if r.status != 200:
            raise RuntimeError(f"github.com/trending ({period}) answered HTTP {r.status}")
        return parse_trending(r.content, period)

    def search(self, query: str, created_since: date) -> list[dict[str, Any]]:
        if self._last_search is not None:
            wait = self.gap - (self.clock() - self._last_search)
            if wait > 0:
                self.sleep(wait)
        self._last_search = self.clock()
        r = self._api("/search/repositories",
                      {"q": f"{query} created:>={created_since.isoformat()} fork:false archived:false",
                       "sort": "stars", "order": "desc", "per_page": SEARCH_PER_PAGE}, kind="search")
        if r.status != 200:
            raise RuntimeError(f"GitHub search answered HTTP {r.status}")
        items = (r.json() or {}).get("items")
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    def repo(self, full_name: str) -> dict[str, Any] | None:
        r = self._api(f"/repos/{full_name}")
        if r.status == 404:
            return None
        if r.status != 200:
            raise RuntimeError(f"GitHub /repos answered HTTP {r.status}")
        data = r.json()
        return data if isinstance(data, dict) else None

    def readme_head(self, full_name: str) -> str | None:
        """The first README_BYTES of the README, raw; None when there is none."""
        r = self._api(f"/repos/{full_name}/readme", accept="application/vnd.github.raw",
                      max_bytes=README_BYTES)
        if r.status == 404:
            return None
        if r.status != 200:
            raise RuntimeError(f"GitHub /readme answered HTTP {r.status}")
        text = r.content.decode("utf-8", "replace")
        del r
        return text if text.strip() else None


# ------------------------------------------------------------------ candidates

@dataclass
class Candidate:
    full_name: str
    html_url: str
    description: str | None = None
    language: str | None = None
    topics: tuple[str, ...] = ()
    license: str | None = None
    archived: bool = False
    fork: bool = False
    created_at: datetime | None = None
    pushed_at: datetime | None = None
    stars: int = 0
    #: Trending stars in period, when the repo was on that page today.
    trending_daily: int | None = None
    trending_weekly: int | None = None
    interests: list[str] = field(default_factory=list)
    enriched: bool = False
    readme: str | None = None


def _ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def apply_rest(c: Candidate, data: Mapping[str, Any]) -> None:
    """Fill a candidate from a REST repository object (search or /repos)."""
    c.full_name = str(data.get("full_name") or c.full_name)
    c.html_url = str(data.get("html_url") or c.html_url)
    c.description = (str(data["description"]).strip() or None) if data.get("description") else c.description
    c.language = data.get("language") or c.language
    c.topics = tuple(str(t) for t in (data.get("topics") or []))[:TOPICS_MAX]
    lic = data.get("license")
    c.license = (lic.get("spdx_id") or lic.get("name")) if isinstance(lic, dict) else None
    c.archived = bool(data.get("archived"))
    c.fork = bool(data.get("fork"))
    c.created_at = _ts(data.get("created_at"))
    c.pushed_at = _ts(data.get("pushed_at"))
    c.stars = int(data.get("stargazers_count") or 0)
    c.enriched = True


def hard_filter(c: Candidate, now: datetime, *, readme_checked: bool = True) -> str | None:
    """Why a candidate can never be shown, or None. The README is checked
    last (it costs a call), so `readme_checked=False` asks the rest only."""
    if c.archived:
        return "archived"
    if c.fork:
        return "fork"
    if c.pushed_at is None or now - c.pushed_at > timedelta(days=STALE_PUSH_DAYS):
        return f"not pushed in {STALE_PUSH_DAYS} days"
    if readme_checked and not (c.readme and c.readme.strip()):
        return "no README"
    return None


Stars7d = tuple[int | None, str | None]

#: The card's label per source: only 'measured' reads as a measurement.
LABELS: dict[str, str] = {
    "measured": "7d",
    "trending_weekly": "trending est.",
    "trending_daily": "trending est.",
    "since_created": "since created",
    # Owner, 2026-10-14: distinct from "trending est.", so the card says
    # which guess it is (stars / age in days × 7).
    "age_average": "avg est.",
}

#: A trending stars-in-period above this share of the total, on a repo older
#: than SUSPECT_AGE_DAYS, is flagged (owner, 2026-10-14: morluto/rea showed
#: 183,758 "stars this week" on 239,045 total at six months old). Flagged, not
#: dropped: logged and counted in the job note.
SUSPECT_SHARE = 0.5
SUSPECT_AGE_DAYS = 30


def trending_suspect(c: Candidate, now: datetime) -> bool:
    """A trending period count over SUSPECT_SHARE of all the repo's stars,
    on a repo older than SUSPECT_AGE_DAYS: real growth rarely looks like
    that; bought or farmed stars do."""
    if c.created_at is None or now - c.created_at <= timedelta(days=SUSPECT_AGE_DAYS) or c.stars <= 0:
        return False
    period = max(c.trending_weekly or 0, c.trending_daily or 0)
    return period > SUSPECT_SHARE * c.stars


def is_estimate(source: str | None) -> bool:
    return source is not None and source != "measured"


def stars_7d(c: Candidate, measured: int | None, now: datetime) -> Stars7d:
    """(stars gained over 7 days, where it came from). Measured from our own
    snapshots when we have the day-7 one; else an estimate, never presented
    as measured (LABELS)."""
    if measured is not None:
        return max(measured, 0), "measured"
    if c.trending_weekly is not None:
        return c.trending_weekly, "trending_weekly"
    if c.trending_daily is not None:
        return c.trending_daily, "trending_daily"
    if c.created_at is not None:
        age_d = (now - c.created_at).total_seconds() / 86400
        if age_d <= 7:
            return c.stars, "since_created"
        if age_d <= SEARCH_CREATED_DAYS:
            return int(round(c.stars * 7 / age_d)), "age_average"
    return None, None


def snake(topic: str) -> str:
    return "_".join(re.split(r"[^a-z0-9]+", topic.lower())).strip("_")[:40]


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML = re.compile(r"<[^>]+>")
_FENCE = re.compile(r"```.*?(```|$)", re.DOTALL)
_HEADING = re.compile(r"^\s*#+\s*", re.MULTILINE)
_NOISE = re.compile(r"[*_`>|]+")


def readme_text(raw: str | None) -> str:
    """The README head as plain words: no images, badges, HTML or code."""
    if not raw:
        return ""
    t = _FENCE.sub(" ", raw)
    t = _MD_IMAGE.sub(" ", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _HTML.sub(" ", t)
    t = _HEADING.sub("", t)
    t = _NOISE.sub(" ", t)
    return " ".join(t.split())


def teaser(c: Candidate) -> str:
    """Description, then the README head, at most TEASER_MAX characters:
    what the summarizer reads (content_items.teaser)."""
    parts = [p for p in (c.description, readme_text(c.readme)) if p]
    text = " — ".join(parts)
    return text if len(text) <= TEASER_MAX else text[:TEASER_MAX - 1].rsplit(" ", 1)[0] + "…"


def embed_text(c: Candidate) -> str:
    topics = ", ".join(t.replace("-", " ") for t in c.topics[:10])
    return " ".join(p for p in (f"{c.full_name}.", c.description or "", f"Topics: {topics}." if topics else "",
                                readme_text(c.readme)[:600]) if p)


# ------------------------------------------------------------------ selection

@dataclass(frozen=True)
class Pick:
    candidate: Candidate
    vec: Sequence[float]
    score: float
    stars_7d: int | None
    source: str | None


def velocity(stars7: int | None) -> float:
    """Star velocity's part of the rank: W_VELOCITY × log10(1 + stars_7d),
    less W_LOW_TRACTION below LOW_TRACTION stars in 7 days."""
    s7 = max(stars7 or 0, 0)
    return W_VELOCITY * math.log10(1 + s7) - (W_LOW_TRACTION if s7 < LOW_TRACTION else 0.0)


def rank_score(c: Candidate, vec: Sequence[float], stars7: int | None, profile: rank.Profile | None) -> float:
    """The tech profile's score for this repo (embedding vs loves and
    not-interesteds, learned topic and source weights), plus star velocity,
    plus W_INTEREST when it came from one of the owner's interest searches.
    No profile yet: the profile's part is 0."""
    base = 0.0
    if profile is not None and len(vec):
        st = rank.StoryIn(0, ("tech",), None, c.full_name, (), tuple(snake(t) for t in c.topics), SOURCE_SLUG,
                          tuple(vec))
        base = rank.score_story(st, "tech", profile, ()).score
    return base + velocity(stars7) + (W_INTEREST if c.interests else 0.0)


def select(scored: Sequence[Pick], may_surface: Callable[[Pick], bool], room: int) -> list[Pick]:
    """The best-ranked picks the resurface rule allows, at most `room`
    (DAILY_MAX less what today already surfaced)."""
    out: list[Pick] = []
    for p in sorted(scored, key=lambda p: (-p.score, p.candidate.full_name.lower())):
        if len(out) >= max(room, 0):
            break
        if may_surface(p):
            out.append(p)
    return out


# ------------------------------------------------------------------ the job

@dataclass
class RepoRun:
    trending: dict[str, int] = field(default_factory=dict)
    searched: int = 0
    candidates: int = 0
    filtered: dict[str, int] = field(default_factory=dict)
    snapshots: int = 0
    tracked: int = 0
    surfaced: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    #: Trending counts over half the total on a 30-day-old repo (trending_suspect).
    flagged: list[str] = field(default_factory=list)
    calls: dict[str, int] = field(default_factory=dict)

    @property
    def job_note(self) -> str:
        f = ", ".join(f"{k} {v}" for k, v in sorted(self.filtered.items())) or "none"
        return (f"surfaced {len(self.surfaced)}; {self.candidates} candidates "
                f"(trending {'/'.join(str(self.trending.get(p, 0)) for p in PERIODS)}, search {self.searched}); "
                f"filtered: {f}; {self.snapshots} snapshots ({self.tracked} tracked); "
                f"calls: search {self.calls.get('search', 0)}, core {self.calls.get('core', 0)}, "
                f"trending {self.calls.get('trending', 0)}"
                + (f"; flagged (trending > half the total): {', '.join(self.flagged)}" if self.flagged else "")
                + (f"; refused by the database: {', '.join(self.refused)}" if self.refused else ""))


Embed = Callable[[Sequence[str]], list[list[float]]]


def _gather(gh: GitHub, today: date, run: RepoRun) -> dict[str, Candidate]:
    by_name: dict[str, Candidate] = {}

    def get(name: str) -> Candidate:
        key = name.lower()
        if key not in by_name:
            by_name[key] = Candidate(name, f"https://github.com/{name}")
        return by_name[key]

    for period in PERIODS:
        repos = gh.trending(period)
        run.trending[period] = len(repos)
        for t in repos:
            c = get(t.full_name)
            c.description = c.description or t.description
            c.language = c.language or t.language
            c.stars = max(c.stars, t.stars)
            if period == "daily":
                c.trending_daily = t.stars_period
            else:
                c.trending_weekly = t.stars_period
    since = today - timedelta(days=SEARCH_CREATED_DAYS)
    for label, query in INTERESTS:
        items = gh.search(query, since)
        run.searched += len(items)
        for item in items:
            name = str(item.get("full_name") or "")
            if not _NAME.match(name):
                continue
            c = get(name)
            apply_rest(c, item)
            c.interests.append(label)
    return by_name


def repos_job(*, gh: GitHub | None = None, embed: Embed = embedder.embed, now: datetime | None = None,
              env: Mapping[str, str] = os.environ) -> RepoRun:
    """The tech_repos system job. "Today's run happened" is this job's own
    success today (job_health), so a run that failed partway is run again
    at the next boot or 07:00; snapshots already written that day are kept
    (one per repo per day, ON CONFLICT DO NOTHING)."""
    if ledger.job_ok_today(JOB_NAME):
        raise JobSkipped("today's repo run already happened")
    close: Callable[[], None] | None = None
    if gh is None:
        token = read_token(env)
        http, close = httpx_get()
        gh = GitHub(token, http)
        del token
    now = now or datetime.now(timezone.utc)
    run = RepoRun()
    try:
        gh.check_token()
        today = ledger.owner_today()
        cands = _gather(gh, today, run)
        run.candidates = len(cands)

        # Enrich trending-only repos, then filter on metadata, then README.
        keep: list[Candidate] = []
        for c in cands.values():
            if not c.enriched:
                data = gh.repo(c.full_name)
                if data is None:
                    run.filtered["gone"] = run.filtered.get("gone", 0) + 1
                    continue
                apply_rest(c, data)
            why = hard_filter(c, now, readme_checked=False)
            if why is None:
                c.readme = gh.readme_head(c.full_name)
                why = hard_filter(c, now)
            if why is not None:
                run.filtered[why] = run.filtered.get(why, 0) + 1
            else:
                keep.append(c)
                if trending_suspect(c, now):
                    run.flagged.append(c.full_name)
                    log.warning("repos: %s flagged: trending %s stars in period on %d total, created %s",
                                c.full_name, max(c.trending_weekly or 0, c.trending_daily or 0), c.stars,
                                c.created_at.date() if c.created_at else "?")

        # Snapshot every enriched candidate (filtered ones too: tracked is tracked).
        enriched = [c for c in cands.values() if c.enriched]
        ids = ledger.repos_upsert([repo_row(c, now) for c in enriched])
        run.snapshots = ledger.repo_snapshot([(ids[c.full_name.lower()], c.stars) for c in enriched])
        # Repos tracked lately that were not candidates today: re-read, snapshot.
        stale = ledger.repos_to_track(TRACK_DAYS, TRACK_MAX, exclude=list(ids.values()))
        tracked: list[Candidate] = []
        for _rid, name in stale:
            data = gh.repo(name)
            if data is not None:
                t = Candidate(name, f"https://github.com/{name}")
                apply_rest(t, data)
                # Keep the stored name: a renamed repo is still the same row.
                t.full_name = name
                tracked.append(t)
        if tracked:
            tids = ledger.repos_upsert([repo_row(t, now) for t in tracked])
            run.tracked = ledger.repo_snapshot([(tids[t.full_name.lower()], t.stars) for t in tracked])
            run.snapshots += run.tracked
        del tracked

        room = DAILY_MAX - ledger.repos_surfaced_today()
        if room > 0 and keep:
            measured = ledger.repo_stars_7d_measured([ids[c.full_name.lower()] for c in keep])
            vecs = embed([embed_text(c) for c in keep])
            from feed.summarize import latest_profiles
            profile = latest_profiles().get("tech")
            scored = []
            for c, v in zip(keep, vecs):
                s7, src = stars_7d(c, measured.get(ids[c.full_name.lower()]), now)
                scored.append(Pick(c, v, rank_score(c, v, s7, profile), s7, src))
            picks = select(scored, lambda p: ledger.repo_may_surface(ids[p.candidate.full_name.lower()],
                                                                   p.stars_7d), room)
            source = ledger.feed_source(SOURCE_SLUG)
            for p in picks:
                rid = ids[p.candidate.full_name.lower()]
                made = ledger.repo_surface(source, rid, surface_row(p, today))
                if made is None:
                    run.refused.append(p.candidate.full_name)
                    log.warning("repos: the database refused %s (the cap or the resurface rule)",
                                p.candidate.full_name)
                else:
                    run.surfaced.append(p.candidate.full_name)
                    log.info("repos: surfaced %s as story %d (score %.2f, stars_7d %s %s)", p.candidate.full_name,
                             made, p.score, p.stars_7d, p.source)
            del scored, vecs
        elif room <= 0:
            log.info("repos: today's %d are already surfaced", DAILY_MAX)
    finally:
        run.calls = dict(gh.calls)
        if close is not None:
            close()
        memory.release()
    log.info("repos: %s", run.job_note)
    return run


def repo_row(c: Candidate, now: datetime) -> ledger.RepoIn:
    return ledger.RepoIn(full_name=c.full_name, html_url=c.html_url, description=(c.description or None),
                         language=c.language, topics=c.topics, license=c.license, archived=c.archived,
                         fork=c.fork, gh_created_at=c.created_at, pushed_at=c.pushed_at, stars=c.stars)


def surface_row(p: Pick, today: date) -> ledger.RepoSurfaceIn:
    c = p.candidate
    return ledger.RepoSurfaceIn(
        full_name=c.full_name, html_url=c.html_url, teaser=teaser(c) or None,
        topics=tuple(dict.fromkeys(t for t in (snake(x) for x in c.topics) if t))[:10],
        vec=tuple(p.vec), day=today, stars=c.stars, stars_7d=p.stars_7d, stars_7d_source=p.source)
