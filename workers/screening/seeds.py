"""The Screening Room's seeds (owner, 2026-10-15; Chat 4 Phase 4).

Fifteen titles the owner has seen and loved, verified on TMDB on 2026-10-15
(search/multi, exact name and year), and four taste notes. db/053 writes the
same list (titles, screening_seeds, a seen-and-loved reaction each) and
app_settings.seed_interests.feed.screening.love (the notes);
tests/test_screening_recs.py fails if the two ever differ.

Seeds shape the profile and are never recommended (they are seen).
"""

from __future__ import annotations

from typing import Literal

Group = Literal["tv", "film", "romcom"]

#: (tmdb_id, kind, name, group)
SEEDS: tuple[tuple[int, str, str, Group], ...] = (
    (76331, "tv", "Succession", "tv"),
    (62852, "tv", "Billions", "tv"),
    (69740, "tv", "Ozark", "tv"),
    (73586, "tv", "Yellowstone", "tv"),
    (236235, "tv", "The Gentlemen", "tv"),
    (153312, "tv", "Tulsa King", "tv"),
    (106646, "movie", "The Wolf of Wall Street", "film"),
    (50839, "movie", "Margin Call", "film"),
    (4566, "movie", "Michael Clayton", "film"),
    (949, "movie", "Heat", "film"),
    (238, "movie", "The Godfather", "film"),
    (473033, "movie", "Uncut Gems", "film"),
    (8488, "movie", "Hitch", "romcom"),
    (455207, "movie", "Crazy Rich Asians", "romcom"),
    (14736, "movie", "Love & Basketball", "romcom"),
)

#: The owner's taste notes, embedded as love phrases (feed engine style).
#: The fourth note, "movies as much as TV", is not a topic: it is a rule,
#: recs.balance (For you keeps at least MIN_EACH_KIND of each).
NOTES: tuple[str, ...] = (
    "power, money and dynasty dramas",
    "crime and business thrillers",
    "romantic comedies",
)
