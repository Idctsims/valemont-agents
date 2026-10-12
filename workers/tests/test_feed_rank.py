"""The feed's ranking math (feed/rank.py; docs/feed_ranking.md §2): centering,
the affinity sign, seed decay, signals, bounded learned weights, recency,
follow matching, the Alabama floor and the cross-pillar cap (through
order_for_you, db/039 feed_for_you's twin), the SQL constants pinned to the
Python ones, and the owner's "10 Not Interested" rule.

Stubbed vectors only: no database, no network, no Claude.
"""

from __future__ import annotations

import math
import random
import re
import unittest

from core.paths import REPO_ROOT
from feed import rank
from feed.rank import Candidate, Follow, Signal, StoryIn

DIMS = 8


def axis(i: int, scale: float = 1.0) -> rank.Vec:
    return tuple(scale if j == i else 0.0 for j in range(DIMS))


#: Every vector shares this offset, like gte-small's (raw cosines all high).
COMMON = tuple(0.8 for _ in range(DIMS))


def topical(topic: int, rng: random.Random, noise: float = 0.05) -> rank.Vec:
    return tuple(c + (0.35 if j == topic else 0.0) + rng.uniform(-noise, noise)
                 for j, c in enumerate(COMMON))


def story(sid: int, vec: rank.Vec, *, title: str = "t", pillars=("home",), sport=None,
          entities=(), topics=(), slug="src") -> StoryIn:
    return StoryIn(sid, tuple(pillars), sport, title, tuple(entities), tuple(topics), slug, vec)


class VectorMath(unittest.TestCase):
    def test_centering_removes_the_shared_offset(self) -> None:
        rng = random.Random(1)
        a, b = topical(0, rng), topical(1, rng)
        mean = rank.mean([topical(t, rng) for t in range(DIMS) for _ in range(5)])
        self.assertGreater(rank.cos(a, b), 0.9)  # anisotropic: unrelated look alike
        self.assertLess(rank.cos(rank.sub(a, mean), rank.sub(b, mean)), 0.2)

    def test_unit_of_a_zero_vector_is_no_signal(self) -> None:
        self.assertIsNone(rank.unit((0.0, 0.0)))
        self.assertEqual(rank.cos((0.0, 0.0), (1.0, 0.0)), 0.0)


class Profiles(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = random.Random(2)
        self.pool = [topical(t, self.rng) for t in range(DIMS) for _ in range(6)]
        self.mean = rank.mean(self.pool)

    def profile(self, signals=(), love_seeds=None, ni_seeds=None) -> rank.Profile:
        return rank.build_profile("home", self.mean, len(self.pool),
                                  love_seeds if love_seeds is not None else [topical(0, self.rng)],
                                  ni_seeds if ni_seeds is not None else [topical(1, self.rng)],
                                  list(signals))

    def test_affinity_sign(self) -> None:
        p = self.profile()
        liked = rank.score_story(story(1, topical(0, self.rng)), "home", p, [])
        disliked = rank.score_story(story(2, topical(1, self.rng)), "home", p, [])
        self.assertGreater(liked.components["affinity"], 0.3)
        self.assertLess(disliked.components["affinity"], -0.3)

    def test_seed_weight_decays_as_k_over_k_plus_n_per_profile(self) -> None:
        loves = [Signal(story(i, topical(2, self.rng)), "love") for i in range(8)]
        nis = [Signal(story(100 + i, topical(3, self.rng)), "ni") for i in range(20)]
        p = self.profile(loves + nis)
        self.assertAlmostEqual(p.seed_weight_love, 12 / 20)
        self.assertAlmostEqual(p.seed_weight_ni, 12 / 32)
        # Ten NI taps do not weaken the love seeds (owner decision 3).
        self.assertAlmostEqual(self.profile(nis).seed_weight_love, 1.0)

    def test_seeds_alone_then_reactions_take_over(self) -> None:
        p0 = self.profile()
        seed_love = rank.unit(rank.sub(topical(0, random.Random(9)), self.mean))
        self.assertGreater(rank.cos(p0.love, seed_love), 0.95)
        many = [Signal(story(i, topical(2, self.rng)), "love") for i in range(200)]
        p = self.profile(many)
        toward_2 = rank.unit(rank.sub(topical(2, self.rng), self.mean))
        self.assertGreater(rank.cos(p.love, toward_2), 0.9)

    def test_no_seeds_and_no_reactions_is_no_profile(self) -> None:
        p = self.profile(love_seeds=[], ni_seeds=[])
        self.assertIsNone(p.love)
        self.assertIsNone(p.ni)
        self.assertEqual((p.seed_weight_love, p.seed_weight_ni), (0.0, 0.0))
        self.assertEqual(rank.score_story(story(1, topical(0, self.rng)), "home", p, []).score, 0.0)

    def test_signal_kinds(self) -> None:
        self.assertEqual(rank.signal_kind("love", True), "love")
        self.assertEqual(rank.signal_kind("not_interested", False), "ni")
        self.assertEqual(rank.signal_kind("neutral", True), "neutral")
        self.assertEqual(rank.signal_kind("undo", True), "open")
        self.assertEqual(rank.signal_kind("undo", False), None)
        self.assertEqual(rank.signal_kind(None, True), "open")

    def test_neutral_is_counted_and_carries_no_signal(self) -> None:
        p = self.profile([Signal(story(1, topical(4, self.rng), topics=("x",)), "neutral")])
        self.assertEqual((p.neutral_count, p.reaction_count), (1, 0))
        self.assertEqual(p.topic_weights, {})

    def test_an_open_counts_three_tenths_of_a_love(self) -> None:
        p = self.profile([Signal(story(1, topical(4, self.rng), slug="skift"), "open")])
        self.assertAlmostEqual(p.source_weights["skift"], rank.learned(0.3, 0, rank.B_SOURCE), places=4)
        self.assertEqual((p.open_count, p.love_count), (1, 0))


class LearnedWeights(unittest.TestCase):
    def test_bounded_and_shrunk(self) -> None:
        self.assertAlmostEqual(rank.learned(1, 0, 0.30), 0.05)
        self.assertAlmostEqual(rank.learned(10, 0, 0.30), 0.20)
        for n in (100, 10_000):
            self.assertLess(rank.learned(n, 0, 0.30), 0.30)
            self.assertGreater(rank.learned(0, n, 0.40), -0.40)

    def test_topic_weight_is_a_mean_not_a_sum(self) -> None:
        rng = random.Random(3)
        mean = rank.mean([topical(t, rng) for t in range(DIMS)])
        sigs = [Signal(story(i, topical(5, rng), topics=("a", "b", "c")), "ni") for i in range(10)]
        p = rank.build_profile("home", mean, 8, [], [], sigs)
        s = rank.score_story(story(99, topical(6, rng), topics=("a", "b", "c", "z")), "home", p, [])
        self.assertAlmostEqual(s.components["topic"], rank.learned(0, 10, rank.B_TOPIC), places=3)

    def test_the_base_is_clamped_to_the_column_check(self) -> None:
        p = rank.Profile("home", (0.0, 0.0), 1, None, None, {}, {"s": 99.0}, {}, 0, 0, 0, 0, 0, 0)
        self.assertEqual(rank.score_story(story(1, (1.0, 0.0), slug="s"), "home", p, []).score, rank.SCORE_BOUND)


ALABAMA = Follow("Alabama Crimson Tide", 1, None, True, ("alabama", "crimson tide", "bama"), "cfb")
LEBRON = Follow("LeBron James", 1, None, False, ("lebron",), "nba")
SIXERS = Follow("Philadelphia 76ers", 2, "sports", False, ("76ers", "sixers"), "nba")
HAMILTON = Follow("Lewis Hamilton", 1, None, False, ("lewis hamilton", "hamilton"), "f1")
NBA = Follow("NBA", 3, "sports", False, (), "nba", sport_wide=True)
KALSHI = Follow("Kalshi", 1, "home", False, ("kalshi",), None)
FOLLOWS = [ALABAMA, LEBRON, SIXERS, HAMILTON, NBA, KALSHI]


class FollowMatching(unittest.TestCase):
    def test_whole_words_and_the_sport_condition(self) -> None:
        m = rank.match_follows
        self.assertEqual(m("sports", FOLLOWS, "Crimson Tide fall to Georgia", (), "cfb").tier, 1)
        # "Alabama" in state news (no sport) is not the program.
        self.assertIsNone(m("home", FOLLOWS, "Alabama legislature passes budget", (), None).tier)
        # "Hamilton" the musical is not the driver.
        self.assertIsNone(m("home", FOLLOWS, "Hamilton returns to Broadway", (), None).tier)
        self.assertIsNone(m("home", FOLLOWS, "Hamiltonian mechanics", (), "f1").tier)
        self.assertEqual(m("home", FOLLOWS, "Kalshi raises", (), None).names, ("Kalshi",))

    def test_haiku_entities_match_too(self) -> None:
        self.assertEqual(rank.match_follows("sports", FOLLOWS, "Sixers win", ("LeBron James",), "nba").tier, 1)

    def test_best_tier_not_the_sum_and_pillar_filter(self) -> None:
        m = rank.match_follows("sports", FOLLOWS, "LeBron leads 76ers", (), "nba")
        self.assertEqual((m.tier, m.names), (1, ("LeBron James", "NBA", "Philadelphia 76ers")))
        p = rank.Profile("sports", (0.0,) * 2, 1, None, None, {}, {}, {}, 0, 0, 0, 0, 0, 0)
        s = rank.score_story(story(1, (1.0, 0.0), title="LeBron leads 76ers", sport="nba"), "sports", p, FOLLOWS)
        self.assertEqual(s.score, rank.TIER_BOOST[1])
        # 76ers and NBA are sports-only follows: on Home only LeBron counts.
        self.assertEqual(rank.match_follows("home", FOLLOWS, "76ers trade", (), "nba").tier, None)

    def test_sport_wide_league(self) -> None:
        self.assertEqual(rank.match_follows("sports", FOLLOWS, "Any NBA story", (), "nba").tier, 3)
        self.assertIsNone(rank.match_follows("sports", FOLLOWS, "Any NFL story", (), "nfl").tier)

    def test_floor_flag(self) -> None:
        self.assertTrue(rank.match_follows("home", FOLLOWS, "Bama beat", (), "cfb").floor)
        self.assertFalse(rank.match_follows("home", FOLLOWS, "LeBron", (), "nba").floor)

    def test_placements_pull_tier_one_sports_stories_into_home(self) -> None:
        cfb = story(1, (0.0,), title="Alabama wins", pillars=("sports",), sport="cfb")
        nba = story(2, (0.0,), title="76ers trade", pillars=("sports",), sport="nba")
        both = story(3, (0.0,), title="Kalshi deal", pillars=("home", "sports"))
        self.assertEqual(rank.placements(cfb, FOLLOWS), [("sports", False), ("home", True)])
        self.assertEqual(rank.placements(nba, FOLLOWS), [("sports", False)])  # tier 2 only
        self.assertEqual(rank.placements(both, FOLLOWS), [("home", False), ("sports", False)])


class ReadPath(unittest.TestCase):
    def test_recency_halves_per_half_life(self) -> None:
        self.assertAlmostEqual(rank.rank_of(2.0, rank.HALF_LIFE_H), 2.0 - math.log(2))
        self.assertAlmostEqual(math.exp(rank.rank_of(1.0, 36)) / math.exp(1.0), 0.25)
        self.assertEqual(rank.rank_of(1.0, -3), 1.0)  # a future date is age 0

    def test_alabama_floor_lifts_the_best_three_into_the_top_nine(self) -> None:
        cands = [Candidate(i, 10 - i * 0.01, 1.0) for i in range(1, 41)]
        cands += [Candidate(100 + i, 1.0 - i * 0.01, 2.0, floor=True) for i in range(50)]
        placed = {p.id: p for p in rank.order_for_you(cands)}
        floored = sorted((p for p in placed.values() if p.floored), key=lambda p: p.pos)
        self.assertEqual([p.id for p in floored], [100, 101, 102])
        self.assertTrue(all(p.pos <= rank.FLOOR_POSITION + rank.FLOOR_MAX for p in floored))
        self.assertGreater(placed[103].pos, rank.PAGE_SIZE)  # the rest rank on their own

    def test_old_floor_stories_are_not_lifted(self) -> None:
        cands = [Candidate(i, 10.0, 1.0) for i in range(1, 20)]
        cands.append(Candidate(99, 1.0, rank.FLOOR_MAX_AGE_H + 1, floor=True))
        self.assertFalse(next(p for p in rank.order_for_you(cands) if p.id == 99).floored)

    def test_a_floor_story_already_high_is_left_alone(self) -> None:
        cands = [Candidate(i, 1.0, 1.0) for i in range(1, 20)] + [Candidate(99, 9.0, 1.0, floor=True)]
        top = rank.order_for_you(cands)[0]
        self.assertEqual((top.id, top.floored), (99, False))

    def test_at_most_five_cross_pillar_stories_on_page_one(self) -> None:
        cands = [Candidate(i, 1.0 - i * 0.01, 1.0) for i in range(1, 41)]
        cands += [Candidate(200 + i, 5.0 - i * 0.01, 1.0, cross_pillar=True) for i in range(8)]
        order = rank.order_for_you(cands)
        page1 = [p.id for p in order[:rank.PAGE_SIZE]]
        self.assertEqual(sorted(i for i in page1 if i >= 200), [200, 201, 202, 203, 204])
        self.assertEqual([p.id for p in order[rank.PAGE_SIZE:rank.PAGE_SIZE + 3]], [205, 206, 207])

    def test_floored_cross_stories_keep_their_place_under_the_cap(self) -> None:
        cands = [Candidate(i, 1.0 - i * 0.01, 1.0) for i in range(1, 41)]
        cands += [Candidate(200 + i, 5.0, 1.0, cross_pillar=True) for i in range(8)]
        cands += [Candidate(300 + i, 0.1, 1.0, floor=True, cross_pillar=True) for i in range(3)]
        page1 = [p.id for p in rank.order_for_you(cands)[:rank.PAGE_SIZE]]
        self.assertTrue({300, 301, 302} <= set(page1))
        self.assertEqual(sum(1 for i in page1 if i >= 200), rank.CROSS_CAP)

    def test_sql_constants_match(self) -> None:
        sql = (REPO_ROOT / "db" / "039_feed_ranking.sql").read_text(encoding="utf-8")
        body = sql[sql.index("FUNCTION public.feed_for_you"):sql.index("FUNCTION public.ctx_feed")]

        def lit(name: str) -> float:
            return float(re.search(r"([0-9.]+)(?:::float8)?\s+AS " + name + r"\b", body).group(1))

        self.assertEqual(lit("half_life_h"), rank.HALF_LIFE_H)
        self.assertEqual(lit("window_h"), rank.WINDOW_H)
        self.assertEqual(lit("floor_max"), rank.FLOOR_MAX)
        self.assertEqual(lit("floor_position"), rank.FLOOR_POSITION)
        self.assertEqual(lit("floor_max_age_h"), rank.FLOOR_MAX_AGE_H)
        self.assertEqual(lit("cross_cap"), rank.CROSS_CAP)
        self.assertEqual(lit("page_size"), rank.PAGE_SIZE)
        self.assertIn(f"BETWEEN -{rank.SCORE_BOUND:g} AND {rank.SCORE_BOUND:g}", sql)


class TenNotInterested(unittest.TestCase):
    """The owner's rule (2026-10-11): a held-out story ON page one of For you
    before the taps falls OFF page one after ten Not Interested taps on its
    topic and a rebuild."""

    def test_held_out_story_falls_off_page_one(self) -> None:
        rng = random.Random(7)
        golf, deals, other, gossip = 0, 1, 2, 3
        spec = [(golf, 12), (deals, 10), (other, 20), (gossip, 3)]
        stories, ages = [], {}
        sid = 0
        for topic, n in spec:
            for _ in range(n):
                sid += 1
                stories.append(story(sid, topical(topic, rng), topics=(f"t{topic}",)))
                ages[sid] = rng.uniform(0.5, 12.0)
        golf_ids = [s.id for s in stories if s.topics == (f"t{golf}",)]
        held = min(golf_ids, key=lambda i: ages[i])  # the freshest golf story
        tapped = [s for s in stories if s.id in golf_ids and s.id != held][:10]
        self.assertEqual(len(tapped), 10)
        mean = rank.mean([s.vec for s in stories])
        love_seeds = [topical(deals, rng)]
        ni_seeds = [topical(gossip, rng)]

        def position(signals) -> int:
            prof = rank.build_profile("home", mean, len(stories), love_seeds, ni_seeds, signals)
            scores = {s.id: rank.score_story(s, "home", prof, []).score for s in stories}
            # Tapped stories leave For you (latest reaction not_interested).
            cands = [Candidate(i, sc, ages[i]) for i, sc in scores.items()
                     if i not in {sig.story.id for sig in signals}]
            return next(p.pos for p in rank.order_for_you(cands) if p.id == held)

        before = position([])
        after = position([Signal(s, "ni") for s in tapped])
        self.assertLessEqual(before, rank.PAGE_SIZE, "the held-out story must start on page one")
        self.assertGreater(after, rank.PAGE_SIZE, f"held-out story at {before} -> {after}")


if __name__ == "__main__":
    unittest.main()
