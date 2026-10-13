"""News categories (feed/category.py; Chat 3 Phase 3 scope addition): the
deterministic topics -> category mapping the backfill uses, on the real topics
the owner reviewed (2026-10-13 dry run), its word-boundary rules, its tie
order, and the enum shared with db/042 and the summary call.

Pure: no database, no network, no Claude.
"""

from __future__ import annotations

import re
import unittest

from core.paths import REPO_ROOT
from feed import category
from feed.category import from_topics


class Mapping(unittest.TestCase):
    def test_real_topics_from_the_dry_run(self) -> None:
        cases = {
            ("m_and_a", "bank_consolidation", "takeover_bid"): "deals",
            ("funding_round", "ai_infrastructure", "venture_capital"): "deals",
            ("hotel_recovery", "leisure_travel", "corporate_travel"): "hospitality",
            ("foreclosure", "commercial_real_estate", "loan_default"): "hospitality",
            ("team_valuation", "media_rights"): "sports_business",
            ("stadium_financing", "ballpark_development"): "sports_business",
            ("international_sales", "licensing", "scripted_tv"): "film_music",
            ("kpop", "anniversary", "music_release"): "film_music",
            ("superyachts", "yacht_launch"): "luxury_marine",
            ("superyacht", "refit", "shipyards"): "luxury_marine",
            ("sports_betting", "prediction_markets"): "betting",
            ("prediction_markets", "regulation"): "betting",
            ("celebrity_news", "collaboration"): "other",
            ("voice_ai", "context_layer", "ai_infrastructure"): "other",
        }
        for topics, want in cases.items():
            self.assertEqual(from_topics(topics), want, topics)

    def test_no_topics_is_no_category(self) -> None:
        self.assertIsNone(from_topics([]))
        self.assertIsNone(from_topics(["", "  "]))

    def test_whole_words_or_plurals_only(self) -> None:
        self.assertNotIn("sports_business", category.category_of_topic("nile_river_cruise"))
        self.assertNotIn("luxury_marine", category.category_of_topic("how_to_watch"))
        self.assertNotIn("hospitality", category.category_of_topic("player_development"))
        self.assertIn("luxury_marine", category.category_of_topic("yachts"))
        self.assertIn("luxury_marine", category.category_of_topic("superyachting"))  # "superyacht*"
        self.assertIn("film_music", category.category_of_topic("films"))

    def test_ties_go_to_the_specific_category(self) -> None:
        # real_estate (hospitality) vs m_and_a (deals): hospitality, Deals last.
        self.assertEqual(from_topics(["industrial_real_estate", "m_and_a"]), "hospitality")
        # prediction markets match betting and deals ("markets"): betting.
        self.assertEqual(from_topics(["prediction_markets"]), "betting")
        self.assertEqual(category.TIE_ORDER[-1], "deals")

    def test_the_most_matches_win(self) -> None:
        self.assertEqual(from_topics(["m_and_a", "private_equity", "hotel_development"]), "deals")

    def test_deterministic(self) -> None:
        t = ["box_office", "streaming", "m_and_a"]
        self.assertEqual({from_topics(t) for _ in range(20)}, {"film_music"})


class Enum(unittest.TestCase):
    def test_seven_categories_and_their_labels(self) -> None:
        self.assertEqual(category.CATEGORIES, ("deals", "hospitality", "sports_business", "film_music",
                                               "luxury_marine", "betting", "other"))
        self.assertEqual(set(category.LABELS), set(category.CATEGORIES))
        self.assertEqual(set(category.TIE_ORDER), set(category.CATEGORIES) - {"other"})

    def test_db_042_checks_the_same_enum(self) -> None:
        sql = (REPO_ROOT / "db" / "042_sports.sql").read_text(encoding="utf-8")
        m = re.search(r"stories_category_check CHECK \(category IN \(([^)]*)\)\)", sql)
        self.assertIsNotNone(m, "db/042 must CHECK stories.category")
        self.assertEqual(tuple(x.strip(" '\n") for x in m.group(1).split(",")), category.CATEGORIES)


class SummaryCall(unittest.TestCase):
    GOOD = {"id": 1, "summary": "The fund closed its second vehicle at a record size.", "entities": [],
            "topics": ["fund_raise"], "sport": "none", "drop": "none", "moves": []}

    def job(self):
        from core import ledger
        return ledger.SummaryJob(1, "Headline here", "A teaser.", "Axios", None, 0, "axios-pro-rata")

    def test_category_is_a_required_enum_field_and_the_prompt_defines_it(self) -> None:
        from feed import summarize
        item = summarize.schema()["properties"]["stories"]["items"]
        self.assertIn("category", item["required"])
        self.assertEqual(item["properties"]["category"]["enum"], list(category.CATEGORIES))
        self.assertIn("category: the one that fits best", summarize.SYSTEM)

    def test_the_category_reaches_the_save(self) -> None:
        from feed import summarize
        (r,), _, _ = summarize.results_for([self.job()], {"stories": [dict(self.GOOD, category="deals")]})
        self.assertEqual(r.category, "deals")
        (r,), _, _ = summarize.results_for([self.job()], {"stories": [dict(self.GOOD, category="gossip")]})
        self.assertIsNone(r.category)  # not ours: none, never a guess

    def test_the_save_keeps_a_category_already_set(self) -> None:
        import inspect

        from core import ledger
        self.assertIn("category = coalesce(category, %s)", inspect.getsource(ledger.feed_save_summaries))
        self.assertIn("AND category IS NULL", inspect.getsource(ledger.feed_set_categories))


if __name__ == "__main__":
    unittest.main()
