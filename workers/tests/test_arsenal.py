"""The Arsenal (Chat 5 Phase 1, db/054): the worker's logic, no network and
no database.

    prompt      the system blocks (voice, rules, seeds and recent keys,
                cached), the two batch requests, the schemas, the split
    cards       validation: a quote over 15 words goes (the card stays),
                repeats and seeds are refused, keys are normalized; words
                must be used in their sentence
    sources     on RECORDED fixtures (tests/fixtures/arsenal, fetched
                2026-10-15): Wikiquote sections (Disputed / Misattributed /
                Quotes about never count), the fuzzy quote match, Wikipedia's
                summary and extract, kaikki's IPA and etymology
    verify      the mapping (the worse of quote and judge wins), and whole
                verifications against the fixtures through a fake HTTP
    pool        when a batch is submitted; the top-up's split
    collect     near-duplicates, UNIQUE skips and invalid cards are counted,
                never errors; an embedding failure still inserts
    drop        zero AI calls when the pool is full; the top-up path when short
    ai          batches: the budget guard at submit, usage at the batch rate
    brief       the one Arsenal line, in AI and fallback briefs
    gate fixes  (owner, 2026-10-16) film/TV quotes credit the character and
                the work; the known words are refused; sections and the
                matched excerpt; search-located pages (a disambiguation
                resolved to the work's page); one second source on
                'insufficient', never on 'contradicted'; the judge held to
                its own coverage answer; the outcome check
"""

from __future__ import annotations

import json
import logging
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from arsenal import cards, collect, drop, pool, sources, verify
from core import ai, brief, ledger
from tests.support import _refuse_database

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "054_arsenal.sql"
FIX = Path(__file__).with_name("fixtures") / "arsenal"
NOW = datetime(2026, 10, 18, 8, 30, tzinfo=timezone.utc)


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class NoDatabase(unittest.TestCase):
    """The pool and the network refuse: every ledger call a test needs is patched."""

    def setUp(self) -> None:
        for target in (mock.patch.object(ledger, "_pool", _refuse_database),
                       mock.patch.object(sources, "_httpx", _refuse_database)):
            target.start()
            self.addCleanup(target.stop)

    def logs_on(self) -> None:
        """tests/__init__.py silences logging; a test that checks log lines turns it back on."""
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)

    def patch(self, name: str, value: Any) -> Any:
        p = mock.patch.object(ledger, name, value)
        got = p.start()
        self.addCleanup(p.stop)
        return got


def card(**kw: Any) -> dict[str, Any]:
    base = {"canonical_key": "crossing_the_rubicon_2", "category": "history_military", "title": "Crossing the Rubicon",
            "origin": "Julius Caesar took a legion across the Rubicon in 49 BC.", "meaning": "No way back.",
            "deploy_line": "Once we sign, we've crossed the Rubicon.", "room": "boardroom",
            "source_work": "Julius Caesar, 49 BC", "quote": None, "quote_by": None, "source_kind": "wikipedia",
            "source_page": "Crossing the Rubicon"}
    base.update(kw)
    return base


# ====================================================================== prompt

class Prompt(NoDatabase):
    CTX = cards.PromptContext(seeds=[("crossing_the_rubicon", "Crossing the Rubicon", "Julius Caesar, 49 BC")],
                              recent_keys=["the_medici_bank"], recent_lemmas=["asymmetric"])

    def test_system_blocks_voice_rules_then_the_cached_lists(self) -> None:
        blocks = cards.system_blocks(self.CTX)
        self.assertEqual(len(blocks), 3)
        self.assertTrue(blocks[0]["text"].startswith("You write the Arsenal"))
        self.assertIn("finance_business", blocks[1]["text"])
        self.assertIn("at most 15 words", blocks[1]["text"])
        self.assertNotIn("cache_control", blocks[0])
        self.assertEqual(blocks[2]["cache_control"], {"type": "ephemeral"})
        for needle in ("NEVER to repeat", "Crossing the Rubicon", "the_medici_bank", "asymmetric"):
            self.assertIn(needle, blocks[2]["text"])

    def test_one_batch_two_requests_with_structured_output(self) -> None:
        split = cards.allocate(30, {c: 1.0 for c in cards.CATEGORIES})
        reqs = pool.batch_requests(self.CTX, split, 10)
        self.assertEqual([r.custom_id for r in reqs], ["refs", "words"])
        for r in reqs:
            self.assertEqual(r.params["thinking"], {"type": "between_tools"})
            self.assertEqual(r.params["output_config"]["format"]["type"], "json_schema")
            self.assertIs(r.params["system"][2]["cache_control"]["type"], "ephemeral")
        self.assertIn("Write 30 references", reqs[0].params["messages"][0]["content"])
        self.assertIn("Write 10 Power Words", reqs[1].params["messages"][0]["content"])
        item = reqs[0].params["output_config"]["format"]["schema"]["properties"]["refs"]["items"]
        self.assertEqual(set(item["required"]), set(item["properties"]))
        self.assertEqual(item["properties"]["category"]["enum"], list(cards.CATEGORIES))
        self.assertEqual(item["properties"]["room"]["enum"], ["boardroom", "pitch", "negotiation", "homies"])

    def test_the_split_follows_the_weights_and_covers_every_category(self) -> None:
        weights = {"finance_business": 1.0, "history_military": 1.0, "film_tv": 1.0, "sports": 1.0,
                   "music_culture": 1.0, "philosophy": 0.6, "literature_myth": 0.3}
        split = cards.allocate(30, weights)
        self.assertEqual(sum(split.values()), 30)
        self.assertTrue(all(n >= 1 for n in split.values()))
        self.assertGreater(split["finance_business"], split["philosophy"])
        self.assertGreater(split["philosophy"], split["literature_myth"])
        # A Love-heavy category gets more of the next batch.
        more = cards.allocate(30, {**weights, "sports": 2.0})
        self.assertGreater(more["sports"], split["sports"])

    def test_the_categories_and_rooms_match_the_migration(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        cats = re.search(r"category IN \(([^)]*)\)", sql.replace("\n", " "))
        self.assertEqual(re.findall(r"'([a-z_]+)'", cats.group(1)), list(cards.CATEGORIES))
        self.assertIn("room IN ('boardroom', 'pitch', 'negotiation', 'homies')", sql)
        self.assertIn("<= 15", sql)  # the quote CHECK
        self.assertEqual(collect.NEAR_DUP, 0.92)
        self.assertIn("p_threshold real DEFAULT 0.92", sql)


# ======================================================================= cards

class Cards(unittest.TestCase):
    def test_a_long_quote_goes_and_the_card_stays(self) -> None:
        sixteen = " ".join(["word"] * 16)
        got, why = cards.validate_ref(card(quote=sixteen, quote_by="Someone"), set())
        self.assertIsNone(why)
        self.assertIsNone(got.quote)
        self.assertIsNone(got.quote_by)
        fifteen = " ".join(["word"] * 15)
        got, _ = cards.validate_ref(card(quote=f"“{fifteen}”", quote_by="Someone"), set())
        self.assertEqual(got.quote, fifteen)
        self.assertEqual(got.quote_by, "Someone")

    def test_repeats_and_seeds_are_refused_and_keys_normalized(self) -> None:
        self.assertEqual(cards.validate_ref(card(canonical_key="crossing_the_rubicon"), {"crossing_the_rubicon"})[1],
                         "repeats crossing_the_rubicon")
        got, _ = cards.validate_ref(card(canonical_key="Hannibal's Alps!"), set())
        self.assertEqual(got.canonical_key, "hannibal_s_alps")
        for bad in ({"category": "cooking"}, {"room": "kitchen"}, {"source_kind": "blog"}, {"origin": "short"},
                    {"deploy_line": "x" * 221}):
            with self.subTest(bad=bad):
                self.assertIsNone(cards.validate_ref(card(**bad), set())[0])

    def test_a_power_word_is_used_in_its_sentence(self) -> None:
        word = {"lemma": "Asymmetric", "meaning": "Uneven on purpose.", "room": "pitch",
                "sentence": "The upside here is asymmetric: we risk a season, they risk the market."}
        got, why = cards.validate_word(word, set())
        self.assertIsNone(why)
        self.assertEqual(got.lemma, "asymmetric")
        self.assertIsNone(cards.validate_word({**word, "sentence": "We should be bold here, honestly."}, set())[0])
        self.assertEqual(cards.validate_word(word, {"asymmetric"})[1], "repeats asymmetric")
        self.assertIsNone(cards.validate_word({**word, "lemma": "SAT-word2"}, set())[0])


# ===================================================================== sources

class Wikiquote(unittest.TestCase):
    def lines(self, name: str) -> list[str]:
        return sources.quote_lines(json.loads(fixture(name))["parse"]["wikitext"])

    def test_sections_that_are_not_the_subjects_words_never_count(self) -> None:
        wikitext = json.loads(fixture("wikiquote_sun_tzu.json"))["parse"]["wikitext"]
        allowed = sources.quote_lines(wikitext)
        everything = [sources.strip_markup(ln.lstrip("*:# ")) for ln in wikitext.splitlines()
                      if ln.strip()[:1] in ("*", ":", "#")]
        misattributed = "Keep your friends close, and your enemies closer."
        self.assertTrue(sources.quote_found(misattributed, everything))  # it is on the page...
        self.assertFalse(sources.quote_found(misattributed, allowed))    # ...under Misattributed only
        self.assertTrue(sources.quote_found("Victorious warriors win first and then go to war", allowed))
        # The paraphrase is not Sun Tzu's text.
        self.assertFalse(sources.quote_found("Every battle is won before it is fought", allowed))

    def test_the_godfather_line_matches_exactly_and_fuzzily(self) -> None:
        lines = self.lines("wikiquote_the_godfather.json")
        self.assertTrue(sources.quote_found("Never tell anybody outside the family what you're thinking again.", lines))
        self.assertTrue(sources.quote_found("never tell anybody outside the family what you’re thinking again", lines))
        self.assertTrue(sources.quote_found("Never tell anybody outside the family what you are thinking again", lines))
        self.assertFalse(sources.quote_found("Never trust anybody outside the family, ever", lines))
        # "Quotes about The Godfather" is about the film, not from it.
        self.assertFalse(sources.quote_found("that book took me to another world", lines))

    def test_markup_is_stripped(self) -> None:
        self.assertEqual(sources.strip_markup("'''Never''' [[w:Sonny|Sonny]], {{citation|x}}see<ref>r</ref> it"),
                         "Never Sonny, see it")


class Wikipedia(unittest.TestCase):
    def http(self, routes: dict[str, tuple[int, str]]) -> Any:
        def get(url: str, params: Any) -> tuple[int, str, None]:
            for prefix, (status, body) in routes.items():
                if url.startswith(prefix):
                    return status, body, None
            return 404, "", None
        return get

    def test_summary_url_and_the_extract_as_text(self) -> None:
        page = sources.wikipedia_page("Crossing the Rubicon", http=self.http({
            sources.WIKIPEDIA_SUMMARY: (200, fixture("wikipedia_summary_crossing_the_rubicon.json")),
            sources.WIKIPEDIA_API: (200, fixture("wikipedia_extract_crossing_the_rubicon.json"))}))
        self.assertEqual(page.url, "https://en.wikipedia.org/wiki/Crossing_the_Rubicon")
        self.assertIn("49 BC", page.text)
        self.assertGreater(len(page.text), 1000)  # the extract, not the 448-character lead
        self.assertTrue(sources.quote_found("The die is cast.", sources.sentences(page.text)))

    def test_missing_and_disambiguation_pages_are_none(self) -> None:
        self.assertIsNone(sources.wikipedia_page("Nope", http=self.http({})))
        self.assertIsNone(sources.wikipedia_page("Mercury", http=self.http({
            sources.WIKIPEDIA_SUMMARY: (200, json.dumps({"type": "disambiguation", "title": "Mercury"}))})))

    def test_429_waits_then_retries_and_two_failures_raise(self) -> None:
        calls: list[float] = []
        answers = iter([(429, "", 3.0), (200, "{}", None)])
        status, _ = sources.fetch("https://x.invalid", http=lambda u, p: next(answers), sleep=calls.append)
        self.assertEqual((status, calls), (200, [3.0]))
        with self.assertRaises(sources.SourceUnavailable):
            sources.fetch("https://x.invalid", http=lambda u, p: (503, "", None), sleep=lambda s: None)
        with self.assertRaises(sources.SourceUnavailable):
            sources.fetch("https://x.invalid", http=lambda u, p: (_ for _ in ()).throw(TimeoutError()),
                          sleep=lambda s: None)


class Kaikki(unittest.TestCase):
    def test_ipa_and_a_two_line_etymology_from_the_source(self) -> None:
        def get(url: str, params: Any) -> tuple[int, str, None]:
            self.assertEqual(url, "https://kaikki.org/dictionary/English/meaning/a/as/asymmetric.jsonl")
            return 200, fixture("kaikki_asymmetric.jsonl"), None
        src = sources.word_source("asymmetric", http=get)
        self.assertEqual(src.source, "kaikki")
        self.assertTrue(src.pronunciation.startswith("/") and "s" in src.pronunciation)
        self.assertTrue(src.etymology.startswith("From a- + symmetric"))
        self.assertLessEqual(len(src.etymology), sources.ETYMOLOGY_CHARS)
        self.assertEqual(src.url, "https://en.wiktionary.org/wiki/asymmetric#Etymology")

    def test_two_lines(self) -> None:
        first = ("From Latin fiduciarius, held in trust, from fiducia, trust, confidence, the reliance one party "
                 "places on another to act in its interest.")
        self.assertEqual(sources.two_lines(first + " " + "word " * 80, 200), first)
        # A sentence end that would keep less than half the room is not used.
        short = sources.two_lines("Short one. " + "word " * 80, 200)
        self.assertTrue(short.startswith("Short one. word") and short.endswith("…"))
        self.assertLessEqual(len(short), 200)


# ===================================================================== verify

def ref(**kw: Any) -> ledger.ArsenalRefToVerify:
    base = dict(id=1, category="history_military", title="Crossing the Rubicon",
                origin="Julius Caesar crossed the Rubicon in 49 BC.", source_work="Julius Caesar, 49 BC", quote=None,
                quote_by=None, source_kind="wikipedia", source_page="Crossing the Rubicon", verification="pending",
                pool_run_id=7)
    base.update(kw)
    return ledger.ArsenalRefToVerify(**base)


def judged(verdict: str, origin: str | None = None, work: str | None = None) -> verify.Ask:
    return lambda r, excerpt: verify.Judgement(verdict, origin, work, f"{verdict} by the excerpt")


class Mapping(unittest.TestCase):
    J = verify.Judgement

    def test_the_worse_of_quote_and_judge_wins(self) -> None:
        cases = [
            (False, self.J("supported", None, None, ""), "verified"),
            (True, self.J("supported", None, None, ""), "corrected"),
            (False, self.J("corrected", "A fixed origin, long enough.", None, "date"), "corrected"),
            (True, self.J("corrected", None, "Fixed work", "name"), "corrected"),
            (False, self.J("corrected", None, None, "no wording"), "unverified"),
            (False, self.J("contradicted", None, None, "x"), "unverified"),
            (True, self.J("insufficient", None, None, "x"), "unverified"),
        ]
        for dropped, j, want in cases:
            with self.subTest(dropped=dropped, verdict=j.verdict):
                self.assertEqual(verify.outcome(quote_dropped=dropped, judgement=j, unreachable=None)[0], want)
        self.assertEqual(verify.outcome(quote_dropped=False, judgement=None, unreachable="down")[0], "unverified")

    def test_an_unreadable_judge_is_insufficient(self) -> None:
        self.assertEqual(verify.parse_judgement({"verdict": "sure"}).verdict, "insufficient")
        self.assertEqual(verify.parse_judgement(None).verdict, "insufficient")
        j = verify.parse_judgement({"verdict": "corrected", "fixed_origin": "x", "fixed_source_work": "Ok work",
                                    "note": "n"})
        self.assertIsNone(j.fixed_origin)  # too short to use
        self.assertEqual(j.fixed_source_work, "Ok work")


class VerifyRef(unittest.TestCase):
    def http(self) -> Any:
        routes = {
            sources.WIKIPEDIA_SUMMARY + "Crossing_the_Rubicon": fixture("wikipedia_summary_crossing_the_rubicon.json"),
            sources.WIKIPEDIA_SUMMARY + "Sun_Tzu": json.dumps({"type": "standard", "title": "Sun Tzu",
                                                               "extract": "Sun Tzu was a Chinese general.",
                                                               "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Sun_Tzu"}}}),
        }

        def get(url: str, params: Any) -> tuple[int, str, None]:
            if url == sources.WIKIQUOTE_API:
                name = {"Sun Tzu": "wikiquote_sun_tzu.json", "The Godfather": "wikiquote_the_godfather.json"}
                page = params.get("page")
                return (200, fixture(name[page]), None) if page in name else (200, '{"error":{"code":"missingtitle"}}', None)
            if url == sources.WIKIPEDIA_API:
                if params.get("titles") == "Crossing the Rubicon":
                    return 200, fixture("wikipedia_extract_crossing_the_rubicon.json"), None
                return 200, '{"query":{"pages":[{"missing":true}]}}', None
            if url in routes:
                return 200, routes[url], None
            return 404, "", None
        return get

    def test_a_supported_card_is_verified_and_links_wikipedia(self) -> None:
        excerpts: list[str] = []
        v = verify.verify_ref(ref(quote="The die is cast.", quote_by="Julius Caesar"), http=self.http(),
                              ask=lambda r, e: excerpts.append(e) or verify.Judgement("supported", None, None, "ok"))
        self.assertEqual((v.verification, v.drop_quote), ("verified", False))
        self.assertEqual(v.source_url, "https://en.wikipedia.org/wiki/Crossing_the_Rubicon")
        self.assertIn("QUOTED LINE", excerpts[0])
        self.assertLessEqual(len(excerpts[0]), sources.JUDGE_CHARS + 200)

    def test_a_misattributed_quote_is_dropped_and_the_card_is_corrected(self) -> None:
        v = verify.verify_ref(ref(category="philosophy", title="Sun Tzu", source_kind="wikiquote", source_page="Sun Tzu",
                                  quote="Keep your friends close, and your enemies closer.", quote_by="Sun Tzu"),
                              http=self.http(), ask=judged("supported"))
        self.assertEqual((v.verification, v.drop_quote), ("corrected", True))
        self.assertIn("quote not found", v.note)

    def test_a_real_quote_links_wikiquote(self) -> None:
        v = verify.verify_ref(ref(category="film_tv", title="Never tell anybody outside the family",
                                  source_kind="wikiquote", source_page="The Godfather",
                                  quote="Never tell anybody outside the family what you're thinking again.",
                                  quote_by="Vito Corleone"), http=self.http(), ask=judged("supported"))
        self.assertEqual((v.verification, v.drop_quote), ("verified", False))
        self.assertEqual(v.source_url, "https://en.wikiquote.org/wiki/The_Godfather")

    def test_the_judges_correction_is_carried(self) -> None:
        v = verify.verify_ref(ref(origin="Caesar crossed the Rubicon in 50 BC."), http=self.http(),
                              ask=judged("corrected", "Julius Caesar crossed the Rubicon in January 49 BC.", None))
        self.assertEqual(v.verification, "corrected")
        self.assertEqual(v.origin, "Julius Caesar crossed the Rubicon in January 49 BC.")

    def test_no_page_and_an_unreachable_source_are_unverified_with_the_reason(self) -> None:
        v = verify.verify_ref(ref(title="Nowhere", source_page="Nowhere at all"), http=self.http(), ask=judged("supported"))
        self.assertEqual(v.verification, "unverified")
        self.assertIn("no Wikipedia or Wikiquote page", v.note)
        down = verify.verify_ref(ref(), http=lambda u, p: (503, "", None), ask=judged("supported"))
        self.assertEqual(down.verification, "unverified")
        self.assertIn("source unreachable", down.note)

    def test_a_contradiction_is_unverified(self) -> None:
        v = verify.verify_ref(ref(), http=self.http(), ask=judged("contradicted"))
        self.assertEqual(v.verification, "unverified")
        self.assertIn("contradicted", v.note)


class VerifyWord(unittest.TestCase):
    def test_kaikki_verifies_and_wiktionary_alone_does_not(self) -> None:
        ok = verify.verify_word("asymmetric", http=lambda u, p: (200, fixture("kaikki_asymmetric.jsonl"), None))
        self.assertEqual(ok.verification, "verified")
        self.assertTrue(ok.pronunciation and ok.etymology and ok.etymology_url)

        def wiktionary_only(url: str, params: Any) -> tuple[int, str, None]:
            if url.startswith(sources.WIKTIONARY_DEFINITION):
                return 200, json.dumps({"en": [{"partOfSpeech": "Adjective"}]}), None
            return 404, "", None
        w = verify.verify_word("asymmetric", http=wiktionary_only)
        self.assertEqual(w.verification, "unverified")
        self.assertIn("no etymology source (wiktionary)", w.note)
        none = verify.verify_word("zzzq", http=lambda u, p: (404, "", None))
        self.assertEqual((none.verification, none.note), ("unverified", "not in kaikki or Wiktionary"))

    def test_the_job_payload(self) -> None:
        self.assertEqual(verify.target_of({"ref_id": 4}), ("ref", 4))
        self.assertEqual(verify.target_of({"word_id": "9"}), ("word", 9))
        with self.assertRaises(ValueError):
            verify.target_of({"title_id": 1})


class VerifyItem(NoDatabase):
    def test_settles_once_and_closes_the_run(self) -> None:
        settled: list[dict] = []
        closed: list[int] = []
        self.patch("arsenal_ref_to_verify", lambda i: ref(id=i))
        self.patch("arsenal_settle_ref", lambda i, **kw: settled.append({"id": i, **kw}) or True)
        self.patch("arsenal_close_run_if_settled", lambda run: closed.append(run) or True)
        http = VerifyRef().http()
        self.assertEqual(verify.verify_item("ref", 3, http=http, ask=judged("supported")), "verified")
        self.assertEqual(settled[0]["verification"], "verified")
        self.assertEqual(settled[0]["source_url"], "https://en.wikipedia.org/wiki/Crossing_the_Rubicon")
        self.assertEqual(closed, [7])
        self.patch("arsenal_ref_to_verify", lambda i: ref(id=i, verification="verified"))
        self.assertEqual(verify.verify_item("ref", 3, http=http, ask=judged("supported")), "skipped: not pending")


# ============================================ gate fixes (owner, 2026-10-16)

class FilmQuoteCredit(unittest.TestCase):
    """A film or TV quote credits the character AND the work, never the real
    person alone."""

    def test_the_work_is_added_to_a_bare_speaker(self) -> None:
        self.assertEqual(cards.credit_film_quote("Sean Parker", "The Social Network (2010)"),
                         "Sean Parker (as written in The Social Network)")
        self.assertEqual(cards.credit_film_quote("Logan Roy", "Succession (2018–2023)"),
                         "Logan Roy (as written in Succession)")

    def test_an_already_credited_speaker_is_kept(self) -> None:
        for by in ("Sean Parker (as written in The Social Network)", "Gordon Gekko in Wall Street"):
            with self.subTest(by=by):
                self.assertEqual(cards.credit_film_quote(by, "Wall Street (1987)" if "Gekko" in by
                                                         else "The Social Network (2010)"), by)

    def test_through_validation_and_no_speaker_drops_the_quote(self) -> None:
        film = card(category="film_tv", canonical_key="a_million_dollars", title="A million dollars isn't cool",
                    source_work="The Social Network (2010)", source_kind="wikiquote", source_page="The Social Network",
                    quote="A million dollars isn't cool. You know what's cool? A billion dollars.",
                    quote_by="Sean Parker")
        got, _ = cards.validate_ref(film, set())
        self.assertEqual(got.quote_by, "Sean Parker (as written in The Social Network)")
        bare, _ = cards.validate_ref({**film, "quote_by": None}, set())
        self.assertIsNotNone(bare)
        self.assertIsNone(bare.quote)  # no speaker to credit: the quote goes, the card stays
        # Outside film and TV a real person's own words keep their speaker.
        other, _ = cards.validate_ref(card(quote="The die is cast.", quote_by="Julius Caesar"), set())
        self.assertEqual(other.quote_by, "Julius Caesar")

    def test_the_rules_are_in_the_voice_and_the_prompt(self) -> None:
        voice = (ROOT / "prompts" / "arsenal_voice.md").read_text(encoding="utf-8")
        self.assertIn("(as written in The Social Network)", voice)
        self.assertIn("Thermopylae", voice)
        self.assertIn("never a Power Word", voice)
        blocks = cards.system_blocks(cards.PromptContext(seeds=[], recent_keys=[], recent_lemmas=[],
                                                         known_lemmas=["fiduciary", "inflection", "provenance"]))
        self.assertIn("as written in The Social Network", blocks[1]["text"])
        self.assertIn("Check the ending", blocks[1]["text"])
        self.assertIn("KNOWN POWER WORDS", blocks[2]["text"])
        self.assertIn("fiduciary, inflection, provenance", blocks[2]["text"])


class KnownWords(NoDatabase):
    def test_a_known_word_is_refused_like_a_seed(self) -> None:
        self.patch("arsenal_seeds", lambda: [])
        self.patch("arsenal_recent_keys", lambda n: [])
        self.patch("arsenal_recent_lemmas", lambda n: ["asymmetric"])
        self.patch("arsenal_seed_lemmas", lambda: ["fiduciary", "inflection", "provenance"])
        _, lemmas = collect.blocked()
        self.assertEqual(lemmas, {"asymmetric", "fiduciary", "inflection", "provenance"})
        word = {"lemma": "Fiduciary", "meaning": "Bound to act for another.", "room": "boardroom",
                "sentence": "We have a fiduciary duty here."}
        got, why = cards.validate_word(word, lemmas)
        self.assertIsNone(got)
        self.assertEqual(why, "repeats fiduciary")


class Sections(unittest.TestCase):
    def test_a_plain_extract_splits_at_its_headings(self) -> None:
        got = sources.split_sections("Lead text.\n\n== Early life ==\nBorn.\n\n=== School ===\nSchool.\n\n== Career ==\nWork.")
        self.assertEqual([(s.heading, s.text) for s in got],
                         [("", "Lead text."), ("Early life", "Born."), ("School", "School."), ("Career", "Work.")])

    def test_wikiquote_sections_keep_their_headings_and_skip_the_excluded(self) -> None:
        wikitext = ("== Sourced ==\n* Luck is a matter of preparation.\n== Misattributed ==\n* A fake line.\n"
                    "== Letters to Lucilius ==\n* Every new beginning comes from some other beginning's end.")
        got = sources.quote_sections(wikitext)
        self.assertEqual([s.heading for s in got], ["Sourced", "Letters to Lucilius"])
        self.assertNotIn("fake", " ".join(s.text for s in got))

    def test_the_excerpt_passes_the_matched_section_not_the_first_characters(self) -> None:
        filler = sources.Section("Early life", "He grew up in a small town and went to school there. " * 150)
        target = sources.Section("Business", "In 1997 he wrote the Day 1 letter to Amazon shareholders.")
        ex = sources.excerpt([sources.Section("", "Jeff Bezos is an American businessman."), filler, target],
                             strong=sources.key_terms("Day 1 letter", "Amazon shareholders"), weak=set())
        self.assertIn("Day 1 letter", ex.text)
        self.assertEqual(ex.headings[:2], ("", "Business"))
        self.assertLessEqual(len(ex.text), sources.JUDGE_CHARS)
        # The quote's own section outranks any amount of term overlap.
        quoted = sources.excerpt([filler, sources.Section("Sayings", "Luck is a matter of preparation.")],
                                 strong=sources.key_terms("school town"), weak=set(),
                                 quote="Luck is a matter of preparation")
        self.assertEqual(quoted.headings[0], "Sayings")

    def test_split_work(self) -> None:
        self.assertEqual(verify.split_work("Jay-Z, Diamonds from Sierra Leone (Remix) (2005)"),
                         ("Jay-Z", "Diamonds from Sierra Leone", "Remix 2005"))
        self.assertEqual(verify.split_work("Moneyball (Michael Lewis, 2003)"), (None, "Moneyball", "Michael Lewis, 2003"))
        self.assertEqual(verify.split_work("Bezos, 2016 Amazon shareholder letter"),
                         ("Bezos", "2016 Amazon shareholder letter", ""))
        self.assertEqual(verify.split_work("The Wolf of Wall Street (2013)"), (None, "The Wolf of Wall Street", "2013"))


def summary(title: str) -> str:
    return json.dumps({"type": "standard", "title": title, "extract": f"{title} lead.",
                       "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{sources.page_path(title)}"}}})


class Retrieval(unittest.TestCase):
    """The verifier searches for the work's page, reads its matched section,
    and tries one second source on 'insufficient' (owner, 2026-10-16)."""

    BOOK = ("Moneyball: The Art of Winning an Unfair Game is a 2003 book by Michael Lewis.\n\n"
            "== Background ==\n" + "Publishing history and reviews of the book. " * 200 + "\n\n"
            "== Summary ==\nBilly Beane, general manager of the Oakland Athletics, used on-base percentage "
            "to field a winning team on a small payroll in 2002.")
    FILM = "Moneyball is a 2011 film starring Brad Pitt.\n\n== Cast ==\nBrad Pitt as Billy Beane."

    def http(self, calls: list[str] | None = None) -> Any:
        def get(url: str, params: Any) -> tuple[int, str, None]:
            if calls is not None:
                calls.append(params.get("srsearch") or params.get("titles") or params.get("page") or url)
            if url == sources.WIKIPEDIA_API and params.get("list") == "search":
                hits = ["Moneyball (film)", "Moneyball (book)"] if "Moneyball" in params["srsearch"] else []
                return 200, json.dumps({"query": {"search": [{"title": t} for t in hits]}}), None
            if url == sources.WIKIPEDIA_API:
                text = {"Moneyball (book)": self.BOOK, "Moneyball (film)": self.FILM}.get(params.get("titles"))
                pages = [{"extract": text}] if text else [{"missing": True}]
                return 200, json.dumps({"query": {"pages": pages}}), None
            if url == sources.WIKIPEDIA_SUMMARY + "Moneyball":
                return 200, json.dumps({"type": "disambiguation", "title": "Moneyball"}), None
            for t in ("Moneyball (book)", "Moneyball (film)"):
                if url == sources.WIKIPEDIA_SUMMARY + sources.page_path(t):
                    return 200, summary(t), None
            if url == sources.WIKIQUOTE_API:
                return 200, '{"error":{"code":"missingtitle"}}', None
            return 404, "", None
        return get

    MONEYBALL = dict(category="sports", title="Moneyball", source_page="Moneyball",
                     origin="Billy Beane's Oakland A's won on on-base percentage with a small payroll in 2002.",
                     source_work="Moneyball (Michael Lewis, 2003)")

    def test_a_disambiguation_is_resolved_to_the_works_page_and_its_section(self) -> None:
        excerpts: list[str] = []

        def ask(r: Any, e: str) -> verify.Judgement:
            excerpts.append(e)
            return verify.Judgement("supported" if "on-base percentage" in e else "insufficient", None, None, "x")
        v = verify.verify_ref(ref(**self.MONEYBALL), http=self.http(), ask=ask)
        self.assertEqual(v.verification, "verified")
        self.assertEqual(v.judged_pages, ("Moneyball (book)",))
        self.assertEqual(v.source_url, "https://en.wikipedia.org/wiki/Moneyball_(book)")
        # The matched section sits past the first 5,000 characters; it is what was passed.
        self.assertGreater(self.BOOK.index("== Summary =="), 5000)
        self.assertIn("== Summary ==", excerpts[0])

    def test_insufficient_tries_one_second_source_then_lands(self) -> None:
        verdicts = iter(["insufficient", "supported"])
        v = verify.verify_ref(ref(**self.MONEYBALL), http=self.http(),
                              ask=lambda r, e: verify.Judgement(next(verdicts), None, None, "x"))
        self.assertEqual(v.verification, "verified")
        self.assertEqual(v.judged_pages, ("Moneyball (book)", "Moneyball (film)"))
        self.assertIn("first source insufficient (Moneyball (book)); second source Moneyball (film)", v.note)
        both = verify.verify_ref(ref(**self.MONEYBALL), http=self.http(), ask=judged("insufficient"))
        self.assertEqual((both.verification, len(both.judged_pages)), ("unverified", 2))  # one retry, no more

    def test_contradicted_never_retries(self) -> None:
        v = verify.verify_ref(ref(**self.MONEYBALL), http=self.http(), ask=judged("contradicted"))
        self.assertEqual((v.verification, v.judged_pages), ("unverified", ("Moneyball (book)",)))


class JudgeHeldToCoverage(unittest.TestCase):
    """'Supported' or 'corrected' on an excerpt the judge itself says does not
    cover every claim is 'insufficient': a silent source never trims a card
    into 'corrected' (the Seneca-on-luck case, dry run 2026-10-16)."""

    def test_an_uncovered_correction_is_insufficient_and_earns_the_second_source(self) -> None:
        seneca = verify.Judgement("corrected", "Seneca is often credited with the luck line, but no text says it.",
                                  None, "says nothing about the luck quote", covers_every_claim=False)
        self.assertEqual(verify.held(seneca).verdict, "insufficient")
        answers = iter([seneca, verify.Judgement("supported", None, None, "the letter says it")])
        v = verify.verify_ref(ref(**Retrieval.MONEYBALL), http=Retrieval().http(), ask=lambda r, e: next(answers))
        self.assertEqual((v.verification, len(v.judged_pages)), ("verified", 2))
        self.assertIsNone(v.origin)  # the uncovered "fix" was never kept

    def test_a_model_answer_without_the_field_counts_as_not_covered(self) -> None:
        j = verify.parse_judgement({"verdict": "supported", "note": "n"})
        self.assertFalse(j.covers_every_claim)
        self.assertEqual(verify.held(j).verdict, "insufficient")
        self.assertEqual(verify.schema()["properties"]["covers_every_claim"], {"type": "boolean"})


class OutcomeVote(unittest.TestCase):
    """Item 5 over the existing pool: Haiku 5.5 takes no temperature, so the
    check reads the best page OUTCOME_SAMPLES times and holds on a strict
    majority (owner decision pending on the vote vs. a single read)."""

    def test_a_majority_conflict_holds_and_a_minority_does_not(self) -> None:
        for answers, want in (([True, True, False], True), ([True, False, False], False), ([False] * 3, False)):
            with self.subTest(answers=answers):
                it = iter(answers)
                res = verify.outcome_check(ref(**Retrieval.MONEYBALL), http=Retrieval().http(),
                                           ask=lambda r, e: (next(it), "the ending"))
                self.assertEqual((res.conflict, res.votes, res.page), (want, tuple(answers), "Moneyball (book)"))

    def test_no_page_is_no_conflict_and_says_so(self) -> None:
        res = verify.outcome_check(ref(title="Nowhere", source_page="Nowhere at all"), http=VerifyRef().http(),
                                   ask=lambda r, e: (True, "x"))
        self.assertEqual((res.conflict, res.votes), (False, ()))
        self.assertIn("no page to read", res.note)

    def test_the_schema_and_the_hold_reason(self) -> None:
        self.assertEqual(verify.outcome_schema()["required"], ["outcome_conflict", "note"])
        self.assertEqual(verify.OUTCOME_SAMPLES, 3)
        from scripts import arsenal_outcome_check
        self.assertEqual(arsenal_outcome_check.REASON, verify.OUTCOME_CONFLICT)


class OutcomeControls(unittest.TestCase):
    """Thermopylae (ref 194, drop 61) is the positive control (owner,
    2026-10-16). The first prompt read the Battle of SALAMIS page and voted
    no 3 times; a soft rewrite then held Trojan Horse, Cannae and Midas,
    whose lines warn WITH the ending. These lock the prompt's question, its
    warn-vs-recommend rule and the page choice; the model itself is checked
    by scripts.arsenal_outcome_control (exit 1 on a miss)."""

    CASES = {c["name"]: c for c in json.loads(fixture("outcome_controls.json"))["cases"]}

    def test_the_prompt_asks_the_question_and_carries_the_control(self) -> None:
        p = verify.OUTCOME_SYSTEM
        self.assertIn("does the reference's real ending (defeat, death, ruin, scandal) contradict what the "
                      "deploy_line asks the listener to believe?", p)
        thermo = self.CASES["thermopylae"]["card"]["deploy_line"].rstrip(".")
        self.assertIn(thermo.split(", ")[1], p)  # "I'm holding one narrow pass where our numbers don't matter"
        self.assertIn("CONFLICT", p)
        # A line that warns with the bad ending agrees with it (the three false holds).
        self.assertIn("WARNS against the move", p)
        self.assertIn("must agree with your note", p)
        # The held-out controls stay out of the prompt, or they test nothing.
        for name in ("custer", "miracle_on_ice"):
            self.assertFalse(self.CASES[name]["in_prompt"])
            self.assertNotIn(self.CASES[name]["card"]["title"].split("'")[0], p)

    def test_the_cards_own_page_outranks_a_page_that_shares_its_terms(self) -> None:
        case = self.CASES["thermopylae"]
        own, decoy = case["own_page"], case["decoy_page"]
        thermo_card = case["card"]
        # The decoy shares more of the card's words than the card's own page.
        texts = {own: "Thermopylae lead.\n\n== Battle ==\nLeonidas held the pass; the Persians took it.",
                 decoy: ("Salamis lead.\n\n== Background ==\nIn 480 BC Xerxes' Persian army, after Thermopylae, "
                         "where Leonidas I and 300 Spartans of the Greek alliance held the pass for three days.")}

        def get(url: str, params: Any) -> tuple[int, str, None]:
            if params.get("list") == "search":
                return 200, json.dumps({"query": {"search": [{"title": decoy}, {"title": own}]}}), None
            if url == sources.WIKIPEDIA_API:
                text = texts.get(params.get("titles"))
                return 200, json.dumps({"query": {"pages": [{"extract": text} if text else {"missing": True}]}}), None
            for t in texts:
                if url == sources.WIKIPEDIA_SUMMARY + sources.page_path(t):
                    return 200, summary(t), None
            return 404, "", None

        r = ref(**{k: v for k, v in thermo_card.items() if k != "meaning" and k != "deploy_line"},
                meaning=thermo_card["meaning"], deploy_line=thermo_card["deploy_line"])
        found, _ = verify.candidates(r, http=get)
        score = {c.title: c.excerpt.score for c in found}
        self.assertGreater(score[decoy], score[own])  # on terms alone the decoy would win
        self.assertEqual(found[0].title, own)
        votes = iter([True, True, False])
        res = verify.outcome_check(r, http=get, ask=lambda c, e: (next(votes), "the Greeks were wiped out"))
        self.assertEqual((res.conflict, res.page), (True, own))

    def test_the_control_script_reads_the_same_fixture(self) -> None:
        from scripts import arsenal_outcome_control
        names = [c["name"] for c in arsenal_outcome_control.cases()]
        self.assertEqual(names, ["thermopylae", "custer", "miracle_on_ice"])
        c = arsenal_outcome_control.card(self.CASES["thermopylae"]["card"])
        self.assertEqual((c.id, c.deploy_line), (194, self.CASES["thermopylae"]["card"]["deploy_line"]))


class OutcomeWriteGate(NoDatabase):
    """Owner, 2026-10-16: the check is a dry run by default; --write needs
    every control to pass IN THE SAME RUN, or nothing is written."""

    POOL_REF = ref(id=195, title="The Trojan Horse", meaning="A gift can hide a cost.",
                   deploy_line="I'm inspecting the gift before it comes inside.")

    def setUp(self) -> None:
        super().setUp()
        from scripts import arsenal_outcome_check as check
        self.check = check
        self.holds: list[int] = []
        self.pool_read: list[bool] = []
        self.patch("arsenal_hold", lambda **kw: self.holds.append(kw["ref_id"]) or 9)
        self.patch("arsenal_undropped_checked_refs",
                   lambda: self.pool_read.append(True) or [ledger.ArsenalPoolRef(self.POOL_REF, False)])
        self.patch("close_pool", lambda: None)
        page = verify.Candidate("wikipedia", "Some page", "https://x.invalid",
                                sources.Excerpt("an excerpt", 1, ("",)), ())
        p = mock.patch.object(verify, "candidates", lambda r, http=None: ([page], []))
        p.start()
        self.addCleanup(p.stop)
        for target in (mock.patch.object(check, "load_env", lambda: None),
                       mock.patch("builtins.print", lambda *a, **k: None)):
            target.start()
            self.addCleanup(target.stop)

    def test_write_is_refused_unless_every_control_passes_in_this_run(self) -> None:
        always_no = lambda card, e: (False, "no conflict")  # noqa: E731 - Thermopylae and Custer then fail
        self.assertEqual(self.check.main(["--write"], ask=always_no), 1)
        self.assertEqual((self.holds, self.pool_read), ([], []))  # refused before the pool was read

    def test_write_holds_once_the_controls_pass(self) -> None:
        def ask(card: ledger.ArsenalRefToVerify, e: str) -> tuple[bool, str]:
            return card.title in ("Thermopylae", "Custer's Last Stand", "The Trojan Horse"), "x"
        self.assertEqual(self.check.main(["--write"], ask=ask), 0)
        self.assertEqual(self.holds, [195])

    def test_the_default_is_a_dry_run_and_ids_never_write(self) -> None:
        conflict = lambda card, e: (True, "x")  # noqa: E731
        self.assertEqual(self.check.main([], ask=conflict), 0)
        self.assertEqual(self.holds, [])
        with self.assertRaises(SystemExit):
            self.check.main(["--write", "--ids", "194"], ask=conflict)


class PoolSize(unittest.TestCase):
    def test_36_references_and_12_words_a_batch(self) -> None:
        self.assertEqual((cards.POOL_REFS, cards.POOL_WORDS), (36, 12))
        self.assertEqual(sum(cards.allocate(cards.POOL_REFS, {c: 1.0 for c in cards.CATEGORIES}).values()), 36)


class OutcomeCheck(unittest.TestCase):
    """A reference whose real ending undercuts its line is reframed or cut,
    and says outcome_conflict (owner, 2026-10-16)."""

    def test_reframed_is_corrected_with_the_new_wording(self) -> None:
        j = verify.Judgement("corrected", None, None, "the Greeks lost at Thermopylae", outcome_conflict=True,
                             fixed_deploy_line="We only need to hold the pass long enough for the round to close.")
        v = verify.verify_ref(ref(), http=VerifyRef().http(), ask=lambda r, e: j)
        self.assertEqual(v.verification, "corrected")
        self.assertTrue(v.note.startswith("outcome_conflict: reframed"))
        self.assertEqual(v.deploy_line, j.fixed_deploy_line)

    def test_no_honest_reframe_is_cut_and_never_retried(self) -> None:
        calls: list[int] = []
        j = verify.Judgement("insufficient", None, None, "a total defeat", outcome_conflict=True)
        v = verify.verify_ref(ref(), http=VerifyRef().http(), ask=lambda r, e: calls.append(1) or j)
        self.assertEqual(v.verification, "unverified")
        self.assertTrue(v.note.startswith("outcome_conflict: a total defeat"))
        self.assertEqual(len(calls), 1)

    def test_the_judge_reads_the_line_and_its_answer_carries_the_flag(self) -> None:
        props = verify.schema()["properties"]
        self.assertEqual(props["outcome_conflict"], {"type": "boolean"})
        self.assertIn("Thermopylae", verify.SYSTEM)
        j = verify.parse_judgement({"verdict": "corrected", "outcome_conflict": True, "fixed_origin": None,
                                    "fixed_source_work": None, "fixed_meaning": "Hold the line, then fall back.",
                                    "fixed_deploy_line": "We hold this pass long enough to close.", "note": "n"})
        self.assertTrue(j.outcome_conflict)
        self.assertEqual(verify.outcome(quote_dropped=False, judgement=j, unreachable=None)[0], "corrected")


# ======================================================================= pool

def status(**kw: Any) -> ledger.ArsenalPoolStatus:
    base = dict(refs_ready=30, refs_pending=0, words_ready=10, words_pending=0,
                ready_categories=tuple(cards.CATEGORIES), last_submitted_at=NOW - timedelta(days=2), open_batch=None)
    base.update(kw)
    return ledger.ArsenalPoolStatus(**base)


class Pool(NoDatabase):
    def test_when_a_batch_is_submitted(self) -> None:
        ob = ledger.ArsenalOpenBatch(1, "msgbatch_x", "claude-sonnet-5-5", NOW)
        self.assertEqual(pool.should_submit(status(open_batch=ob), NOW)[0], False)
        self.assertEqual(pool.should_submit(status(), NOW)[0], False)
        self.assertEqual(pool.should_submit(status(last_submitted_at=NOW - timedelta(days=6)), NOW)[0], True)
        self.assertEqual(pool.should_submit(status(last_submitted_at=None), NOW)[0], True)
        # Short: pending counts toward the week (a fresh batch is still verifying).
        self.assertEqual(pool.should_submit(status(refs_ready=10, refs_pending=5), NOW)[0], True)
        self.assertEqual(pool.should_submit(status(refs_ready=10, refs_pending=11), NOW)[0], False)
        self.assertEqual(pool.should_submit(status(words_ready=3, words_pending=3), NOW)[0], True)

    def test_the_top_up_goes_to_the_thin_categories_first(self) -> None:
        split = pool.topup_split(("finance_business", "film_tv"))
        self.assertEqual(sum(split.values()), cards.TOPUP_REFS)
        self.assertEqual(set(split), {"history_military", "sports", "music_culture"})
        self.assertNotIn("literature_myth", split)

    def test_the_job_submits_one_batch_with_the_weights(self) -> None:
        submitted: list[dict] = []
        logged: list[tuple] = []
        self.patch("arsenal_unembedded", lambda: [])
        self.patch("arsenal_stale_pending", lambda hours: [])
        self.patch("arsenal_pool_status", lambda: status(refs_ready=0, words_ready=0, last_submitted_at=None))
        self.patch("arsenal_seeds", lambda: [("x_seed", "X", "Work")])
        self.patch("arsenal_recent_keys", lambda n: [])
        self.patch("arsenal_recent_lemmas", lambda n: [])
        self.patch("arsenal_seed_lemmas", lambda: ["seedword"])
        self.patch("arsenal_weights", lambda: {("category", c): 1.0 for c in cards.CATEGORIES})
        self.patch("arsenal_log_run", lambda kind, st, **kw: logged.append((kind, st, kw)) or 5)
        with mock.patch.object(ai, "submit_batch",
                               lambda **kw: submitted.append(kw) or ("msgbatch_1", kw["model"])):
            run = pool.pool_job(now=NOW)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["model"], "claude-sonnet-5-5")
        self.assertFalse(submitted[0].get("critical", False))
        self.assertEqual(logged[0][:2], ("batch", "submitted"))
        self.assertEqual((logged[0][2]["refs_requested"], logged[0][2]["words_requested"]), (36, 12))
        self.assertIn("submitted msgbatch_1", run.job_note)


# ===================================================================== collect

class Collect(NoDatabase):
    def setUp(self) -> None:
        super().setUp()
        self.inserted: list[str] = []
        self.patch("arsenal_insert_ref", lambda c, **kw: self.inserted.append(c.canonical_key) or
                   (None if c.canonical_key == "taken_key" else len(self.inserted)))
        self.patch("arsenal_insert_word", lambda w, **kw: 100)
        # The nearest real reference: the vector's first value is the cosine.
        self.patch("arsenal_nearest", lambda vec: ("crossing_the_rubicon", vec[0]))

    def test_duplicates_and_invalid_cards_are_counted_never_raised(self) -> None:
        raw = [card(canonical_key="fresh_one"), card(canonical_key="near_dup_one"), card(canonical_key="taken_key"),
               card(canonical_key="bad", category="cooking"), card(canonical_key="seed_key")]
        vectors = {"fresh_one": [0.1], "near_dup_one": [0.95], "taken_key": [0.2]}
        embed = lambda texts: [[0.95] if i == 1 else [0.1] for i, _ in enumerate(texts)]  # noqa: E731
        ins = collect.insert_candidates(raw, [{"lemma": "fiduciary", "meaning": "Held in trust.", "room": "pitch",
                                               "sentence": "We act as fiduciaries here, not tourists."}],
                                        run_id=3, blocked_keys={"seed_key"}, blocked_lemmas=set(), embed=embed)
        self.assertEqual(len(ins.ref_ids), 1)
        self.assertEqual(ins.skipped_dup, 2)      # near-duplicate, UNIQUE key
        self.assertEqual(ins.skipped_invalid, 2)  # a bad category, a seed's key
        self.assertEqual(ins.word_ids, [100])
        del vectors

    def test_every_near_dup_skip_is_logged_with_its_cosine_and_match(self) -> None:
        self.logs_on()
        raw = [card(canonical_key=k) for k in ("a_one", "b_two", "c_three", "d_four")]
        scores = iter([0.41, 0.93, 0.919, 0.97])
        with self.assertLogs("valemont.arsenal.collect", level="INFO") as logs:
            ins = collect.insert_candidates(raw, [], run_id=3, blocked_keys=set(), blocked_lemmas=set(),
                                            embed=lambda texts: [[next(scores)] for _ in texts])
        self.assertEqual([n.key for n in ins.near_dup_skips], ["b_two", "d_four"])  # 0.919 stays (< 0.92)
        self.assertEqual(ins.skipped_dup, 2)
        skips = [m for m in logs.output if "skipped: near-duplicate" in m]
        self.assertEqual(len(skips), 2)
        self.assertIn("near-duplicate of crossing_the_rubicon, cosine 0.9300", skips[0])
        note = collect.distribution_note(ins)
        self.assertTrue(note.startswith("nearest cosine n=4 min 0.41"))
        self.assertIn("max 0.97", note)
        self.assertIn("near-dup skips: b_two~crossing_the_rubicon 0.930, d_four~crossing_the_rubicon 0.970", note)

    def test_nothing_embedded_yet_means_no_skip(self) -> None:
        self.patch("arsenal_nearest", lambda vec: None)
        ins = collect.insert_candidates([card(canonical_key="first_ever")], [], run_id=3, blocked_keys=set(),
                                        blocked_lemmas=set(), embed=lambda texts: [[0.99]])
        self.assertEqual((len(ins.ref_ids), ins.skipped_dup), (1, 0))
        self.assertIsNone(collect.distribution_note(ins))

    def test_an_embedding_failure_still_inserts_and_says_so(self) -> None:
        def broken(texts: Any) -> Any:
            raise RuntimeError("546")
        ins = collect.insert_candidates([card(canonical_key="fresh_two")], [], run_id=3, blocked_keys=set(),
                                        blocked_lemmas=set(), embed=broken)
        self.assertEqual(len(ins.ref_ids), 1)
        self.assertIn("near-duplicate check skipped", ins.notes[0])

    def test_a_batch_is_read_once_under_the_lock(self) -> None:
        ob = ledger.ArsenalOpenBatch(9, "msgbatch_9", "claude-sonnet-5-5", NOW)

        class Lock:
            def __init__(self, got: bool) -> None:
                self.got = got

            def __enter__(self) -> bool:
                return self.got

            def __exit__(self, *a: Any) -> None:
                return None

        self.patch("arsenal_collect_lock", lambda run: Lock(False))
        with mock.patch.object(ai, "batch_results", _refuse_database):
            self.assertIn("another collector", collect.collect(ob).job_note)
        self.patch("arsenal_collect_lock", lambda run: Lock(True))
        self.patch("arsenal_open_batch", lambda: None)
        with mock.patch.object(ai, "batch_results", _refuse_database):
            self.assertIn("already collected", collect.collect(ob).job_note)


# ======================================================================== drop

class Drop(NoDatabase):
    def test_zero_ai_calls_when_the_pool_is_full(self) -> None:
        picks: list[tuple] = []
        self.patch("arsenal_drop_on", lambda day: None)
        self.patch("arsenal_pool_status", lambda: status())
        self.patch("arsenal_pick_drop", lambda day, topped_up=False: picks.append((day, topped_up)) or 42)
        with mock.patch.object(ai, "complete", _refuse_database), mock.patch.object(pool, "topup", _refuse_database):
            run = drop.make_drop(date(2026, 10, 16))
        self.assertEqual((run.drop_id, run.topped_up), (42, False))
        self.assertEqual(picks, [(date(2026, 10, 16), False)])

    def test_a_day_with_a_drop_is_left_alone(self) -> None:
        self.patch("arsenal_drop_on", lambda day: 7)
        self.patch("arsenal_pick_drop", _refuse_database)
        self.assertEqual(drop.make_drop(date(2026, 10, 16)).drop_id, 7)

    def test_a_short_pool_tops_up_once_then_picks(self) -> None:
        self.assertTrue(drop.is_short(status(ready_categories=("sports", "film_tv"))))
        self.assertTrue(drop.is_short(status(words_ready=0)))
        self.assertFalse(drop.is_short(status()))
        calls: list[str] = []
        self.patch("arsenal_drop_on", lambda day: None)
        self.patch("arsenal_pool_status", lambda: status(ready_categories=("sports",), refs_ready=1,
                                                          last_submitted_at=NOW - timedelta(days=3)))
        self.patch("arsenal_pick_drop", lambda day, topped_up=False: calls.append(f"pick {topped_up}") or 43)
        up = pool.TopUp(run_id=11, refs=6, words=2, cost=Decimal("0.04"), verified={"verified": 7, "unverified": 1})
        with mock.patch.object(pool, "topup", lambda st, **kw: calls.append("topup") or up):
            run = drop.make_drop(date(2026, 10, 16), now=NOW)
        self.assertEqual(calls, ["topup", "pick True"])
        self.assertTrue(run.topped_up)
        self.assertIn("topped up (run 11", run.job_note)

    def test_a_short_pool_defers_while_a_batch_is_open_or_young(self) -> None:
        """Owner, 2026-10-16: today's drop comes from the batch pool, not the top-up."""
        self.logs_on()
        self.patch("arsenal_drop_on", lambda day: None)
        self.patch("arsenal_pick_drop", _refuse_database)
        short = dict(ready_categories=("sports",), refs_ready=1, words_ready=0)
        ob = ledger.ArsenalOpenBatch(5, "msgbatch_open", "claude-sonnet-5-5", NOW - timedelta(hours=5))
        for label, st in (("open", status(**short, open_batch=ob)),
                          ("young", status(**short, last_submitted_at=NOW - timedelta(minutes=40)))):
            with self.subTest(label), mock.patch.object(pool, "topup", _refuse_database), \
                    self.assertLogs("valemont.arsenal.drop", level="WARNING") as logs:
                self.patch("arsenal_pool_status", lambda st=st: st)
                run = drop.make_drop(date(2026, 10, 16), now=NOW)  # 03:30 Chicago
                self.assertTrue(run.deferred)
                self.assertIsNone(run.drop_id)
                self.assertIn("deferred", run.job_note)
                self.assertIn("deferred", logs.output[0])
        # A batch older than 2 h and collected: the top-up runs.
        self.assertIsNone(drop.defer_reason(status(**short, last_submitted_at=NOW - timedelta(hours=2, minutes=1)), NOW))
        # From 21:00 Chicago it never waits: the day gets its drop.
        nine_pm = datetime(2026, 10, 17, 2, 5, tzinfo=timezone.utc)  # 21:05 CDT
        self.assertIsNone(drop.defer_reason(status(**short, open_batch=ob), nine_pm))
        self.assertIsNotNone(drop.defer_reason(status(**short, open_batch=ob), nine_pm - timedelta(minutes=10)))

    def test_a_full_pool_picks_even_while_a_batch_is_open(self) -> None:
        ob = ledger.ArsenalOpenBatch(5, "msgbatch_open", "claude-sonnet-5-5", NOW)
        self.patch("arsenal_drop_on", lambda day: None)
        self.patch("arsenal_pool_status", lambda: status(open_batch=ob))
        self.patch("arsenal_pick_drop", lambda day, topped_up=False: 44)
        self.assertEqual(drop.make_drop(date(2026, 10, 16), now=NOW).drop_id, 44)

    def test_a_top_up_with_nothing_verified_fails_loudly_never_an_empty_day(self) -> None:
        """Change 2 (owner, 2026-10-16): the top-up verifies inline BEFORE the
        pick; when nothing verifies, the job raises with an ERROR log."""
        self.logs_on()
        order: list[str] = []
        self.patch("arsenal_drop_on", lambda day: None)
        self.patch("arsenal_pool_status", lambda: status(ready_categories=(), refs_ready=0, words_ready=0,
                                                          last_submitted_at=None))
        self.patch("arsenal_seeds", lambda: [])
        self.patch("arsenal_recent_keys", lambda n: [])
        self.patch("arsenal_recent_lemmas", lambda n: [])
        self.patch("arsenal_seed_lemmas", lambda: ["seedword"])
        self.patch("arsenal_log_run", lambda kind, st, **kw: 12)
        ids = iter(range(500, 600))
        self.patch("arsenal_insert_ref", lambda c, **kw: next(ids))
        self.patch("arsenal_insert_word", lambda w, **kw: next(ids))
        self.patch("arsenal_nearest", lambda v: None)
        self.patch("arsenal_queue_verify", _refuse_database)  # never through job_queue

        def pick(day: date, topped_up: bool = False) -> int:
            order.append("pick")
            raise RuntimeError("arsenal_pick_drop: the verified pool is short (0 categories with an eligible reference; 3 needed)")

        self.patch("arsenal_pick_drop", pick)
        answer = {"refs": [card(canonical_key=f"topup_{i}", category=c) for i, c in
                           enumerate(("sports", "film_tv", "philosophy"))],
                  "words": [{"lemma": "provenance", "meaning": "Where it came from.", "room": "pitch",
                             "sentence": "Investors buy provenance as much as the product."}]}

        def complete(**kw: Any) -> Any:
            order.append("sonnet")
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(answer))])

        def verify_item(target: str, item_id: int, **kw: Any) -> str:
            order.append(f"verify {target}")
            return "unverified"

        with mock.patch.object(ai, "complete", complete), mock.patch.object(verify, "verify_item", verify_item), \
                self.assertLogs("valemont.arsenal.drop", level="ERROR") as logs, \
                self.assertRaises(drop.DropFailed) as failed:
            drop.make_drop(date(2026, 10, 16), embed=lambda texts: [[0.0]] * len(texts), now=NOW)
        self.assertEqual(order, ["sonnet", "verify ref", "verify ref", "verify ref", "verify word", "pick"])
        self.assertIn("NO DROP after the top-up", logs.output[0])
        self.assertIn("'unverified': 4", str(failed.exception))

    def test_without_a_top_up_a_pick_failure_propagates_as_is(self) -> None:
        self.patch("arsenal_drop_on", lambda day: None)
        self.patch("arsenal_pool_status", lambda: status())
        self.patch("arsenal_pick_drop", lambda day, topped_up=False: (_ for _ in ()).throw(RuntimeError("db down")))
        with self.assertRaises(RuntimeError) as caught:
            drop.make_drop(date(2026, 10, 16), now=NOW)
        self.assertNotIsInstance(caught.exception, drop.DropFailed)

    def test_the_top_up_is_one_sonnet_call_verified_inline(self) -> None:
        logged: list[tuple] = []
        settled: list[str] = []
        self.patch("arsenal_seeds", lambda: [])
        self.patch("arsenal_recent_keys", lambda n: [])
        self.patch("arsenal_recent_lemmas", lambda n: [])
        self.patch("arsenal_seed_lemmas", lambda: ["seedword"])
        self.patch("arsenal_log_run", lambda kind, st, **kw: logged.append((kind, st)) or 11)
        self.patch("arsenal_insert_ref", lambda c, **kw: 200)
        self.patch("arsenal_insert_word", lambda w, **kw: 300)
        self.patch("arsenal_nearest", lambda v: ("x_seed", 0.4))
        answer = {"refs": [card(canonical_key="topup_one")], "words": [
            {"lemma": "provenance", "meaning": "Where it came from.", "room": "pitch",
             "sentence": "Investors buy provenance as much as the product."}]}
        calls: list[dict] = []

        def complete(**kw: Any) -> Any:
            calls.append(kw)
            kw["on_recorded"](1, kw["model"], Decimal("0.03"))
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(answer))])

        with mock.patch.object(ai, "complete", complete), \
                mock.patch.object(verify, "verify_item", lambda t, i, **kw: settled.append(f"{t} {i}") or "verified"):
            up = pool.topup(status(ready_categories=("sports",)), embed=lambda texts: [[0.0]] * len(texts))
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["purpose"], calls[0]["model"], calls[0]["critical"]),
                         ("arsenal_topup", "claude-sonnet-5-5", False))
        self.assertEqual(logged, [("topup", "submitted"), ("topup", "collected")])
        self.assertEqual(settled, ["ref 200", "word 300"])
        self.assertEqual((up.refs, up.words, up.cost), (1, 1, Decimal("0.03")))


# ========================================================================== ai

class Batches(NoDatabase):
    def test_submit_applies_the_guard_and_results_cost_at_the_batch_rate(self) -> None:
        created: list[Any] = []
        usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        message = SimpleNamespace(model="claude-sonnet-5-5", usage=usage)
        sdk = SimpleNamespace(messages=SimpleNamespace(batches=SimpleNamespace(
            create=lambda requests: created.append(requests) or SimpleNamespace(id="msgbatch_t"),
            results=lambda batch_id: iter([
                SimpleNamespace(custom_id="refs", result=SimpleNamespace(type="succeeded", message=message)),
                SimpleNamespace(custom_id="words", result=SimpleNamespace(type="expired")),
            ]))))
        recorded: list[dict] = []
        with mock.patch.object(ai, "guard", lambda model, critical: "claude-haiku-5-5"), \
                mock.patch.object(ledger, "record_ai_usage", lambda **kw: recorded.append(kw) or 1):
            batch_id, model = ai.submit_batch(requests=[ai.BatchRequest("refs", {"messages": []}, 100)],
                                              model="claude-sonnet-5-5", client=sdk)
            self.assertEqual((batch_id, model), ("msgbatch_t", "claude-haiku-5-5"))
            self.assertEqual(created[0][0]["params"]["model"], "claude-haiku-5-5")  # downgraded at 80%
            results = ai.batch_results("msgbatch_t", purpose="arsenal_pool", model="claude-sonnet-5-5", client=sdk)
        self.assertEqual([r.kind for r in results], ["succeeded", "expired"])
        self.assertEqual(len(recorded), 1)
        self.assertTrue(recorded[0]["batch"])
        self.assertEqual(recorded[0]["cost_usd"], Decimal("1.000000"))  # $2/M input, halved


# ======================================================================= brief

class BriefLine(unittest.TestCase):
    PAYLOAD = {"arsenal_brief": {"power_word": "asymmetric", "word_meaning": "uneven on purpose",
                                 "ref": {"title": "Crossing the Rubicon", "room": "boardroom", "category": "history_military"}}}

    def test_one_line_in_ai_and_fallback_briefs(self) -> None:
        self.assertIn("arsenal_brief", brief.ONE_LINE_SECTIONS)
        lines = brief.FALLBACK_SECTIONS["arsenal_brief"](self.PAYLOAD, "2026-10-16")
        self.assertEqual(lines, ["Power Word: asymmetric. Strongest reference: Crossing the Rubicon, for the boardroom."])
        homies = {"arsenal_brief": {"power_word": "inflection", "ref": {"title": "Mamba Mentality", "room": "homies"}}}
        self.assertEqual(brief.FALLBACK_SECTIONS["arsenal_brief"](homies, "x"),
                         ["Power Word: inflection. Strongest reference: Mamba Mentality, with the homies."])
        self.assertEqual(brief.FALLBACK_SECTIONS["arsenal_brief"]({"arsenal_brief": {}}, "x"), [])

    def test_the_ai_brief_carries_the_data_line_never_the_models(self) -> None:
        """Owner, 2026-10-16: brief 1018's line named the word, dropped the
        reference and closed with "Test every move today against it"."""
        model_body = {"headline": "A day.", "one_move": "Ship it.", "sections": [
            {"key": "arsenal_brief", "title": "x",
             "lines": ["Power word: asymmetric. Far more upside than downside. Test every move today against it."]}]}
        response = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(model_body))])
        payload = {**self.PAYLOAD, "local_date": "2026-10-16"}
        with mock.patch.object(brief, "prompt", lambda p, s: ("system", "user")), \
                mock.patch.object(brief, "validate", lambda body, keys: body), \
                mock.patch.object(ai, "complete", lambda **kw: response):
            got = brief.generate_ai(payload, [ledger.BriefSection("arsenal_brief", "Arsenal", 60)])
        (section,) = got.body["sections"]
        self.assertEqual(section["lines"],
                         ["Power Word: asymmetric. Strongest reference: Crossing the Rubicon, for the boardroom."])
        for exhortation in ("Test every move", "today against it"):
            self.assertNotIn(exhortation, section["lines"][0])
        self.assertIn("arsenal_brief", brief.DATA_LINE_SECTIONS)

    def test_the_section_is_registered_in_the_migration(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("('arsenal_brief', 'Arsenal', 'public.ctx_arsenal_brief()'::regprocedure, true, 60)", sql)
        self.assertIn("('arsenal', 'The Arsenal', 'public.ctx_arsenal()'::regprocedure, false, 60)", sql)


if __name__ == "__main__":
    unittest.main()
