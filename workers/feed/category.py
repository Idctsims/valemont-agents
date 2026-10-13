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

#: The one definition both calls send. sports_business and betting tightened
#: by the owner, 2026-10-13 16:19, after the first pass put game coverage in
#: sports_business and fantasy and stock-market pieces in betting; at 16:37,
#: sports media rights and league structure moved from film_music and other
#: into sports_business, and film_music lost sports rights and broadcasting.
PROMPT = ("category: the one that fits best: deals (funding, M&A, VC, private equity, family offices, IPOs, "
          "earnings), hospitality (hotels, clubs, restaurants, real estate, development, travel), "
          "sports_business (the business of sport: ownership, stakes, valuations, athlete businesses, "
          "stadiums, sponsorship; sports media rights and broadcasting deals on any platform; league "
          "economics and structure, such as schedule length, eligibility and NIL rulings and expansion; and "
          "the business of a sport's industry; NOT games, coaching or player performance, which are other), "
          "film_music (film, TV, music, entertainment streaming, creators, talent; NOT sports rights or sports "
          "broadcasting, which are sports_business), luxury_marine (yachts, boats, luxury and lifestyle "
          "brands), betting (the betting business: sportsbooks, prediction markets, Kalshi, betting "
          "regulation; NOT fantasy sports, NOT the stock market), other.")


def valid(value: object) -> str | None:
    """The category if it is one of ours; None otherwise (never a guess)."""
    return value if isinstance(value, str) and value in CATEGORIES else None
