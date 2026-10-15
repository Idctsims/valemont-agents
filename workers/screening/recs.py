"""Screening Room recommendations: the math (Chat 4 Phase 4; db/053;
docs/screening.md §7).

Pure functions, no I/O: screening/rec_jobs.py feeds them from TMDB and the
ledger, and tests/test_screening_recs.py drives them with stub vectors.

    vectors     one gte-small embedding per title (core/embed.py), of
                embed_text(): its genres, its TMDB keywords and its
                overview. Stored on titles.embedding, embedded once.
    centered    c(x) = x - mean, the mean of every embedded title the run
                sees (pool, loves, not-interesteds): gte-small is
                anisotropic, as the feed found (feed/rank.py).
    signals     each title's weight from the owner's record (signal_of):
                a seed 1.0 (the owner's seen-and-loved list), Love 1.0,
                "Seen, loved" 1.0, Finished 0.5, Finished AND loved 1.5 (the
                strongest: owner, 2026-10-15), a trailer Love 0.5; Not
                interested 1.0, "Seen, not for me" 1.0, a trailer Not
                interested 0.5. Neutral weighs nothing. The latest reaction
                decides love or not.
    profile     love = s*S_notes + (1-s)*R_love, s = K_NOTES/(K_NOTES + n_love)
                ni   = R_ni (no not-interested notes)
                S_notes = unit mean of the owner's taste notes, centered;
                R = unit weighted mean of the centered signal titles. The
                feed engine's blend (feed/rank.build_profile), the same
                love / neutral / not-interested weighting.
    similarity  W_MEAN * cos(c(x), love) + W_NEAR * near(x) - cos(c(x), ni)
                near(x) = the best cos(c(x), c(l)) over the loved titles l:
                the mean alone blurs fifteen seeds across three moods (a
                rom-com and Heat average to neither), so the nearest loved
                title carries half the weight.
    score       W_AFF * similarity + TONIGHT (streams on one of the owner's
                services: flatrate, ads or free) + FRESH * freshness
                freshness = 1 for a title out (or a new season) in the last
                FRESH_DAYS, falling to 0 at twice that, + the trending lane.
    because     the loved title with the best cos(c(x), c(l)): its name is
                "Because you loved <name>". Ties: the lower title id. No
                Claude call.

ELIGIBLE (eligible): in the candidate pool, at or above the quality floor
(vote_average >= QUALITY_MIN_AVG and vote_count >= QUALITY_MIN_COUNT), and
not excluded: seen (a seed, "Seen, ...", or finished), on any shelf, or
reacted to (Love, Neutral, Not interested: a card the owner dealt with leaves
the row; Love feeds the profile, Add to Want shelves it). The web applies the
same exclusions again at read time (db/053 screening_for_you), so a tap
removes a card before the next re-rank.
"""

from __future__ import annotations

import math
import operator
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Literal, Mapping, Sequence

Vec = tuple[float, ...]
Kind = Literal["movie", "tv"]
Key = tuple[int, str]  # (tmdb_id, kind)

# ------------------------------------------------------------ the weights
#: The quality floor (owner, 2026-10-15; tunable, recorded in db/053's
#: screening_settings and here).
QUALITY_MIN_AVG = 6.8
QUALITY_MIN_COUNT = 200

K_NOTES = 6
W_AFF = 4.0
W_MEAN = 0.5
W_NEAR = 0.5
TONIGHT = 0.4
FRESH = 0.3
FRESH_DAYS = 365
TRENDING_FRESH = 0.5

W_SEED = 1.0
W_LOVE = 1.0
W_SEEN_LOVED = 1.0
W_FINISHED = 0.5
W_FINISHED_LOVED = 1.5
W_TRAILER = 0.5
W_NI = 1.0

#: For you shows 12; each run keeps a little more so taps never empty it.
FOR_YOU = 12
#: The seen-it pass (one time, re-openable): about 30 well-known titles, ten
#: per seed group, the closest to that group's seeds first.
PASS_SIZE = 30
PASS_MIN_COUNT = 1000

LANES = ("recommendations", "similar", "discover", "trending")


# ------------------------------------------------------------ vector math

def sub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return tuple(map(operator.sub, a, b))


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(map(operator.mul, a, b))


def norm(a: Sequence[float]) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Sequence[float]) -> Vec | None:
    n = norm(a)
    return tuple(x / n for x in a) if n > 1e-12 else None


def mean(vectors: Sequence[Sequence[float]], weights: Sequence[float] | None = None) -> Vec:
    if not vectors:
        raise ValueError("mean of no vectors")
    ws = list(weights) if weights is not None else [1.0] * len(vectors)
    total = sum(ws)
    if total <= 0:
        raise ValueError("weights sum to zero")
    dims = len(vectors[0])
    return tuple(sum(w * v[i] for v, w in zip(vectors, ws)) / total for i in range(dims))


def cos(a: Sequence[float], b: Sequence[float]) -> float:
    na, nb = norm(a), norm(b)
    return dot(a, b) / (na * nb) if na > 1e-12 and nb > 1e-12 else 0.0


def blend(seed: Vec | None, reacted: Vec | None, seed_weight: float) -> Vec | None:
    if seed is None:
        return reacted
    if reacted is None:
        return seed
    return tuple(seed_weight * s + (1 - seed_weight) * r for s, r in zip(seed, reacted))


# ------------------------------------------------------------ candidates

def passes_floor(vote_average: float | None, vote_count: int | None) -> bool:
    return (vote_average is not None and vote_count is not None
            and vote_average >= QUALITY_MIN_AVG and vote_count >= QUALITY_MIN_COUNT)


@dataclass
class Pool:
    """Candidates by (tmdb_id, kind): the lanes each came from and the loved
    titles (by key) whose recommendations or similar lists named it."""
    results: dict[Key, dict] = field(default_factory=dict)
    lanes: dict[Key, set[str]] = field(default_factory=dict)
    via: dict[Key, set[Key]] = field(default_factory=dict)

    def add(self, r: Mapping, lane: str, via: Key | None = None) -> None:
        kind = r.get("media_type")
        if kind not in ("movie", "tv") or not isinstance(r.get("id"), int):
            return
        key = (int(r["id"]), str(kind))
        self.results.setdefault(key, dict(r))
        self.lanes.setdefault(key, set()).add(lane)
        if via is not None:
            self.via.setdefault(key, set()).add(via)

    def keys_passing(self, excluded: Iterable[Key] = ()) -> list[Key]:
        """Keys at or above the floor, not excluded, in a stable order."""
        ex = set(excluded)
        return sorted(k for k, r in self.results.items()
                      if k not in ex and passes_floor(r.get("vote_average"), r.get("vote_count")))


# ------------------------------------------------------------ the record

SignalKind = Literal["love", "ni", "neutral"]


@dataclass(frozen=True)
class Record:
    """One title in the owner's record, as db/053's v_title_state sums it."""
    title_id: int
    #: The latest card / title page / seen-pass reaction: love,
    #: not_interested, neutral, or None (none, or cleared).
    reaction: str | None
    seen: bool
    seed: bool
    finished: bool
    on_shelf: bool
    #: The latest reaction on a trailer matched to this title, if any.
    trailer_reaction: str | None = None
    #: Answered "Seen, loved" or "Seen, not for me" in the seen-it pass.
    seen_pass: str | None = None


@dataclass(frozen=True)
class Signal:
    title_id: int
    kind: SignalKind
    weight: float


def signal_of(r: Record) -> Signal | None:
    """What a title tells the profile (module doc, "signals")."""
    if r.reaction == "not_interested":
        return Signal(r.title_id, "ni", W_NI)
    if r.reaction == "love":
        if r.finished:
            return Signal(r.title_id, "love", W_FINISHED_LOVED)
        if r.seed:
            return Signal(r.title_id, "love", W_SEED)
        return Signal(r.title_id, "love", W_SEEN_LOVED if r.seen_pass == "seen_loved" else W_LOVE)
    if r.reaction == "neutral":
        return Signal(r.title_id, "neutral", 0.0)
    if r.finished:
        return Signal(r.title_id, "love", W_FINISHED)
    if r.trailer_reaction == "love":
        return Signal(r.title_id, "love", W_TRAILER)
    if r.trailer_reaction == "not_interested":
        return Signal(r.title_id, "ni", W_TRAILER)
    return None


def excluded(r: Record) -> bool:
    """Never recommended: seen, shelved, or already reacted to."""
    return r.seen or r.seed or r.finished or r.on_shelf or r.reaction in ("love", "neutral", "not_interested")


# ------------------------------------------------------------ the profile

@dataclass(frozen=True)
class Profile:
    mean: Vec
    love: Vec | None
    ni: Vec | None
    #: (title_id, unit centered vector) of every love signal, by title id:
    #: near() and the because.
    loved: tuple[tuple[int, Vec], ...]
    love_count: int
    ni_count: int
    neutral_count: int
    seed_count: int
    notes_weight: float


def build_profile(mean_vec: Sequence[float], signals: Sequence[Signal], vecs: Mapping[int, Sequence[float]],
                  note_vecs: Sequence[Sequence[float]], seeds: Iterable[int] = ()) -> Profile:
    """`vecs`: title_id -> embedding, for every signal title that has one."""
    mv = tuple(mean_vec)

    def c(v: Sequence[float]) -> Vec:
        return sub(v, mv)

    loves = [s for s in signals if s.kind == "love" and s.title_id in vecs and s.weight > 0]
    nis = [s for s in signals if s.kind == "ni" and s.title_id in vecs and s.weight > 0]
    r_love = unit(mean([c(vecs[s.title_id]) for s in loves], [s.weight for s in loves])) if loves else None
    r_ni = unit(mean([c(vecs[s.title_id]) for s in nis], [s.weight for s in nis])) if nis else None
    s_notes = unit(mean([c(v) for v in note_vecs])) if note_vecs else None
    w = K_NOTES / (K_NOTES + len(loves)) if s_notes is not None else 0.0
    seed_set = set(seeds)
    return Profile(
        mean=mv, love=blend(s_notes, r_love, w), ni=r_ni,
        loved=tuple((s.title_id, u) for s in sorted(loves, key=lambda s: s.title_id)
                    if (u := unit(c(vecs[s.title_id]))) is not None),
        love_count=len(loves), ni_count=len(nis),
        neutral_count=sum(1 for s in signals if s.kind == "neutral"),
        seed_count=sum(1 for s in loves if s.title_id in seed_set), notes_weight=round(w, 4),
    )


# ------------------------------------------------------------ scoring

@dataclass(frozen=True)
class TitleIn:
    title_id: int
    vec: Vec
    #: The owner's services it streams on tonight (services.split's tonight).
    tonight: tuple[str, ...] = ()
    #: Its US release, or a series' latest episode (a season under way).
    latest: date | None = None
    trending: bool = False


@dataclass(frozen=True)
class Scored:
    title_id: int
    score: float
    because: int | None
    components: Mapping[str, object]


def because_of(cx: Sequence[float], loved: Sequence[tuple[int, Vec]]) -> tuple[int | None, float]:
    """The nearest loved title (best cosine; ties: the lower id) and that
    cosine. Deterministic. `loved` holds unit vectors, sorted by id."""
    best_id, best = None, -2.0
    ux = unit(cx)
    if ux is None:
        return None, 0.0
    for tid, lv in loved:
        s = dot(ux, lv)
        if s > best + 1e-12:
            best_id, best = tid, s
    return best_id, (best if best_id is not None else 0.0)


def freshness(latest: date | None, today: date, trending: bool) -> float:
    f = 0.0
    if latest is not None and latest <= today:
        age = (today - latest).days
        f = 1.0 if age <= FRESH_DAYS else max(0.0, 1.0 - (age - FRESH_DAYS) / FRESH_DAYS)
    return min(1.0, f + (TRENDING_FRESH if trending else 0.0))


def score(t: TitleIn, p: Profile, today: date) -> Scored:
    cx = sub(t.vec, p.mean)
    love_cos = cos(cx, p.love) if p.love is not None else 0.0
    ni_cos = cos(cx, p.ni) if p.ni is not None else 0.0
    because, near = because_of(cx, p.loved)
    similarity = W_MEAN * love_cos + W_NEAR * near - ni_cos
    tonight = TONIGHT if t.tonight else 0.0
    fresh = FRESH * freshness(t.latest, today, t.trending)
    total = W_AFF * similarity + tonight + fresh
    return Scored(t.title_id, round(total, 4), because, {
        "love_cos": round(love_cos, 4), "near": round(near, 4), "ni_cos": round(ni_cos, 4),
        "similarity": round(similarity, 4), "tonight": tonight, "fresh": round(fresh, 4),
    })


def rank(titles: Sequence[TitleIn], p: Profile, today: date) -> list[Scored]:
    """Best first; ties: the lower title id."""
    return sorted((score(t, p, today) for t in titles), key=lambda s: (-s.score, s.title_id))


#: "Movies as much as TV" (owner's taste note): For you's 12 hold at least
#: this many of each kind when the pool has them.
MIN_EACH_KIND = 4


def balance(ranked: Sequence[Scored], kinds: Mapping[int, str], n: int = FOR_YOU,
            min_each: int = MIN_EACH_KIND) -> list[Scored]:
    """The best `min_each` of each kind, then the best of the rest by score,
    `n` in all, ordered by score (ties: the lower id). `ranked` is rank()'s
    order. db/053 screening_for_you is the same rule in SQL (tests_live pins
    the two)."""
    guaranteed: list[Scored] = []
    for kind in ("movie", "tv"):
        guaranteed += [s for s in ranked if kinds.get(s.title_id) == kind][:min_each]
    ids = {s.title_id for s in guaranteed}
    rest = [s for s in ranked if s.title_id not in ids]
    chosen = guaranteed[:n] + rest[:max(0, n - len(guaranteed))]
    return sorted(chosen, key=lambda s: (-s.score, s.title_id))


# ------------------------------------------------------------ the seen-it pass

@dataclass(frozen=True)
class PassIn:
    title_id: int
    vec: Vec
    vote_count: int
    kind: str
    genres: tuple[str, ...] = ()


#: Well known enough that the owner has likely seen it. TV counts run lower.
PASS_MIN_COUNT_TV = 300


def pass_group(c: PassIn) -> str | None:
    """Which seed group a pass title can stand in: a series for the TV
    loves, a romance for the rom-com loves, any other film for the film
    loves. None: not well known enough."""
    if c.vote_count < (PASS_MIN_COUNT_TV if c.kind == "tv" else PASS_MIN_COUNT):
        return None
    if c.kind == "tv":
        return "tv"
    return "romcom" if "Romance" in c.genres else "film"


def seen_pass(cands: Sequence[PassIn], seed_groups: Mapping[int, str], vecs: Mapping[int, Sequence[float]],
              mean_vec: Sequence[float], *, size: int = PASS_SIZE) -> list[tuple[int, str]]:
    """About `size` well-known titles adjacent to the seeds, an equal share
    per seed group (TV, film, rom-com), interleaved. A title stands in for
    one group (pass_group); within a group, the closest to that group's seeds
    first (best cosine to any of them; ties: the lower id). Returns
    (title_id, group)."""
    mv = tuple(mean_vec)
    seeds = sorted((tid, g) for tid, g in seed_groups.items() if tid in vecs)
    groups = sorted({g for _, g in seeds})
    if not groups:
        return []
    cseed: dict[str, list[Vec]] = {g: [] for g in groups}
    for tid, g in seeds:
        cseed[g].append(sub(vecs[tid], mv))
    per = max(1, size // len(groups))
    picks: dict[str, list[int]] = {g: [] for g in groups}
    rows: dict[str, list[tuple[float, int]]] = {g: [] for g in groups}
    for cand in cands:
        g = pass_group(cand)
        if g is None or g not in rows or cand.title_id in seed_groups:
            continue
        cx = sub(cand.vec, mv)
        rows[g].append((max(cos(cx, sv) for sv in cseed[g]), cand.title_id))
    for g in groups:
        picks[g] = [tid for _, tid in sorted(rows[g], key=lambda x: (-x[0], x[1]))[:per]]
    out: list[tuple[int, str]] = []
    for i in range(per):
        for g in groups:
            if i < len(picks[g]):
                out.append((picks[g][i], g))
    return out


# ------------------------------------------------------------ text

def embed_text(genres: Sequence[str], keywords: Sequence[str], overview: str | None) -> str:
    """What a title is embedded from (owner, 2026-10-15): overview + genres +
    TMDB keywords. The name is left out: a title's name says little about
    what it is like, and two films called "Heat" are not alike."""
    parts = []
    if genres:
        parts.append("Genres: " + ", ".join(genres) + ".")
    if keywords:
        parts.append("Keywords: " + ", ".join(keywords[:20]) + ".")
    if overview:
        parts.append(overview.strip())
    return " ".join(parts)
