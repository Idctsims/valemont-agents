"""News categories (Chat 3 Phase 3 scope addition): one enum and one
definition shared by the summary call, the one-off Haiku categorize pass
(feed/categorize.py; owner 2026-10-12 23:38: no keyword mapping) and db/042's
CHECK; the pass's validation, grouping, budget stop and write rule.

Stubbed: no database, no network, no Claude.
"""

from __future__ import annotations

import inspect
import json
import re
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from core import ai, ledger
from core.paths import REPO_ROOT
from feed import categorize, category, summarize


def job(sid: int, title: str = "A headline") -> ledger.CategorizeJob:
    return ledger.CategorizeJob(sid, title, "A summary.", "Axios")


class Enum(unittest.TestCase):
    def test_seven_categories_and_their_labels(self) -> None:
        self.assertEqual(category.CATEGORIES, ("deals", "hospitality", "sports_business", "film_music",
                                               "luxury_marine", "betting", "other"))
        self.assertEqual(set(category.LABELS), set(category.CATEGORIES))

    def test_db_042_checks_the_same_enum(self) -> None:
        sql = (REPO_ROOT / "db" / "042_sports.sql").read_text(encoding="utf-8")
        m = re.search(r"stories_category_check CHECK \(category IN \(([^)]*)\)\)", sql)
        self.assertIsNotNone(m, "db/042 must CHECK stories.category")
        self.assertEqual(tuple(x.strip(" '\n") for x in m.group(1).split(",")), category.CATEGORIES)

    def test_both_calls_send_the_same_definition_and_enum(self) -> None:
        self.assertIn(category.PROMPT, summarize.SYSTEM)
        self.assertIn(category.PROMPT, categorize.SYSTEM)
        s_item = summarize.schema()["properties"]["stories"]["items"]
        c_item = categorize.schema()["properties"]["stories"]["items"]
        self.assertIn("category", s_item["required"])
        self.assertEqual(s_item["properties"]["category"]["enum"], list(category.CATEGORIES))
        self.assertEqual(c_item["properties"]["category"]["enum"], list(category.CATEGORIES))

    def test_no_keyword_mapping_is_left(self) -> None:
        self.assertFalse(hasattr(category, "from_topics"))
        self.assertFalse((REPO_ROOT / "workers" / "scripts" / "feed_backfill_category.py").exists())


class SummaryCall(unittest.TestCase):
    GOOD = {"id": 1, "summary": "The fund closed its second vehicle at a record size.", "entities": [],
            "topics": ["fund_raise"], "sport": "none", "drop": "none", "moves": []}

    def test_the_category_reaches_the_save(self) -> None:
        j = ledger.SummaryJob(1, "Headline here", "A teaser.", "Axios", None, 0, "axios-pro-rata")
        (r,), _, _ = summarize.results_for([j], {"stories": [dict(self.GOOD, category="deals")]})
        self.assertEqual(r.category, "deals")
        (r,), _, _ = summarize.results_for([j], {"stories": [dict(self.GOOD, category="gossip")]})
        self.assertIsNone(r.category)  # not ours: none, never a guess

    def test_the_save_keeps_a_category_already_set(self) -> None:
        self.assertIn("category = coalesce(category, %s)", inspect.getsource(ledger.feed_save_summaries))
        self.assertIn("AND category IS NULL", inspect.getsource(ledger.feed_set_categories))


class _PassBase(unittest.TestCase):
    """Stubs and the fake Haiku shared by the pass tests; no tests here."""

    def setUp(self) -> None:
        self.written: list[list[tuple[int, str]]] = []
        p = mock.patch.object(ledger, "feed_set_categories",
                              lambda pairs, **kw: self.written.append(list(pairs)) or len(pairs))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(ledger, "_pool", side_effect=AssertionError("database reached"))
        p.start()
        self.addCleanup(p.stop)

    def fake(self, answer, calls: list) -> Any:
        def complete(**kw: Any) -> Any:
            calls.append(kw)
            stories = json.loads(kw["messages"][0]["content"].split("\n", 1)[1])
            kw["on_recorded"](1, kw["model"], 0.0003)
            body = {"stories": [{"id": s["id"], "category": answer(s)} for s in stories]}
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(body))])
        return complete


class Pass(_PassBase):
    def test_haiku_non_critical_in_groups_of_25_and_the_cost_recorded(self) -> None:
        calls: list = []
        with mock.patch.object(ai, "complete", self.fake(lambda s: "deals", calls)):
            res = categorize.run([job(i) for i in range(1, 61)])
        self.assertEqual([len(json.loads(c["messages"][0]["content"].split("\n", 1)[1])) for c in calls], [25, 25, 10])
        for c in calls:
            self.assertEqual((c["model"], c["critical"], c["purpose"], c["thinking"]),
                             ("claude-haiku-5-5", False, "feed_categorize", {"type": "disabled"}))
        self.assertEqual((res.asked, res.written, res.calls, str(res.cost_usd)), (60, 60, 3, "0.0009"))
        self.assertEqual(len(res.by_category["deals"]), 60)

    def test_an_unknown_answer_or_story_is_dropped_never_guessed(self) -> None:
        group = [job(1), job(2), job(3)]
        got = categorize.answers_for(group, {"stories": [
            {"id": 1, "category": "betting"}, {"id": 2, "category": "gossip"}, {"id": 99, "category": "deals"},
            {"id": 1, "category": "deals"}, "junk"]})
        self.assertEqual(got, {1: "betting"})
        self.assertEqual(categorize.answers_for(group, None), {})

    def test_a_spent_budget_stops_the_pass_with_what_was_written(self) -> None:
        calls: list = []
        real = self.fake(lambda s: "other", calls)

        def complete(**kw: Any) -> Any:
            if calls:
                raise ai.BudgetExceeded("spent")
            return real(**kw)

        with mock.patch.object(ai, "complete", complete):
            res = categorize.run([job(i) for i in range(1, 51)])
        self.assertEqual((res.calls, res.written), (1, 25))
        self.assertTrue(res.stopped.startswith("AI budget"))


class Definitions(unittest.TestCase):
    """Owner, 2026-10-13 16:19: sports_business and betting, tighter."""

    def test_sports_business_is_the_business_not_the_games(self) -> None:
        for phrase in ("ownership", "stakes", "valuations", "media rights", "athlete businesses",
                       "league economics", "NOT games, coaching or player performance"):
            self.assertIn(phrase, category.PROMPT)

    def test_betting_is_the_betting_business_not_fantasy_or_stocks(self) -> None:
        for phrase in ("sportsbooks", "prediction markets", "Kalshi", "betting regulation",
                       "NOT fantasy sports", "NOT the stock market"):
            self.assertIn(phrase, category.PROMPT)


class Recategorize(_PassBase):
    """The re-run for an explicit id set (owner, 2026-10-13): overwrites,
    but only through the compare-and-set writer, and records every move."""

    def setUp(self) -> None:
        super().setUp()
        self.recat: list[list[tuple[int, str, str]]] = []
        p = mock.patch.object(ledger, "feed_recategorize",
                              lambda triples, **kw: self.recat.append(list(triples)) or len(triples))
        p.start()
        self.addCleanup(p.stop)

    def test_it_overwrites_through_compare_and_set_and_records_moves(self) -> None:
        calls: list = []
        current = {1: "betting", 2: "betting", 3: "sports_business"}
        answer = {1: "other", 2: "betting", 3: "other"}
        with mock.patch.object(ai, "complete", self.fake(lambda s: answer[s["id"]], calls)):
            res = categorize.run([job(i) for i in (1, 2, 3)], current=current)
        self.assertEqual(self.written, [])  # never the NULL-only writer
        self.assertEqual(self.recat, [[(1, "betting", "other"), (3, "sports_business", "other")]])
        self.assertEqual(res.moves, [(1, "betting", "other"), (3, "sports_business", "other")])
        self.assertEqual((res.asked, res.written, res.unchanged), (3, 2, 1))
        self.assertEqual(calls[0]["purpose"], "feed_recategorize")

    def test_a_job_outside_the_id_set_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            categorize.run([job(1), job(2)], current={1: "betting"})


if __name__ == "__main__":
    unittest.main()
