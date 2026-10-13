"""Story assignment, level 2 of the dedupe (level 1, exact, is the database's
UNIQUE constraints). Pure: candidates in, a decision out.

THE RULE (owner, 2026-10-11, from the first ingest's calibration; see
docs/feed_sources.md "Story join calibration"). An item joins a candidate
story when, against its BEST-matching member item (title-only embeddings):

    cosine >= JOIN_COSINE (0.94)
or  rapidfuzz token_set_ratio(normalized titles) >= JOIN_FUZZ (90)
    AND cosine >= FUZZ_MIN_COSINE (0.90)

Members, not a centroid. The first rule compared with a running-mean
centroid at 0.86, and the centroid drifted toward a generic topic direction
until one story held 230 unrelated items: gte-small scores ordinary
same-sport pairs at 0.86-0.93. A member match cannot drift.

Guards:
  * candidates are stories FIRST seen within CANDIDATE_HOURS (48): a story
    cannot keep absorbing items for days through a chain of near neighbours
    (A~B~C with A unlike C). The window is applied in SQL
    (ledger.FEED_CANDIDATES_SQL).
  * MAX_ITEMS (25) per story: an item that would join a full story starts a
    new one instead, logged as a cap_overflow.

Every join, every near miss (NEAR_MISS_FROM <= cosine < JOIN_COSINE, not
joined) and every overflow is logged in feed_similarity_log with both scores.

The lead item is the one from the lowest source priority number, ties to the
earliest published, then the lowest id; an item with no url (a failed Google
News decode) can never lead (ledger.feed_join_story).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from rapidfuzz import fuzz

from core.embed import cosine

JOIN_COSINE = 0.94
JOIN_FUZZ = 90.0
FUZZ_MIN_COSINE = 0.90
NEAR_MISS_FROM = 0.88
CANDIDATE_HOURS = 48
MAX_ITEMS = 25

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return _WS.sub(" ", _PUNCT.sub(" ", t.lower())).strip()


@dataclass(frozen=True)
class Member:
    title: str
    #: Title-only embedding (float32 array('f') in a run; any float sequence).
    vector: Sequence[float]


@dataclass(frozen=True)
class Candidate:
    story_id: int
    item_count: int
    members: tuple[Member, ...]


@dataclass(frozen=True)
class Score:
    story_id: int
    cosine: float
    fuzz: float


@dataclass(frozen=True)
class Decision:
    #: None: found a new story.
    story_id: int | None
    joined_by: str | None
    best: Score | None
    #: Candidates not joined whose best cosine is in [NEAR_MISS_FROM, JOIN_COSINE).
    near_misses: tuple[Score, ...] = field(default=())
    #: A story the item matched but could not join because it was full.
    overflow: Score | None = None


def best_member(vector: Sequence[float], norm_title: str, cand: Candidate) -> Score:
    """The candidate's best-matching member: highest cosine; its fuzz."""
    best_cos, best_title = -1.0, ""
    for m in cand.members:
        c = cosine(vector, m.vector)
        if c > best_cos:
            best_cos, best_title = c, m.title
    return Score(cand.story_id, best_cos, float(fuzz.token_set_ratio(norm_title, normalize_title(best_title))))


def joins(s: Score) -> str | None:
    if s.cosine >= JOIN_COSINE:
        return "both" if s.fuzz >= JOIN_FUZZ else "cosine"
    if s.fuzz >= JOIN_FUZZ and s.cosine >= FUZZ_MIN_COSINE:
        return "fuzz"
    return None


def assign(vector: Sequence[float], title: str, candidates: Sequence[Candidate]) -> Decision:
    norm = normalize_title(title)
    scores = [best_member(vector, norm, c) for c in candidates if c.members]
    if not scores:
        return Decision(None, None, None)
    counts = {c.story_id: c.item_count for c in candidates}
    eligible = sorted((s for s in scores if joins(s)), key=lambda s: (-s.cosine, -s.fuzz))
    chosen: Score | None = None
    overflow: Score | None = None
    for s in eligible:
        if counts.get(s.story_id, 0) >= MAX_ITEMS:
            overflow = overflow or s
            continue
        chosen = s
        break
    best = chosen or max(scores, key=lambda s: (s.cosine, s.fuzz))
    near = tuple(sorted((s for s in scores
                         if NEAR_MISS_FROM <= s.cosine < JOIN_COSINE and not joins(s)
                         and (chosen is None or s.story_id != chosen.story_id)),
                        key=lambda s: -s.cosine))
    return Decision(chosen.story_id if chosen else None, joins(chosen) if chosen else None,
                    best, near, overflow if chosen is None else None)


def running_mean(centroid: Sequence[float], count: int, vector: Sequence[float]) -> list[float]:
    """The mean of `count` vectors whose mean is `centroid`, plus `vector`.
    Kept for stories.centroid (title + teaser, Phase 2's ranking); never
    used to decide a join."""
    return [(c * count + v) / (count + 1) for c, v in zip(centroid, vector)]


@dataclass(frozen=True)
class LeadKey:
    """Sorts so the lead is the minimum."""
    priority: int
    published_at: datetime
    item_id: int


def better_lead(new: LeadKey, current: LeadKey | None) -> bool:
    if current is None:
        return True
    return (new.priority, new.published_at, new.item_id) < (current.priority, current.published_at,
                                                            current.item_id)
