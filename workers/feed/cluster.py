"""Story assignment, level 2 of the dedupe (level 1, exact, is the database's
UNIQUE constraints). Pure: candidates in, a decision out.

An item joins the best candidate story (last seen within CANDIDATE_HOURS)
when EITHER
    cosine(item embedding, story centroid) >= JOIN_COSINE      (0.86)
or  rapidfuzz token_set_ratio(normalized titles) >= JOIN_FUZZ  (90)
otherwise it founds a new story. Both thresholds are the owner's
(2026-10-11) and are calibrated from feed_similarity_log: every join and
every near miss (NEAR_MISS_FROM <= cosine < JOIN_COSINE) is recorded with
both scores.

The lead item is the one from the lowest source priority number, ties to the
earliest published, then the lowest id; an item with no url (a failed Google
News decode) can never lead. The centroid is the running mean of member
embeddings.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from rapidfuzz import fuzz

from core.embed import cosine

JOIN_COSINE = 0.86
JOIN_FUZZ = 90.0
NEAR_MISS_FROM = 0.78
CANDIDATE_HOURS = 48

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return _WS.sub(" ", _PUNCT.sub(" ", t.lower())).strip()


@dataclass(frozen=True)
class Candidate:
    story_id: int
    title: str
    centroid: tuple[float, ...]
    item_count: int


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
    #: Candidates not joined whose cosine is in [NEAR_MISS_FROM, JOIN_COSINE).
    near_misses: tuple[Score, ...] = field(default=())


def assign(vector: Sequence[float], title: str, candidates: Sequence[Candidate]) -> Decision:
    norm = normalize_title(title)
    scores = [Score(c.story_id, cosine(vector, c.centroid),
                    float(fuzz.token_set_ratio(norm, normalize_title(c.title))))
              for c in candidates]
    if not scores:
        return Decision(None, None, None)
    by_cos = max(scores, key=lambda s: (s.cosine, s.fuzz))
    by_fuzz = max(scores, key=lambda s: (s.fuzz, s.cosine))
    chosen: Score | None = None
    joined_by: str | None = None
    if by_cos.cosine >= JOIN_COSINE:
        chosen = by_cos
        joined_by = "both" if by_cos.fuzz >= JOIN_FUZZ else "cosine"
    elif by_fuzz.fuzz >= JOIN_FUZZ:
        chosen = by_fuzz
        joined_by = "fuzz"
    near = tuple(sorted((s for s in scores
                         if NEAR_MISS_FROM <= s.cosine < JOIN_COSINE
                         and (chosen is None or s.story_id != chosen.story_id)),
                        key=lambda s: -s.cosine))
    return Decision(chosen.story_id if chosen else None, joined_by, chosen or by_cos, near)


def running_mean(centroid: Sequence[float], count: int, vector: Sequence[float]) -> list[float]:
    """The mean of `count` vectors whose mean is `centroid`, plus `vector`."""
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
