"""Sports News, worker side (Chat 3 Phase 3; docs/sports_news.md):

* boost math: a window of h hours ranks a story as h hours fresher, in the
  twin exactly as in db/042's SQL text; the entity cap (6, 9 in a window) on
  Sports only; a chip filters before ranking;
* the LIV term on the owner's examples (decision 9);
* move validation, rules 1-6, and the `moves` field of the summary call;
* the summary queue's priority pattern (decision 10) and D8's sport rule;
* the sports seeds in db/042 are the design note's phrases, verbatim.

Stubbed only: no database, no network, no Claude.
"""

from __future__ import annotations

import json
import math
import re
import unittest
from unittest import mock

from core import ledger
from core.paths import REPO_ROOT
from feed import moves, rank, sources, summarize
from feed.rank import Candidate, Follow, StoryIn

SQL_042 = (REPO_ROOT / "db" / "042_sports.sql").read_text(encoding="utf-8")


def story(sid: int, title: str, *, sport: str | None = "golf", entities=(), topics=()) -> StoryIn:
    return StoryIn(sid, ("sports",), sport, title, tuple(entities), tuple(topics), "src", (0.0,))


SCHEFFLER = Follow("Scottie Scheffler", 1, None, False, ("scheffler", "scottie scheffler"), "golf", kind="person")
RAHM = Follow("Jon Rahm", 3, "sports", False, ("jon rahm", "rahm"), "golf", kind="person")
MCILROY = Follow("Rory McIlroy", 3, "sports", False, ("rory mcilroy", "mcilroy"), "golf", kind="person")
LEBRON = Follow("LeBron James", 1, None, False, ("lebron", "lebron james"), "nba", kind="person")
SIXERS = Follow("Philadelphia 76ers", 2, "sports", False, ("76ers", "sixers", "philadelphia 76ers"), "nba", kind="team")
ALABAMA = Follow("Alabama Crimson Tide", 1, None, True, ("alabama", "crimson tide"), "cfb", kind="program")
GOLF_FOLLOWS = [SCHEFFLER, RAHM, MCILROY]


class BoostMath(unittest.TestCase):
    def test_a_boost_of_h_hours_is_h_hours_of_freshness(self) -> None:
        self.assertAlmostEqual(rank.rank_of(2.0, 30, boost_h=12), rank.rank_of(2.0, 18))
        self.assertAlmostEqual(rank.rank_of(1.0, 0, boost_h=18) - 1.0, math.log(2))
        self.assertEqual(rank.rank_of(1.0, 5, 0.0), rank.rank_of(1.0, 5))

    def test_the_twin_orders_by_the_boosted_rank(self) -> None:
        cands = [Candidate(1, 1.0, 0), Candidate(2, 0.8, 0, boost_h=12)]
        self.assertEqual([p.id for p in rank.order_for_you(cands)], [2, 1])

    def test_sql_constants_match(self) -> None:
        body = SQL_042[SQL_042.index("FUNCTION public.feed_for_you"):SQL_042.index("FUNCTION public.ctx_sports_feed")]

        def lit(name: str) -> float:
            return float(re.search(r"([0-9.]+)(?:::float8)?\s+AS " + name + r"\b", body).group(1))

        for name, value in (("half_life_h", rank.HALF_LIFE_H), ("window_h", rank.WINDOW_H),
                            ("floor_max", rank.FLOOR_MAX), ("floor_position", rank.FLOOR_POSITION),
                            ("floor_max_age_h", rank.FLOOR_MAX_AGE_H), ("cross_cap", rank.CROSS_CAP),
                            ("page_size", rank.PAGE_SIZE), ("entity_cap", rank.ENTITY_CAP),
                            ("entity_cap_boosted", rank.ENTITY_CAP_BOOSTED)):
            self.assertEqual(lit(name), value, name)
        # The SQL adds the boost exactly as rank_of does.
        self.assertIn("c.c_score + ln(2.0) * c.boost_h::float8 / k.half_life_h", body)
        self.assertIn("- ln(2.0) * c.age_h / k.half_life_h", body)
        # Sport-wide windows on Sports only; entity windows need the follow (D2).
        self.assertIn("(w.w_entity IS NULL AND p_pillar = 'sports')", body)
        self.assertIn("coalesce(sc.components -> 'follows', '[]'::jsonb) ? w.w_name", body)


class EntityCap(unittest.TestCase):
    def _pool(self, n_bama: int = 12) -> list[Candidate]:
        cands = [Candidate(i, 5.0 - i * 0.01, 1, tier1=("Alabama Crimson Tide",)) for i in range(1, n_bama + 1)]
        cands += [Candidate(100 + i, 3.0 - i * 0.01, 1) for i in range(40)]
        return cands

    def test_six_per_tier_one_follow_on_sports_page_one(self) -> None:
        page1 = [p.id for p in rank.order_for_you(self._pool(), "sports")[:rank.PAGE_SIZE]]
        self.assertEqual(sorted(i for i in page1 if i < 100), [1, 2, 3, 4, 5, 6])

    def test_nine_while_the_follow_has_a_window(self) -> None:
        order = rank.order_for_you(self._pool(), "sports", boosted={"Alabama Crimson Tide"})
        self.assertEqual(sum(1 for p in order[:rank.PAGE_SIZE] if p.id < 100), rank.ENTITY_CAP_BOOSTED)
        # The overflow follows page one, in rank order.
        self.assertEqual([p.id for p in order[rank.PAGE_SIZE:rank.PAGE_SIZE + 3]], [10, 11, 12])

    def test_no_entity_cap_on_home(self) -> None:
        page1 = [p.id for p in rank.order_for_you(self._pool(), "home")[:rank.PAGE_SIZE]]
        self.assertEqual(sum(1 for i in page1 if i < 100), 12)

    def test_floored_stories_count_first_and_keep_their_place(self) -> None:
        cands = [Candidate(i, 5.0, 1, tier1=("Alabama Crimson Tide",)) for i in range(1, 9)]
        cands += [Candidate(50 + i, 0.1, 1, floor=True, tier1=("Alabama Crimson Tide",)) for i in range(3)]
        cands += [Candidate(100 + i, 3.0 - i * 0.01, 1) for i in range(40)]
        page1 = {p.id for p in rank.order_for_you(cands, "sports")[:rank.PAGE_SIZE]}
        self.assertTrue({50, 51, 52} <= page1)
        self.assertEqual(sum(1 for i in page1 if i < 100), rank.ENTITY_CAP)

    def test_a_chip_ranks_within_its_sport(self) -> None:
        """db/042 filters by sport BEFORE the floor and caps. The Golf chip's
        page one is golf's best 30, not the golf left in All's top 30."""
        golf = [Candidate(200 + i, 1.0 - i * 0.01, 1) for i in range(35)]
        other = [Candidate(i, 4.0 - i * 0.01, 1) for i in range(1, 41)]
        all_page1 = {p.id for p in rank.order_for_you(golf + other, "sports")[:rank.PAGE_SIZE]}
        chip_page1 = [p.id for p in rank.order_for_you(golf, "sports")[:rank.PAGE_SIZE]]
        self.assertEqual(len([i for i in all_page1 if i >= 200]), 0)
        self.assertEqual(chip_page1, [200 + i for i in range(30)])
        self.assertIn("AND (p_sport IS NULL OR v.sport = p_sport)", SQL_042)


class LivTerm(unittest.TestCase):
    """docs/sports_news.md §3 and owner decision 9, on the database's titles."""

    def term(self, title: str, **kw) -> float:
        return rank.liv_term(story(1, title, **kw), GOLF_FOLLOWS)

    def test_filler_ranks_low(self) -> None:
        self.assertEqual(self.term("Bryson DeChambeau's LIV Escape Route Is Gone"), rank.LIV_PENALTY)
        # Story 949: "LIV Golf" is in Haiku's entities, not the title.
        self.assertEqual(self.term("Phil Mickelson & Bryson DeChambeau's $1 Billion Pitch",
                                   entities=("Phil Mickelson", "Bryson DeChambeau", "Ari Emanuel", "LIV Golf")),
                         rank.LIV_PENALTY)

    def test_a_top_player_spares_it(self) -> None:
        self.assertEqual(self.term("Rahm leaving LIV Golf in blow to future of bankrupt league"), 0.0)
        self.assertEqual(self.term("McIlroy's Meritocracy Rule Opens Door For LIV Golfers"), 0.0)
        self.assertEqual(self.term("LIV event this week", entities=("Scottie Scheffler",)), 0.0)

    def test_the_tour_split_and_livs_business_future_spare_it(self) -> None:
        for title in ("Golf, but quieter: Is this the end of LIV Golf?",
                      "Kinnings Forces LIV Golfers To Choose Tour Status By November",
                      "Top takeaways from LIV Golf's bankruptcy filings",
                      "DP World Tour to deny 2027 membership to LIV 2.0 golfers",
                      "LIV Golf seeks new funding"):
            self.assertEqual(self.term(title), 0.0, title)
        self.assertEqual(self.term("LIV news", topics=("tour_status",)), 0.0)

    def test_only_liv_stories_and_only_golf_or_no_sport(self) -> None:
        self.assertEqual(self.term("Scheffler wins again"), 0.0)
        self.assertEqual(self.term("Olivia and the delivery team"), 0.0)  # whole words only
        self.assertEqual(self.term("LIV Golf Escape Route", sport=None), rank.LIV_PENALTY)
        self.assertEqual(self.term("LIV Golf Escape Route", sport="nfl"), 0.0)

    def test_the_term_enters_the_score(self) -> None:
        prof = rank.build_profile("sports", (0.0,), 1, [], [], [])
        s = rank.score_story(story(1, "Bryson's LIV Escape Route Is Gone"), "sports", prof, GOLF_FOLLOWS)
        self.assertEqual(s.components["liv"], rank.LIV_PENALTY)
        self.assertAlmostEqual(s.score, rank.LIV_PENALTY)


def ent(eid: int, kind: str, name: str, sport: str | None, *aliases: str, followed: bool = False) -> ledger.MoveEntity:
    return ledger.MoveEntity(eid, kind, name, tuple(aliases) or (name.lower(),), sport, followed)


LEBRON_E = ent(1, "person", "LeBron James", "nba", "lebron", "lebron james", followed=True)
SIXERS_E = ent(2, "team", "Philadelphia 76ers", "nba", "76ers", "sixers", "philadelphia 76ers")
LAKERS_E = ent(3, "team", "Los Angeles Lakers", "nba", "los angeles lakers", "lakers")
CELTICS_E = ent(4, "team", "Boston Celtics", "nba", "boston celtics", "celtics")
UNFOLLOWED = ent(5, "person", "Joel Embiid", "nba", "joel embiid")
FERRARI_E = ent(6, "team", "Ferrari", "f1", "ferrari")
KOEPKA_E = ent(7, "person", "Brooks Koepka", "golf", "koepka", "brooks koepka", followed=True)
PGA_E = ent(8, "tour", "PGA Tour", "golf", "pga tour")


def ctx(open_persons=(), dismissed=()) -> ledger.MoveContext:
    return ledger.MoveContext((LEBRON_E, SIXERS_E, LAKERS_E, CELTICS_E, UNFOLLOWED, FERRARI_E, KOEPKA_E, PGA_E),
                              (ledger.CurrentLink(1, 2, "plays_for"), ledger.CurrentLink(5, 2, "plays_for")),
                              frozenset(open_persons), frozenset(dismissed))


class MoveValidation(unittest.TestCase):
    def check(self, person: str, kind: str, to: str = "", frm: str = "", sport: str | None = "nba", c=None):
        return moves.check(moves.RawMove(person, frm, to, kind), 99, sport, c or ctx())

    def test_a_trade_with_both_entities_resolving_is_proposed(self) -> None:
        v = self.check("LeBron James", "trade", "Los Angeles Lakers", "Philadelphia 76ers")
        self.assertEqual(v.proposal, ledger.ProposalIn(1, "trade", "plays_for", 2, 3, 99))
        # alias, any case; `from` may be left out (the current link is used)
        self.assertEqual(self.check("lebron", "signing", "LAKERS").proposal.from_id, 2)

    def test_rule_1_the_person_must_resolve_and_be_followed(self) -> None:
        self.assertIsNone(self.check("King James", "trade", "Lakers").proposal)
        self.assertIsNone(self.check("Joel Embiid", "trade", "Lakers").proposal)
        self.assertIsNone(self.check("LeBron James", "trade", "Lakers", sport="cfb").proposal)

    def test_rule_2_the_destination_must_resolve_in_the_persons_sport(self) -> None:
        self.assertIsNone(self.check("LeBron James", "trade", "Lakeshow").proposal)
        self.assertIsNone(self.check("LeBron James", "trade", "Ferrari").proposal)
        self.assertIsNone(self.check("LeBron James", "release", "Lakers").proposal)

    def test_rule_3_from_must_be_the_current_team(self) -> None:
        v = self.check("LeBron James", "trade", "Boston Celtics", "Los Angeles Lakers")
        self.assertIsNone(v.proposal)
        self.assertIn("current link", v.reason)

    def test_rule_4_the_move_must_change_something(self) -> None:
        self.assertIsNone(self.check("LeBron James", "signing", "76ers").proposal)
        self.assertIsNone(self.check("Brooks Koepka", "retirement", sport="golf").proposal)  # no current link
        r = self.check("LeBron James", "retirement")
        self.assertEqual((r.proposal.to_id, r.proposal.from_id), (None, 2))

    def test_rule_5_one_open_proposal_per_person(self) -> None:
        self.assertIsNone(self.check("LeBron James", "trade", "Lakers", c=ctx(open_persons={1})).proposal)

    def test_rule_6_a_dismissed_move_is_quiet_for_30_days(self) -> None:
        self.assertEqual(moves.DISMISS_QUIET_D, 30)
        self.assertIsNone(self.check("LeBron James", "trade", "Lakers", c=ctx(dismissed={(1, "trade", 3)})).proposal)
        self.assertIsNotNone(self.check("LeBron James", "trade", "Celtics", c=ctx(dismissed={(1, "trade", 3)})).proposal)

    def test_a_golfer_joins_a_tour(self) -> None:
        v = self.check("Brooks Koepka", "signing", "PGA Tour", sport="golf")
        self.assertEqual(v.proposal, ledger.ProposalIn(7, "signing", "member_of", None, 8, 99))

    def test_parse_keeps_well_formed_moves_only(self) -> None:
        raw = [{"person": " LeBron  James ", "from": "", "to": "Lakers", "kind": "trade"},
               {"person": "", "to": "x", "kind": "trade"}, {"person": "A", "kind": "rumour"}, "junk"]
        self.assertEqual(moves.parse(raw), (moves.RawMove("LeBron James", "", "Lakers", "trade"),))
        self.assertEqual(moves.parse(None), ())
        self.assertEqual(len(moves.parse([{"person": "A", "kind": "release"}] * 9)), moves.MAX_MOVES)

    def test_propose_writes_once_per_person_per_run(self) -> None:
        written = []
        with mock.patch.object(ledger, "feed_insert_proposal", lambda p, **kw: written.append(p) or len(written)):
            n = moves.propose([(10, "nba", (moves.RawMove("LeBron James", "", "Lakers", "trade"),)),
                               (11, "nba", (moves.RawMove("LeBron James", "", "Celtics", "trade"),))], ctx())
        self.assertEqual((n, [p.story_id for p in written]), (1, [10]))

    def test_propose_reads_nothing_when_there_are_no_moves(self) -> None:
        with mock.patch.object(ledger, "feed_move_context", side_effect=AssertionError("no read")):
            self.assertEqual(moves.propose([(1, "nba", ())]), 0)


class SummaryCall(unittest.TestCase):
    GOOD = {"id": 1, "summary": "The club named a new coach on Monday.", "entities": ["LeBron James"],
            "topics": ["trade"], "sport": "nba", "drop": "none"}

    def job(self, sid: int = 1, sport: str | None = "nba", slug: str = "espn-nba") -> ledger.SummaryJob:
        return ledger.SummaryJob(sid, "Headline here", "A teaser.", "ESPN", sport, 0, slug)

    def test_moves_is_a_required_field_and_the_prompt_forbids_rumours(self) -> None:
        item = summarize.schema()["properties"]["stories"]["items"]
        self.assertIn("moves", item["required"])
        self.assertEqual(item["properties"]["moves"]["items"]["properties"]["kind"]["enum"], list(moves.KINDS))
        for phrase in ("HAS happened", "Never for a rumour", "almost always []"):
            self.assertIn(phrase, summarize.SYSTEM)

    def test_accepted_moves_are_collected_with_the_final_sport(self) -> None:
        found: list = []
        item = dict(self.GOOD, moves=[{"person": "LeBron James", "from": "", "to": "Lakers", "kind": "trade"}])
        summarize.results_for([self.job()], {"stories": [item]}, found=found)
        self.assertEqual(found, [(1, "nba", (moves.RawMove("LeBron James", "", "Lakers", "trade"),))])

    def test_d8_the_model_sport_wins_for_a_multi_sport_source(self) -> None:
        al = self.job(sport="cfb", slug="al-com-alabama")
        (r,), _, _ = summarize.results_for([al], {"stories": [dict(self.GOOD, sport="nfl")]},
                                           model_sport=frozenset({"al-com-alabama"}))
        self.assertTrue(r.sport_from_model)
        self.assertEqual(summarize.story_sport(al, "nfl", True), "nfl")
        self.assertEqual(summarize.story_sport(al, None, True), "cfb")   # fallback: the source's
        self.assertEqual(summarize.story_sport(self.job(sport="nba"), "nfl", False), "nba")
        sent = json.loads(summarize.user_message([al], frozenset({"al-com-alabama"})).split("\n", 1)[1])
        self.assertIsNone(sent[0]["sport"])  # the model is not told cfb

    def test_d8_al_com_is_the_multi_sport_source(self) -> None:
        self.assertEqual(sources.model_sport_slugs(), frozenset({"al-com-alabama"}))
        with self.assertRaises(sources.SourcesInvalid):
            sources._spec({"slug": "x", "name": "X", "pillars": ["sports"], "kind": "rss",
                           "url": "https://x.invalid/", "sport_from": "guess"})

    def test_d8_the_save_sql_mirrors_story_sport(self) -> None:
        import inspect
        src = inspect.getsource(ledger.feed_save_summaries)
        self.assertIn("sport = CASE WHEN %s THEN coalesce(%s, sport) ELSE coalesce(sport, %s) END", src)


class QueuePriority(unittest.TestCase):
    FOLLOWS = [LEBRON, SIXERS, ALABAMA, SCHEFFLER, RAHM,
               Follow("J.J. Spaun", 2, "sports", False, ("j.j. spaun",), "golf", kind="person")]

    def test_people_and_derived_teams_tier_one_and_two_only(self) -> None:
        pat = rank.priority_pattern(self.FOLLOWS)
        # PostgreSQL ARE and Python re agree on this subset of syntax.
        rx = re.compile(pat)
        for title in ("lebron on 10-point sixers debut", "76ers win", "scheffler leads", "j.j. spaun wins"):
            self.assertRegex(title, rx)
        for title in ("alabama beats georgia", "rahm leaves liv", "flebronx", "jj spaun wins"):
            self.assertNotRegex(title, rx)

    def test_no_follows_no_priority(self) -> None:
        self.assertIsNone(rank.priority_pattern([ALABAMA]))

    def test_the_queue_sql_orders_priority_first_then_publish_time(self) -> None:
        self.assertIn("ORDER BY coalesce(lower(st.title) ~ %(priority)s::text, false) DESC, i.published_at DESC",
                      ledger.FEED_SUMMARY_QUEUE_SQL)

    def test_derived_team_follows_come_from_the_current_link(self) -> None:
        self.assertIn("JOIN v_entity_links_current l ON l.from_entity_id = p.id", ledger.FEED_FOLLOWS_SQL)
        self.assertIn("WHERE f.tier = 1", ledger.FEED_FOLLOWS_SQL)
        self.assertIn("NOT e.is_test", ledger.FEED_FOLLOWS_SQL)


class PillarSeparation(unittest.TestCase):
    """A Love on Sports changes the sports profile and never Home's: each
    pillar's profile is built from that pillar's reactions alone."""

    def test_a_sports_love_trains_only_the_sports_profile(self) -> None:
        from feed import profile as feed_profile

        vec = (1.0, 0.0, 0.0)
        loved = ledger.RankStory(7, ("home", "sports"), "nba", "LeBron on the Sixers", (), (), "espn-nba", vec)
        asked: list[str] = []
        built: dict[str, rank.Profile] = {}

        def signals(pillar: str, **kw):
            asked.append(pillar)
            return [ledger.ReactionSignal(loved, "love", False)] if pillar == "sports" else []

        def insert(p, **kw):
            built[p.pillar] = p
            return len(built)

        with mock.patch.object(ledger, "feed_seed_phrases", lambda **kw: {}), \
                mock.patch.object(ledger, "feed_mean_vector", lambda pillar, days, **kw: ((0.0, 0.0, 0.0), 5)), \
                mock.patch.object(ledger, "feed_reaction_signals", signals), \
                mock.patch.object(ledger, "feed_insert_profile", insert):
            feed_profile.build_profiles(embed=lambda texts: [])
        self.assertEqual(asked, ["home", "sports"])
        self.assertEqual((built["sports"].love_count, built["home"].love_count), (1, 0))
        self.assertIsNone(built["home"].love)
        self.assertIsNotNone(built["sports"].love)


class SportsSeeds(unittest.TestCase):
    """db/042's sports seeds are the design note's §2 phrases, verbatim."""

    def test_the_phrases(self) -> None:
        doc = (REPO_ROOT / "docs" / "sports_news.md").read_text(encoding="utf-8")
        sec = doc[doc.index("## 2. Sports seeds"):doc.index("## 3.")]
        phrases = re.findall(r"^\| \d \| `([^`]+)` \|", sec, flags=re.M)
        self.assertEqual(len(phrases), 10)
        block = SQL_042[SQL_042.index("'{feed,sports}'"):SQL_042.index("updated_at = now()")]
        sql_phrases = [p.replace("''", "'") for p in re.findall(r"'((?:[^']|'')+)'", block)[1:]]
        sql_phrases = [p for p in sql_phrases if p not in ("love", "not_interested")]
        self.assertEqual(sql_phrases, phrases)

    def test_set_once(self) -> None:
        self.assertIn("coalesce(jsonb_array_length(seed_interests #> '{feed,sports,love}'), 0) = 0", SQL_042)


if __name__ == "__main__":
    unittest.main()
