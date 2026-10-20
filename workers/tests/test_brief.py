"""The Morning Brief (core/brief.py): schedule, boot catch-up, one delivery
per date, the validator, the fallback and the shared voice. Stubbed ledger and
client: no database, no network, no push.
"""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from datetime import date, datetime, time
from types import SimpleNamespace
from typing import Any
from unittest import mock

from core import brief, ledger, push
from core.voice import load_voice

SECTIONS = [ledger.BriefSection("goals", "Goals", 10), ledger.BriefSection("ventures", "Ventures", 20),
            ledger.BriefSection("capital", "Capital", 30)]
KEYS = [s.key for s in SECTIONS]

PAYLOAD: dict[str, Any] = {
    "local_date": "2026-10-11", "weekday": "Sunday", "iso_week": "2026-W41", "local_time": "06:15",
    "goals": {"week": {"held": 8, "done": 2, "goals": [
        {"title": "Sign the SBC permit", "state": "open", "carried": 2},
        {"title": "Call Kendrick about the renders", "state": "open"},
        {"title": "Gym 4x", "state": "done"}]}},
    "ventures": {"active": [
        {"name": "Sail Beach Club", "slug": "sail-beach-club", "next_action": "Submit DEP application",
         "dates": [{"label": "DEP filing", "due": "2026-10-07", "overdue": True},
                   {"label": "Lender call", "due": "2026-10-11"}]},
        {"name": "Gravity Club", "slug": "gravity-club", "next_action": "Pick a renderer", "dates": []}]},
    "capital": {"note": "x", "modes": {"paper": {"label": "PAPER", "total": 1000, "change_today": 0}}},
    "brief": {"today": None},
}

GOOD = {
    "headline": "Permits lead; one overdue date",
    "sections": [{"key": "goals", "title": "x", "lines": ["Goals: 2 of 8 done."]},
                 {"key": "ventures", "title": "x", "lines": ["Sail Beach Club: DEP filing overdue."]}],
    "one_move": "File the DEP application before noon.",
}


class Schedule(unittest.TestCase):
    def test_the_generation_window_is_every_quarter_hour_from_0615_to_0930(self) -> None:
        times = brief.retry_times()
        self.assertEqual(times[0], time(6, 15))
        self.assertEqual(times[-1], time(9, 30))
        self.assertEqual(len(times), 14)
        self.assertTrue(all((b.hour * 60 + b.minute) - (a.hour * 60 + a.minute) == 15 for a, b in zip(times, times[1:])))

    def test_the_cron_fires_exactly_those_times(self) -> None:
        from core.system_jobs import brief_trigger
        trigger = brief_trigger()
        fired, prev = [], None
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/Chicago")
        now = datetime(2026, 10, 11, 0, 0, tzinfo=tz)
        for _ in range(20):
            nxt = trigger.get_next_fire_time(prev, now)
            if nxt is None or nxt.date() != now.date():
                break
            fired.append(nxt.timetz().replace(tzinfo=None))
            prev, now = nxt, nxt
        self.assertEqual(fired, brief.retry_times())

    def test_fallback_only_from_0930(self) -> None:
        self.assertFalse(brief.may_fall_back(time(9, 29)))
        self.assertTrue(brief.may_fall_back(time(9, 30)))

    def test_push_is_0945_late_after_0950_stale_from_1800(self) -> None:
        self.assertEqual(brief.PUSH_AT, time(9, 45))
        self.assertEqual(brief.push_decision(time(9, 44), False), "wait")
        self.assertEqual(brief.push_decision(time(9, 45), False), "push")
        self.assertEqual(brief.push_decision(time(9, 50), False), "late")
        self.assertEqual(brief.push_decision(time(17, 59), False), "late")
        self.assertEqual(brief.push_decision(time(18, 0), False), "stale")
        self.assertEqual(brief.push_decision(time(10, 0), True), "done")


class BootCatchUp(unittest.TestCase):
    """The matrix: before 06:15, between, after 09:45, after 18:00."""

    CASES = [
        # (local time, has brief, delivered) -> (generate decision, push decision)
        (time(5, 0), False, False, "wait", "wait"),
        (time(7, 0), False, False, "generate", "wait"),
        (time(7, 0), True, False, "skip", "wait"),
        (time(11, 0), False, False, "generate", "late"),
        (time(11, 0), True, True, "skip", "done"),
        (time(19, 0), False, False, "generate", "stale"),
        (time(19, 0), True, False, "skip", "stale"),
    ]

    def test_matrix(self) -> None:
        for now, has, delivered, gen, push_ in self.CASES:
            with self.subTest(now=now, has=has, delivered=delivered):
                self.assertEqual(brief.generate_decision(now, has), gen)
                self.assertEqual(brief.push_decision(now, delivered), push_)


class FakeLedger:
    def __init__(self, now: datetime, *, brief_exists: bool = True, delivered: bool = False) -> None:
        self.now = now
        self.delivered = delivered
        self.briefs: list[dict[str, Any]] = []
        self.deliveries: list[dict[str, Any]] = []
        if brief_exists:
            self.briefs.append({"id": 1, "body": GOOD})

    def owner_local_now(self, conn: Any = None) -> datetime:
        return self.now

    def brief_delivered(self, kind: str, day: date, conn: Any = None) -> bool:
        return self.delivered or bool(self.deliveries)

    def latest_brief(self, kind: str, day: date, conn: Any = None) -> Any:
        if not self.briefs:
            return None
        b = self.briefs[-1]
        return ledger.BriefRow(b["id"], day, b["body"], False, "m", 10, self.now)

    def deliver_brief_once(self, *, kind, day, brief_id, late, send, conn: Any = None) -> int | None:
        # The SQL holds a lock and checks; the fake models the same contract.
        if self.deliveries:
            return None
        nid = send()
        self.deliveries.append({"brief_id": brief_id, "late": late, "notification_id": nid})
        return len(self.deliveries)

    #: Set by a test to simulate another job finishing while this one waited.
    on_lock: Any = None

    @contextmanager
    def brief_generation_lock(self, kind: str, day: date, conn: Any = None):
        self.locks = getattr(self, "locks", 0) + 1
        if self.on_lock:
            self.on_lock()
        yield

    def context_snapshot(self, conn: Any = None) -> dict:
        return PAYLOAD

    def brief_sections(self, conn: Any = None) -> list:
        return SECTIONS

    def insert_brief(self, **row: Any) -> int:
        row.pop("conn", None)
        self.briefs.append({"id": len(self.briefs) + 1, **row})
        return len(self.briefs)


class PushJob(unittest.TestCase):
    def run_job(self, now: datetime, **kw: Any) -> tuple[FakeLedger, list[push.Message]]:
        fake = FakeLedger(now, **kw)
        sent: list[push.Message] = []

        def fake_send(message: push.Message) -> list:
            sent.append(message)
            return [SimpleNamespace(notification_id=99)]

        with mock.patch.multiple(ledger, owner_local_now=fake.owner_local_now, brief_delivered=fake.brief_delivered,
                                 latest_brief=fake.latest_brief, deliver_brief_once=fake.deliver_brief_once,
                                 brief_generation_lock=fake.brief_generation_lock), \
                mock.patch.object(push, "send", fake_send):
            brief.morning_brief_push_job()
        return fake, sent

    def test_0945_pushes_the_headline_to_brief_once(self) -> None:
        fake, sent = self.run_job(datetime(2026, 10, 11, 9, 45))
        [m] = sent
        self.assertEqual((m.kind, m.title, m.body, m.deep_link), ("morning_brief", "Morning brief", GOOD["headline"], "/brief"))
        self.assertEqual(fake.deliveries, [{"brief_id": 1, "late": False, "notification_id": 99}])

    def test_a_boot_after_0950_sends_it_late(self) -> None:
        fake, sent = self.run_job(datetime(2026, 10, 11, 11, 0))
        self.assertEqual(sent[0].body, f"Late · {GOOD['headline']}")
        self.assertTrue(fake.deliveries[0]["late"])

    def test_already_delivered_never_pushes_again(self) -> None:
        fake, sent = self.run_job(datetime(2026, 10, 11, 9, 45), delivered=True)
        self.assertEqual(sent, [])

    def test_after_1800_it_is_stale_and_not_sent(self) -> None:
        fake, sent = self.run_job(datetime(2026, 10, 11, 18, 30))
        self.assertEqual((sent, fake.deliveries), ([], []))

    def test_before_0945_nothing(self) -> None:
        fake, sent = self.run_job(datetime(2026, 10, 11, 9, 0))
        self.assertEqual(sent, [])

    def test_a_second_fire_the_same_day_cannot_push_twice(self) -> None:
        fake = FakeLedger(datetime(2026, 10, 11, 9, 45))
        sends: list[int] = []
        for _ in range(2):
            fake.deliver_brief_once(kind="morning", day=date(2026, 10, 11), brief_id=1, late=False,
                                    send=lambda: sends.append(1) or 7)
        self.assertEqual(len(sends), 1)

    def test_a_failed_push_records_no_delivery(self) -> None:
        fake = FakeLedger(datetime(2026, 10, 11, 9, 45))

        def boom(message: push.Message) -> list:
            raise push.PushError("no devices")

        with mock.patch.multiple(ledger, owner_local_now=fake.owner_local_now, brief_delivered=fake.brief_delivered,
                                 latest_brief=fake.latest_brief, deliver_brief_once=fake.deliver_brief_once,
                                 brief_generation_lock=fake.brief_generation_lock), \
                mock.patch.object(push, "send", boom), self.assertRaises(push.PushError):
            brief.morning_brief_push_job()
        self.assertEqual(fake.deliveries, [], "a push that failed can be retried")


class BootRace(unittest.TestCase):
    """2026-10-10 17:48: at boot the generate and push jobs started together,
    both found no brief, and both called Claude (briefs 124 and 125). Now
    generation is under a lock with a re-check inside it."""

    def test_the_push_job_uses_the_brief_the_generate_job_made_while_it_waited(self) -> None:
        fake = FakeLedger(datetime(2026, 10, 10, 17, 48), brief_exists=False)
        # The generate job finishes while the push job waits for the lock.
        fake.on_lock = lambda: fake.briefs.append({"id": 124, "body": GOOD}) if not fake.briefs else None
        built: list = []
        sent: list = []
        with mock.patch.multiple(ledger, owner_local_now=fake.owner_local_now, brief_delivered=fake.brief_delivered,
                                 latest_brief=fake.latest_brief, deliver_brief_once=fake.deliver_brief_once,
                                 brief_generation_lock=fake.brief_generation_lock),                 mock.patch.object(brief, "build_today", side_effect=lambda **kw: built.append(kw)),                 mock.patch.object(push, "send", lambda m: sent.append(m) or [SimpleNamespace(notification_id=1)]):
            brief.morning_brief_push_job()
        self.assertEqual(built, [], "no second brief, no second AI call")
        self.assertEqual(fake.deliveries[0]["brief_id"], 124)
        self.assertEqual(len(sent), 1)

    def test_with_no_brief_at_all_exactly_one_is_made_under_the_lock(self) -> None:
        fake = FakeLedger(datetime(2026, 10, 11, 6, 15), brief_exists=False)
        with mock.patch.multiple(ledger, owner_local_now=fake.owner_local_now, latest_brief=fake.latest_brief,
                                 brief_generation_lock=fake.brief_generation_lock, context_snapshot=fake.context_snapshot,
                                 brief_sections=fake.brief_sections, insert_brief=fake.insert_brief),                 mock.patch.object(brief, "generate_ai", side_effect=brief.BriefRefused("no")):
            brief.morning_brief_job()
            brief.morning_brief_job()  # the next quarter-hour finds it
        self.assertEqual(len(fake.briefs), 1)
        self.assertEqual(fake.locks, 1, "the second run never needed the lock")


class Validator(unittest.TestCase):
    def test_a_good_brief_passes_and_is_cleaned(self) -> None:
        out = brief.validate(GOOD, KEYS)
        self.assertEqual(out["one_move"], GOOD["one_move"])
        self.assertEqual([s["key"] for s in out["sections"]], ["goals", "ventures"])

    def test_the_word_cap(self) -> None:
        long = dict(GOOD, sections=[{"key": "goals", "title": "Goals", "lines": ["word " * 300]}])
        with self.assertRaisesRegex(brief.BriefInvalid, r"cap is 260"):
            brief.validate(long, KEYS)
        at_cap = dict(GOOD, sections=[{"key": "goals", "title": "Goals",
                                       "lines": ["w " * (260 - brief.word_count(dict(GOOD, sections=[])))]}])
        self.assertEqual(brief.word_count(brief.validate(at_cap, KEYS)), 260)

    def test_an_unknown_section_is_refused(self) -> None:
        with self.assertRaisesRegex(brief.BriefInvalid, r"unknown section 'bets'"):
            brief.validate(dict(GOOD, sections=[{"key": "bets", "title": "Bets", "lines": ["x"]}]), KEYS)

    def test_missing_one_move_or_headline_is_refused(self) -> None:
        for field in ("one_move", "headline"):
            with self.subTest(field=field), self.assertRaisesRegex(brief.BriefInvalid, f"missing {field}"):
                brief.validate({k: v for k, v in GOOD.items() if k != field}, KEYS)
            with self.subTest(field=field, blank=True), self.assertRaises(brief.BriefInvalid):
                brief.validate(dict(GOOD, **{field: "  "}), KEYS)

    def test_a_section_twice_or_empty_is_refused(self) -> None:
        twice = dict(GOOD, sections=[GOOD["sections"][0], GOOD["sections"][0]])
        with self.assertRaisesRegex(brief.BriefInvalid, "twice"):
            brief.validate(twice, KEYS)
        with self.assertRaisesRegex(brief.BriefInvalid, "no lines"):
            brief.validate(dict(GOOD, sections=[{"key": "goals", "title": "G", "lines": []}]), KEYS)


def response(text: str, stop: str = "end_turn") -> Any:
    return SimpleNamespace(model=brief.MODEL, stop_reason=stop,
                           content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=300,
                                                 cache_read_input_tokens=0, cache_creation_input_tokens=0))


class Client:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        return self.answers.pop(0)


class Generation(unittest.TestCase):
    def setUp(self) -> None:
        self.rows: list[dict[str, Any]] = []
        patches = [
            mock.patch.object(ledger, "ai_spend_month_to_date", lambda: 0),
            mock.patch.object(ledger, "notification_sent_this_month", lambda kind: False),
            mock.patch.object(ledger, "record_ai_usage", lambda **r: self.rows.append(r) or 500 + len(self.rows)),
            mock.patch.dict("os.environ", {"AI_MONTHLY_BUDGET_USD": "20"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_the_call_is_sonnet_critical_structured_with_the_shared_voice(self) -> None:
        client = Client(response(json.dumps(GOOD)))
        made = brief.generate_ai(PAYLOAD, SECTIONS, client=client)
        [call] = client.calls
        self.assertEqual(call["model"], "claude-sonnet-5-5")
        self.assertEqual(call["thinking"], {"type": "between_tools"})
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(call["output_config"]["format"]["schema"]["properties"]["sections"]["items"]["properties"]["key"]["enum"], KEYS)
        self.assertEqual(call["system"][0]["text"], load_voice().text, "the brief speaks in the ONE shared voice")
        self.assertIn("cache_control", call["system"][-1])
        self.assertTrue(self.rows[0]["critical"], "critical: never downgraded at 80%")
        self.assertEqual((made.model, made.ai_usage_id, made.fallback), ("claude-sonnet-5-5", 501, False))
        # Titles come from the registry; Capital, left out by GOOD but with data, is filled from its template.
        self.assertEqual([s["title"] for s in made.body["sections"]], ["Goals", "Ventures", "Capital"])

    def test_a_section_with_data_the_model_left_out_is_filled_in_registry_order(self) -> None:
        """Owner, 2026-10-20: briefs 1127 and 1129 had no Arsenal line though
        each day's drop existed before 06:15 (the schema cannot require a key)."""
        payload = dict(PAYLOAD, arsenal_brief={"power_word": "latitude",
                                               "ref": {"title": "28-3", "room": "negotiation"}},
                       screening_brief={"top_pick": {"title": "56 Days", "because_you_loved": "You"}})
        sections = [SECTIONS[0], ledger.BriefSection("screening_brief", "Screening Room", 55),
                    ledger.BriefSection("arsenal_brief", "Arsenal", 60), SECTIONS[1]]
        made = brief.generate_ai(payload, sections, client=Client(response(json.dumps(GOOD))))
        self.assertEqual([s["key"] for s in made.body["sections"]],
                         ["goals", "screening_brief", "arsenal_brief", "ventures"])
        screening = next(s for s in made.body["sections"] if s["key"] == "screening_brief")
        self.assertEqual(screening["lines"], ["Pick: 56 Days, because you loved You."])
        arsenal = next(s for s in made.body["sections"] if s["key"] == "arsenal_brief")
        self.assertEqual(arsenal, {"key": "arsenal_brief", "title": "Arsenal",
                                   "lines": ["Power Word: latitude. Strongest reference: 28-3, for the negotiation."]})
        self.assertEqual(made.words, brief.word_count(made.body))

    def test_nothing_is_filled_without_data_or_a_template_and_the_word_cap_holds(self) -> None:
        empty = dict(PAYLOAD, capital={"error": "boom"}, arsenal_brief={})
        sections = SECTIONS + [ledger.BriefSection("arsenal_brief", "Arsenal", 60),
                               ledger.BriefSection("lookbook_brief", "Lookbook", 70)]
        made = brief.generate_ai(dict(empty, lookbook_brief={"x": 1}), sections,
                                 client=Client(response(json.dumps(GOOD))))
        self.assertEqual([s["key"] for s in made.body["sections"]], ["goals", "ventures"])
        # A fill that would break the cap is dropped again: 251 words leave no room.
        full = dict(GOOD, sections=[dict(GOOD["sections"][0], lines=["word " * 24] * 10)])
        made = brief.generate_ai(PAYLOAD, SECTIONS, client=Client(response(json.dumps(full))))
        self.assertEqual(brief.word_count(full), 251)
        self.assertEqual([s["key"] for s in made.body["sections"]], ["goals"])
        self.assertLessEqual(made.words, brief.MAX_WORDS)

    def test_only_brief_sections_reach_the_prompt(self) -> None:
        client = Client(response(json.dumps(GOOD)))
        brief.generate_ai(PAYLOAD, SECTIONS, client=client)
        data = json.loads(client.calls[0]["messages"][0]["content"].split("CONTEXT:\n", 1)[1])
        self.assertEqual(set(data["context"]), set(KEYS), "ctx_brief (brief_section=false) never feeds the brief")

    def test_one_invalid_answer_is_retried_then_accepted(self) -> None:
        bad = dict(GOOD, one_move="")
        client = Client(response(json.dumps(bad)), response(json.dumps(GOOD)))
        made = brief.generate_ai(PAYLOAD, SECTIONS, client=client)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(made.ai_usage_id, 502, "the accepted call's cost row")

    def test_invalid_twice_raises_for_the_fallback(self) -> None:
        client = Client(response("not json"), response(json.dumps(dict(GOOD, sections=[{"key": "bets", "title": "B", "lines": ["x"]}]))))
        with self.assertRaises(brief.BriefInvalid):
            brief.generate_ai(PAYLOAD, SECTIONS, client=client)

    def test_a_refusal_raises_at_once(self) -> None:
        client = Client(response("", stop="refusal"))
        with self.assertRaises(brief.BriefRefused):
            brief.generate_ai(PAYLOAD, SECTIONS, client=client)
        self.assertEqual(len(client.calls), 1)


class Fallback(unittest.TestCase):
    def test_the_template_is_valid_grounded_and_plain(self) -> None:
        made = brief.fallback_brief(PAYLOAD, SECTIONS)
        self.assertTrue(made.fallback)
        self.assertIsNone(made.model)
        body = made.body
        brief.validate(body, KEYS)
        self.assertLessEqual(made.words, brief.MAX_WORDS)
        self.assertEqual(body["headline"], "Sunday: 1 overdue, 1 due today")
        self.assertEqual(body["one_move"], "Clear Sail Beach Club: DEP filing (overdue since Oct 7).")
        lines = {s["key"]: s["lines"] for s in body["sections"]}
        self.assertEqual(lines["goals"][0], "Goals: 2 of 8 done this week.")
        self.assertEqual(lines["ventures"][0], "Sail Beach Club: DEP filing (overdue since Oct 7).", "overdue first")
        self.assertEqual(lines["ventures"][1], "Sail Beach Club: Lender call (due today).")
        self.assertEqual(lines["capital"], ["Paper: $1,000.00 (+$0.00 today)."], "paper is called paper")

    def test_the_feed_section_needs_only_its_registry_row(self) -> None:
        """db/039 registers 'feed' as a brief section: the fallback words it
        from ctx_feed's top stories, and omits it when there are none."""
        sections = list(SECTIONS) + [ledger.BriefSection("feed", "Feed", 40)]
        feed = {"top_stories": [
            {"title": "Miami marina sold", "source": "Bisnow South Florida",
             "why_it_matters": "A new Sail Beach Club neighbor on the bay."},
            {"title": "Studio merger", "source": "Variety"}]}
        made = brief.fallback_brief(dict(PAYLOAD, feed=feed), sections)
        brief.validate(made.body, [*KEYS, "feed"])
        lines = {s["key"]: s["lines"] for s in made.body["sections"]}
        self.assertEqual(lines["feed"], [
            "Miami marina sold (Bisnow South Florida). A new Sail Beach Club neighbor on the bay.",
            "Studio merger (Variety)."])
        empty = brief.fallback_brief(dict(PAYLOAD, feed={"top_stories": [], "note": "No ranked stories yet."}), sections)
        self.assertNotIn("feed", {s["key"] for s in empty.body["sections"]})

    def test_a_section_with_no_data_is_omitted_never_padded(self) -> None:
        payload = dict(PAYLOAD, goals={"week": {"held": 0, "done": 0, "goals": []}},
                       capital={"modes": {}}, ventures={"error": "provider down"})
        made = brief.fallback_brief(payload, SECTIONS)
        self.assertEqual(made.body["sections"], [])
        self.assertEqual(made.body["one_move"], "Set this week's goals.")

    def test_build_today_falls_back_on_refusal_and_stores_it(self) -> None:
        fake = FakeLedger(datetime(2026, 10, 11, 6, 15), brief_exists=False)
        with mock.patch.multiple(ledger, context_snapshot=fake.context_snapshot, brief_sections=fake.brief_sections,
                                 insert_brief=fake.insert_brief), \
                mock.patch.object(brief, "generate_ai", side_effect=brief.BriefRefused("no")):
            brief.build_today(allow_fallback=False)
        row = fake.briefs[-1]
        self.assertTrue(row["fallback"])
        self.assertEqual(row["voice_version"], load_voice().version)

    def test_an_api_failure_raises_before_0930_and_falls_back_after(self) -> None:
        fake = FakeLedger(datetime(2026, 10, 11, 6, 15), brief_exists=False)
        with mock.patch.multiple(ledger, context_snapshot=fake.context_snapshot, brief_sections=fake.brief_sections,
                                 insert_brief=fake.insert_brief), \
                mock.patch.object(brief, "generate_ai", side_effect=RuntimeError("API down")):
            with self.assertRaises(RuntimeError):
                brief.build_today(allow_fallback=False)
            self.assertEqual(fake.briefs, [])
            brief.build_today(allow_fallback=True)
        self.assertTrue(fake.briefs[-1]["fallback"])


if __name__ == "__main__":
    unittest.main()
