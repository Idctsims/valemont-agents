"""The verifier yield fix (Chat 5's last item; docs/arsenal.md §11).

* ON SUBJECT: the pages that misled the verifier are never candidates (the
  actress for Ozark, Andy Jassy for Bezos, Rufus Sewell for The Diplomat,
  Sam Snead for Sam Zell, a Total War game for Sun Tzu, a soundtrack, the
  bare concept page "Power", another series of the same name); the card's
  own page and its person's page are.
* THE GUARDED TRIM (owner, 2026-11-04): a silent excerpt clears a card as
  "corrected by trimming" only with the core confirmed, the card rewritten,
  every dropped detail listed, and nothing the deploy line, the meaning, the
  title or the year depends on dropped.
* RE-ATTRIBUTION: a correction naming a different person is a contradiction.
* THE UNFOUND QUOTE: a card resting on a quote no source holds stays unverified.
* EPISODES: a TV series' episode summaries are read from its wikitext.

No database, no network, no model.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from arsenal import sources, verify
from tests.test_arsenal import ref

J = verify.Judgement


def card(**kw: Any):
    base = dict(id=1, category="film_tv", title="Managing the carrier-attack crisis",
                origin="In 2023, a British carrier is struck in the Gulf and Kate Wyler takes the London post.",
                source_work="The Diplomat (2023)", quote=None, quote_by=None, source_kind="wikipedia",
                source_page="The Diplomat (American TV series)", verification="pending", pool_run_id=None,
                meaning="Control the sequence before anyone commits.",
                deploy_line="I can decide the order, and that's what I'm bringing.")
    base.update(kw)
    return ref(**base)


def trim_judgement(**kw: Any) -> verify.Judgement:
    base = dict(verdict="corrected", fixed_origin="In 2023 a British carrier is attacked in the Gulf.",
                fixed_source_work=None, note="the core is confirmed", covers_every_claim=False,
                core_confirmed=True, dropped_details=("Washington and London push different responses",),
                deploy_depends_on_dropped=False)
    base.update(kw)
    return J(**base)


class OnSubject(unittest.TestCase):
    def test_the_pages_that_misled_it_are_refused(self) -> None:
        cases = [
            (card(), "Rufus Sewell"),
            (card(), "The Diplomat (British TV series)"),
            (card(title="Bezos's Day 1", category="finance_business", source_work="Bezos, 2016 Amazon shareholder letter",
                  source_page="Jeff Bezos"), "Andy Jassy"),
            (card(title="The Grave Dancer", category="finance_business", source_work='Sam Zell, "The Grave Dancer" (1976)',
                  source_page="Sam Zell"), "Sam Snead"),
            (card(title="Win first, then go to war", category="philosophy", source_work="Sun Tzu, The Art of War",
                  source_page="Sun Tzu"), "Total War: Shogun 2"),
            (card(title="Scylla or Charybdis", source_work="The Odyssey (2026)", source_page="The Odyssey (film)"),
             "The Odyssey (soundtrack)"),
            (card(title="Ghost's two lives", source_work="Power (2014)", source_page="Power (TV series)"), "Power"),
        ]
        for c, page in cases:
            with self.subTest(page=page):
                self.assertFalse(verify.on_subject(c, page)[0], verify.on_subject(c, page)[1])

    def test_the_cards_own_person_and_work_pages_pass(self) -> None:
        bezos = card(title="Bezos's Day 1", category="finance_business",
                     source_work="Bezos, 2016 Amazon shareholder letter", source_page="Jeff Bezos")
        self.assertTrue(verify.on_subject(card(), "The Diplomat (American TV series)")[0])
        self.assertTrue(verify.on_subject(bezos, "Jeff Bezos")[0])
        self.assertTrue(verify.on_subject(card(title="Win first", category="philosophy",
                                               source_work="Sun Tzu, The Art of War", source_page="Sun Tzu"),
                                          "The Art of War")[0])
        # A film's episode list names the work: allowed for film and TV.
        self.assertTrue(verify.on_subject(card(source_page="Succession (TV series)", source_work="Succession (2018)"),
                                          "List of Succession episodes")[0])

    def test_episode_pages_only_for_a_series(self) -> None:
        self.assertEqual(verify.episode_pages(card()), ["List of The Diplomat (American TV series) episodes",
                                                        "List of The Diplomat episodes"])
        self.assertEqual(verify.episode_pages(card(source_page="Heat (1995 film)")), [])
        self.assertEqual(verify.episode_pages(card(category="sports")), [])


class GuardedTrim(unittest.TestCase):
    def test_a_guarded_trim_clears_as_corrected_and_lists_what_it_dropped(self) -> None:
        j = verify.held(trim_judgement(), card())
        self.assertEqual(j.verdict, "corrected")
        self.assertTrue(j.note.startswith(verify.TRIMMED))
        self.assertIn("Washington and London push different responses", j.note)
        self.assertEqual(verify.outcome(quote_dropped=False, judgement=j, unreachable=None)[0], "corrected")

    def test_every_guard_refuses_the_trim(self) -> None:
        cases = {
            "the core not confirmed": trim_judgement(core_confirmed=False),
            "nothing listed as dropped": trim_judgement(dropped_details=()),
            "no rewrite": trim_judgement(fixed_origin=None),
            "the judge says the line depends on it": trim_judgement(deploy_depends_on_dropped=True),
            "an outcome conflict rides on it": trim_judgement(outcome_conflict=True),
            "a dropped detail names the card's title": trim_judgement(dropped_details=("the carrier-attack crisis",)),
            "a dropped detail is the deploy line's point": trim_judgement(dropped_details=("decides the order",)),
            "the year does not survive": trim_judgement(fixed_origin="A British carrier is attacked in the Gulf."),
        }
        for why, j in cases.items():
            with self.subTest(why=why):
                self.assertEqual(verify.held(j, card()).verdict, "insufficient")

    def test_the_grave_dancer_year_is_never_trimmed(self) -> None:
        zell = card(title="The Grave Dancer", category="finance_business",
                    origin="Sam Zell took the nickname from his 1976 article on distressed real estate.",
                    source_work='Sam Zell, "The Grave Dancer" (1976)', source_page="Sam Zell",
                    meaning="Buy what others bury.", deploy_line="Everyone's selling; I'm buying.")
        j = trim_judgement(fixed_origin="Sam Zell wrote an article on distressed property in 1982.",
                           dropped_details=("the 1976 date",))
        self.assertEqual(verify.held(j, zell).verdict, "insufficient")


class Guards(unittest.TestCase):
    def test_a_correction_to_another_person_is_a_contradiction(self) -> None:
        cake = card(title="Let them eat cake", category="history_military", source_work="Marie Antoinette, 1789",
                    source_page="Let them eat cake")
        j = J("corrected", "The phrase predates her.", "Jean-Jacques Rousseau, Confessions", "it is Rousseau's")
        self.assertEqual(verify.reattributed(cake, j).verdict, "contradicted")
        # A work with no person (Roc-A-Fella's album) is a detail fix.
        roc = card(title="Roc-A-Fella", category="music_culture", source_work="Roc-A-Fella Records, 1995",
                   source_page="Roc-A-Fella Records")
        self.assertEqual(verify.reattributed(roc, J("corrected", "x" * 12, "Reasonable Doubt (1996)", "")).verdict,
                         "corrected")

    def test_a_card_resting_on_an_unfound_quote_stays_unverified(self) -> None:
        vader = card(title="Luke, I am your father", source_work="The Empire Strikes Back (1980)",
                     source_page="The Empire Strikes Back", quote="Luke, I am your father.", quote_by="Darth Vader",
                     origin="Darth Vader reveals himself with the exact line 'Luke, I am your father.'")
        self.assertTrue(verify.rests_on_unfound_quote(vader))
        self.assertFalse(verify.rests_on_unfound_quote(card()))

    def test_an_uncovered_outcome_reframe_is_held(self) -> None:
        j = J("corrected", None, None, "reframed", outcome_conflict=True, fixed_meaning="A loss teaches.",
              covers_every_claim=False)
        held = verify.held(j, card())
        self.assertEqual((held.verdict, held.outcome_conflict), ("insufficient", True))


class Refusals(unittest.TestCase):
    """db/064 (owner, 2026-11-04): every refusal counted by category and reason."""

    def test_reasons(self) -> None:
        from arsenal import collect
        self.assertEqual(collect.refusal_reason("repeats crossing_the_rubicon"), "duplicate_key")
        for why in ("bad canonical_key 'X'", "unknown category 'cooking'", "unknown room 'x'",
                    "title, origin missing or out of bounds", "the sentence does not use 'x'", "not an object"):
            with self.subTest(why=why):
                self.assertEqual(collect.refusal_reason(why), "invalid_field")
        self.assertEqual(collect.refusal_reason("something new"), "other")

    def test_insert_candidates_counts_every_refusal(self) -> None:
        from unittest import mock

        from arsenal import cards, collect
        from core import ledger
        ok = {"canonical_key": "fresh_one", "category": "sports", "title": "Fresh one",
              "origin": "An origin long enough to pass.", "meaning": "A meaning.", "deploy_line": "A line to deploy.",
              "room": "pitch", "source_work": "A work", "quote": None, "quote_by": None, "source_kind": "wikipedia",
              "source_page": "A page"}
        refs = [dict(ok, canonical_key="old_key", title="Old one"),                       # repeats a blocked key
                dict(ok, canonical_key="bad_cat", title="Bad one", category="cooking"),   # unknown category
                dict(ok, canonical_key="near_one", title="Near one", category="film_tv"),  # near-duplicate
                dict(ok, canonical_key="taken_key", title="Taken one", category="film_tv"),  # UNIQUE at insert
                ok]
        words = [{"lemma": "known", "meaning": "a meaning", "room": "pitch", "sentence": "Say known here."}]
        with mock.patch.object(ledger, "arsenal_nearest", lambda v: ("old", 0.99 if v == [9.0] else 0.5)), \
                mock.patch.object(ledger, "arsenal_insert_ref",
                                  lambda card, **kw: None if card.canonical_key == "taken_key" else 7), \
                mock.patch.object(ledger, "arsenal_insert_word", lambda *a, **kw: 9):
            ins = collect.insert_candidates(
                refs, words, run_id=1, blocked_keys={"old_key"}, blocked_lemmas={"known"},
                embed=lambda texts: [[9.0] if t.startswith("Near one") else [0.1] for t in texts])
        self.assertTrue(cards.CATEGORIES)
        self.assertEqual(ins.refusals, {"sports": {"duplicate_key": 1}, "unknown": {"invalid_field": 1},
                                        "film_tv": {"near_dup": 1, "duplicate_key": 1},
                                        "words": {"duplicate_key": 1}})
        self.assertEqual((ins.skipped_invalid, ins.skipped_dup, ins.ref_ids), (3, 2, [7]))


class Episodes(unittest.TestCase):
    def test_summaries_are_read_from_the_episode_tables(self) -> None:
        wikitext = ("== Episodes ==\n{{Episode list\n| EpisodeNumber = 1\n| Title = The Cinderella Thing\n"
                    "| ShortSummary = The British aircraft carrier [[HMS Courageous]] is attacked in the Persian Gulf. "
                    "Kate Wyler accepts the London post instead of Kabul.\n| LineColor = 333\n}}\n")

        def get(url: str, params: Any) -> tuple[int, str, None]:
            return 200, json.dumps({"parse": {"wikitext": wikitext}}), None
        s = sources.episode_summaries("The Diplomat (American TV series)", http=get)
        self.assertEqual(s.heading, "Episodes")
        self.assertIn("The Cinderella Thing: The British aircraft carrier HMS Courageous is attacked", s.text)
        self.assertIn("Kabul", s.text)
        self.assertIsNone(sources.episode_summaries("Heat (1995 film)",
                                                    http=lambda u, p: (200, json.dumps({"parse": {"wikitext": "x"}}), None)))


if __name__ == "__main__":
    unittest.main()
