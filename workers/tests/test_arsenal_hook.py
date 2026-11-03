"""The Arsenal, Phase 3: the Screening Room hook (docs/arsenal.md §10; db/062).

* PROMPT SHAPE: the pool's voice and rules (outcome rule, film/TV credit),
  uncached; the title (name, year, kind, overview, genres, keywords) and the
  owner's weights; 3 to 5 references and 1 Power Word; source_work names the
  title; the structured schema.
* THE OWNER'S RULES ON A CARD: the source work names the title (forced when
  not); a quote credits the character AND the title in every category, or
  goes; at most 5 kept; near-duplicates and invalid cards counted, never
  raised.
* ONCE PER TITLE: the database's rule (UNIQUE title_id, the trigger's ON
  CONFLICT, no card after a draft) read from db/062; a drafted hook never
  drafts again, a failed one only by a recorded hand re-run, a test hook
  makes no AI call. tests_live/test_arsenal_hook_sql.py runs the SQL itself.
* VERIFY + OUTCOME: every card through the pool's verifier, the word through
  kaikki, then controls once and each checked card's vote on the record; a
  failed control records nothing; a retry skips what is done.
* CREDIT: the Anthropic credit error propagates out of the job (the queue
  defers it an hour without spending an attempt) and stops the sweep.

No database, no network, no model: every ledger call is patched.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest import mock

from arsenal import cards, hook, outcome, sources, verify
from core import ai, ledger, system_jobs
from tests.test_arsenal import ROOT, NoDatabase, controls_ask, ref

MIGRATION = ROOT / "db" / "062_arsenal_title_hook.sql"
T0 = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)

SUCCESSION = ledger.TitleForHook(id=77, name="Succession", kind="tv", year=2018,
                                 overview="The Roy family controls one of the biggest media conglomerates.",
                                 genres=("Drama",), keywords=("media mogul", "succession", "family business"))


def hook_row(**kw: Any) -> ledger.ArsenalTitleHook:
    base = dict(id=5, title_id=77, status="queued", ref_ids=(), word_id=None, is_test=False, created_at=T0,
                settled_at=None, note=None)
    base.update(kw)
    return ledger.ArsenalTitleHook(**base)


def item(key: str, **kw: Any) -> dict[str, Any]:
    base = {"canonical_key": key, "category": "film_tv", "title": f"The {key.replace('_', ' ')}",
            "origin": "Logan Roy outmaneuvers his children at the board vote in season one.",
            "meaning": "Never assume the vote is counted.", "deploy_line": "Count the votes before you call it.",
            "room": "boardroom", "source_work": "Succession (2018)", "quote": None, "quote_by": None,
            "source_kind": "wikipedia", "source_page": "Succession (TV series)"}
    base.update(kw)
    return base


WORD = {"lemma": "fiefdom", "meaning": "a territory one person controls", "room": "boardroom",
        "sentence": "That division has become his fiefdom."}


def message(data: Any) -> Any:
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(data))])


class Prompt(unittest.TestCase):
    def test_the_message_carries_the_title_and_the_weights(self) -> None:
        msg = hook.hook_message(SUCCESSION, {("category", "film_tv"): 1.3, ("category", "sports"): 0.9,
                                             ("room", "pitch"): 1.15})
        for part in ("Succession (2018), a series.", "Genres: Drama", "media mogul, succession, family business",
                     "The Roy family controls", "Categories: film_tv 1.30, sports 0.90", "Rooms: pitch 1.15",
                     "3 to 5 references", "exactly 1 Power Word", 'source_work names this title on every reference: '
                     '"Succession (2018)"', '"Succession (TV series)"', "credits the character AND the title",
                     "Mostly film_tv", "Check the ending"):
            self.assertIn(part, msg)

    def test_a_film_hints_its_film_page_and_trims_a_long_overview(self) -> None:
        film = ledger.TitleForHook(1, "Heat", "movie", 1995, "x " * 900, (), ())
        msg = hook.hook_message(film, {})
        self.assertIn('"Heat (film)"', msg)
        self.assertIn("Heat (1995), a film.", msg)
        self.assertIn("…", msg)
        self.assertIn("(none yet)", msg)
        no_year = ledger.TitleForHook(2, "Untitled", "movie", None, None, (), ())
        self.assertEqual(hook.work_of(no_year), "Untitled")
        self.assertIn("Overview: (none)", hook.hook_message(no_year, {}))

    def test_the_system_is_the_pool_s_voice_and_rules_uncached(self) -> None:
        ctx = cards.PromptContext(seeds=[("k", "T", "W")], proposed=[("a", "A")], recent_lemmas=["x"])
        blocks = hook.system_blocks(ctx)
        self.assertEqual([b["text"] for b in blocks], [b["text"] for b in cards.system_blocks(ctx)])
        self.assertFalse(any("cache_control" in b for b in blocks))
        rules = blocks[1]["text"]
        self.assertIn("Check the ending", rules)
        self.assertIn("credits the character AND the work", rules)

    def test_the_schema_is_refs_and_one_word(self) -> None:
        s = hook.hook_schema()
        self.assertEqual(s["required"], ["refs", "word"])
        self.assertEqual(s["properties"]["refs"]["items"], cards.ref_item_schema())
        self.assertEqual(s["properties"]["word"], cards.word_item_schema())


class OwnerRules(NoDatabase):
    def card(self, **kw: Any) -> ledger.ArsenalCardIn:
        c, why = cards.validate_ref(item("board_vote", **kw), set())
        self.assertIsNotNone(c, why)
        return c

    def test_the_source_work_names_the_title_or_is_forced_to(self) -> None:
        kept, notes = hook.fit_card(self.card(source_work="Succession, season 1 (HBO)"), SUCCESSION)
        self.assertEqual((kept.source_work, notes), ("Succession, season 1 (HBO)", []))
        forced, notes = hook.fit_card(self.card(category="finance_business", source_work="Rupert Murdoch, 2019"),
                                      SUCCESSION)
        self.assertEqual(forced.source_work, "Succession (2018)")
        self.assertIn("-> 'Succession (2018)'", notes[0])

    def test_a_quote_credits_the_character_and_the_title_in_every_category(self) -> None:
        for category in ("film_tv", "finance_business"):
            with self.subTest(category=category):
                c, _ = hook.fit_card(self.card(category=category, quote="You are not serious people.",
                                               quote_by="Logan Roy"), SUCCESSION)
                self.assertEqual((c.quote, c.quote_by),
                                 ("You are not serious people.", "Logan Roy (as written in Succession)"))
        c, notes = hook.fit_card(self.card(category="finance_business", quote="You are not serious people.",
                                           quote_by=None), SUCCESSION)
        self.assertEqual((c.quote, c.quote_by), (None, None))
        self.assertIn("quote dropped", notes[0])

    def test_screen_keeps_five_counts_the_rest_and_the_word(self) -> None:
        self.patch("arsenal_nearest", lambda v: ("old_key", 0.95) if v == [9.0] else ("old_key", 0.5))
        data = {"refs": [item(f"card_{i}") for i in range(6)] + [{"canonical_key": "x"}, item("seed_key")],
                "word": WORD}
        vecs = {f"The card {i} — Succession (2018)": [1.0] for i in range(6)}
        vecs["The card 2 — Succession (2018)"] = [9.0]
        s = hook.screen(data, SUCCESSION, blocked_keys={"seed_key"}, blocked_lemmas=set(),
                        embed=lambda texts: [vecs[t] for t in texts])
        self.assertEqual([c.canonical_key for c, _ in s.refs], ["card_0", "card_1", "card_3", "card_4"])
        self.assertEqual((s.invalid, s.near_dup), (2, 1))
        self.assertTrue(any("card_5: past 5" in n for n in s.notes))
        self.assertTrue(any("card_2~old_key 0.950" in n for n in s.notes))
        self.assertEqual(s.word.lemma, "fiefdom")

    def test_a_bad_word_is_noted_and_a_failed_embed_still_writes(self) -> None:
        s = hook.screen({"refs": [item("a_card")], "word": {"lemma": "x1"}}, SUCCESSION, blocked_keys=set(),
                        blocked_lemmas=set(), embed=lambda t: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertEqual([(c.canonical_key, v) for c, v in s.refs], [("a_card", None)])
        self.assertIsNone(s.word)
        self.assertTrue(any(n.startswith("embed failed") for n in s.notes))
        self.assertTrue(any(n.startswith("word refused") for n in s.notes))


class Draft(NoDatabase):
    def setUp(self) -> None:
        super().setUp()
        self.calls: list[dict] = []
        self.written: list[tuple] = []
        self.failed: list[tuple] = []
        self.patch("arsenal_title_for_hook", lambda i: SUCCESSION)
        self.patch("arsenal_weights", lambda: {("category", "film_tv"): 1.0})
        self.patch("arsenal_seeds", lambda: [])
        self.patch("arsenal_proposed", lambda: [("board_vote_old", "Old")])
        self.patch("arsenal_recent_lemmas", lambda n=60: [])
        self.patch("arsenal_seed_lemmas", lambda: [])
        self.patch("arsenal_recent_keys", lambda n=60: [])
        self.patch("arsenal_nearest", lambda v: None)
        self.patch("arsenal_title_hook_draft", lambda hid, tid, refs, word, **kw: self.written.append(
            (hid, tid, [c.canonical_key for c, _ in refs], word.lemma if word else None, kw)) or ledger.HookDraft(
            "drafted", tuple(range(100, 100 + len(refs))), 200 if word else None, 0))
        self.patch("arsenal_title_hook_fail", lambda hid, note, **kw: self.failed.append((hid, note, kw)))

    def complete(self, data: Any):
        def run(**kw: Any) -> Any:
            self.calls.append(kw)
            kw["on_recorded"](901, "claude-sonnet-5-5", "0.031")
            return message(data) if data is not None else SimpleNamespace(stop_reason="refusal", content=[])
        return mock.patch.object(ai, "complete", run)

    def test_one_sonnet_call_non_critical_then_one_write(self) -> None:
        with self.complete({"refs": [item("board_vote"), item("kendall_bust")], "word": WORD}):
            d = hook.draft(hook_row(), embed=lambda t: [[0.1]] * len(t))
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual((call["purpose"], call["model"], call["critical"]),
                         ("arsenal_title_hook", "claude-sonnet-5-5", False))
        self.assertEqual(call["output_config"]["format"]["schema"], hook.hook_schema())
        self.assertIn("Succession (2018)", call["messages"][0]["content"])
        self.assertEqual(self.written[0][:4], (5, 77, ["board_vote", "kendall_bust"], "fiefdom"))
        self.assertEqual(self.written[0][4]["ai_usage_id"], 901)
        self.assertIsNone(self.written[0][4]["rerun_by"])
        self.assertEqual((d.status, d.ref_ids, d.word_id), ("drafted", (100, 101), 200))

    def test_a_refusal_or_no_card_fails_the_hook_with_its_reason(self) -> None:
        with self.complete(None):
            self.assertEqual(hook.draft(hook_row()).status, "failed")
        with self.complete({"refs": [{"canonical_key": "bad"}], "word": WORD}):
            self.assertEqual(hook.draft(hook_row(), embed=lambda t: []).status, "failed")
        self.assertEqual(self.written, [])
        self.assertEqual(self.failed[0][1], "the model refused or answered no JSON")
        self.assertTrue(self.failed[1][1].startswith("no card passed: 0 card(s) passed, 1 invalid"))
        self.assertEqual(self.failed[0][2]["ai_usage_id"], 901)

    def test_a_missing_title_fails_without_a_call(self) -> None:
        self.patch("arsenal_title_for_hook", lambda i: None)
        with self.complete({}):
            self.assertIsNone(hook.draft(hook_row()))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.failed[0][1], "title 77 not found")


class OncePerTitle(NoDatabase):
    def test_the_database_holds_it(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("title_id         BIGINT       NOT NULL UNIQUE REFERENCES titles (id)", sql)
        self.assertIn("ON CONFLICT (title_id) DO NOTHING", sql)
        self.assertIn("IF v_hook IS NOT NULL AND NOT coalesce(v_test, false) THEN", sql)
        self.assertIn("'arsenal_title_hook'", sql)
        self.assertIn("a drafted hook never changes (once per title)", sql)
        self.assertIn("a title_hook card needs its title''s hook still to draft (once per title)", sql)
        self.assertIn("AFTER INSERT ON watch_status", sql)
        # The kind the trigger queues is the kind the worker handles.
        self.assertEqual(ledger.ARSENAL_TITLE_HOOK_KIND, "arsenal_title_hook")

    def test_a_drafted_hook_never_drafts_again(self) -> None:
        self.patch("arsenal_title_hook", lambda i: hook_row(status="drafted", ref_ids=(100,), word_id=200))
        with mock.patch.object(hook, "draft", side_effect=AssertionError("no draft")), \
                mock.patch.object(hook, "check", lambda *a, **kw: "checked") as _:
            self.assertEqual(hook.run_hook(5), "checked")
        with self.assertRaises(hook.HookError):
            hook.run_hook(5, rerun_by="owner")

    def test_a_failed_hook_drafts_again_only_by_a_hand_rerun(self) -> None:
        self.patch("arsenal_title_hook", lambda i: hook_row(status="failed", note="no card passed"))
        with mock.patch.object(hook, "draft", side_effect=AssertionError("no draft")):
            self.assertEqual(hook.run_hook(5), "skipped: failed (no card passed); a hand re-run drafts it again")
        seen = []
        with mock.patch.object(hook, "draft", lambda h, **kw: seen.append(kw["rerun_by"]) or ledger.HookDraft(
                "drafted", (1,), None, 0)), mock.patch.object(hook, "check", lambda *a, **kw: "checked"):
            self.assertEqual(hook.run_hook(5, rerun_by="owner, test"), "drafted 1 card(s), no word; checked")
        self.assertEqual(seen, ["owner, test"])

    def test_a_test_hook_and_a_missing_one_make_no_call(self) -> None:
        for row, expected in ((hook_row(is_test=True), "skipped: test title"), (None, "skipped: no such hook")):
            with self.subTest(expected=expected):
                self.patch("arsenal_title_hook", lambda i, row=row: row)
                with mock.patch.object(ai, "complete", side_effect=AssertionError("no AI")):
                    self.assertEqual(hook.run_hook(5), expected)

    def test_the_job_reads_its_hook_id_and_is_registered(self) -> None:
        self.patch("arsenal_title_hook", lambda i: hook_row(is_test=True))
        self.assertEqual(hook.hook_job({"hook_id": 5, "title_id": 77}).job_note, "hook 5: skipped: test title")
        with self.assertRaises(ValueError):
            hook.hook_job({"title_id": 77})
        self.assertIn(ledger.ARSENAL_TITLE_HOOK_KIND, system_jobs.QUEUE_HANDLERS)
        self.assertFalse(system_jobs.ARSENAL_TITLE_HOOK.scheduled)
        names = [j.name for j in system_jobs.build_system_jobs({})]
        self.assertIn("arsenal_title_hook", names)
        self.assertIn("arsenal_title_hook_sweep", names)


class VerifyAndOutcome(NoDatabase):
    def setUp(self) -> None:
        super().setUp()
        self.recorded: list[tuple] = []
        self.verified: list[tuple] = []
        self.states: dict[int, ledger.ArsenalArmState] = {}
        self.patch("arsenal_record_outcome", lambda i, **kw: self.recorded.append(
            (i, kw["conflict"], kw["votes"], kw["controls"], kw["source"])) or (9, 70 if kw["conflict"] else None))
        self.patch("arsenal_ref_to_verify", lambda i: ref(id=i, title=f"Card {i}", verification="verified"))
        self.patch("arsenal_arm_state", lambda i: self.states.get(i))
        page = verify.Candidate("wikipedia", "Succession (TV series)", "https://x.invalid",
                                sources.Excerpt("x", 1, ("",)), ())
        p = mock.patch.object(verify, "candidates", lambda r, http=None: ([page], []))
        p.start()
        self.addCleanup(p.stop)

    def st(self, verification: str, checked: bool = False) -> ledger.ArsenalArmState:
        return ledger.ArsenalArmState("pool", verification, False, checked, False)

    def verify_item(self, results: dict[tuple[str, int], str]):
        def run(target: str, item_id: int, **kw: Any) -> str:
            self.verified.append((target, item_id))
            return results.get((target, item_id), "skipped: not pending")
        return mock.patch.object(verify, "verify_item", run)

    def test_verify_maps_then_controls_once_then_each_checked_card(self) -> None:
        self.states = {1: self.st("verified"), 2: self.st("corrected"), 3: self.st("unverified")}
        seen: list[str] = []
        base = controls_ask("Card 2")
        with self.verify_item({("ref", 1): "verified", ("ref", 2): "corrected", ("ref", 3): "unverified",
                               ("word", 9): "verified"}):
            note = hook.check([1, 2, 3], 9, ask_outcome=lambda c, e: seen.append(c.title) or base(c, e))
        self.assertEqual(self.verified, [("ref", 1), ("ref", 2), ("ref", 3), ("word", 9)])
        self.assertEqual(seen[:9], ["Thermopylae"] * 3 + ["Custer's Last Stand"] * 3 + ["Miracle on Ice"] * 3)
        self.assertEqual(seen[9:], ["Card 1"] * 3 + ["Card 2"] * 3)
        self.assertEqual(self.recorded, [(1, False, "nnn", "3/3", "worker"), (2, True, "YYY", "3/3", "worker")])
        self.assertEqual(note, "verify corrected 1, unverified 1, verified 1; word verified; "
                               "outcome checked 2, held 1 (controls 3/3)")

    def test_a_retry_skips_what_is_done(self) -> None:
        self.states = {1: self.st("verified", checked=True), 2: self.st("unverified")}
        with self.verify_item({}):
            note = hook.check([1, 2], None, ask_outcome=lambda c, e: self.fail("nothing left to check"))
        self.assertEqual(note, "verify skipped: not pending 2; word no word; outcome: nothing to check")
        self.assertEqual(self.recorded, [])

    def test_a_failed_control_records_nothing_and_raises_for_the_queue(self) -> None:
        self.states = {1: self.st("verified")}
        with self.verify_item({}), self.assertRaises(outcome.ControlsFailed):
            hook.check([1], None, ask_outcome=lambda c, e: (False, "x"))
        self.assertEqual(self.recorded, [])


class Credit(NoDatabase):
    def test_the_credit_error_leaves_the_job_and_the_queue_defers_it(self) -> None:
        self.patch("arsenal_title_hook", lambda i: hook_row())
        with mock.patch.object(hook, "draft", side_effect=ai.CreditExhausted(ai.CREDIT_NOTE)):
            with self.assertRaises(ai.CreditExhausted):
                hook.hook_job({"hook_id": 5})
        deferred, retried = [], []
        job = SimpleNamespace(id=31, kind=ledger.ARSENAL_TITLE_HOOK_KIND, payload={"hook_id": 5}, attempts=1)
        claims = iter([job, None])
        self.patch("queue_fail_exhausted", lambda: 0)
        self.patch("queue_claim", lambda: next(claims))
        self.patch("queue_defer", lambda i, err, delay: deferred.append((i, err)))
        self.patch("queue_retry", lambda *a: retried.append(a))
        self.patch("queue_done", lambda i: self.fail("not done"))

        def handler(payload: Any) -> None:
            raise ai.CreditExhausted(ai.CREDIT_NOTE)
        system_jobs.poll_queue({ledger.ARSENAL_TITLE_HOOK_KIND: handler})
        self.assertEqual(deferred, [(31, ai.CREDIT_NOTE)])
        self.assertEqual(retried, [])

    def test_the_sweep_stops_on_credit_and_reports_other_failures(self) -> None:
        self.patch("arsenal_stranded_title_hooks", lambda limit, age: [1, 2, 3])
        runs = []

        def run(i: int) -> str:
            runs.append(i)
            if i == 2:
                raise ai.CreditExhausted(ai.CREDIT_NOTE)
            return "ok"
        with self.assertRaises(ai.CreditExhausted):
            hook.sweep_job(run_one=run)
        self.assertEqual(runs, [1, 2])

        def flaky(i: int) -> str:
            if i == 1:
                raise RuntimeError("wiki down")
            return "ok"
        with self.assertRaises(hook.SweepFailed) as cm:
            hook.sweep_job(run_one=flaky)
        self.assertIn("1 of 3 stranded hook(s) failed: hook 1: RuntimeError: wiki down", str(cm.exception))
        self.patch("arsenal_stranded_title_hooks", lambda limit, age: [])
        self.assertEqual(hook.sweep_job(run_one=flaky).job_note, "no stranded hooks")


class LedgerText(unittest.TestCase):
    def test_a_title_card_is_written_with_its_origin_and_title(self) -> None:
        self.assertIn("source_title_id, embedding", ledger.ARSENAL_INSERT_REF_SQL)
        with mock.patch.object(ledger, "_brief_conn") as conn:
            execute = conn.return_value.__enter__.return_value.execute
            execute.return_value.fetchone.return_value = (12,)
            c, _ = cards.validate_ref(item("board_vote"), set())
            self.assertEqual(ledger.arsenal_insert_ref(c, pool_run_id=None, vector=None, source_title_id=77), 12)
            params = execute.call_args.args[1]
            self.assertEqual(params[12:15], ("title_hook", None, 77))
            ledger.arsenal_insert_ref(c, pool_run_id=4, vector=None)
            self.assertEqual(execute.call_args.args[1][12:15], ("pool", 4, None))

    def test_the_stranded_query_is_real_hooks_with_no_live_job(self) -> None:
        sql = ledger.ARSENAL_STRANDED_HOOKS_SQL
        for part in ("NOT h.is_test", "h.status = 'queued'", "q.kind = 'arsenal_title_hook'",
                     "q.status IN ('queued', 'running')", "arsenal_outcome_checks"):
            self.assertIn(part, sql)


if __name__ == "__main__":
    unittest.main()
