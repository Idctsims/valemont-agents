"""feed_profile and feed_score (Chat 3 Phase 2; docs/feed_ranking.md).

feed_profile   daily 04:00 America/Chicago, and at boot when no profile was
               built today. Per pillar: the 7-day mean vector (database-side),
               the seed phrases embedded fresh, the reaction signals, then a
               new interest_profiles row (feed/rank.build_profile). Then EVERY
               kept story of the last WINDOW_H hours is rescored against the
               new profiles (one story_scores row per placement).
feed_score     chained after every feed_summarize: stories that finished
               summarizing since the last pass and have no score yet are
               scored against the latest profiles. Nothing to score, or no
               profile yet, is not a failure.

Both write only through core/ledger.py. scripts/feed_profile.py runs the
rebuild by hand.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from core import embed as embedder
from core import ledger, memory
from core.jobs import JobSkipped
from feed import rank
from feed.summarize import MAX_ATTEMPTS

log = logging.getLogger("valemont.feed.profile")

Embed = Callable[[Sequence[str]], list[list[float]]]


def follows_from(rows: Sequence[ledger.FollowRow]) -> list[rank.Follow]:
    return [rank.Follow(r.name, r.tier, r.pillar, r.floor, r.aliases, r.sport, r.sport_wide) for r in rows]  # type: ignore[arg-type]


def story_from(r: ledger.RankStory) -> rank.StoryIn:
    return rank.StoryIn(r.id, r.pillars, r.sport, r.title, r.entities, r.topics, r.source_slug, r.vec)


def profile_from(r: ledger.ProfileRow) -> rank.Profile:
    return rank.Profile(r.pillar, r.mean, r.mean_n, r.love, r.ni, r.topic_weights,  # type: ignore[arg-type]
                        r.source_weights, r.entity_weights, r.love_count, r.ni_count, r.open_count,
                        r.neutral_count, r.seed_weight_love, r.seed_weight_ni)


@dataclass
class PillarStats:
    pillar: str
    mean_n: int = 0
    love: int = 0
    ni: int = 0
    open: int = 0
    neutral: int = 0
    seed_weight_love: float = 0.0
    seed_weight_ni: float = 0.0
    built: bool = False


@dataclass
class RebuildStats:
    pillars: list[PillarStats] = field(default_factory=list)
    scored: int = 0
    stories: int = 0

    def line(self) -> str:
        parts = []
        for p in self.pillars:
            if not p.built:
                parts.append(f"{p.pillar}: no stories in the window, no profile")
                continue
            parts.append(f"{p.pillar}: mean over {p.mean_n}, love {p.love}, not interested {p.ni}, "
                         f"opened {p.open}, neutral {p.neutral}, seed weight love "
                         f"{p.seed_weight_love:.2f} / NI {p.seed_weight_ni:.2f}")
        return "; ".join(parts) + f"; {self.scored} scores for {self.stories} stories"


def build_profiles(*, embed: Embed = embedder.embed) -> tuple[dict[str, rank.Profile], dict[str, int], RebuildStats]:
    """A new profile per pillar that has stories in the mean window. Returns
    (profiles, their ids, stats)."""
    seeds = ledger.feed_seed_phrases()
    phrases = [(p, kind, text) for p in rank.PILLARS for kind in ("love", "not_interested")
               for text in seeds.get(p, {}).get(kind, [])]
    vectors = embed([t for _, _, t in phrases]) if phrases else []
    seed_vecs: dict[tuple[str, str], list[rank.Vec]] = {}
    for (p, kind, _), v in zip(phrases, vectors):
        seed_vecs.setdefault((p, kind), []).append(tuple(v))

    profiles: dict[str, rank.Profile] = {}
    ids: dict[str, int] = {}
    stats = RebuildStats()
    for pillar in rank.PILLARS:
        ps = PillarStats(pillar)
        stats.pillars.append(ps)
        mean_vec, n = ledger.feed_mean_vector(pillar, rank.MEAN_WINDOW_D)
        if mean_vec is None or n == 0:
            log.warning("feed profile: no %s stories in %d days; no profile built", pillar, rank.MEAN_WINDOW_D)
            continue
        signals = []
        for s in ledger.feed_reaction_signals(pillar):
            kind = rank.signal_kind(s.reaction, s.opened)
            if kind is not None and s.story.vec:
                signals.append(rank.Signal(story_from(s.story), kind))
        prof = rank.build_profile(pillar, mean_vec, n, seed_vecs.get((pillar, "love"), []),
                                  seed_vecs.get((pillar, "not_interested"), []), signals)
        ids[pillar] = ledger.feed_insert_profile(prof)
        profiles[pillar] = prof
        ps.mean_n, ps.love, ps.ni, ps.open, ps.neutral = n, prof.love_count, prof.ni_count, prof.open_count, prof.neutral_count
        ps.seed_weight_love, ps.seed_weight_ni, ps.built = prof.seed_weight_love, prof.seed_weight_ni, True
    return profiles, ids, stats


def score_stories(stories: Sequence[ledger.RankStory], profiles: Mapping[str, rank.Profile],
                  profile_ids: Mapping[str, int], follows: Sequence[rank.Follow]) -> int:
    scores = rank.score_all([story_from(s) for s in stories if s.vec], profiles, follows)
    return ledger.feed_insert_scores(scores, profile_ids)


def rebuild(*, embed: Embed = embedder.embed) -> RebuildStats:
    """Profiles for every pillar, then a rescore of the window. The window's
    vectors are the job's biggest structure: dropped, and the memory handed
    back, when it ends (core/memory.py; memory audit, 2026-10-13)."""
    try:
        follows = follows_from(ledger.feed_follows())
        profiles, ids, stats = build_profiles(embed=embed)
        stories = ledger.feed_stories_to_score(rank.WINDOW_H, MAX_ATTEMPTS, unscored=False)
        stats.stories = len(stories)
        stats.scored = score_stories(stories, profiles, ids, follows) if profiles else 0
        del stories
        log.info("feed profile: %s", stats.line())
        return stats
    finally:
        memory.release()


def profile_job(*, embed: Embed = embedder.embed) -> RebuildStats:
    """The feed_profile system job, 04:00 nightly and at boot: skipped when a
    profile was already built today (owner's local date, database clock), so
    a redeploy never rebuilds twice a day, and a 04:00 run after an
    early-morning boot finds today's profile already fresh. The script
    (scripts/feed_profile.py --rebuild) always rebuilds."""
    if ledger.feed_profile_built_today():
        raise JobSkipped("a profile was already built today")
    return rebuild(embed=embed)


def score_new_job() -> int:
    """The feed_score system job: score stories with no score row yet."""
    latest = ledger.feed_latest_profiles()
    if not latest:
        raise JobSkipped("no interest profile yet; feed_profile builds the first")
    profiles = {p: profile_from(r) for p, r in latest.items()}
    ids = {p: r.id for p, r in latest.items()}
    try:
        stories = ledger.feed_stories_to_score(rank.WINDOW_H, MAX_ATTEMPTS, unscored=True)
        if not stories:
            return 0
        n = score_stories(stories, profiles, ids, follows_from(ledger.feed_follows()))
        log.info("feed score: %d scores for %d new stories", n, len(stories))
        return n
    finally:
        memory.release()
