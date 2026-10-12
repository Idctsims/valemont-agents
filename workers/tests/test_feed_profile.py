"""feed_profile, feed_score and feed_why (feed/profile.py, feed/why.py) with a
stubbed ledger, stubbed vectors and a stubbed Claude: the rebuild writes one
profile per pillar and rescores every placement, a cross-pillar Alabama story
carries the floor into Home, the once-a-day skip, feed_score's skip with no
profile, and why-it-matters' NULL default, validator and caps.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from core import ai, ledger
from core.jobs import JobSkipped
from feed import profile, rank, why

DIMS = 4


def vec(i: int) -> tuple[float, ...]:
    return tuple(1.0 + (0.5 if j == i else 0.0) for j in range(DIMS))


def rstory(sid: int, title: str, *, pillars=("home",), sport=None, entities=(), topics=(), v=None):
    return ledger.RankStory(sid, tuple(pillars), sport, title, tuple(entities), tuple(topics), "src",
                            v or vec(sid % DIMS))


FOLLOW_ROWS = [
    ledger.FollowRow("Alabama Crimson Tide", 1, None, True, ("alabama", "crimson tide"), "cfb", False),
    ledger.FollowRow("NFL", 3, "sports", False, (), "nfl", True),
]


class StubLedger:
    """Every ledger function the three modules call, recorded."""

    def __init__(self, test: unittest.TestCase) -> None:
        self.profiles: list[rank.Profile] = []
        self.scores: list[tuple[list[rank.Score], dict]] = []
        self.unscored_flags: list[bool] = []
        self.built_today = False
        self.latest: dict[str, ledger.ProfileRow] = {}
        self.signals: dict[str, list[ledger.ReactionSignal]] = {"home": [], "sports": []}
        self.stories = [
            rstory(1, "Fund raises $50M for Miami marina"),
            rstory(2, "Crimson Tide beat Tennessee", pillars=("sports",), sport="cfb"),
            rstory(3, "Bears trade for a receiver", pillars=("sports",), sport="nfl"),
        ]
        self.why_saved: list[tuple[dict, int | None]] = []
        self.why_queue: list[ledger.WhyJob] = []
        self.calls_today = 0
        self.ventures: dict[str, Any] = {"active": [{"name": "Sail Beach Club", "slug": "sail-beach-club"}],
                                         "not_set_up": ["Clipd"]}
        fns = {
            "feed_seed_phrases": lambda: {"home": {"love": ["marinas"], "not_interested": ["gossip"]},
                                          "sports": {"love": [], "not_interested": []}},
            "feed_mean_vector": lambda pillar, days: ((1.1,) * DIMS, 40 if pillar == "home" else 60),
            "feed_reaction_signals": lambda pillar: self.signals[pillar],
            "feed_insert_profile": self._insert_profile,
            "feed_follows": lambda: FOLLOW_ROWS,
            "feed_stories_to_score": self._to_score,
            "feed_insert_scores": lambda scores, ids: self.scores.append((list(scores), dict(ids))) or len(scores),
            "feed_profile_built_today": lambda: self.built_today,
            "feed_latest_profiles": lambda: self.latest,
            "feed_why_queue": lambda top: list(self.why_queue),
            "ai_calls_today": lambda purpose: self.calls_today,
            "ctx_ventures": lambda: self.ventures,
            "feed_save_why": lambda answers, uid: self.why_saved.append((dict(answers), uid)) or len(answers),
        }
        for name, fn in fns.items():
            p = mock.patch.object(ledger, name, fn)
            p.start()
            test.addCleanup(p.stop)

    def _insert_profile(self, p: rank.Profile) -> int:
        self.profiles.append(p)
        return 100 + len(self.profiles)

    def _to_score(self, hours: int, max_attempts: int, *, unscored: bool) -> list[ledger.RankStory]:
        self.unscored_flags.append(unscored)
        return self.stories


def fake_embed(texts):
    return [list(vec(0 if "marina" in t else 1)) for t in texts]


class Rebuild(unittest.TestCase):
    def setUp(self) -> None:
        self.db = StubLedger(self)

    def test_one_profile_per_pillar_and_every_placement_scored(self) -> None:
        stats = profile.rebuild(embed=fake_embed)
        self.assertEqual([p.pillar for p in self.db.profiles], ["home", "sports"])
        home, sports = self.db.profiles
        self.assertEqual((home.mean_n, home.seed_weight_love), (40, 1.0))
        self.assertIsNotNone(home.love)
        self.assertIsNone(sports.love)  # no sports seeds yet, no reactions
        self.assertEqual(self.db.unscored_flags, [False])  # a rebuild rescores everything
        scores, ids = self.db.scores[0]
        self.assertEqual(ids, {"home": 101, "sports": 102})
        placed = {(s.story_id, s.pillar): s for s in scores}
        # Story 2 (Alabama, sports) is also placed on Home, cross-pillar, floor set.
        self.assertEqual(set(placed), {(1, "home"), (2, "sports"), (2, "home"), (3, "sports")})
        self.assertTrue(placed[(2, "home")].cross_pillar and placed[(2, "home")].floor)
        self.assertFalse(placed[(2, "sports")].cross_pillar)
        self.assertEqual(placed[(3, "sports")].follow_tier, 3)
        self.assertEqual((stats.scored, stats.stories), (4, 3))
        self.assertIn("home: mean over 40", stats.line())

    def test_reactions_feed_the_profile(self) -> None:
        self.db.signals["home"] = [
            ledger.ReactionSignal(rstory(10, "a", topics=("m_and_a",)), "love", False),
            ledger.ReactionSignal(rstory(11, "b"), "not_interested", True),
            ledger.ReactionSignal(rstory(12, "c"), None, True),
            ledger.ReactionSignal(rstory(13, "d"), "undo", False),  # no signal at all
            ledger.ReactionSignal(rstory(14, "e"), "neutral", True),
        ]
        profile.rebuild(embed=fake_embed)
        home = self.db.profiles[0]
        self.assertEqual((home.love_count, home.ni_count, home.open_count, home.neutral_count), (1, 1, 1, 1))
        self.assertAlmostEqual(home.seed_weight_love, 12 / 13)
        self.assertIn("m_and_a", home.topic_weights)

    def test_a_pillar_with_no_stories_gets_no_profile_and_no_scores(self) -> None:
        with mock.patch.object(ledger, "feed_mean_vector",
                               lambda pillar, days: ((1.1,) * DIMS, 9) if pillar == "home" else (None, 0)):
            stats = profile.rebuild(embed=fake_embed)
        self.assertEqual([p.pillar for p in self.db.profiles], ["home"])
        self.assertTrue(all(s.pillar == "home" for s in self.db.scores[0][0]))
        self.assertIn("sports: no stories", stats.line())

    def test_the_job_skips_when_a_profile_was_built_today(self) -> None:
        self.db.built_today = True
        with self.assertRaises(JobSkipped):
            profile.profile_job(embed=fake_embed)
        self.assertEqual(self.db.profiles, [])
        self.db.built_today = False
        profile.profile_job(embed=fake_embed)
        self.assertEqual(len(self.db.profiles), 2)


class ScoreNew(unittest.TestCase):
    def setUp(self) -> None:
        self.db = StubLedger(self)

    def test_no_profile_yet_is_a_skip(self) -> None:
        with self.assertRaises(JobSkipped):
            profile.score_new_job()

    def test_scores_only_unscored_stories_against_the_latest_profiles(self) -> None:
        self.db.latest = {"home": ledger.ProfileRow(
            7, "home", (1.1,) * DIMS, 40, vec(0), None, {}, {}, {}, 0, 0, 0, 0, 1.0, 0.0, None)}
        n = profile.score_new_job()
        self.assertEqual(self.db.unscored_flags, [True])
        scores, ids = self.db.scores[0]
        self.assertEqual(ids, {"home": 7})
        self.assertEqual(n, len(scores))
        self.assertTrue(all(s.pillar == "home" for s in scores))  # no sports profile: none scored there


NAMES = ["Sail Beach Club", "Clipd"]


class WhyValidator(unittest.TestCase):
    def test_null_is_the_default(self) -> None:
        for item in ({"why": None, "venture": None}, {"why": "", "venture": "Clipd"},
                     {"why": "Clipd could use this.", "venture": None}, {}):
            with self.subTest(item=item):
                self.assertIsNone(why.validate(item, NAMES))

    def test_the_venture_must_be_real_and_named_in_the_line(self) -> None:
        self.assertIsNone(why.validate({"venture": "SBC", "why": "SBC competitor opens nearby."}, NAMES))
        self.assertIsNone(why.validate({"venture": "Clipd", "why": "A rival app raised money."}, NAMES))
        self.assertEqual(why.validate({"venture": "Clipd", "why": "A direct Clipd rival raised $20M."}, NAMES),
                         "A direct Clipd rival raised $20M.")

    def test_one_sentence_of_at_most_twenty_words(self) -> None:
        long = "Sail Beach Club " + " ".join(["word"] * 18) + "."
        self.assertIsNone(why.validate({"venture": "Sail Beach Club", "why": long}, NAMES))
        two = "Sail Beach Club gains a neighbor. It is a rival."
        self.assertIsNone(why.validate({"venture": "Sail Beach Club", "why": two}, NAMES))

    def test_answers_cover_every_story_and_ignore_strangers(self) -> None:
        group = [ledger.WhyJob(1, "a", None, "X"), ledger.WhyJob(2, "b", None, "Y")]
        payload = {"stories": [{"id": 2, "venture": "Clipd", "why": "A Clipd rival raised money."},
                               {"id": 99, "venture": "Clipd", "why": "Clipd stranger."}]}
        self.assertEqual(why.answers_for(group, payload, NAMES), {1: None, 2: "A Clipd rival raised money."})
        self.assertEqual(why.answers_for(group, None, NAMES), {1: None, 2: None})

    def test_a_refused_line_says_why_and_a_null_says_nothing(self) -> None:
        long = "Clipd " + " ".join(["word"] * 22) + "."
        self.assertEqual(why.check({"venture": None, "why": None}, NAMES), (None, None))
        self.assertEqual(why.check({"venture": "Clipd", "why": long}, NAMES), (None, "23 words; the cap is 20"))
        self.assertEqual(why.check({"venture": "SBC", "why": "SBC x."}, NAMES)[1], "names no known venture ('SBC')")
        why.REFUSED.clear()
        why.answers_for([ledger.WhyJob(5, "t", None, "X")], {"stories": [{"id": 5, "venture": "Clipd", "why": long}]}, NAMES)
        self.assertEqual([(sid, reason) for sid, _, reason in why.REFUSED], [(5, "23 words; the cap is 20")])
        why.REFUSED.clear()

    def test_the_context_says_what_every_venture_does(self) -> None:
        ctx = {"active": [{"name": "Sail Beach Club", "slug": "sail-beach-club", "stage": "pre_launch",
                           "next_action": "Pick a renderer", "workstreams": [{"name": "Permitting"}]}],
               "not_set_up": ["Perfect Timing Management", "Freelance web dev", "Brand new venture"]}
        got = {v["name"]: v for v in why.venture_context(ctx)}
        self.assertEqual(got["Sail Beach Club"]["what_it_does"],
                         "Experiential hospitality: a beach/members' club on Biscayne Bay, Miami.")
        self.assertEqual((got["Sail Beach Club"]["stage"], got["Sail Beach Club"]["workstreams"]), ("pre_launch", ["Permitting"]))
        self.assertEqual(got["Perfect Timing Management"], {"name": "Perfect Timing Management",
                                                            "what_it_does": "Artist and influencer management."})
        # Matched ignoring case; an unknown venture is still listed, by name.
        self.assertEqual(got["Freelance web dev"]["what_it_does"], "Next.js/Supabase sites (senior care vertical).")
        self.assertEqual(got["Brand new venture"], {"name": "Brand new venture"})
        msg = why.user_message(ctx, [ledger.WhyJob(1, "h", None, "o")])
        self.assertIn("Artist and influencer management.", msg)

    def test_every_owner_venture_has_a_line(self) -> None:
        for name in ("Sail Beach Club", "Perfect Timing Management", "Excursion", "Clipd", "Valemont Grow",
                     "Sims & Vale Capital", "Freelance web dev"):
            self.assertIn(name.lower(), why.VENTURE_ABOUT)

    def test_venture_names_skip_archived(self) -> None:
        ctx = {"active": [{"name": "Sail Beach Club"}], "not_set_up": ["Clipd"], "archived": ["Test"]}
        self.assertEqual(why.venture_names(ctx), ["Sail Beach Club", "Clipd"])


def response(payload: Any) -> Any:
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(payload))])


class WhyJob(unittest.TestCase):
    def setUp(self) -> None:
        self.db = StubLedger(self)
        self.calls: list[dict] = []

    def complete(self, payload: Any):
        def fake(**kw):
            self.calls.append(kw)
            kw["on_recorded"](555, kw["model"], 0)
            return response(payload)
        return fake

    def test_nothing_queued_makes_no_call(self) -> None:
        with mock.patch.object(ai, "complete", self.complete({})):
            self.assertEqual(why.why_job(), 0)
        self.assertEqual(self.calls, [])

    def test_lines_are_saved_with_the_call_and_nulls_are_recorded_as_asked(self) -> None:
        self.db.why_queue = [ledger.WhyJob(1, "Beach club opens next door", "s", "Skift"),
                             ledger.WhyJob(2, "Celebrity weds", None, "Complex")]
        payload = {"stories": [{"id": 1, "venture": "Sail Beach Club", "why": "A new Sail Beach Club competitor two miles away."},
                               {"id": 2, "venture": None, "why": None}]}
        with mock.patch.object(ai, "complete", self.complete(payload)):
            self.assertEqual(why.why_job(), 1)
        self.assertEqual(self.db.why_saved, [({1: "A new Sail Beach Club competitor two miles away.", 2: None}, 555)])
        call = self.calls[0]
        self.assertEqual((call["model"], call["critical"], call["purpose"]), (why.MODEL, False, "feed_why"))
        self.assertIn("Sail Beach Club", call["messages"][0]["content"])

    def test_groups_respect_the_daily_cap(self) -> None:
        self.db.why_queue = [ledger.WhyJob(i, f"t{i}", None, "X") for i in range(1, 9)]
        self.db.calls_today = why.DAILY_CALLS
        with mock.patch.object(ai, "complete", self.complete({})), self.assertRaises(JobSkipped):
            why.why_job()
        self.assertEqual(self.calls, [])

    def test_a_spent_budget_is_a_skip(self) -> None:
        self.db.why_queue = [ledger.WhyJob(1, "t", None, "X")]
        with mock.patch.object(ai, "complete", side_effect=ai.BudgetExceeded("spent")), \
                self.assertRaises(JobSkipped):
            why.why_job()

    def test_ask_again_forgets_then_asks_the_named_stories(self) -> None:
        resets: list[list[int]] = []
        jobs = [ledger.WhyJob(763, "Creator Dhar Mann on studio deals", "s", "Variety")]
        payload = {"stories": [{"id": 763, "venture": "Clipd", "why": "A Clipd rival raised money."}]}
        with mock.patch.object(ledger, "feed_why_jobs", lambda ids: jobs), \
                mock.patch.object(ledger, "feed_reset_why", lambda ids: resets.append(list(ids)) or len(ids)), \
                mock.patch.object(ai, "complete", self.complete(payload)):
            got = why.ask_again([763])
        self.assertEqual((resets, got), ([[763]], {763: "A Clipd rival raised money."}))
        self.assertEqual(self.db.why_saved, [({763: "A Clipd rival raised money."}, 555)])

    def test_no_ventures_is_a_skip_without_a_call(self) -> None:
        self.db.why_queue = [ledger.WhyJob(1, "t", None, "X")]
        self.db.ventures = {"active": [], "not_set_up": []}
        with mock.patch.object(ai, "complete", self.complete({})), self.assertRaises(JobSkipped):
            why.why_job()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
