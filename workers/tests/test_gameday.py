"""Game Day (Chat 7 Phase 3; docs/gameday.md): the brief's slate section
(core/brief.py sports_brief), the TV-tier twin between db/068 and the web, and
db/068's static shape. Stubbed ledger and client: no database, no network.
"""

from __future__ import annotations

import json
import re
import unittest
from typing import Any
from unittest import mock

from core import brief, ledger
from core.paths import REPO_ROOT
from tests.test_brief import GOOD, PAYLOAD, SECTIONS, Client, response

DB068 = REPO_ROOT / "db" / "068_gameday.sql"
TIERS_TS = REPO_ROOT / "apps" / "web" / "src" / "lib" / "gameday" / "tiers.ts"
SLATE_TS = REPO_ROOT / "apps" / "web" / "src" / "lib" / "gameday" / "slate.ts"

SLATE = ["CFB: No. 5 Alabama Crimson Tide at No. 14 LSU Tigers, 2:30 PM on ABC.",
         "CFB: No. 1 Miami Hurricanes at No. 11 Notre Dame Fighting Irish, 6:30 PM on NBC.",
         "NBA: Miami Heat at Golden State Warriors, 9:00 PM on NBA TV.",
         "F1, São Paulo Grand Prix: Qualifying today 12:00 PM; Race Sun 11:00 AM.",
         "Golf, World Wide Technology Championship: R3 today; Chris Kirk leads at -9 after R2."]
SPORTS = ledger.BriefSection("sports_brief", "Game Day", 47)


def _sql_list(sql: str, marker: str) -> list[str]:
    """The quoted names of the IN (...) list right after `marker`."""
    block = sql[sql.index(marker):]
    start = block.index("IN (") + 4
    inner = block[start:block.index(")", start)]
    return re.findall(r"'([^']+)'", inner)


def _ts_list(ts: str, name: str) -> list[str]:
    block = ts[ts.index(f"export const {name}"):]
    return re.findall(r'"([^"]+)"', block[block.index("[") + 1:block.index("]")])


class TierTwin(unittest.TestCase):
    """db/068 sports_tv_tier and apps/web lib/gameday/tiers.ts must agree."""

    def test_the_lists_are_the_same(self) -> None:
        sql, ts = DB068.read_text(encoding="utf-8"), TIERS_TS.read_text(encoding="utf-8")
        fn = sql[sql.index("FUNCTION public.sports_tv_tier"):]
        self.assertEqual(_sql_list(fn, "WHERE upper(btrim(b)) IN ('ABC'"), _ts_list(ts, "BROADCAST_NETWORKS"))
        cable_at = fn.index("WHERE upper(btrim(b)) IN ('ESPN'")
        self.assertEqual(_sql_list(fn[cable_at:], "WHERE upper(btrim(b)) IN"), _ts_list(ts, "NATIONAL_CABLE"))
        self.assertEqual(_ts_list(ts, "BROADCAST_NETWORKS"), ["ABC", "CBS", "NBC", "FOX"])
        self.assertIn("GOLF CHNL", _ts_list(ts, "NATIONAL_CABLE"))

    def test_alabama_and_the_sec_are_the_same_ids_in_both(self) -> None:
        sql, ts = DB068.read_text(encoding="utf-8"), SLATE_TS.read_text(encoding="utf-8")
        self.assertIn('ALABAMA_TEAM_ID = "333"', ts)
        self.assertIn('SEC_CONFERENCE_ID = "8"', ts)
        self.assertIn("'333' IN", sql)
        self.assertIn("'8' IN", sql)


class Db068Shape(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = DB068.read_text(encoding="utf-8")

    def test_no_transaction_control_or_psql_meta(self) -> None:
        for bad in ("\nBEGIN;", "\nCOMMIT;", "\n\\i ", "\n\\copy"):
            self.assertNotIn(bad, self.sql)

    def test_readers_are_invoker_and_only_the_two_test_helpers_are_definers(self) -> None:
        fns = re.findall(r"CREATE OR REPLACE FUNCTION public\.(\w+)\((.*?)\$\$", self.sql, re.S)
        names = {n for n, _ in fns}
        self.assertEqual(names, {"gameday_tz", "gameday_day", "gameday_today", "sports_tv_tier", "gameday_at",
                                 "gameday_title", "gameday_score", "gameday_slate", "gameday_live", "gameday_event",
                                 "ctx_sports_today", "gameday_brief", "ctx_sports_brief", "add_test_gameday_event",
                                 "set_test_sports_live"})
        for name, body in fns:
            with self.subTest(fn=name):
                self.assertIn("SET search_path = ''", body)
                definer = "SECURITY DEFINER" in body
                self.assertEqual(definer, name in ("add_test_gameday_event", "set_test_sports_live"))
                if definer:
                    self.assertIn("e2e_marker_ok(p_marker)", self.sql[self.sql.index(f"public.{name}("):])

    def test_the_brief_provider_and_the_self_test(self) -> None:
        self.assertIn("VALUES ('sports_brief', 'Game Day', 'public.ctx_sports_brief()'::regprocedure, true, 47)",
                      self.sql)
        self.assertIn("RAISE EXCEPTION 'selftest-rollback'", self.sql)
        self.assertIn("INSERT INTO migration_log (name) VALUES ('068_gameday')", self.sql)


DB069 = REPO_ROOT / "db" / "069_nfl_first_marker_closed.sql"


def _fn(sql: str, name: str) -> str:
    """One CREATE FUNCTION's text, up to the next top-level statement after its closing $$."""
    start = sql.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    body_end = sql.index("$$;", sql.index("AS $$", start)) + 3
    return sql[start:body_end]


class Db069Shape(unittest.TestCase):
    """db/069 (owner, Nov 5): NFL first on any night; e2e_marker_ok fails closed."""

    def setUp(self) -> None:
        self.sql = DB069.read_text(encoding="utf-8")

    def test_gameday_brief_is_db068s_with_only_the_nfl_rule_changed(self) -> None:
        old = _fn(DB068.read_text(encoding="utf-8"), "gameday_brief")
        expected = (old.replace("    v_sunday boolean := extract(isodow FROM p_day) = 7;\n", "")
                    .replace("    -- The owner's five's national-TV games, NFL first on a Sunday.",
                             "    -- The owner's five's national-TV games, NFL first on any night it plays (owner, Nov 5).")
                    .replace("row_number() OVER (ORDER BY (v_sunday AND e.league = 'nfl') DESC,",
                             "row_number() OVER (ORDER BY (e.league = 'nfl') DESC,")
                    .replace("'play, NFL first on a Sunday). Use these lines as given.',",
                             "'play, NFL first on any night it plays). Use these lines as given.',"))
        self.assertNotEqual(expected, old)
        self.assertEqual(_fn(self.sql, "gameday_brief"), expected)

    def test_the_marker_check_is_coalesced_to_false_and_keeps_its_shape(self) -> None:
        fn = _fn(self.sql, "e2e_marker_ok")
        self.assertIn("SELECT coalesce(", fn)
        self.assertTrue(fn.rstrip().endswith("false);\n$$;") or fn.rstrip().endswith("false);\n$$;".rstrip()))
        for part in ("SECURITY DEFINER", "SET search_path = ''", "STABLE", "public.is_owner()",
                     "FROM public.e2e_markers m ORDER BY m.id DESC LIMIT 1"):
            self.assertIn(part, fn)
        self.assertNotIn("GRANT", self.sql.split("-- Self-test")[0].split("DO $$")[0])
        self.assertIn("RAISE EXCEPTION 'selftest-rollback'", self.sql)
        self.assertIn("INSERT INTO migration_log (name) VALUES ('069_nfl_first_marker_closed')", self.sql)


class SportsBriefSection(unittest.TestCase):
    def setUp(self) -> None:
        for p in (mock.patch.object(ledger, "ai_spend_month_to_date", lambda: 0),
                  mock.patch.object(ledger, "notification_sent_this_month", lambda kind: False),
                  mock.patch.object(ledger, "record_ai_usage", lambda **r: 501),
                  mock.patch.dict("os.environ", {"AI_MONTHLY_BUDGET_USD": "20"})):
            p.start()
            self.addCleanup(p.stop)

    def test_the_template_is_the_databases_lines_at_most_five(self) -> None:
        self.assertIn("sports_brief", brief.FALLBACK_SECTIONS)
        self.assertIn("sports_brief", brief.DATA_LINE_SECTIONS)
        self.assertNotIn("sports_brief", brief.ONE_LINE_SECTIONS)
        payload = dict(PAYLOAD, sports_brief={"lines": SLATE + ["a sixth line", "", 7]})
        self.assertEqual(brief.template_lines(payload, "sports_brief", "2026-11-07"), SLATE)
        self.assertEqual(brief.template_lines(dict(PAYLOAD, sports_brief={}), "sports_brief", "x"), [])

    def test_the_ai_brief_prints_the_slate_as_given_never_the_models_pick(self) -> None:
        body = dict(GOOD, sections=GOOD["sections"] + [
            {"key": "sports_brief", "title": "x", "lines": ["Watch the Lakers tonight!"]}])
        made = brief.generate_ai(dict(PAYLOAD, sports_brief={"lines": SLATE}), SECTIONS + [SPORTS],
                                 client=Client(response(json.dumps(body))))
        sports = next(s for s in made.body["sections"] if s["key"] == "sports_brief")
        self.assertEqual(sports, {"key": "sports_brief", "title": "Game Day", "lines": SLATE})

    def test_left_out_by_the_model_it_is_filled_from_the_data(self) -> None:
        made = brief.generate_ai(dict(PAYLOAD, sports_brief={"lines": SLATE[:2]}), SECTIONS + [SPORTS],
                                 client=Client(response(json.dumps(GOOD))))
        self.assertEqual(next(s for s in made.body["sections"] if s["key"] == "sports_brief")["lines"], SLATE[:2])

    def test_the_word_cap_holds_after_the_data_lines_replace_the_models(self) -> None:
        # A model brief at 236 words with a one-line sports section; the five
        # data lines (~65 words) would take it past 260: trimmed from the end.
        long_goals = {"key": "goals", "title": "G", "lines": ["word " * 24] * 9}
        body = dict(GOOD, sections=[long_goals, {"key": "sports_brief", "title": "x", "lines": ["one"]}])
        self.assertLessEqual(brief.word_count(body), brief.MAX_WORDS)
        made = brief.generate_ai(dict(PAYLOAD, sports_brief={"lines": SLATE}), SECTIONS + [SPORTS],
                                 client=Client(response(json.dumps(body))))
        sports = next(s for s in made.body["sections"] if s["key"] == "sports_brief")
        self.assertLessEqual(made.words, brief.MAX_WORDS)
        self.assertGreaterEqual(len(sports["lines"]), 1)
        self.assertLess(len(sports["lines"]), 5)
        self.assertEqual(sports["lines"], SLATE[:len(sports["lines"])], "trimmed from the end, in order")

    def test_fit_data_lines_never_goes_below_one_line(self) -> None:
        body: dict[str, Any] = {"headline": "h", "one_move": "m", "sections": [
            {"key": "goals", "title": "G", "lines": ["word " * 260]},
            {"key": "sports_brief", "title": "S", "lines": SLATE[:3]}]}
        out = brief.fit_data_lines(body)
        self.assertEqual(out["sections"][1]["lines"], SLATE[:1])

    def test_the_fallback_brief_carries_the_slate(self) -> None:
        made = brief.fallback_brief(dict(PAYLOAD, sports_brief={"lines": SLATE}), SECTIONS + [SPORTS])
        self.assertTrue(made.fallback)
        self.assertEqual(next(s for s in made.body["sections"] if s["key"] == "sports_brief")["lines"], SLATE)

    def test_a_dead_night_has_no_section(self) -> None:
        """db/068 returns NULL on a dead night; build_today drops a NULL slice
        before the model sees it, and the template has nothing to fill."""
        made = brief.fallback_brief(dict(PAYLOAD, sports_brief=None), SECTIONS + [SPORTS])
        self.assertNotIn("sports_brief", [s["key"] for s in made.body["sections"]])


if __name__ == "__main__":
    unittest.main()
