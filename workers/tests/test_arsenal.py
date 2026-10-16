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
"""

from __future__ import annotations

import json
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
        self.patch("arsenal_weights", lambda: {("category", c): 1.0 for c in cards.CATEGORIES})
        self.patch("arsenal_log_run", lambda kind, st, **kw: logged.append((kind, st, kw)) or 5)
        with mock.patch.object(ai, "submit_batch",
                               lambda **kw: submitted.append(kw) or ("msgbatch_1", kw["model"])):
            run = pool.pool_job(now=NOW)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["model"], "claude-sonnet-5-5")
        self.assertFalse(submitted[0].get("critical", False))
        self.assertEqual(logged[0][:2], ("batch", "submitted"))
        self.assertEqual((logged[0][2]["refs_requested"], logged[0][2]["words_requested"]), (30, 10))
        self.assertIn("submitted msgbatch_1", run.job_note)


# ===================================================================== collect

class Collect(NoDatabase):
    def setUp(self) -> None:
        super().setUp()
        self.inserted: list[str] = []
        self.patch("arsenal_insert_ref", lambda c, **kw: self.inserted.append(c.canonical_key) or
                   (None if c.canonical_key == "taken_key" else len(self.inserted)))
        self.patch("arsenal_insert_word", lambda w, **kw: 100)
        self.patch("arsenal_near_dup", lambda vec, t: vec[0] > 0.9)

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
        self.patch("arsenal_pool_status", lambda: status(ready_categories=("sports",), refs_ready=1))
        self.patch("arsenal_pick_drop", lambda day, topped_up=False: calls.append(f"pick {topped_up}") or 43)
        up = pool.TopUp(run_id=11, refs=6, words=2, cost=Decimal("0.04"), verified={"verified": 7, "unverified": 1})
        with mock.patch.object(pool, "topup", lambda st, **kw: calls.append("topup") or up):
            run = drop.make_drop(date(2026, 10, 16))
        self.assertEqual(calls, ["topup", "pick True"])
        self.assertTrue(run.topped_up)
        self.assertIn("topped up (run 11", run.job_note)

    def test_the_top_up_is_one_sonnet_call_verified_inline(self) -> None:
        logged: list[tuple] = []
        settled: list[str] = []
        self.patch("arsenal_seeds", lambda: [])
        self.patch("arsenal_recent_keys", lambda n: [])
        self.patch("arsenal_recent_lemmas", lambda n: [])
        self.patch("arsenal_log_run", lambda kind, st, **kw: logged.append((kind, st)) or 11)
        self.patch("arsenal_insert_ref", lambda c, **kw: 200)
        self.patch("arsenal_insert_word", lambda w, **kw: 300)
        self.patch("arsenal_near_dup", lambda v, t: False)
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
        self.assertEqual(lines, ["Power Word: asymmetric (uneven on purpose). Deploy Crossing the Rubicon in the boardroom."])
        homies = {"arsenal_brief": {"power_word": "inflection", "ref": {"title": "Mamba Mentality", "room": "homies"}}}
        self.assertEqual(brief.FALLBACK_SECTIONS["arsenal_brief"](homies, "x"),
                         ["Power Word: inflection. Deploy Mamba Mentality with the homies."])
        self.assertEqual(brief.FALLBACK_SECTIONS["arsenal_brief"]({"arsenal_brief": {}}, "x"), [])

    def test_the_section_is_registered_in_the_migration(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("('arsenal_brief', 'Arsenal', 'public.ctx_arsenal_brief()'::regprocedure, true, 60)", sql)
        self.assertIn("('arsenal', 'The Arsenal', 'public.ctx_arsenal()'::regprocedure, false, 60)", sql)


if __name__ == "__main__":
    unittest.main()
