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

from typing import AbstractSet, Sequence

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


#: The owner's one yes/no question (2026-10-13, final round; it replaced a
#: narrower money-only question). Both calls ask it of every story and
#: return it as `money`.
MONEY_QUESTION = ("Is this primarily about the BUSINESS of sports: money, ownership, deals, media rights and "
                  "broadcasting, audiences and ratings, valuations, or how a league or sport is structured and "
                  "governed (schedules, eligibility, NIL, expansion)? Games, coaching, player performance and "
                  "fantasy are no.")

MONEY_PROMPT = (f"money: true or false: {MONEY_QUESTION} For a story about sport, this answer decides "
                "between sports_business (true) and other (false).")


def sports_entity(entities: Sequence[str], names: AbstractSet[str]) -> bool:
    """True when one of the story's entities (as the summary named them) IS a
    sports league, team, program or tour from the entities table: the name
    or an alias, whole string, case-insensitive. Exact, not a substring:
    F1 team aliases are common words ("williams", "mercedes", "audi")."""
    return any(" ".join(e.lower().split()) in names for e in entities)


def decide(category: str | None, money: object, sport: str | None, sports_ent: bool = False) -> str | None:
    """The category a story gets. The business-of-sports answer decides
    (true -> sports_business, false -> other, the owner's rule) for a story
    with a sport, one answered sports_business, or one answered film_music
    or other whose entities include a sports league, team, program or tour
    (`sports_ent`). Betting keeps its own chip (sportsbooks, prediction
    markets, Kalshi). Anything else keeps the enum answer. None, never a
    guess, when the category is not ours or a decided story has no boolean
    answer."""
    if category is None:
        return None
    gated = bool(sport) or category == "sports_business" or (sports_ent and category in ("film_music", "other"))
    if category == "betting" or not gated:
        return category
    if not isinstance(money, bool):
        return None
    return "sports_business" if money else "other"
