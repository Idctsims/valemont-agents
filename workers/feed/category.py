"""News categories (Chat 3 Phase 3 scope addition, owner 2026-10-12 21:56).

One `category` per Home story, for the /news chips:

    deals            Deals & Capital
    hospitality      Hospitality & Real Estate
    sports_business  Sports Business
    film_music       Film/TV & Music
    luxury_marine    Luxury & Marine
    betting          Betting & Markets
    other            anything else

Haiku decides it, never a keyword rule (owner, 2026-10-12 23:38): the
summary call returns it for new stories, and a one-off categorize pass
(feed/categorize.py) gives it to stories summarized before. Both send
PROMPT, the same definition, and validate against CATEGORIES, the same enum
db/042 CHECKs.
"""

from __future__ import annotations

CATEGORIES: tuple[str, ...] = ("deals", "hospitality", "sports_business", "film_music", "luxury_marine",
                               "betting", "other")

LABELS: dict[str, str] = {
    "deals": "Deals & Capital",
    "hospitality": "Hospitality & Real Estate",
    "sports_business": "Sports Business",
    "film_music": "Film/TV & Music",
    "luxury_marine": "Luxury & Marine",
    "betting": "Betting & Markets",
    "other": "Other",
}

#: The one definition both calls send.
PROMPT = ("category: the one that fits best: deals (funding, M&A, VC, private equity, family offices, IPOs, "
          "earnings), hospitality (hotels, clubs, restaurants, real estate, development, travel), "
          "sports_business (team ownership, valuations, media rights, stadiums, sponsorship, NIL), film_music "
          "(film, TV, streaming, music, creators, talent), luxury_marine (yachts, boats, luxury and lifestyle "
          "brands), betting (sports betting, prediction markets, Kalshi), other.")


def valid(value: object) -> str | None:
    """The category if it is one of ours; None otherwise (never a guess)."""
    return value if isinstance(value, str) and value in CATEGORIES else None
