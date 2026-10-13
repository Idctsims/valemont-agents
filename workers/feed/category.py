"""News categories (Chat 3 Phase 3 scope addition, owner 2026-10-12 21:56).

One `category` per Home story, for the /news chips:

    deals            Deals & Capital
    hospitality      Hospitality & Real Estate
    sports_business  Sports Business
    film_music       Film/TV & Music
    luxury_marine    Luxury & Marine
    betting          Betting & Markets
    other            anything else

New stories get it from the summary call (one enum field). Stories summarized
before that get it from their Haiku topics by the deterministic mapping
below (scripts/feed_backfill_category.py), never from a guess:

  * a rule is a run of snake_case segments; it matches a topic when those
    segments appear in the topic, in order and adjacent. A segment matches
    the same word or its plural ("yacht" matches yacht_launch and yachts);
    one ending in "*" matches any word it begins ("superyacht*" matches
    superyachts and superyachting). So "nil" never matches nile or vanilla,
    and "watch" is not a rule (how_to_watch).
  * each category scores the number of the story's topics it matches;
  * the highest score wins; a tie goes to the more specific category, in
    TIE_ORDER (Deals, the broadest, last);
  * no match: other. No topics at all: no category (the summary call will
    give it one).
"""

from __future__ import annotations

from typing import Iterable, Sequence

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

#: Ties go to the earlier category: the specific ones before Deals.
TIE_ORDER: tuple[str, ...] = ("betting", "sports_business", "luxury_marine", "hospitality", "film_music", "deals")

_RULES: dict[str, tuple[str, ...]] = {
    "betting": (
        "prediction_market", "betting", "bet", "sportsbook", "sports_betting", "gambling", "kalshi", "polymarket",
        "wager", "igaming", "casino",
    ),
    "sports_business": (
        "media_rights", "broadcast_rights", "franchise_valuation", "team_valuation", "valuation_ranking",
        "team_ownership", "ownership_stake", "minority_stake", "team_sale", "stadium", "stadium_financing",
        "arena_financing", "sports_media", "sponsorship", "nil", "front_office", "league_business",
        "athlete_business", "sports_business", "naming_rights",
    ),
    "luxury_marine": (
        "yacht*", "superyacht*", "boat*", "marine", "marina", "shipbuild*", "refit", "luxury_brand",
        "luxury_goods", "luxury_fashion", "fashion", "watches", "watchmaking", "luxury_watch", "jewel*",
        "luxury_car", "supercar", "lifestyle_brand", "automotive",
    ),
    "hospitality": (
        "hotel*", "hospitality", "resort", "real_estate", "commercial_real_estate", "condo*", "zoning",
        "land_assemblage", "real_estate_development", "property", "restaurant", "members_club", "beach_club",
        "nightlife", "luxury_travel", "travel", "foreclosure", "construction", "permitting", "waterfront",
    ),
    "film_music": (
        "film*", "movie", "box_office", "televis*", "tv", "streaming", "music*", "album", "documentar*",
        "festival", "casting", "scripted", "unscripted", "anime", "cinema", "creator", "creator_economy",
        "podcast", "hip_hop", "concert", "live_music", "format_sales", "co_production", "international_sales",
        "licensing", "content_production", "programming", "award", "biopic", "actor", "talent", "mipcom",
        "entertainment", "studio", "record_label", "artist_management", "song",
    ),
    "deals": (
        "m_and_a", "acquisition", "merger", "funding", "venture", "venture_capital", "private_equity", "ipo",
        "fundraise", "fundraising", "fund", "family_office", "startup", "invest*", "debt", "loan", "market",
        "stock", "earning", "deal", "valuation", "spac", "buyout", "capital", "financing", "bankruptcy",
    ),
}
#: Parsed once: category -> rules, each a tuple of segments.
_PARSED: dict[str, tuple[tuple[str, ...], ...]] = {
    c: tuple(tuple(r.split("_")) for r in rules) for c, rules in _RULES.items()
}


def _seg(rule_seg: str, seg: str) -> bool:
    if rule_seg.endswith("*"):
        return seg.startswith(rule_seg[:-1])
    return seg == rule_seg or seg == rule_seg + "s"


def _matches(rule: Sequence[str], segs: Sequence[str]) -> bool:
    n = len(rule)
    return any(all(_seg(rule[k], segs[i + k]) for k in range(n)) for i in range(len(segs) - n + 1))


def category_of_topic(topic: str) -> set[str]:
    segs = [s for s in topic.lower().split("_") if s]
    return {c for c, rules in _PARSED.items() if any(_matches(r, segs) for r in rules)}


def from_topics(topics: Iterable[str]) -> str | None:
    """The category for these topics, or None when there are no topics."""
    topics = [t for t in topics if t and t.strip()]
    if not topics:
        return None
    score = {c: 0 for c in TIE_ORDER}
    for t in topics:
        for c in category_of_topic(t):
            score[c] += 1
    best = max(score.values())
    if best == 0:
        return "other"
    return next(c for c in TIE_ORDER if score[c] == best)
