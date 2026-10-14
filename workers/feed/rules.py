"""The drop list, applied when a story is created and before any AI spend.

    title_regex   live: a matching title files the story as filtered.
    url_regex     live: a matching canonical URL files the story as filtered.
    seed_phrase_drop
                  NOT a filter (retired 2026-10-11, owner). gte-small is
                  anisotropic (unrelated pairs score 0.73-0.76; see
                  docs/feed_sources.md), so these phrases cannot separate
                  gossip from business news. Each story's best similarity to
                  them (from SEED_RECORD_FROM) is stored on the story for the
                  record only. Their job moved to Haiku's `drop` field
                  (feed/summarize.py). The rows stay in feed_rules, active.

Patterns are matched case-insensitively and must be valid in Python and in
Postgres alike (db/032 compiles them on insert; tests compile them here).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from core.embed import cosine

SEED_RECORD_FROM = 0.70


@dataclass(frozen=True)
class Rule:
    id: int
    kind: str
    pillar: str | None
    pattern: str


@dataclass(frozen=True)
class SeedPhrase:
    id: int
    pattern: str
    vector: tuple[float, ...]


class Rules:
    def __init__(self, rules: Sequence[Rule]) -> None:
        self._title = [(r, re.compile(r.pattern, re.I)) for r in rules if r.kind == "title_regex"]
        self._url = [(r, re.compile(r.pattern, re.I)) for r in rules if r.kind == "url_regex"]
        self.seed_rules = [r for r in rules if r.kind == "seed_phrase_drop"]

    @staticmethod
    def _applies(rule: Rule, pillars: Sequence[str]) -> bool:
        """A rule with no pillar is Home's and Sports' (db/032 meant "both"
        before Tech existed): it never touches a tech story, where "promo
        code" or "deals" can be exactly the free-credits news the owner wants
        (db/045). A tech rule names pillar 'tech'."""
        if rule.pillar is None:
            return "tech" not in pillars
        return rule.pillar in pillars

    def filter_reason(self, *, title: str, url: str | None, pillars: Sequence[str]) -> str | None:
        """'url_regex:<id>' or 'title_regex:<id>' for the first live rule that
        matches, else None (kept). URL rules first: they are the more exact."""
        if url:
            for rule, rx in self._url:
                if self._applies(rule, pillars) and rx.search(url):
                    return f"url_regex:{rule.id}"
        for rule, rx in self._title:
            if self._applies(rule, pillars) and rx.search(title):
                return f"title_regex:{rule.id}"
        return None


def best_seed(vector: Sequence[float], seeds: Sequence[SeedPhrase]) -> tuple[float, int] | None:
    """(score, rule id) of the closest seed phrase if it reaches
    SEED_RECORD_FROM, else None. Recorded, never acted on."""
    best: tuple[float, int] | None = None
    for s in seeds:
        score = cosine(vector, s.vector)
        if best is None or score > best[0]:
            best = (score, s.id)
    return best if best is not None and best[0] >= SEED_RECORD_FROM else None
