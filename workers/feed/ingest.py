"""feed_ingest: every 30 minutes and at boot (core/system_jobs.py).

For each active source, lowest priority number first, completely before the next:

  1. Conditional GET (ETag / If-Modified-Since), 15 s timeout, our
     User-Agent. 304 is a good fetch with nothing new.
  2. Parse (feed/parse.py), at most 50 items; keep include_categories.
  3. EVERY fetch (owner, 2026-10-14; it was the first fetch only): a dated
     item already older than 72 h (date-only items: 96 h) when first seen is
     SEEDED, recorded as seen and never shown, so no later fetch can store it
     as new. First fetch (last_ok_at NULL): undated items are seeded too,
     because "first seen" would date a back catalogue to now.
     Later fetches: an undated item's published_at is its first-seen time.
     Every published_at is capped at the fetch time (ESPN dates ahead).
     Google News items published more than gnews_window_days + STALE_GRACE
     before the fetch are dropped on every fetch, before decoding (owner,
     2026-10-11): Google News can lead with old pages, e.g. an Axios Miami
     author page dated 2024.
  4. Exact dedupe: an external id this source already has is skipped before
     anything else (so Google News links are decoded only once); then a
     canonical URL that exists anywhere is skipped.
  5. Google News items: decoded to the publisher URL, at most 2 decodes a
     second. A failed decode keeps google_url, sets decode_failed, url NULL
     (the item can never lead; db/032).
  6. Embed the title alone (clustering) and title + teaser (kept for Phase 2)
     (core/embed.py), then place each item: join a story or found one
     (feed/cluster.py: best member match, 48 h from first seen, 25 items at
     most). A new story gets the live drop rules (title_regex, url_regex).
     Every join, near miss and cap overflow is logged (feed_similarity_log),
     and each story's best seed-phrase score is recorded, never acted on
     (feed/rules.py).
  7. Only then is the fetch recorded as ok (with the new ETag), so a crash
     between fetch and store cannot hide items behind a 304.

Every fetch logs one INFO line: "feed <slug> fetch: N items, newest: a | b |
c", the newest 3 item GUIDs cut to 40 characters (owner, 2026-10-14), or
"not modified". No table: Railway's logs are the record, so an item that
looks late can be checked against what earlier fetches held.

One source failing never fails the run: its failure is recorded on its row
(redacted), and after FAILURES_TO_ALERT in a row ONE push goes out, linking
/settings/sources, re-armed by its next success. The run itself fails only
on what is not one source's fault: the embed function or the database. That
is recorded in job_health and pushed by the health monitor.

Tech Hub (Chat 4 Phase 1, db/045):
  * kinds: "page" (feed/watch.py parses the listing container, reading at
    most the source's config.max_bytes of a streamed response) and "hn"
    (Hacker News via Algolia; the points threshold is checked on every hit).
    A tech RSS feed is streamed and cut after its MAX_ITEMS-th entry
    (parse.head_items): Vercel's Atom is 3.7 MB, OpenAI's RSS 785 KB.
  * a tech item joins only a tech story and any other item never one
    (cluster candidates are split by world), so no story is ever in two.
  * a tech source's first fetch keeps its BACKFILL newest items whatever
    their age (so every source shows something), all marked backfill: first
    seen at deploy, never counted in the latency report.
  * a page item's link keeps its fragment (a release-notes entry is
    page#heading), and an item states its published precision ('date' for a
    listing or a midnight-stamped feed).
"""

from __future__ import annotations

import logging
import time
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from core import embed as embedder
from core import ledger, memory, push
from core.jobs import describe
from feed import candidates as candidates_mod
from feed import cluster, watch
from feed.parse import MAX_ITEMS, RawItem, ParseFailed, canonical_url, head_items, keeps_category, parse_feed
from feed.rules import Rule, Rules, SeedPhrase, best_seed
from feed.sources import load_sources
from screening import trailers

log = logging.getLogger("valemont.feed")

USER_AGENT = "ValemontCommand/1.0 (+https://valemont-command.vercel.app/privacy)"
TIMEOUT_S = 15.0
#: Older than this when first seen: recorded as seen, never shown (every
#: fetch; owner, 2026-10-14). The name is historical: it was first-fetch only.
FIRST_FETCH_MAX_AGE = timedelta(hours=72)
DATE_GRACE = timedelta(hours=24)
#: A tech source's first fetch keeps this many newest items whatever their age.
BACKFILL = 5
#: The most a streamed tech feed is read before it is cut, in bytes.
TECH_FEED_MAX_BYTES = 4_000_000
CHUNK = 64 * 1024
#: Days past a Google News source's query window before an item is stale.
STALE_GRACE = timedelta(days=2)
DECODES_PER_SECOND = 2
FAILURES_TO_ALERT = 6
SOURCES_LINK = "/settings/sources"


@dataclass(frozen=True)
class Response:
    status: int
    content: bytes
    etag: str | None
    last_modified: str | None


#: (url, headers) -> Response, or with keyword `max_bytes` / `head` for a
#: streamed read (tech sources). Injected by tests; production uses httpx.
Get = Callable[..., Response]
#: google news url -> publisher url, or None when it cannot be decoded.
Decode = Callable[[str], str | None]
Embed = Callable[[Sequence[str]], list[list[float]]]


class Fetcher:
    """One httpx client for a whole run (46 sources), closed at the end: a
    module-level httpx.get() built a new client, and a new TLS context, for
    every source (Chat 3 Phase 1 hotfix)."""

    def __init__(self) -> None:
        import httpx

        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S,
                                   follow_redirects=True)

    def __call__(self, url: str, headers: dict, *, max_bytes: int | None = None,
                 head: int | None = None) -> Response:
        """A whole GET; or, with `max_bytes`, a streamed one that stops reading
        at that many bytes, and with `head`, stops once the feed holds `head`
        entries (cut and closed by parse.head_items). The stream is closed
        before returning, so the rest of a large response is never held."""
        if max_bytes is None and head is None:
            r = self.client.get(url, headers=headers)
            return Response(r.status_code, r.content, r.headers.get("etag"), r.headers.get("last-modified"))
        limit = max_bytes or TECH_FEED_MAX_BYTES
        with self.client.stream("GET", url, headers=headers) as r:
            buf = bytearray()
            if r.status_code == 200:
                for chunk in r.iter_bytes(CHUNK):
                    buf += chunk
                    if head is not None:
                        cut = head_items(bytes(buf), head)
                        if cut is not None:
                            buf = bytearray(cut)
                            break
                    if len(buf) >= limit:
                        del buf[limit:]
                        break
            return Response(r.status_code, bytes(buf), r.headers.get("etag"), r.headers.get("last-modified"))

    def close(self) -> None:
        self.client.close()


def _gnews_decode(url: str) -> str | None:
    from googlenewsdecoder import gnewsdecoder

    try:
        out = gnewsdecoder(url)
    except Exception as exc:
        log.warning("feed: decode raised %s", type(exc).__name__)
        return None
    return out.get("decoded_url") if out.get("success") else None


class Throttle:
    """At most `per_second` calls a second, by sleeping between them."""

    def __init__(self, per_second: float, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.gap = 1.0 / per_second
        self.sleep, self.clock = sleep, clock
        self.last: float | None = None

    def wait(self) -> None:
        if self.last is not None:
            remaining = self.gap - (self.clock() - self.last)
            if remaining > 0:
                self.sleep(remaining)
        self.last = self.clock()


@dataclass
class SourceStats:
    slug: str
    status: str = "ok"
    fetched: int = 0
    new_stories: int = 0
    joined: int = 0
    filtered: int = 0
    seeded: int = 0
    known: int = 0
    decode_failed: int = 0
    overflow: int = 0
    #: Google News items older than the source's window + STALE_GRACE.
    stale: int = 0
    #: Items already older than 72 h when first seen: recorded, never shown.
    aged: int = 0
    #: Screening: feed entries that are not trailers (Shorts, clips...), dropped.
    not_trailers: int = 0
    error: str | None = None


@dataclass
class RunStats:
    sources: list[SourceStats] = field(default_factory=list)
    #: Every YouTube feed failed this run: one degraded state (settle_youtube).
    youtube_outage: bool = False

    def total(self, attr: str) -> int:
        return sum(getattr(s, attr) for s in self.sources)

    def line(self) -> str:
        failed = [s.slug for s in self.sources if s.status == "error"]
        if self.youtube_outage:
            return ("YouTube OUTAGE (all its feeds failed; counted once); "
                    + self._line([s for s in failed if not s.startswith("yt-")]))
        return self._line(failed)

    def _line(self, failed: list[str]) -> str:
        return (f"{len(self.sources)} sources, {self.total('new_stories')} new stories, "
                f"{self.total('joined')} joins, {self.total('filtered')} filtered, "
                f"{self.total('seeded')} seeded, {self.total('decode_failed')} decode failures, "
                f"{self.total('overflow')} cap overflows, {self.total('stale')} stale, "
                f"{self.total('aged')} too old to show"
                + (f"; failing: {', '.join(failed)}" if failed else ""))


#: The cluster candidate (feed/candidates.py; the name the tests and the run
#: have always used).
_Candidate = candidates_mod.Candidate


class Ingest:
    """One run. Dependencies are injected so tests drive it with no network."""

    def __init__(self, *, get: Get, decode: Decode = _gnews_decode,
                 embed: Embed = embedder.embed, send: Callable[[push.Message], Any] = push.send,
                 throttle: Throttle | None = None, now: Callable[[], datetime] | None = None,
                 cache: candidates_mod.CandidateCache | None = None) -> None:
        self.get, self.decode, self.embed, self.send = get, decode, embed, send
        #: The process's candidate cache (ingest_job); None: a full load per
        #: run, as before the cache (the tests' default).
        self.cache = cache
        self.throttle = throttle or Throttle(DECODES_PER_SECOND)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.rules: Rules | None = None
        self.seeds: list[SeedPhrase] = []
        self.candidates: list[_Candidate] = []
        #: YouTube failures of this run, recorded only once outage() has decided.
        self.deferred: list[tuple[ledger.FeedSource, SourceStats]] = []

    # -- YouTube as one source of trouble -------------------------------------

    def settle_youtube(self, stats: Sequence[SourceStats], youtube_slugs: Sequence[str]) -> bool:
        """After every source has run: when EVERY YouTube source failed this
        run (and there are at least YT_MIN_SOURCES), it is a YouTube outage
        (its feed server 404s them all for hours): ONE degraded state, no
        per-source failure counted, no per-source alert (owner, 2026-10-14).
        Otherwise each failed YouTube source is recorded like any other.
        Returns whether this run was an outage."""
        yt = [s for s in stats if s.slug in set(youtube_slugs)]
        outage = len(yt) >= YT_MIN_SOURCES and all(s.status == "error" for s in yt)
        if not outage:
            for src, st in self.deferred:
                self._record_fail(src, st)
        self.deferred = []
        YOUTUBE.observe(outage, len(yt), self.send)
        return outage

    # -- setup ---------------------------------------------------------------

    def prepare(self, seed_cache: dict[tuple[int, str], tuple[float, ...]]) -> None:
        rules = [Rule(*r) for r in ledger.feed_rules()]
        self.rules = Rules(rules)
        missing = [r for r in self.rules.seed_rules if (r.id, r.pattern) not in seed_cache]
        if missing:
            for r, v in zip(missing, self.embed([r.pattern for r in missing])):
                seed_cache[(r.id, r.pattern)] = tuple(v)
        self.seeds = [SeedPhrase(r.id, r.pattern, seed_cache[(r.id, r.pattern)])
                      for r in self.rules.seed_rules]
        if self.cache is not None:
            log.info("feed %s", self.cache.refresh().line())
            self.candidates = self.cache.candidates()
            return
        self.candidates = [_Candidate(c.story_id, c.item_count, [cluster.Member(t, v) for t, v in c.members],
                                      c.world)
                           for c in ledger.feed_candidates(cluster.CANDIDATE_HOURS)]

    # -- one source ----------------------------------------------------------

    def _fail(self, src: ledger.FeedSource, stats: SourceStats, exc: BaseException) -> None:
        stats.status, stats.error = "error", describe(exc)
        if is_youtube(src):
            # Held until every YouTube source of the run is known (outage()).
            self.deferred.append((src, stats))
            return
        self._record_fail(src, stats)

    def _record_fail(self, src: ledger.FeedSource, stats: SourceStats) -> None:
        assert stats.error is not None
        failures, alerted = ledger.feed_source_failed(src.id, stats.error)
        log.warning("feed %s failed (%d in a row): %s", src.slug, failures, stats.error)
        if failures >= FAILURES_TO_ALERT and not alerted:
            try:
                self.send(push.Message(
                    kind="feed_source_failing", title=f"{src.name} feed is failing",
                    body=f"{failures} fetches in a row failed. Last: {stats.error}"[:240],
                    deep_link=SOURCES_LINK, tag=f"feed-{src.slug}"))
            except push.PushError as push_exc:
                log.error("feed %s: alert not sent: %s", src.slug, push_exc)
                return
            ledger.feed_source_alerted(src.id)

    def run_source(self, src: ledger.FeedSource) -> SourceStats:
        stats = SourceStats(src.slug)
        fetched_at = self.now()
        headers = {}
        if src.etag:
            headers["If-None-Match"] = src.etag
        if src.last_modified:
            headers["If-Modified-Since"] = src.last_modified
        try:
            resp = self._fetch(src, headers)
            if resp.status == 304:
                ledger.feed_source_ok(src.id, status="not_modified", etag=src.etag,
                                      last_modified=src.last_modified)
                stats.status = "not_modified"
                log.info("feed %s fetch: not modified", src.slug)
                return stats
            if resp.status != 200:
                raise FetchFailed(f"HTTP {resp.status}")
            items = [i for i in self._parse(src, resp.content, fetched_at)
                     if keeps_category(i, src.include_categories)]
            if "screening" in src.pillars:
                # Trailers and teasers only (owner, 2026-10-14): no Shorts,
                # clips, featurettes or reactions (screening/trailers.py).
                kept = [i for i in items if trailers.is_trailer(i.title, i.link)]
                stats.not_trailers = len(items) - len(kept)
                items = kept
            # The body is done with: only the validators are kept (memory).
            resp = Response(resp.status, b"", resp.etag, resp.last_modified)
        except (FetchFailed, ParseFailed, watch.ConfigInvalid) as exc:
            self._fail(src, stats, exc)
            return stats
        except Exception as exc:  # network, timeout, TLS: the source's problem
            self._fail(src, stats, exc)
            return stats
        stats.fetched = len(items)
        # Owner, 2026-10-14 (Phase 1 watch item 1): what this fetch held, so a
        # later "late" item can be checked against earlier fetches' logs.
        log.info("feed %s fetch: %d items, newest: %s", src.slug, len(items), newest_guids(items))
        try:
            self._store(src, items, fetched_at, stats)
        except embedder.EmbedError:
            raise  # not this source's fault: fail the run, loudly
        except Exception as exc:
            # A row the database refused. Recorded on the source; the run goes on.
            log.exception("feed %s: storing failed", src.slug)
            self._fail(src, stats, exc)
            return stats
        ledger.feed_source_ok(src.id, status="ok", etag=resp.etag, last_modified=resp.last_modified)
        return stats

    def _fetch(self, src: ledger.FeedSource, headers: dict) -> Response:
        """A tech page or feed is read streamed and capped; everything else as
        before (one whole GET)."""
        if src.kind == "page":
            return self.get(src.url, headers, max_bytes=watch.page_config(src.config or {}).max_bytes)
        if "tech" in src.pillars and src.kind == "rss":
            return self.get(src.url, headers, max_bytes=TECH_FEED_MAX_BYTES, head=MAX_ITEMS)
        return self.get(src.url, headers)

    @staticmethod
    def too_old(raw: RawItem, fetched_at: datetime) -> bool:
        """Published more than FIRST_FETCH_MAX_AGE (72 h) before this fetch. A
        date-only item (stamped 00:00 UTC of its day) gets one more day: it may
        have gone up at the end of that day."""
        if raw.published_at is None:
            return False
        limit = FIRST_FETCH_MAX_AGE + (DATE_GRACE if raw.precision == "date" else timedelta(0))
        return fetched_at - raw.published_at > limit

    @staticmethod
    def _parse(src: ledger.FeedSource, content: bytes, fetched_at: datetime) -> list[RawItem]:
        if src.kind == "page":
            return watch.parse_page(content, src.url, watch.page_config(src.config or {}), fetched_at.date())
        if src.kind == "hn":
            return watch.parse_hn(content, watch.hn_config(src.config or {}))
        return parse_feed(content, gnews=src.kind == "gnews")

    def _store(self, src: ledger.FeedSource, items: Sequence[RawItem], fetched_at: datetime,
               stats: SourceStats) -> None:
        first = src.last_ok_at is None
        known_ext, _ = ledger.feed_known(src.id, [i.external_id for i in items], [])
        fresh = [i for i in items if i.external_id not in known_ext]
        stats.known = len(items) - len(fresh)
        # A tech or screening source's first fetch: its BACKFILL newest dated
        # items are kept whatever their age, every first-fetch item marked
        # backfill.
        tech_first = first and world_of(src.pillars) in BACKFILL_WORLDS
        newest = {i.external_id for i in sorted((i for i in fresh if i.published_at is not None),
                                                key=lambda i: i.published_at, reverse=True)[:BACKFILL]
                  } if tech_first else set()

        stale_after = timedelta(days=src.gnews_window_days) + STALE_GRACE
        seeded: list[ledger.FeedItemIn] = []
        keep: list[ledger.FeedItemIn] = []
        for raw in fresh:
            if (src.kind == "gnews" and raw.published_at is not None
                    and fetched_at - raw.published_at > stale_after):
                stats.stale += 1
                continue
            # Too old on EVERY fetch, not only the first (owner, 2026-10-14):
            # recorded as seen and never shown, so a later fetch knows it.
            # Before, the first fetch dropped it unrecorded and the next one
            # stored it as new (341 Home/Sports and 326 tech items).
            aged = raw.published_at is not None and self.too_old(raw, fetched_at) and raw.external_id not in newest
            try:
                item = self._item(src, raw, fetched_at, backfill=tech_first)
            except _NoLink:
                continue
            if aged:
                seeded.append(item)
                stats.aged += 1
            elif first and raw.published_at is None:
                seeded.append(item)
            else:
                keep.append(item)

        if seeded:
            stats.seeded = ledger.feed_insert_seeded(src, seeded)
        # Level 1 on the canonical URL, across every source and within this fetch.
        _, known_urls = ledger.feed_known(src.id, [], [i.url for i in keep if i.url])
        seen: set[str] = set()
        unique = []
        for it in keep:
            if it.url and (it.url in known_urls or it.url in seen):
                stats.known += 1
                continue
            if it.url:
                seen.add(it.url)
            unique.append(it)
        if not unique:
            return
        # Title-only vectors decide clustering; title + teaser vectors are kept
        # for Phase 2. With no teaser the two texts are the same: one call.
        title_vecs = self.embed([it.title for it in unique])
        with_teaser = [i for i, it in enumerate(unique) if it.teaser]
        text_vecs = list(title_vecs)
        if with_teaser:
            for i, v in zip(with_teaser, self.embed([f"{unique[i].title}. {unique[i].teaser}" for i in with_teaser])):
                text_vecs[i] = v
        for it, vec, tvec in zip(unique, text_vecs, title_vecs):
            stats.decode_failed += int(it.decode_failed)
            self._place(src, it, vec, tvec, stats)

    def _item(self, src: ledger.FeedSource, raw: RawItem, fetched_at: datetime, *,
              backfill: bool = False) -> ledger.FeedItemIn:
        published = min(raw.published_at, fetched_at) if raw.published_at else fetched_at
        # An undated item is dated by when it was first seen, and says so
        # (db/048): 'none' never pushes and is never a latency sample.
        precision = raw.precision if raw.published_at else "none"
        if src.kind == "gnews":
            self.throttle.wait()
            decoded = self.decode(raw.link) if raw.link else None
            url = canonical_url(decoded)
            return ledger.FeedItemIn(raw.external_id, url, raw.link, raw.title, raw.teaser,
                                     published, url is None, precision, backfill)
        # A release-notes entry is page#heading: a page item keeps its fragment.
        url = canonical_url(raw.link, keep_fragment=src.kind == "page")
        if url is None:
            # An RSS item without a usable link has nothing to open, and db/032
            # allows a NULL url only for a failed Google News decode: skipped.
            raise _NoLink()
        return ledger.FeedItemIn(raw.external_id, url, None, raw.title, raw.teaser, published, False,
                                 precision, backfill)

    def _place(self, src: ledger.FeedSource, it: ledger.FeedItemIn, vec: Sequence[float],
               title_vec: Sequence[float], stats: SourceStats) -> None:
        # Same world only: a tech or screening item never joins another
        # world's story, nor the reverse, so a join can never make a story
        # cross the line.
        world = world_of(src.pillars)
        decision = cluster.assign(title_vec, it.title,
                                  [c for c in self.candidates if c.world == world])  # type: ignore[arg-type]
        seed = best_seed(vec, self.seeds)
        logged = [ledger.SimilarityRow(s.story_id, "near_miss", s.cosine, s.fuzz) for s in decision.near_misses]
        for s in decision.near_misses:
            log.info("feed near miss %.3f (fuzz %.0f): %r vs story %d", s.cosine, s.fuzz, it.title, s.story_id)
        member = cluster.Member(it.title, array("f", title_vec))
        if decision.story_id is not None:
            best = decision.best
            logged = [ledger.SimilarityRow(best.story_id, "join", best.cosine, best.fuzz, decision.joined_by)] + logged
            item_id = ledger.feed_join_story(src, it, vec, title_vec, decision.story_id, seed=seed, logged=logged)
            if item_id is None:
                stats.known += 1
                return
            log.info("feed join %.3f (fuzz %.0f, by %s): %r -> story %d", best.cosine, best.fuzz,
                     decision.joined_by, it.title, decision.story_id)
            stats.joined += 1
            if self.cache is not None:
                # The run's list shares the cache's objects: one update.
                self.cache.joined(decision.story_id, item_id, member)
                return
            for c in self.candidates:
                if c.story_id == decision.story_id:
                    c.members.append(member)
                    c.count += 1
            return
        if decision.overflow is not None:
            o = decision.overflow
            logged.append(ledger.SimilarityRow(o.story_id, "cap_overflow", o.cosine, o.fuzz))
            log.warning("feed: story %d is full (%d items); %r starts a new story", o.story_id,
                        cluster.MAX_ITEMS, it.title)
            stats.overflow += 1
        assert self.rules is not None
        reason = self.rules.filter_reason(title=it.title, url=it.url, pillars=src.pillars)
        made = ledger.feed_new_story(src, it, vec, title_vec, filtered_reason=reason, seed=seed,
                                     near_misses=logged)
        if made is None:
            stats.known += 1
            return
        stats.new_stories += 1
        stats.filtered += int(reason is not None)
        if self.cache is not None:
            self.candidates.insert(0, self.cache.founded(made[0], made[1], member, world))
        else:
            self.candidates.insert(0, _Candidate(made[0], 1, [member], world))


#: How many GUIDs a fetch's log line carries, and how much of each.
LOG_GUIDS = 3
LOG_GUID_CHARS = 40


def newest_guids(items: Sequence[RawItem], n: int = LOG_GUIDS, width: int = LOG_GUID_CHARS) -> str:
    """The fetch's newest `n` item GUIDs (external ids), newest published
    first (undated ones last, in feed order), each cut to `width`
    characters: "a | b | c", or "none"."""
    order = sorted(range(len(items)), key=lambda i: (items[i].published_at is None,
                                                     -(items[i].published_at.timestamp())
                                                     if items[i].published_at else 0.0, i))
    ids = [items[i].external_id[:width] for i in order[:n]]
    return " | ".join(ids) if ids else "none"


def world_of(pillars: Sequence[str]) -> str:
    """'tech', 'screening' or 'main' (Home and Sports): the exclusive pillars
    (db/045, db/051) are worlds of their own."""
    if "tech" in pillars:
        return "tech"
    if "screening" in pillars:
        return "screening"
    return "main"


YOUTUBE_FEEDS = "https://www.youtube.com/feeds/videos.xml"
#: An outage needs at least this many YouTube sources to all fail at once.
YT_MIN_SOURCES = 3
#: Consecutive outage runs before ONE push (6 runs of 30 min = 3 hours).
YT_ALERT_RUNS = 6
YT_OUTAGE_KIND = "feed_youtube_outage"
YT_REALERT_HOURS = 24


def is_youtube(src: ledger.FeedSource) -> bool:
    return src.url.startswith(YOUTUBE_FEEDS)


class YoutubeOutage:
    """The whole-YouTube degraded state, across runs, in this process. One
    push when it has lasted YT_ALERT_RUNS runs; re-armed when a run sees
    YouTube answer again. After a restart, a push sent in the last
    YT_REALERT_HOURS is adopted, not repeated."""

    def __init__(self) -> None:
        self.runs = 0
        self.alerted: bool | None = None

    def observe(self, outage: bool, sources: int, send: Callable[[push.Message], Any]) -> None:
        if not outage:
            if self.runs:
                log.info("feed: YouTube answers again after %d outage run(s)", self.runs)
            self.runs, self.alerted = 0, False
            return
        self.runs += 1
        log.warning("feed: YouTube outage: all %d YouTube feeds failed this run (%d run(s) in a row); "
                    "no per-source failures counted", sources, self.runs)
        if self.alerted is None:
            self.alerted = ledger.notification_sent_since(YT_OUTAGE_KIND, YT_REALERT_HOURS)
        if self.runs >= YT_ALERT_RUNS and not self.alerted:
            try:
                send(push.Message(kind=YT_OUTAGE_KIND, title="YouTube feeds are down",
                                  body=f"All {sources} trailer feeds have failed for {self.runs} runs in a row. "
                                       "Trailers resume when YouTube answers again.",
                                  deep_link=SOURCES_LINK, tag="feed-youtube"))
                self.alerted = True
            except push.PushError as exc:
                log.error("feed: YouTube outage alert not sent: %s", exc)


YOUTUBE = YoutubeOutage()


#: First fetch keeps BACKFILL newest dated items whatever their age (db/045):
#: Tech's rule, and Screening's (owner, 2026-10-14: "the Phase 1 rules apply").
BACKFILL_WORLDS = frozenset({"tech", "screening"})


class FetchFailed(RuntimeError):
    """A non-200, non-304 answer."""


class _NoLink(Exception):
    pass


# ------------------------------------------------------------------ the job

_SEED_CACHE: dict[tuple[int, str], tuple[float, ...]] = {}
_SYNCED = False
#: The process's cluster candidates (egress fix 1): full at boot and daily,
#: incremental between (feed/candidates.py).
CANDIDATES = candidates_mod.CandidateCache()


def sync_sources() -> tuple[int, int]:
    upserted, deactivated = ledger.feed_sync_sources(load_sources())
    log.info("feed: synced %d source(s) from sources.toml, %d deactivated", upserted, deactivated)
    return upserted, deactivated


def ingest_job(*, ingest: Ingest | None = None) -> RunStats:
    """The feed_ingest system job. Syncs sources.toml on its first run in a
    process (boot), then runs every active source."""
    global _SYNCED
    if not _SYNCED:
        sync_sources()
        _SYNCED = True
    fetcher = None if ingest else Fetcher()
    run = ingest or Ingest(get=fetcher, cache=CANDIDATES)  # type: ignore[arg-type]
    ok = False
    try:
        run.prepare(_SEED_CACHE)
        stats = RunStats()
        sources = ledger.feed_active_sources()
        for src in sources:
            stats.sources.append(run.run_source(src))
        stats.youtube_outage = run.settle_youtube(stats.sources, [s.slug for s in sources if is_youtube(s)])
        log.info("feed ingest: %s", stats.line())
        ok = True
        return stats
    finally:
        # A run that failed part-way may have written what the cache never
        # saw: the next run loads in full.
        if not ok and run.cache is not None:
            run.cache.invalidate()
        # The run's list (the cache keeps the candidates themselves), then
        # hand the freed memory back (core/memory.py).
        run.candidates = []
        if fetcher is not None:
            fetcher.close()
        memory.release()
