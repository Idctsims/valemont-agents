"""Screening Room recommendations: the TMDB side and the jobs (Chat 4
Phase 4; db/053; docs/screening.md §7). The math is screening/recs.py.

GATHER (gather): for every loved title (the seeds first, then Loves, "Seen,
loved" and finished titles, newest first, at most MAX_EXPAND), TMDB's
/recommendations and /similar (page 1 each); /discover for each kind by the
genres and keywords the loved titles weigh most (less the not-interested
ones), sorted by vote count, at the quality floor; and /trending/{kind}/week
(the freshness lane). Then the floor (recs.passes_floor) and the owner's
exclusions; every survivor without an embedding gets its details (with
keywords: one call) and is embedded once (core/embed.py).
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from core import embed as embedder
from core import ledger, memory, push
from core.jobs import JobSkipped
from screening import recs, tmdb

OWNER_TZ = "America/Chicago"

log = logging.getLogger("valemont.screening.recs")

#: Loved titles whose recommendations and similar lists are read, per run.
MAX_EXPAND = 40
#: Discover: the top genres (ANDed: "Drama and Crime") and keywords (ORed).
DISCOVER_GENRES = 2
DISCOVER_KEYWORDS = 6
DISCOVER_PAGES = 1

Key = recs.Key


@dataclass(frozen=True)
class Taste:
    """A loved or not-interested title's TMDB ids for the discover lane."""
    key: Key
    weight: float
    genre_ids: tuple[int, ...]
    keyword_ids: tuple[int, ...]


def discover_filters(tastes: Sequence[Taste], kind: str) -> list[dict[str, Any]]:
    """discover's filter sets for one kind, from the loved titles of that
    kind (weight > 0) less the not-interested ones (weight < 0): the top
    genres together, and the top keywords (any of them). Empty when the kind
    has no loved title."""
    mine = [t for t in tastes if t.key[1] == kind]
    if not any(t.weight > 0 for t in mine):
        return []
    g: Counter[int] = Counter()
    k: Counter[int] = Counter()
    for t in mine:
        for gid in t.genre_ids:
            g[gid] += t.weight
        for kid in t.keyword_ids:
            k[kid] += t.weight
    top_g = [gid for gid, w in sorted(g.items(), key=lambda x: (-x[1], x[0])) if w > 0][:DISCOVER_GENRES]
    # A keyword counts only when two or more loved titles share it.
    top_k = [kid for kid, w in sorted(k.items(), key=lambda x: (-x[1], x[0])) if w >= 2 * recs.W_LOVE - 1e-9][:DISCOVER_KEYWORDS]
    floor = {"sort_by": "vote_count.desc", "vote_average.gte": recs.QUALITY_MIN_AVG,
             "vote_count.gte": recs.QUALITY_MIN_COUNT}
    out = []
    if top_g:
        out.append({**floor, "with_genres": ",".join(str(x) for x in top_g)})
    if top_k:
        out.append({**floor, "with_keywords": "|".join(str(x) for x in top_k)})
    return out


@dataclass
class GatherStats:
    expanded: int = 0
    discover_calls: int = 0
    raw: int = 0

    def line(self) -> str:
        return f"{self.expanded} loved titles expanded, {self.discover_calls} discover calls, {self.raw} candidates"


def gather(t: tmdb.Tmdb, loved: Sequence[Key], tastes: Sequence[Taste], *,
           stats: GatherStats | None = None) -> recs.Pool:
    """The candidate pool. `loved` in priority order (seeds first)."""
    pool = recs.Pool()
    st = stats if stats is not None else GatherStats()
    for key in list(dict.fromkeys(loved))[:MAX_EXPAND]:
        for lane in ("recommendations", "similar"):
            for r in t.related(key[1], key[0], lane):
                pool.add(r, lane, via=key)
        st.expanded += 1
    for kind in ("movie", "tv"):
        for f in discover_filters(tastes, kind):
            for page in range(1, DISCOVER_PAGES + 1):
                st.discover_calls += 1
                for r in t.discover(kind, page=page, **f):
                    pool.add(r, "discover")
        for r in t.trending_week(kind):
            pool.add(r, "trending")
    st.raw = len(pool.results)
    return pool


@dataclass
class Detail:
    """What a run keeps of one title's details (the rest is dropped at once)."""
    title: tmdb.TitleIn
    genre_ids: tuple[int, ...]
    keyword_ids: tuple[int, ...]
    #: Official YouTube trailers and teasers, newest first (trailer source #2).
    videos: tuple[tmdb.Video, ...] = ()

    @property
    def text(self) -> str:
        return recs.embed_text(self.title.genres, self.title.keywords, self.title.overview)


def detail_of(t: tmdb.Tmdb, key: Key) -> Detail | None:
    d = t.details(key[1], key[0])
    if d is None:
        return None
    return Detail(tmdb.title_from(key[1], d), tmdb.genre_ids(d), tmdb.keyword_ids(d),
                  tuple(tmdb.official_videos(d)))


Embed = Callable[[Sequence[str]], list[list[float]]]


# ------------------------------------------------------------------ the record

def as_record(r: ledger.TitleRecord) -> recs.Record:
    return recs.Record(r.title_id, r.reaction, r.seen, r.seed, r.finished, r.shelf is not None,
                       r.trailer_reaction, r.seen_pass)


def loved_keys(records: Sequence[ledger.TitleRecord]) -> list[Key]:
    """The titles whose lists feed the pool: the seeds (in their order), then
    every other love signal, the newest first."""
    seeds = [(r.tmdb_id, r.kind) for r in sorted(records, key=lambda r: r.title_id) if r.seed]
    others = []
    for r in sorted(records, key=lambda r: (r.reacted_at is None, -(r.reacted_at.timestamp() if r.reacted_at else 0),
                                            r.title_id)):
        s = recs.signal_of(as_record(r))
        if s is not None and s.kind == "love" and not r.seed:
            others.append((r.tmdb_id, r.kind))
    return list(dict.fromkeys(seeds + others))


# ------------------------------------------------------------------ jobs

FULL_JOB = "screening_recs"
#: job_queue kinds (core/system_jobs.QUEUE_HANDLERS).
RERANK_KIND = "screening_rerank"
TEST_PUSH_KIND = "screening_test_release_push"
RELEASE_JOB = "screening_release_push"
TMDB_SOURCE = "tmdb-videos"

#: Titles fetched and embedded per run at most (the day one pool is ~300).
EMBED_CAP = 450
#: The TMDB trailer path's titles from the release lists: the best this many
#: by the profile, with at least this similarity (recs.score's similarity).
TRAILER_TOP = 15
TRAILER_MIN_SIMILARITY = 0.10
TRAILER_LIST_MAX_AGE_DAYS = 60
TRAILERS_PER_TITLE = 2
#: Titles a re-rank fetches and embeds at most (module doc of rerank_job).
RERANK_FETCH = 3


@dataclass
class RecRun:
    reason: str
    pool: int = 0
    eligible: int = 0
    scored: int = 0
    embedded: int = 0
    calls: int = 0
    seen_pass: int = 0
    trailers: int = 0
    run_id: int | None = None
    loves: int = 0
    nis: int = 0
    top: list[str] = field(default_factory=list)

    @property
    def job_note(self) -> str:
        return (f"{self.reason}: {self.scored} scored, {self.eligible} eligible of {self.pool}; "
                f"{self.loves} loves, {self.nis} not interested; {self.embedded} embedded; "
                f"{self.trailers} TMDB trailers; {self.calls} TMDB calls"
                + (f"; seen-it pass {self.seen_pass}" if self.seen_pass else ""))


def _client(client: tmdb.Tmdb | None, env: Mapping[str, str]) -> tuple[tmdb.Tmdb, Callable[[], None] | None]:
    if client is not None:
        return client, None
    token = tmdb.read_token(env)
    http, close = tmdb.httpx_get()
    return tmdb.Tmdb(token, http), close


def _fetch(t: tmdb.Tmdb, keys: Sequence[Key], embed: Embed, run: RecRun, *,
           keep: Callable[[Key, int, Detail], None] | None = None) -> dict[Key, int]:
    """Details for each key: upserted into titles (fresh votes, providers,
    keywords); each title with no vector embedded once. `keep(key, id,
    detail)` sees every detail before it is dropped. Returns key -> title id."""
    ids: dict[Key, int] = {}
    texts: list[tuple[int, str]] = []
    have = ledger.screening_titles_by_key(list(keys))
    for key in keys:
        if have.get(key) is not None and have[key].is_test:
            continue
        d = detail_of(t, key)
        if d is None:
            log.warning("screening recs: %s/%d is gone from TMDB", key[1], key[0])
            continue
        tid = ledger.titles_upsert(d.title)
        ids[key] = tid
        if not (have.get(key) and have[key].embedded) and d.text:
            texts.append((tid, d.text))
        if keep is not None:
            keep(key, tid, d)
    if texts:
        for (tid, _), vec in zip(texts, embed([x for _, x in texts])):
            ledger.titles_set_embedding(tid, vec)
        run.embedded += len(texts)
    return ids


def _latest(p: ledger.PoolTitle) -> date | None:
    return p.last_episode_date if p.kind == "tv" else p.release_date


#: The taste notes' vectors, per process: they change only with a migration,
#: and a re-rank runs inside the 10 s job_queue poller, which must stay fast
#: (a poll that takes over 20 s reads as stale to the health monitor).
_NOTES_CACHE: dict[tuple[str, ...], list[tuple[float, ...]]] = {}


def _note_vectors(notes: Sequence[str], embed: Embed) -> list[tuple[float, ...]]:
    key = tuple(notes)
    if not key:
        return []
    if key not in _NOTES_CACHE:
        _NOTES_CACHE.clear()
        _NOTES_CACHE[key] = [tuple(v) for v in embed(list(key))]
    return _NOTES_CACHE[key]


@dataclass
class Ranked:
    profile: recs.Profile
    scored: dict[int, recs.Scored]
    eligible: list[int]


def rank_and_record(reason: str, *, embed: Embed, run: RecRun, today: date | None = None) -> Ranked | None:
    """Score the stored pool (and shelf and trailer titles) against a profile
    built from the owner's record now; write the run and every score. None
    when nothing is embedded yet (no full run so far)."""
    today = today or ledger.owner_today()
    records = ledger.screening_records()
    by_id = {r.title_id: as_record(r) for r in records}
    pool = ledger.screening_pool()
    extra = [p for p in ledger.screening_extra_titles() if p.id not in {q.id for q in pool}]
    signals = [s for r in by_id.values() if (s := recs.signal_of(r)) is not None]
    ids = sorted({p.id for p in pool} | {p.id for p in extra} | {s.title_id for s in signals})
    vecs = ledger.screening_title_vectors(ids)
    if not vecs or not pool:
        log.info("screening recs: nothing to rank yet (%d vectors, %d in the pool)", len(vecs), len(pool))
        return None
    mean_vec = recs.mean(list(vecs.values()))
    note_vecs = _note_vectors(ledger.screening_note_phrases(), embed)
    prof = recs.build_profile(mean_vec, signals, vecs, note_vecs, seeds=[r.title_id for r in records if r.seed])
    eligible = [p for p in pool if p.id in vecs and recs.passes_floor(p.vote_average, p.vote_count)
                and not (p.id in by_id and recs.excluded(by_id[p.id]))]
    titles = [recs.TitleIn(p.id, vecs[p.id], p.tonight, _latest(p), p.trending)  # type: ignore[arg-type]
              for p in eligible + extra if p.id in vecs]
    ranked = recs.rank(titles, prof, today)
    elig = {p.id for p in eligible}
    kinds = {p.id: p.kind for p in eligible}
    names = {p.id: p.name for p in pool + extra}
    names.update({r.title_id: r.name for r in records})
    top = recs.balance([s for s in ranked if s.title_id in elig], kinds)
    run.run_id = ledger.screening_insert_run(
        prof, reason=reason, pool_n=len(pool), eligible_n=len(eligible), scored_n=len(ranked), tmdb_calls=run.calls,
        top=[{"title_id": s.title_id, "name": names.get(s.title_id), "kind": kinds.get(s.title_id),
              "score": s.score, "because": names.get(s.because) if s.because else None} for s in top])
    ledger.title_scores_upsert(ranked, run.run_id)
    run.pool, run.eligible, run.scored = len(pool), len(eligible), len(ranked)
    run.loves, run.nis = prof.love_count, prof.ni_count
    run.top = [str(names.get(s.title_id)) for s in top]
    return Ranked(prof, {s.title_id: s for s in ranked}, [s.title_id for s in top])


def recs_job(*, client: tmdb.Tmdb | None = None, embed: Embed | None = None, env: Mapping[str, str] = os.environ,
             today: date | None = None, force: bool = False, reason: str = "full") -> RecRun:
    """The screening_recs system job: nightly 04:30 America/Chicago, and at
    boot unless today's run already succeeded. Gather, fetch and embed,
    write the pool, the seen-it pass (once), rank, then the TMDB trailers."""
    if not force and ledger.job_ok_today(FULL_JOB):
        raise JobSkipped("today's full run already happened")
    embed = embed or embedder.embed
    run = RecRun(reason)
    t, close = _client(client, env)
    try:
        # 1. Every title in the owner's record gets a vector (the seeds, day one).
        _fetch(t, [(r.tmdb_id, r.kind) for r in ledger.screening_unembedded(EMBED_CAP)], embed, run)
        records = ledger.screening_records()
        by_key = {(r.tmdb_id, r.kind): r for r in records}
        loved = loved_keys(records)[:MAX_EXPAND]
        nis = [(r.tmdb_id, r.kind) for r in records
               if (s := recs.signal_of(as_record(r))) is not None and s.kind == "ni"][:20]
        # 2. Tastes for discover: the loved and not-interested titles' genres and keywords.
        tastes: list[Taste] = []

        def taste(key: Key, _tid: int, d: Detail) -> None:
            r = by_key.get(key)
            s = recs.signal_of(as_record(r)) if r is not None else None
            if s is not None and s.weight > 0:
                tastes.append(Taste(key, s.weight if s.kind == "love" else -s.weight, d.genre_ids, d.keyword_ids))

        _fetch(t, loved + nis, embed, run, keep=taste)
        # 3. The pool: lists, floor, exclusions; every survivor fetched (fresh
        #    providers and votes) and embedded once.
        pool = gather(t, loved, tastes)
        excluded = {(r.tmdb_id, r.kind) for r in records if recs.excluded(as_record(r))}
        keep = pool.keys_passing(excluded=excluded)
        pass_needed = ledger.screening_seen_pass_count() == 0
        known = [k for k, r in pool.results.items() if k not in excluded and pass_needed
                 and (r.get("vote_count") or 0) >= (recs.PASS_MIN_COUNT_TV if k[1] == "tv" else recs.PASS_MIN_COUNT)]
        want = list(dict.fromkeys(keep + known))[:EMBED_CAP]
        pass_in: dict[int, tuple[str, tuple[str, ...], int]] = {}

        def note_pass(key: Key, tid: int, d: Detail) -> None:
            if key in set(known):
                pass_in[tid] = (key[1], d.title.genres, int(d.title.vote_count or 0))

        ids = _fetch(t, want, embed, run, keep=note_pass if pass_needed else None)
        via_ids = ledger.screening_titles_by_key(loved)
        ledger.screening_candidates_upsert([
            (ids[k], sorted(pool.lanes.get(k, ())), [via_ids[v].id for v in pool.via.get(k, ()) if v in via_ids])
            for k in keep if k in ids])
        # 4. The seen-it pass, once.
        if pass_needed and pass_in:
            seeds = {r.title_id: r.seed_group for r in records if r.seed and r.seed_group}
            vecs = ledger.screening_title_vectors(sorted(set(pass_in) | set(seeds)))
            mean_vec = recs.mean(list(vecs.values()))
            cands = [recs.PassIn(tid, vecs[tid], n, kind, genres)  # type: ignore[arg-type]
                     for tid, (kind, genres, n) in pass_in.items() if tid in vecs]
            rows = recs.seen_pass(cands, seeds, vecs, mean_vec)  # type: ignore[arg-type]
            run.seen_pass = ledger.screening_seen_pass_write(rows)
        # 5. Rank and record.
        run.calls = t.calls
        ranked = rank_and_record(reason, embed=embed, run=run, today=today)
        # 6. Trailer source #2.
        if ranked is not None:
            run.trailers = tmdb_trailers(t, embed, ranked, records, run)
        run.calls = t.calls
        log.info("screening recs: %s; top: %s", run.job_note, ", ".join(run.top))
        return run
    finally:
        if close is not None:
            close()
        memory.release()


def rerank_job(payload: Mapping[str, Any] | None = None, *, client: tmdb.Tmdb | None = None,
               embed: Embed | None = None, env: Mapping[str, str] = os.environ) -> RecRun:
    """The `screening_rerank` queue job (db/053 queues one, debounced, after
    a reaction, a shelf move or a trailer reaction): the stored pool scored
    against the record as it is now. No lists are read. It runs inside the
    job_queue poller, so it stays short: at most RERANK_FETCH titles the
    owner reacted to with no vector yet are fetched and embedded first (the
    nightly run takes any others)."""
    embed = embed or embedder.embed
    run = RecRun("rerank")
    started = time.monotonic()
    try:
        missing = ledger.screening_unembedded(RERANK_FETCH)
        if missing:
            t, close = _client(client, env)
            try:
                _fetch(t, [(r.tmdb_id, r.kind) for r in missing], embed, run)
                run.calls = t.calls
            finally:
                if close is not None:
                    close()
        if rank_and_record("rerank", embed=embed, run=run) is None:
            log.info("screening rerank: no pool yet; the nightly run builds it")
        else:
            log.info("screening rerank: %s; %.1f s; top: %s", run.job_note, time.monotonic() - started,
                     ", ".join(run.top))
        return run
    finally:
        memory.release()


# ------------------------------------------------------------ trailer source #2

YT_KEY = re.compile(r"^https://(?:www\.)?youtube\.com/watch\?(?:[^#]*&)?v=([A-Za-z0-9_-]{6,20})")


def youtube_key(url: str | None) -> str | None:
    """db/053 content_items.youtube_key, in Python (tests pin the two)."""
    m = YT_KEY.match(url or "")
    return m.group(1) if m else None


def trailer_item(title: tmdb.TitleIn, v: tmdb.Video, now: datetime) -> ledger.FeedItemIn:
    """A TMDB video as a feed item, keyed exactly as the YouTube RSS path keys
    the same video (external_id yt:video:<key>, the watch URL), so the
    database's youtube_key index lets only one of the two in."""
    return ledger.FeedItemIn(
        external_id=f"yt:video:{v.key}", url=f"https://www.youtube.com/watch?v={v.key}", google_url=None,
        title=f"{title.name} | {v.name or v.type}"[:300], teaser=None,
        published_at=min(v.published_at or now, now), decode_failed=False, precision="time", backfill=False)


def trailer_targets(ranked: Ranked, listed: Mapping[int, Detail], shelf: Mapping[int, Detail]) -> list[tuple[int, Detail, bool]]:
    """(title id, detail, from_list): every Want and Watching title, then the
    release lists' best TRAILER_TOP by score with at least
    TRAILER_MIN_SIMILARITY."""
    out = [(tid, d, False) for tid, d in sorted(shelf.items())]
    scored = []
    for tid, d in listed.items():
        s = ranked.scored.get(tid)
        if tid in shelf or s is None or float(s.components.get("similarity", 0.0)) < TRAILER_MIN_SIMILARITY:
            continue
        scored.append((s.score, tid, d))
    for _, tid, d in sorted(scored, key=lambda x: (-x[0], x[1]))[:TRAILER_TOP]:
        out.append((tid, d, True))
    return out


def pick_videos(d: Detail, from_list: bool, now: datetime) -> list[tmdb.Video]:
    """A title's newest TRAILERS_PER_TITLE official videos; from a release
    list, only those posted in the last TRAILER_LIST_MAX_AGE_DAYS."""
    vids = list(d.videos)
    if from_list:
        vids = [v for v in vids if v.published_at is not None
                and (now - v.published_at).days <= TRAILER_LIST_MAX_AGE_DAYS]
    return vids[:TRAILERS_PER_TITLE]


def tmdb_trailers(t: tmdb.Tmdb, embed: Embed, ranked: Ranked, records: Sequence[ledger.TitleRecord],
                  run: RecRun) -> int:
    """Trailer source #2 (owner, 2026-10-15): TMDB's official YouTube
    trailers and teasers for upcoming and now-playing movies and on-the-air
    TV that score above the profile threshold, plus every Want and Watching
    title. Same rows as the RSS path (a story in pillar screening, its item
    from the `tmdb-videos` source, a title_trailers match), one per YouTube
    key whichever source comes first. Returns trailers added."""
    source = ledger.feed_source(TMDB_SOURCE)
    now = datetime.now(timezone.utc)
    shelf_keys = [(r.tmdb_id, r.kind) for r in records if r.shelf in ("want", "watching")]
    lists: list[Key] = []
    for which in ("upcoming", "now_playing"):
        lists += [(int(r["id"]), "movie") for r in t.movie_list(which)]
    lists += [(int(r["id"]), "tv") for r in t.tv_on_the_air()]
    excluded = {(r.tmdb_id, r.kind) for r in records if recs.excluded(as_record(r))}
    lists = [k for k in dict.fromkeys(lists) if k not in excluded and k not in set(shelf_keys)]
    shelf: dict[int, Detail] = {}
    listed: dict[int, Detail] = {}
    _fetch(t, shelf_keys, embed, run, keep=lambda k, tid, d: shelf.__setitem__(tid, d))
    _fetch(t, lists, embed, run, keep=lambda k, tid, d: listed.__setitem__(tid, d))
    # The release lists' titles were not in the pool: score them now.
    vecs = ledger.screening_title_vectors(sorted(listed))
    today = ledger.owner_today()
    for tid, d in listed.items():
        if tid in vecs and tid not in ranked.scored:
            tonight = d.title.tonight
            latest = d.title.last_episode_date if d.title.kind == "tv" else d.title.release_date
            ranked.scored[tid] = recs.score(recs.TitleIn(tid, vecs[tid], tonight, latest), ranked.profile, today)  # type: ignore[arg-type]
    if ranked.scored and run.run_id is not None:
        ledger.title_scores_upsert([ranked.scored[tid] for tid in listed if tid in ranked.scored], run.run_id)
    picks: list[tuple[int, ledger.FeedItemIn]] = []
    for tid, d, from_list in trailer_targets(ranked, listed, shelf):
        for v in pick_videos(d, from_list, now):
            picks.append((tid, trailer_item(d.title, v, now)))
    known = ledger.screening_known_youtube_keys([youtube_key(it.url) or "" for _, it in picks])
    picks = [(tid, it) for tid, it in picks if youtube_key(it.url) not in known]
    if not picks:
        return 0
    added = 0
    for (tid, it), vec in zip(picks, embed([it.title for _, it in picks])):
        made = ledger.feed_new_story(source, it, vec, vec, filtered_reason=None, seed=None)
        if made is None:
            continue  # the RSS path (or a race) got it first
        _story, item_id = made
        ledger.title_trailer_record(item_id, tid, f"tmdb:{it.external_id}")
        added += 1
    log.info("screening recs: %d TMDB trailer(s) added of %d new", added, len(picks))
    return added


# ------------------------------------------------------------ release alerts

RELEASE_AT = (8, 30)
RELEASE_UNTIL = (20, 0)
RELEASE_KIND = "screening_release"


def _phrase(e: ledger.ReleaseEvent) -> str:
    if e.what == "arrival":
        return f"{e.name}, now on {e.service}"
    if e.what == "episode":
        return f"{e.name}, new episode" + (f" on {e.service}" if e.service else "")
    return f"{e.name}" + (f" on {e.service}" if e.service else ", out today")


def release_message(events: Sequence[ledger.ReleaseEvent], *, test: bool = False) -> push.Message:
    """'Tonight: <title> on <service>', the others listed in the body. The
    tap opens the first title. Several events of one title read as one."""
    by_title: dict[int, list[ledger.ReleaseEvent]] = {}
    for e in events:
        by_title.setdefault(e.title_id, []).append(e)
    firsts = [evs[0] for evs in by_title.values()]
    first = firsts[0]
    label = "Tonight (test)" if test else "Tonight"
    head = f"{label}: {first.name} on {first.service}" if first.service else f"{label}: {_phrase(first)}"
    if len(firsts) > 1:
        head += f" (+{len(firsts) - 1} more)"
        body = "Also: " + "; ".join(_phrase(e) for e in firsts[1:])
    else:
        body = {"episode": "New episode today.", "arrival": f"Now streaming on {first.service}.",
                "release": "Out today."}.get(first.what, "Out today.")
    return push.Message(kind=RELEASE_KIND + ("_test" if test else ""), title=head[:120], body=body[:300],
                        deep_link=f"/screening/title/{first.kind}/{first.tmdb_id}", tag="screening-release")


def _notification_id(results: Any) -> int | None:
    for r in results or []:
        nid = getattr(r, "notification_id", None)
        if nid is not None:
            return int(nid)
    return None


def release_push_job(*, send: Callable[[push.Message], Any] = push.send,
                     now_local: datetime | None = None) -> str:
    """The screening_release_push system job, 08:30 America/Chicago (and at
    boot, between 08:30 and 20:00, when today's push has not gone): one push
    when a Want or Watching title becomes available today. Never twice for
    the same title and event; at most one a day (db/053)."""
    local = now_local or datetime.now(ZoneInfo(OWNER_TZ))
    if (local.hour, local.minute) < RELEASE_AT or (local.hour, local.minute) >= RELEASE_UNTIL:
        raise JobSkipped("outside 08:30-20:00 Chicago")
    events = [e for e in ledger.screening_events() if not e.alerted]
    if not events:
        raise JobSkipped("nothing on the shelves comes out today")
    push_id = ledger.release_push(events, lambda todo: _notification_id(send(release_message(todo))))
    if push_id is None:
        raise JobSkipped("today's release push already went")
    return f"release push {push_id}: {len(events)} event(s)"


def test_release_push(payload: Mapping[str, Any] | None = None, *,
                      send: Callable[[push.Message], Any] = push.send) -> int:
    """A forced TEST release push (scripts/screening_test_push.py; the
    `screening_test_release_push` queue job arms it for 08:30): recorded
    is_test, exempt from the one-a-day rule and the dedupe."""
    tid = (payload or {}).get("title_id")
    event = ledger.screening_test_push_title(int(tid) if tid is not None else None)
    if event is None:
        raise RuntimeError("no real Want or Watching title to announce (shelve one first)")
    push_id = ledger.release_push([event], lambda todo: _notification_id(send(release_message(todo, test=True))),
                                  test=True)
    log.info("screening: test release push %s for title %d", push_id, event.title_id)
    return int(push_id or 0)


# ------------------------------------------------------------------ dry run


@dataclass
class DryRun:
    picks: list[tuple[recs.Scored, Detail, list[str]]] = field(default_factory=list)
    seen_pass: list[tuple[Detail, str]] = field(default_factory=list)
    pool: int = 0
    floor_kept: int = 0
    calls: int = 0
    stats: GatherStats = field(default_factory=GatherStats)


def dry_run(t: tmdb.Tmdb, embed: Embed, today: Any, *, notes: Sequence[str],
            seeds: Sequence[tuple[int, str, str, str]]) -> DryRun:
    """Day one from the seeds alone, nothing written: what the first real
    run would recommend (scripts/screening_recs.py --dry-run)."""
    from screening import services

    out = DryRun()
    seed_keys = [(s[0], s[1]) for s in seeds]
    details: dict[Key, Detail] = {}
    for key in seed_keys:
        d = detail_of(t, key)
        if d is None:
            raise RuntimeError(f"seed {key} is gone from TMDB")
        details[key] = d
    tastes = [Taste(k, recs.W_SEED, details[k].genre_ids, details[k].keyword_ids) for k in seed_keys]
    pool = gather(t, seed_keys, tastes, stats=out.stats)
    out.pool = len(pool.results)
    keep = pool.keys_passing(excluded=seed_keys)
    out.floor_kept = len(keep)
    # The seen-it pass reads well-known titles regardless of the floor.
    known = [k for k, r in pool.results.items() if k not in seed_keys
             and (r.get("vote_count") or 0) >= (recs.PASS_MIN_COUNT_TV if k[1] == "tv" else recs.PASS_MIN_COUNT)]
    for key in sorted(set(keep) | set(known)):
        d = detail_of(t, key)
        if d is not None:
            details[key] = d
    keys = list(details)
    vecs_list = embed([details[k].text for k in keys])
    ids = {k: i + 1 for i, k in enumerate(keys)}  # stand-in title ids
    vecs = {ids[k]: tuple(v) for k, v in zip(keys, vecs_list)}
    mean_vec = recs.mean(list(vecs.values()))
    note_vecs = [tuple(v) for v in embed(list(notes))] if notes else []
    signals = [recs.Signal(ids[k], "love", recs.W_SEED) for k in seed_keys]
    prof = recs.build_profile(mean_vec, signals, vecs, note_vecs, seeds=[ids[k] for k in seed_keys])
    titles = []
    for k in keep:
        if k not in details:
            continue
        ti = details[k].title
        tonight = tuple(r["service"] for r in services.split(ti.providers)["tonight"])
        latest = ti.last_episode_date if ti.kind == "tv" else ti.release_date
        titles.append(recs.TitleIn(ids[k], vecs[ids[k]], tonight, latest, "trending" in pool.lanes.get(k, ())))
    ranked = recs.rank(titles, prof, today)
    kinds = {ids[k]: k[1] for k in keys}
    by_id = {ids[k]: k for k in keys}
    for s in recs.balance(ranked, kinds):
        k = by_id[s.title_id]
        out.picks.append((s, details[k], sorted(pool.lanes.get(k, ()))))
    groups = {ids[(s[0], s[1])]: s[3] for s in seeds}
    cands = [recs.PassIn(ids[k], vecs[ids[k]], int(pool.results[k].get("vote_count") or 0), k[1], details[k].title.genres)
             for k in known if k in ids]
    for tid, g in recs.seen_pass(cands, groups, vecs, mean_vec):
        out.seen_pass.append((details[by_id[tid]], g))
    out.calls = t.calls
    out.seed_names = {ids[k]: details[k].title.name for k in seed_keys}  # type: ignore[attr-defined]
    return out
