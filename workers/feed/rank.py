"""The feed's ranking math (Chat 3 Phase 2; docs/feed_ranking.md §2).

Pure functions, no I/O: feed/profile.py feeds them from the ledger, and
tests/test_feed_rank.py drives them with stubbed vectors.

    centered    c(x) = x - mean_vec[pillar]   (gte-small is anisotropic)
    profiles    love = s*S_love + (1-s)*R_love, s = K_SEED / (K_SEED + n_love)
                ni   = s*S_ni   + (1-s)*R_ni,   s = K_SEED / (K_SEED + n_ni)
                S = unit mean of centered seed phrases, R = unit weighted mean
                of centered reacted stories (love 1.0, open 0.3, NI 1.0)
    affinity    cos(c(story), love) - cos(c(story), ni)
    base        W_AFF*affinity + follow boost + source + topic + entity
                (stored as story_scores.score; time-free)
    rank        base - ln 2 * age_h / HALF_LIFE_H   (at read time, db/039
                feed_for_you; order_for_you below is its twin)

The constants of the read path (HALF_LIFE_H, WINDOW_H, FLOOR_*, CROSS_CAP,
PAGE_SIZE) are literals in db/039's feed_for_you as well; a test pins the SQL
text to these values.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Literal, Mapping, Sequence

Vec = tuple[float, ...]
Pillar = Literal["home", "sports"]
PILLARS: tuple[Pillar, ...] = ("home", "sports")

# ---------------------------------------------------------------- the weights
K_SEED = 12
W_AFF = 4.0
TIER_BOOST: Mapping[int, float] = {1: 1.00, 2: 0.50, 3: 0.15}
B_SOURCE = 0.30
B_TOPIC = 0.40
B_ENTITY = 0.30
M_PRIOR = 5.0
W_LOVE = 1.0
W_OPEN = 0.3
W_NI = 1.0
#: story_scores.score CHECK (db/039). A real base stays within about +-10.
SCORE_BOUND = 50.0
MEAN_WINDOW_D = 7

# ------------------------------------------- the read path (db/039 literals)
HALF_LIFE_H = 18.0
WINDOW_H = 72
FLOOR_MAX = 3
FLOOR_POSITION = 6
FLOOR_MAX_AGE_H = 48.0
CROSS_CAP = 5
PAGE_SIZE = 30


# ---------------------------------------------------------------- vector math

def sub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return tuple(x - y for x, y in zip(a, b))


def norm(a: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def unit(a: Sequence[float]) -> Vec | None:
    """Unit length, or None for a zero vector (no direction, no signal)."""
    n = norm(a)
    return tuple(x / n for x in a) if n > 1e-12 else None


def mean(vectors: Sequence[Sequence[float]], weights: Sequence[float] | None = None) -> Vec:
    if not vectors:
        raise ValueError("mean of no vectors")
    ws = list(weights) if weights is not None else [1.0] * len(vectors)
    total = sum(ws)
    dims = len(vectors[0])
    return tuple(sum(w * v[i] for v, w in zip(vectors, ws)) / total for i in range(dims))


def cos(a: Sequence[float], b: Sequence[float]) -> float:
    na, nb = norm(a), norm(b)
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na > 1e-12 and nb > 1e-12 else 0.0


def blend(seed: Vec | None, reacted: Vec | None, seed_weight: float) -> Vec | None:
    """seed_weight * seed + (1 - seed_weight) * reacted; either part alone
    when the other is missing; None when both are."""
    if seed is None:
        return reacted
    if reacted is None:
        return seed
    return tuple(seed_weight * s + (1 - seed_weight) * r for s, r in zip(seed, reacted))


# -------------------------------------------------------------------- follows

@dataclass(frozen=True)
class Follow:
    name: str
    tier: int
    #: None = both pillars.
    pillar: Pillar | None
    floor: bool
    #: Lowercase, matched as whole words.
    aliases: tuple[str, ...]
    #: Set: the story's sport must equal it.
    sport: str | None
    #: Matches every story of its sport, no alias needed.
    sport_wide: bool = False


_ALIAS_CACHE: dict[str, re.Pattern[str]] = {}


def _alias(alias: str) -> re.Pattern[str]:
    pat = _ALIAS_CACHE.get(alias)
    if pat is None:
        pat = _ALIAS_CACHE[alias] = re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])")
    return pat


def follow_matches(f: Follow, title: str, entities: Sequence[str], sport: str | None) -> bool:
    if f.sport is not None and sport != f.sport:
        return False
    if f.sport_wide:
        return True
    text = " | ".join([title, *entities]).lower()
    return any(_alias(a).search(text) for a in f.aliases)


@dataclass(frozen=True)
class FollowMatch:
    tier: int | None
    floor: bool
    names: tuple[str, ...]


def match_follows(pillar: Pillar, follows: Iterable[Follow], title: str, entities: Sequence[str],
                  sport: str | None) -> FollowMatch:
    """The follows of this pillar (or of both) the story matches: the best
    tier (lowest number), whether any is a floor follow, and their names."""
    hits = [f for f in follows if (f.pillar is None or f.pillar == pillar)
            and follow_matches(f, title, entities, sport)]
    if not hits:
        return FollowMatch(None, False, ())
    return FollowMatch(min(f.tier for f in hits), any(f.floor for f in hits),
                       tuple(sorted({f.name for f in hits})))


# -------------------------------------------------------------------- stories

@dataclass(frozen=True)
class StoryIn:
    id: int
    pillars: tuple[str, ...]
    sport: str | None
    title: str
    entities: tuple[str, ...]
    topics: tuple[str, ...]
    source_slug: str | None
    #: stories.centroid: the mean of its items' title + teaser embeddings.
    vec: Vec


def placements(story: StoryIn, follows: Sequence[Follow]) -> list[tuple[Pillar, bool]]:
    """(pillar, cross_pillar) pairs to score the story in: every pillar it
    belongs to, plus any other pillar where it matches a tier-1 follow
    (owner decision 1: tier-1 sports stories on Home)."""
    out: list[tuple[Pillar, bool]] = [(p, False) for p in PILLARS if p in story.pillars]
    for p in PILLARS:
        if p not in story.pillars and match_follows(p, follows, story.title, story.entities, story.sport).tier == 1:
            out.append((p, True))
    return out


# ------------------------------------------------------------------- profiles

SignalKind = Literal["love", "ni", "open", "neutral"]


def signal_kind(reaction: str | None, opened: bool) -> SignalKind | None:
    """What a story's latest reaction (v_feed_reaction_latest) says. An open
    counts only when there is no love / NI / neutral; undo is no reaction."""
    if reaction == "love":
        return "love"
    if reaction == "not_interested":
        return "ni"
    if reaction == "neutral":
        return "neutral"
    return "open" if opened else None


@dataclass(frozen=True)
class Signal:
    story: StoryIn
    kind: SignalKind


@dataclass(frozen=True)
class Profile:
    pillar: Pillar
    mean: Vec
    mean_n: int
    love: Vec | None
    ni: Vec | None
    topic_weights: Mapping[str, float]
    source_weights: Mapping[str, float]
    entity_weights: Mapping[str, float]
    love_count: int
    ni_count: int
    open_count: int
    neutral_count: int
    seed_weight_love: float
    seed_weight_ni: float

    @property
    def reaction_count(self) -> int:
        return self.love_count + self.ni_count


def learned(love: float, ni: float, bound: float) -> float:
    """bound * (L - N) / (L + N + M_PRIOR): bounded in (-bound, bound), shrunk
    toward 0 until evidence builds up."""
    return bound * (love - ni) / (love + ni + M_PRIOR)


def learned_weights(signals: Sequence[Signal], keys: Callable[[StoryIn], Iterable[str]],
                    bound: float) -> dict[str, float]:
    totals: dict[str, list[float]] = {}
    for s in signals:
        if s.kind == "neutral":
            continue
        w = {"love": W_LOVE, "open": W_OPEN, "ni": W_NI}[s.kind]
        for k in dict.fromkeys(keys(s.story)):
            t = totals.setdefault(k, [0.0, 0.0])
            t[1 if s.kind == "ni" else 0] += w
    return {k: round(learned(l, n, bound), 4) for k, (l, n) in sorted(totals.items())}


def _source_keys(s: StoryIn) -> list[str]:
    return [s.source_slug] if s.source_slug else []


def _entity_keys(s: StoryIn) -> list[str]:
    return [e.strip().lower() for e in s.entities if e.strip()]


def build_profile(pillar: Pillar, mean_vec: Vec, mean_n: int, love_seeds: Sequence[Vec],
                  ni_seeds: Sequence[Vec], signals: Sequence[Signal]) -> Profile:
    def c(v: Sequence[float]) -> Vec:
        return sub(v, mean_vec)

    loves = [s for s in signals if s.kind == "love"]
    opens = [s for s in signals if s.kind == "open"]
    nis = [s for s in signals if s.kind == "ni"]
    neutral = sum(1 for s in signals if s.kind == "neutral")

    s_love = unit(mean([c(v) for v in love_seeds])) if love_seeds else None
    s_ni = unit(mean([c(v) for v in ni_seeds])) if ni_seeds else None
    love_part = loves + opens
    r_love = (unit(mean([c(s.story.vec) for s in love_part],
                        [W_LOVE] * len(loves) + [W_OPEN] * len(opens))) if love_part else None)
    r_ni = unit(mean([c(s.story.vec) for s in nis])) if nis else None

    w_love = K_SEED / (K_SEED + len(loves)) if s_love is not None else 0.0
    w_ni = K_SEED / (K_SEED + len(nis)) if s_ni is not None else 0.0
    return Profile(
        pillar=pillar, mean=mean_vec, mean_n=mean_n,
        love=blend(s_love, r_love, w_love), ni=blend(s_ni, r_ni, w_ni),
        topic_weights=learned_weights(signals, lambda s: s.topics, B_TOPIC),
        source_weights=learned_weights(signals, _source_keys, B_SOURCE),
        entity_weights=learned_weights(signals, _entity_keys, B_ENTITY),
        love_count=len(loves), ni_count=len(nis), open_count=len(opens), neutral_count=neutral,
        seed_weight_love=w_love, seed_weight_ni=w_ni,
    )


# -------------------------------------------------------------------- scoring

@dataclass(frozen=True)
class Score:
    story_id: int
    pillar: Pillar
    score: float
    components: Mapping[str, object]
    follow_tier: int | None
    floor: bool
    cross_pillar: bool


def _mean_known(weights: Mapping[str, float], keys: Iterable[str]) -> float:
    vals = [weights[k] for k in dict.fromkeys(keys) if k in weights]
    return sum(vals) / len(vals) if vals else 0.0


def score_story(story: StoryIn, pillar: Pillar, profile: Profile, follows: Sequence[Follow],
                cross_pillar: bool = False) -> Score:
    cv = sub(story.vec, profile.mean)
    love_cos = cos(cv, profile.love) if profile.love is not None else 0.0
    ni_cos = cos(cv, profile.ni) if profile.ni is not None else 0.0
    affinity = love_cos - ni_cos
    m = match_follows(pillar, follows, story.title, story.entities, story.sport)
    follow = TIER_BOOST[m.tier] if m.tier is not None else 0.0
    source = profile.source_weights.get(story.source_slug, 0.0) if story.source_slug else 0.0
    topic = _mean_known(profile.topic_weights, story.topics)
    entity = _mean_known(profile.entity_weights, _entity_keys(story))
    base = W_AFF * affinity + follow + source + topic + entity
    base = max(-SCORE_BOUND, min(SCORE_BOUND, base))
    components = {
        "affinity": round(affinity, 4), "love_cos": round(love_cos, 4), "ni_cos": round(ni_cos, 4),
        "follow": follow, "follows": list(m.names), "source": round(source, 4),
        "topic": round(topic, 4), "entity": round(entity, 4),
    }
    return Score(story.id, pillar, round(base, 4), components, m.tier, m.floor, cross_pillar)


def score_all(stories: Sequence[StoryIn], profiles: Mapping[str, Profile],
              follows: Sequence[Follow]) -> list[Score]:
    """Every placement of every story that has a profile for its pillar."""
    out = []
    for st in stories:
        for pillar, cross in placements(st, follows):
            prof = profiles.get(pillar)
            if prof is not None:
                out.append(score_story(st, pillar, prof, follows, cross))
    return out


# ------------------------------------------------------- the read path's twin

@dataclass(frozen=True)
class Candidate:
    id: int
    score: float
    age_h: float
    floor: bool = False
    cross_pillar: bool = False


@dataclass(frozen=True)
class Placed:
    id: int
    pos: int
    rank: float
    floored: bool


def rank_of(score: float, age_h: float) -> float:
    return score - math.log(2.0) * max(age_h, 0.0) / HALF_LIFE_H


def order_for_you(cands: Sequence[Candidate]) -> list[Placed]:
    """db/039 feed_for_you's ordering, step for step, for candidates already
    filtered (window, pillar, reactions). tests_live pins the two together."""
    r = {c.id: rank_of(c.score, c.age_h) for c in cands}

    def key(cid: int, value: float) -> tuple[float, int]:
        return (-value, -cid)

    floor_ids = [c.id for c in sorted((c for c in cands if c.floor and c.age_h < FLOOR_MAX_AGE_H),
                                      key=lambda c: key(c.id, r[c.id]))][:FLOOR_MAX]
    others = sorted((c for c in cands if not c.floor), key=lambda c: key(c.id, r[c.id]))
    ref = r[others[FLOOR_POSITION - 1].id] if len(others) >= FLOOR_POSITION else None
    f = set(floor_ids)
    e: dict[int, float] = {}
    lifted: set[int] = set()
    for c in cands:
        if c.id in f and ref is not None and r[c.id] < ref:
            e[c.id] = ref + 1e-6 / (1.0 + (ref - r[c.id]))
            lifted.add(c.id)
        else:
            e[c.id] = r[c.id]
    cross_f = [c.id for c in cands if c.cross_pillar and c.id in f]
    cross_rest = sorted((c for c in cands if c.cross_pillar and c.id not in f), key=lambda c: key(c.id, e[c.id]))
    allowed = set(cross_f) | {c.id for c in cross_rest[:max(CROSS_CAP - len(cross_f), 0)]}
    eligible = sorted((c for c in cands if not c.cross_pillar or c.id in allowed),
                      key=lambda c: key(c.id, e[c.id]))
    page1 = {c.id for c in eligible[:PAGE_SIZE]}
    ordered = sorted(cands, key=lambda c: (c.id not in page1, -e[c.id], -c.id))
    return [Placed(c.id, i + 1, e[c.id], c.id in lifted) for i, c in enumerate(ordered)]
