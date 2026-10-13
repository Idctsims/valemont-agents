"""feed_ingest: every 30 minutes and at boot (core/system_jobs.py).

For each active source, lowest priority number first, completely before the next:

  1. Conditional GET (ETag / If-Modified-Since), 15 s timeout, our
     User-Agent. 304 is a good fetch with nothing new.
  2. Parse (feed/parse.py), at most 50 items; keep include_categories.
  3. First fetch (last_ok_at NULL): dated items older than 72 h are ignored
     (no backfill flood); undated items are SEEDED, recorded as seen and
     never shown, because "first seen" would date a back catalogue to now.
     Later fetches: an undated item's published_at is its first-seen time.
     Every published_at is capped at the fetch time (ESPN dates ahead).
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

One source failing never fails the run: its failure is recorded on its row
(redacted), and after FAILURES_TO_ALERT in a row ONE push goes out, linking
/settings/sources, re-armed by its next success. The run itself fails only
on what is not one source's fault: the embed function or the database. That
is recorded in job_health and pushed by the health monitor.
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
from feed import cluster
from feed.parse import RawItem, ParseFailed, canonical_url, keeps_category, parse_feed
from feed.rules import Rule, Rules, SeedPhrase, best_seed
from feed.sources import load_sources

log = logging.getLogger("valemont.feed")

USER_AGENT = "ValemontCommand/1.0 (+https://valemont-command.vercel.app/privacy)"
TIMEOUT_S = 15.0
FIRST_FETCH_MAX_AGE = timedelta(hours=72)
DECODES_PER_SECOND = 2
FAILURES_TO_ALERT = 6
SOURCES_LINK = "/settings/sources"


@dataclass(frozen=True)
class Response:
    status: int
    content: bytes
    etag: str | None
    last_modified: str | None


#: (url, headers) -> Response. Injected by tests; production uses httpx.
Get = Callable[[str, dict], Response]
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

    def __call__(self, url: str, headers: dict) -> Response:
        r = self.client.get(url, headers=headers)
        return Response(r.status_code, r.content, r.headers.get("etag"), r.headers.get("last-modified"))

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
    error: str | None = None


@dataclass
class RunStats:
    sources: list[SourceStats] = field(default_factory=list)

    def total(self, attr: str) -> int:
        return sum(getattr(s, attr) for s in self.sources)

    def line(self) -> str:
        failed = [s.slug for s in self.sources if s.status == "error"]
        return (f"{len(self.sources)} sources, {self.total('new_stories')} new stories, "
                f"{self.total('joined')} joins, {self.total('filtered')} filtered, "
                f"{self.total('seeded')} seeded, {self.total('decode_failed')} decode failures, "
                f"{self.total('overflow')} cap overflows"
                + (f"; failing: {', '.join(failed)}" if failed else ""))


@dataclass
class _Candidate:
    story_id: int
    count: int
    members: list[cluster.Member]

    @property
    def item_count(self) -> int:
        """cluster.assign reads candidates by these three names, so the run's
        own list is passed as is (no per-item copy; Chat 3 Phase 1 hotfix)."""
        return self.count


class Ingest:
    """One run. Dependencies are injected so tests drive it with no network."""

    def __init__(self, *, get: Get, decode: Decode = _gnews_decode,
                 embed: Embed = embedder.embed, send: Callable[[push.Message], Any] = push.send,
                 throttle: Throttle | None = None, now: Callable[[], datetime] | None = None) -> None:
        self.get, self.decode, self.embed, self.send = get, decode, embed, send
        self.throttle = throttle or Throttle(DECODES_PER_SECOND)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.rules: Rules | None = None
        self.seeds: list[SeedPhrase] = []
        self.candidates: list[_Candidate] = []

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
        self.candidates = [_Candidate(c.story_id, c.item_count, [cluster.Member(t, v) for t, v in c.members])
                           for c in ledger.feed_candidates(cluster.CANDIDATE_HOURS)]

    # -- one source ----------------------------------------------------------

    def _fail(self, src: ledger.FeedSource, stats: SourceStats, exc: BaseException) -> None:
        stats.status, stats.error = "error", describe(exc)
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
            resp = self.get(src.url, headers)
            if resp.status == 304:
                ledger.feed_source_ok(src.id, status="not_modified", etag=src.etag,
                                      last_modified=src.last_modified)
                stats.status = "not_modified"
                return stats
            if resp.status != 200:
                raise FetchFailed(f"HTTP {resp.status}")
            items = [i for i in parse_feed(resp.content, gnews=src.kind == "gnews")
                     if keeps_category(i, src.include_categories)]
        except (FetchFailed, ParseFailed) as exc:
            self._fail(src, stats, exc)
            return stats
        except Exception as exc:  # network, timeout, TLS: the source's problem
            self._fail(src, stats, exc)
            return stats
        stats.fetched = len(items)
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

    def _store(self, src: ledger.FeedSource, items: Sequence[RawItem], fetched_at: datetime,
               stats: SourceStats) -> None:
        first = src.last_ok_at is None
        known_ext, _ = ledger.feed_known(src.id, [i.external_id for i in items], [])
        fresh = [i for i in items if i.external_id not in known_ext]
        stats.known = len(items) - len(fresh)

        seeded: list[ledger.FeedItemIn] = []
        keep: list[ledger.FeedItemIn] = []
        for raw in fresh:
            if first and raw.published_at is not None and fetched_at - raw.published_at > FIRST_FETCH_MAX_AGE:
                continue
            try:
                item = self._item(src, raw, fetched_at)
            except _NoLink:
                continue
            if first and raw.published_at is None:
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

    def _item(self, src: ledger.FeedSource, raw: RawItem, fetched_at: datetime) -> ledger.FeedItemIn:
        published = min(raw.published_at, fetched_at) if raw.published_at else fetched_at
        if src.kind == "gnews":
            self.throttle.wait()
            decoded = self.decode(raw.link) if raw.link else None
            url = canonical_url(decoded)
            return ledger.FeedItemIn(raw.external_id, url, raw.link, raw.title, raw.teaser,
                                     published, url is None)
        url = canonical_url(raw.link)
        if url is None:
            # An RSS item without a usable link has nothing to open, and db/032
            # allows a NULL url only for a failed Google News decode: skipped.
            raise _NoLink()
        return ledger.FeedItemIn(raw.external_id, url, None, raw.title, raw.teaser, published, False)

    def _place(self, src: ledger.FeedSource, it: ledger.FeedItemIn, vec: Sequence[float],
               title_vec: Sequence[float], stats: SourceStats) -> None:
        decision = cluster.assign(title_vec, it.title, self.candidates)  # type: ignore[arg-type]
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
        self.candidates.insert(0, _Candidate(made[0], 1, [member]))


class FetchFailed(RuntimeError):
    """A non-200, non-304 answer."""


class _NoLink(Exception):
    pass


# ------------------------------------------------------------------ the job

_SEED_CACHE: dict[tuple[int, str], tuple[float, ...]] = {}
_SYNCED = False


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
    run = ingest or Ingest(get=fetcher)  # type: ignore[arg-type]
    try:
        run.prepare(_SEED_CACHE)
        stats = RunStats()
        for src in ledger.feed_active_sources():
            stats.sources.append(run.run_source(src))
        log.info("feed ingest: %s", stats.line())
        return stats
    finally:
        # The run's candidate vectors are the job's biggest structure: drop
        # them, then hand the freed memory back (core/memory.py).
        run.candidates = []
        if fetcher is not None:
            fetcher.close()
        memory.release()
