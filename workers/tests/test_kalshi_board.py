"""The Kalshi board (Chat 8 Phase 1; db/071; venues/kalshi/board.py,
sports/kalshi_board.py). No database, no network: the Kalshi transport is a
fake serving trimmed real responses (tests/fixtures/kalshi_board, recorded
2026-11-06: rules text cut to 60 characters, four markets per event), and the
database side is a recording Writer.
"""

from __future__ import annotations

import copy
import json
import pathlib
import threading
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from unittest import mock
from decimal import Decimal
from typing import Any

from sports import kalshi_board as job
from venues.kalshi import board as kb
from venues.kalshi.client import KalshiClient
from venues.kalshi.events import KalshiShapeError
from venues.kalshi.fees import FeeRegime, UnsupportedFeeType
from tests.test_system_jobs import SystemTestCase

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "kalshi_board"
UTC = timezone.utc
#: The fixtures' moment: Nov 6, 2026, 18:00 UTC (noon Chicago). The recorded
#: games start 00:00 UTC Nov 7; two others at ~19:10 UTC Nov 6.
NOW = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_bytes())


def markets(series: str) -> list[dict[str, Any]]:
    return load(f"markets_{series}")["markets"]


def series_obj(ticker: str, fee_type: str = "quadratic", mult: Any = 1) -> kb.Series:
    regime = FeeRegime(series=ticker, fee_type=fee_type, multiplier=Decimal(str(mult)), effective_from=None,
                       fetched_at=NOW)
    return kb.Series(ticker, ticker, "Basketball", ("Basketball",), "Sports", regime)


def parse(series: str, i: int = 0, **override: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = copy.deepcopy(markets(series)[i])
    raw.update(override)
    return kb.parse_market(raw, series_obj(series), "$.markets[0]")


class FakeKalshi:
    """Serves the fixtures by path and query; records every URL."""

    def __init__(self, *, overrides: dict[str, Any] | None = None) -> None:
        self.urls: list[str] = []
        self.overrides = overrides or {}
        self.series = {s["ticker"]: s for s in load("series")["series"]}

    def __call__(self, url: str) -> tuple[int, Any]:
        self.urls.append(url)
        path, _, query = url.split("/trade-api/v2", 1)[1].partition("?")
        q = dict(urllib.parse.parse_qsl(query))
        if path in self.overrides:
            return 200, copy.deepcopy(self.overrides[path])
        if path == "/milestones":
            # Only events of recorded series: an unrecorded one would answer
            # no market, which (rightly) fails the run.
            body = load("milestones")
            for m in body["milestones"]:
                for key in ("primary_event_tickers", "related_event_tickers"):
                    m[key] = [t for t in m.get(key) or [] if (FIXTURES / f"markets_{kb.series_of(t)}.json").exists()]
            return 200, body
        if path == "/series":
            return 200, load("series")
        if path.startswith("/series/"):
            t = path.split("/")[-1]
            return 200, {"series": self.series.get(t) or {
                "ticker": t, "title": f"{t} title", "fee_type": "quadratic", "fee_multiplier": 1, "tags": []}}
        if path == "/multivariate_event_collections":
            return 200, load("collections")
        if path == "/markets":
            if "event_ticker" in q:
                e = q["event_ticker"]
                f = FIXTURES / f"markets_{kb.series_of(e)}.json"
                rows = json.loads(f.read_bytes())["markets"] if f.exists() else []
                return 200, {"markets": [m for m in rows if m["event_ticker"] == e], "cursor": ""}
            s = q["series_ticker"]
            f = FIXTURES / f"markets_{s}.json"
            return 200, {"markets": json.loads(f.read_bytes())["markets"] if f.exists() else [], "cursor": ""}
        return 404, None


def client(fake: FakeKalshi) -> KalshiClient:
    return KalshiClient(transport=fake, min_interval=0, sleep=lambda s: None)


class RecordingWriter(job.Writer):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def series(self, rows):
        self.calls.append(("series", [r["series_ticker"] for r in rows]))
        return len(rows)

    def events(self, rows, at):
        self.calls.append(("events", [r["event_ticker"] for r in rows]))
        return len(rows)

    def markets(self, rows, at):
        self.calls.append(("markets", [r["market_ticker"] for r in rows]))
        return len(rows)

    def touch(self, events, series, at):
        self.calls.append(("touch", (list(events), list(series))))

    def sweep(self, events, markets):
        self.calls.append(("sweep", (list(events), list(markets))))
        return 0

    def link(self):
        self.calls.append(("link", None))
        return 0

    def prune(self):
        self.calls.append(("prune", None))
        return {"events": 0, "markets": 0, "series": 0}

    def sent(self, kind: str) -> list[str]:
        return [t for k, v in self.calls if k == kind for t in v]


# --------------------------------------------------------------- kinds

class Kinds(unittest.TestCase):
    """Owner-approved kinds (Nov 6), from the fields Kalshi sends."""

    def kind(self, series: str, i: int = 0) -> str:
        return parse(series, i)[0]["market_kind"]

    def test_player_markets_are_props(self) -> None:
        for s in ("KXNBAREB", "KXNBAREBPROT", "KXNHLGOAL", "KXNBAPTSLEADER"):
            with self.subTest(s):
                self.assertEqual(self.kind(s), "prop")

    def test_cfb_team_totals_are_game_lines(self) -> None:
        # The probe's first misread (it counted these as props).
        row, ex = parse("KXNCAAFTEAMTOTAL")
        self.assertEqual(row["market_kind"], "game_line")
        self.assertEqual((row["subject"], row["stat"], row["line"]), ("Virginia Tech", "team_total", "37.5"))
        self.assertFalse(ex["miss"])

    def test_a_first_half_winner_is_a_game_line(self) -> None:
        # The probe's second misread (it counted these as other).
        row, _ = parse("KXNCAAF1H")
        self.assertEqual(row["market_kind"], "game_line")
        self.assertEqual((row["subject"], row["stat"]), ("Virginia Tech", "winner"))

    def test_moneyline_spread_and_total(self) -> None:
        ml, _ = parse("KXNBAGAME")
        self.assertEqual((ml["market_kind"], ml["subject"], ml["stat"]), ("game_line", "Charlotte", "winner"))
        sp, _ = parse("KXNCAAFSPREAD")
        self.assertEqual((sp["market_kind"], sp["subject"], sp["stat"], sp["line"]),
                         ("game_line", "Virginia Tech", "spread", "7.5"))
        tot, _ = parse("KXNCAAFTOTAL")
        self.assertEqual((tot["market_kind"], tot["subject"], tot["stat"], tot["line"]),
                         ("game_line", None, "total", "79.5"))

    def test_period_lines_parse_from_the_sub_title(self) -> None:
        # Titles vary by period; Kalshi's sub-title is canonical (live, Nov 6).
        cases = [
            ("Virginia Tech wins 1H by over 7.5 points", "Virginia Tech wins by over 7.5 points", 7.5,
             ("Virginia Tech", "spread")),
            ("Charlotte wins the 1st half by over 7.5 points", "Charlotte wins by over 7.5 points", 7.5,
             ("Charlotte", "spread")),
            ("Columbus wins 1st Period by over 1.5 goals", "Columbus wins by over 1.5 goals", 1.5, ("Columbus", "spread")),
            ("Everton wins by more than 2.5 goals?", "Everton wins by more than 2.5 goals", 2.5, ("Everton", "spread")),
            ("1st Quarter: Over 66.5 points scored", "Over 66.5 points", 66.5, (None, "total")),
            ("Will over 5.5 goals be scored?", "Over 5.5 goals scored", 5.5, (None, "total")),
            ("Virginia Tech over 6.5 1H points scored", "Virginia Tech over 6.5 points", 6.5,
             ("Virginia Tech", "team_total")),
            ("Charlotte over 97.5 points scored", "Charlotte over 97.5 points", 97.5, ("Charlotte", "team_total")),
        ]
        for title, sub, line, want in cases:
            with self.subTest(title):
                p = kb.parse_subject("game_line", title, sub, "greater", Decimal(str(line)))
                self.assertEqual((p.subject, p.stat, p.miss), (*want, False))

    def test_correct_score_is_other(self) -> None:
        row, ex = parse("KXMLSSCORE")
        self.assertEqual((row["market_kind"], row["subject"], row["stat"]), ("other", "Chicago Fire wins 5-2", None))
        self.assertFalse(ex["miss"])

    def test_the_series_tail_decides_a_teams_market(self) -> None:
        team = {"custom_strike": {"basketball_team": "x"}, "primary_participant_key": None}
        for series, want in (("KXNBA1HSPREAD", "game_line"), ("KXNBA2QTOTAL", "game_line"),
                             ("KXNHL3P", "game_line"), ("KXNCAAF1HFT", "other"), ("KXNBAOT", "other"),
                             ("KXNBAWINMARGIN", "other"), ("KXEPLGAME", "game_line"), ("KXATPMATCH", "game_line"),
                             ("KXUFCFIGHT", "game_line"), ("KXPGATOUR", "other"), ("KXNBASPREADPROT", "game_line")):
            with self.subTest(series):
                self.assertEqual(kb.market_kind(series, team), want)

    def test_a_player_key_wins_over_the_series_tail(self) -> None:
        self.assertEqual(kb.market_kind("KXNBAGAME", {"primary_participant_key": "basketball_player"}), "prop")
        self.assertEqual(kb.market_kind("KXNBATOTAL", {"custom_strike": {"football_player": "x"}}), "prop")


class Parsing(unittest.TestCase):
    def test_a_prop_reads_subject_stat_and_line(self) -> None:
        row, ex = parse("KXNBAREB")
        self.assertEqual((row["subject"], row["stat"], row["line"], row["label"]),
                         ("Ryan Kalkbrenner", "rebounds", "9.5", "Ryan Kalkbrenner: 10+"))
        self.assertIsNone(row["variant"])
        self.assertFalse(ex["miss"])

    def test_the_protected_series_is_a_variant(self) -> None:
        row, _ = parse("KXNBAREBPROT")
        self.assertEqual((row["variant"], row["stat"]), ("protected", "rebounds"))

    def test_a_leader_market_reads_its_stat_from_the_title(self) -> None:
        row, ex = parse("KXNBAPTSLEADER")
        self.assertEqual((row["subject"], row["stat"], row["line"]), ("Sion James", "points leader", None))
        self.assertFalse(ex["miss"])

    def test_a_parse_miss_keeps_the_market_with_nulls(self) -> None:
        row, ex = parse("KXNBAREB", title="Something unusual", yes_sub_title="Odd one")
        self.assertTrue(ex["miss"])
        self.assertEqual((row["market_kind"], row["subject"], row["stat"], row["line"]),
                         ("prop", "Odd one", None, "9.5"))
        batch = kb.Batch()
        raw = dict(markets("KXNBAREB")[0], title="Something unusual")
        kb.parse_markets_page({"markets": [raw, markets("KXNBAREB")[1]]}, {"KXNBAREB": series_obj("KXNBAREB")},
                              batch, "t")
        self.assertEqual(len(batch.rows), 2)
        self.assertEqual(batch.misses, {"KXNBAREB": 1})

    def test_custom_strike_ids_are_never_stored(self) -> None:
        row, _ = parse("KXNBAREB")
        self.assertNotIn("custom_strike", row)
        self.assertNotIn("15a36614", json.dumps(row))

    def test_the_no_side_is_the_complement_on_every_fixture_market(self) -> None:
        checked = 0
        for f in FIXTURES.glob("markets_*.json"):
            s = f.stem.removeprefix("markets_")
            for i in range(len(markets(s))):
                _, ex = parse(s, i)
                if ex["complement"] is not None:
                    checked += 1
                    self.assertTrue(ex["complement"], f"{s}[{i}]")
        self.assertGreater(checked, 30)

    def test_an_inactive_market_is_skipped(self) -> None:
        batch = kb.Batch()
        raw = dict(markets("KXNBAREB")[0], status="inactive")
        kb.parse_markets_page({"markets": [raw]}, {"KXNBAREB": series_obj("KXNBAREB")}, batch, "t")
        self.assertEqual(batch.rows, [])


class FeeAtAsk(unittest.TestCase):
    def test_taker_fee_at_the_ask_uses_the_regime(self) -> None:
        raw = markets("KXNBAREB")[0]
        ask = Decimal(raw["yes_ask_dollars"])
        for fee_type, mult in (("quadratic", 1), ("quadratic_with_maker_fees", 1), ("quadratic", "0.5")):
            with self.subTest(fee_type=fee_type, mult=mult):
                s = series_obj("KXNBAREB", fee_type, mult)
                row, _ = kb.parse_market(copy.deepcopy(raw), s, "$")
                self.assertEqual(Decimal(row["taker_fee_at_ask"]), s.regime.taker_fee(ask))
        half = kb.parse_market(copy.deepcopy(raw), series_obj("KXNBAREB", "quadratic", "0.5"), "$")[0]
        full = kb.parse_market(copy.deepcopy(raw), series_obj("KXNBAREB"), "$")[0]
        self.assertLess(Decimal(half["taker_fee_at_ask"]), Decimal(full["taker_fee_at_ask"]))

    def test_no_fee_without_a_tradable_ask(self) -> None:
        for ask in ("0.0000", "1.0000", None):
            with self.subTest(ask=ask):
                row, _ = parse("KXNBAREB", yes_ask_dollars=ask)
                self.assertIsNone(row["taker_fee_at_ask"])

    def test_an_unmodelled_fee_type_or_multiplier_fails_naming_the_series(self) -> None:
        base = {"ticker": "KXODD", "title": "Odd", "fee_type": "flat", "fee_multiplier": 1, "tags": []}
        with self.assertRaisesRegex(UnsupportedFeeType, "KXODD"):
            kb.parse_series(base, NOW)
        with self.assertRaisesRegex(UnsupportedFeeType, "KXODD"):
            kb.parse_series(dict(base, fee_type="quadratic", fee_multiplier=0), NOW)
        with self.assertRaisesRegex(KalshiShapeError, "KXODD.*fee_type"):
            kb.parse_series({k: v for k, v in base.items() if k != "fee_type"}, NOW)

    def test_every_recorded_series_fee_parses(self) -> None:
        for raw in load("series")["series"]:
            with self.subTest(raw["ticker"]):
                kb.parse_series(raw, NOW)


class Collections(unittest.TestCase):
    def test_eligibility_and_yes_only_by_event(self) -> None:
        body = load("collections")
        c, cursor = kb.parse_collections(body)
        coll = body["multivariate_contracts"][0]
        self.assertIsNone(cursor)
        self.assertEqual(set(c.by_event), set(coll["associated_event_tickers"]))
        yes_only = {a["ticker"] for a in coll["associated_events"] if a.get("is_yes_only")}
        for t, combo in c.by_event.items():
            self.assertEqual(combo.collections, (coll["collection_ticker"],))
            self.assertEqual(combo.yes_only, t in yes_only)
        meta = c.meta[coll["collection_ticker"]]
        self.assertEqual((meta["is_single_market_per_event"], meta["is_all_yes"], meta["is_ordered"]),
                         (False, False, False))

    def test_the_per_event_leg_limit_and_quoters(self) -> None:
        body = load("collections")
        coll = body["multivariate_contracts"][0]
        c, _ = kb.parse_collections(body)
        for a in coll["associated_events"]:
            with self.subTest(a["ticker"]):
                combo = c.by_event[a["ticker"]]
                self.assertEqual(combo.size_max, a.get("size_max"))  # None stays None
                self.assertEqual(combo.quoters, len(a["active_quoters"]) if "active_quoters" in a else None)
        # Both shapes are in the recorded page: a stated cap and an absent one.
        self.assertTrue(any(a.get("size_max") is not None for a in coll["associated_events"]))
        self.assertTrue(any(a.get("size_max") is None for a in coll["associated_events"]))

    def test_an_absent_cap_is_never_0_or_1_and_the_tighter_cap_wins(self) -> None:
        t = "KXNBAREB-26NOV06BOSCHA"
        body = {"multivariate_contracts": [
            {"collection_ticker": "A-R", "associated_event_tickers": [t],
             "associated_events": [{"ticker": t, "is_yes_only": False, "active_quoters": []}]},
            {"collection_ticker": "B-R", "associated_event_tickers": [t],
             "associated_events": [{"ticker": t, "is_yes_only": True, "size_max": 3, "active_quoters": ["q1", "q2"]}]},
            {"collection_ticker": "C-R", "associated_event_tickers": [t],
             "associated_events": [{"ticker": t, "size_max": 2}]},
        ]}
        c, _ = kb.parse_collections(body)
        self.assertEqual(c.by_event[t], kb.Combo(("A-R", "B-R", "C-R"), True, 2, 2))
        alone, _ = kb.parse_collections({"multivariate_contracts": body["multivariate_contracts"][:1]})
        self.assertEqual((alone.by_event[t].size_max, alone.by_event[t].quoters), (None, 0))
        row = job.event_row(t, kb.Game("G", "g", NOW, None, None, (t,)), (None, None), alone.by_event[t])
        self.assertIsNone(row["combo_size_max"])
        self.assertEqual(row["combo_quoters"], 0)
        none = job.event_row(t, kb.Game("G", "g", NOW, None, None, (t,)), (None, None), None)
        self.assertEqual((none["combo_eligible"], none["combo_size_max"], none["combo_quoters"]), (False, None, None))
        bad = copy.deepcopy(body)
        bad["multivariate_contracts"][1]["associated_events"][0]["size_max"] = -1
        with self.assertRaisesRegex(KalshiShapeError, "size_max"):
            kb.parse_collections(bad)

    def test_keep_limits_to_the_window(self) -> None:
        body = load("collections")
        first = body["multivariate_contracts"][0]["associated_event_tickers"][0]
        c, _ = kb.parse_collections(body, keep={first, "KXNOTLISTED-1"})
        self.assertEqual(set(c.by_event), {first})


class Contract(unittest.TestCase):
    """Every shape the job reads fails loud, naming where."""

    def test_milestones(self) -> None:
        body = load("milestones")
        del body["milestones"][0]["start_date"]
        with self.assertRaisesRegex(KalshiShapeError, r"milestones\[0\]\.start_date"):
            kb.parse_milestones(body, NOW, NOW + kb.WINDOW)
        with self.assertRaisesRegex(KalshiShapeError, "milestones"):
            kb.parse_milestones({"items": []}, NOW, NOW + kb.WINDOW)

    def test_collections(self) -> None:
        body = load("collections")
        del body["multivariate_contracts"][0]["associated_event_tickers"]
        with self.assertRaisesRegex(KalshiShapeError, "associated_event_tickers"):
            kb.parse_collections(body)

    def test_markets(self) -> None:
        s = {"KXNBAREB": series_obj("KXNBAREB")}
        for field in ("ticker", "event_ticker", "title", "close_time", "status"):
            with self.subTest(field):
                raw = dict(markets("KXNBAREB")[0])
                del raw[field]
                with self.assertRaises(KalshiShapeError):
                    kb.parse_markets_page({"markets": [raw]}, s, kb.Batch(), "t")
        raw = dict(markets("KXNBAREB")[0], yes_ask_dollars=0.42)
        with self.assertRaisesRegex(KalshiShapeError, "yes_ask_dollars"):
            kb.parse_markets_page({"markets": [raw]}, s, kb.Batch(), "t")
        raw = dict(markets("KXNBAREB")[0], event_ticker="KXUNKNOWN-1")
        with self.assertRaisesRegex(KalshiShapeError, "not on the board"):
            kb.parse_markets_page({"markets": [raw]}, s, kb.Batch(), "t")

    def test_window_and_primary(self) -> None:
        games, _ = kb.parse_milestones(load("milestones"), NOW, NOW + kb.WINDOW)
        ev = kb.window_events(games)
        self.assertEqual(ev["KXNBAREB-26NOV06BOSCHA"].game_ticker, "KXNBAGAME-26NOV06BOSCHA")
        self.assertEqual(ev["KXNBAGAME-26NOV06BOSCHA"].label, "NBA")
        late, _ = kb.parse_milestones(load("milestones"), NOW + timedelta(days=2), NOW + timedelta(days=4))
        self.assertEqual(late, [])


class Fetching(unittest.TestCase):
    def test_never_a_comma_list_and_the_series_rule(self) -> None:
        fake = FakeKalshi()
        tickers = ["KXNBAREB-26NOV06BOSCHA", "KXNBAREB-26NOV06WASATL", "KXNBAREB-26NOV06PHICLE",
                   "KXNBAGAME-26NOV06BOSCHA"]
        series = {"KXNBAREB": series_obj("KXNBAREB"), "KXNBAGAME": series_obj("KXNBAGAME")}
        got = list(kb.market_batches(client(fake), tickers, series, per_event_max=2))
        self.assertEqual([g for g, _ in got], [["KXNBAGAME-26NOV06BOSCHA"], tickers[:3]])
        qs = [dict(urllib.parse.parse_qsl(u.partition("?")[2])) for u in fake.urls]
        self.assertFalse(any("," in q.get("event_ticker", "") for q in qs))
        self.assertIn({"event_ticker": "KXNBAGAME-26NOV06BOSCHA", "status": "open", "limit": "100"}, qs)
        self.assertIn({"series_ticker": "KXNBAREB", "status": "open", "limit": "100"}, qs)
        self.assertTrue(all(r["event_ticker"] in tickers for _, b in got for r in b.rows))


# --------------------------------------------------------------- the jobs

class FullJob(unittest.TestCase):
    def run_full(self, fake: FakeKalshi | None = None, state: job.BoardState | None = None,
                 writer: RecordingWriter | None = None) -> tuple[job.BoardRun, RecordingWriter, job.BoardState]:
        w = writer or RecordingWriter()
        st = state if state is not None else job.BoardState()
        run = job.full_job(client=client(fake or FakeKalshi()), now=lambda: NOW, state=st, writer=w)
        return run, w, st

    def test_the_full_pull(self) -> None:
        run, w, st = self.run_full()
        self.assertGreater(run.markets, 30)
        self.assertEqual(run.no_mismatch, 0)
        self.assertGreater(run.no_checked, 30)
        self.assertEqual(run.sent, run.markets)
        kinds = [k for k, _ in w.calls]
        # Series first; every events write before the markets that need it.
        self.assertEqual(kinds[0], "series")
        self.assertLess(kinds.index("events"), kinds.index("markets"))
        self.assertEqual(kinds[-3:], ["touch", "link", "prune"])
        self.assertIn("KXNBAREB-26NOV06BOSCHA", w.sent("events"))
        self.assertEqual(len(st.markets), run.markets)
        self.assertIn("KXNBAREB-26NOV06BOSCHA", st.window)
        self.assertIn("Basketball", run.by_sport)
        self.assertIn("NO complement mismatches 0/", run.job_note)

    def test_a_second_pull_sends_only_what_changed(self) -> None:
        _, _, st = self.run_full()
        run2, w2, _ = self.run_full(state=st)
        self.assertEqual((run2.sent, w2.sent("markets"), w2.sent("events"), w2.sent("series")), (0, [], [], []))
        # The sweep and the touch still cover every event asked for.
        self.assertIn("KXNBAREB-26NOV06BOSCHA", [t for k, v in w2.calls if k == "sweep" for t in v[0]])
        moved = load("markets_KXNBAREB")
        moved["markets"][0]["yes_ask_dollars"] = "0.9900"
        fake = FakeKalshi(overrides={})
        orig = fake.__call__

        def patched(url: str):
            if "/markets?" in url and "KXNBAREB" in url and "PROT" not in url:
                fake.urls.append(url)
                return 200, copy.deepcopy(moved)
            return orig(url)

        run3, w3, _ = self.run_full(fake=patched, state=st)  # type: ignore[arg-type]
        self.assertEqual(w3.sent("markets"), [moved["markets"][0]["ticker"]])
        self.assertEqual(run3.sent, 1)

    def test_no_market_for_the_window_fails_loud(self) -> None:
        fake = FakeKalshi(overrides={"/markets": {"markets": [], "cursor": ""}})
        with self.assertRaisesRegex(job.BoardFailed, "no open market returned"):
            self.run_full(fake=fake)

    def test_a_window_series_with_events_but_no_market_fails_the_run_after_the_rest_is_written(self) -> None:
        # The comma-list lesson (owner, Nov 6): an empty answer is a contract
        # failure, never an empty board. KXNBAREB lists window events here.
        fake = FakeKalshi()
        orig = fake.__call__

        def patched(url: str):
            q = dict(urllib.parse.parse_qsl(url.partition("?")[2]))
            if url.split("?")[0].endswith("/markets") and kb.series_of(
                    q.get("event_ticker") or q.get("series_ticker", "")) == "KXNBAREB":
                fake.urls.append(url)
                return 200, {"markets": [], "cursor": ""}
            return orig(url)

        w = RecordingWriter()
        with self.assertRaisesRegex(job.BoardFailed, r"KXNBAREB \(\d+ events\)"):
            job.full_job(client=client(patched), now=lambda: NOW, state=job.BoardState(), writer=w)  # type: ignore[arg-type]
        self.assertIn("KXNBAGAME-26NOV06BOSCHA-BOS", w.sent("markets"))  # the rest was written
        self.assertEqual([k for k, _ in w.calls][-3:], ["touch", "link", "prune"])
        dry = job.full_job(client=client(patched), now=lambda: NOW, state=job.BoardState(), dry_run=True)  # type: ignore[arg-type]
        self.assertEqual(set(dry.empty_series), {"KXNBAREB"})
        self.assertIn("EMPTY series KXNBAREB", dry.job_note)

    def test_an_excluded_series_is_never_read_and_is_counted(self) -> None:
        self.assertIn("KXNFLPASSYDSPRECISE", job.EXCLUDED_SERIES)  # by name, owner Nov 7
        self.assertNotIn("KXNFLPASSYDS", job.EXCLUDED_SERIES)
        fake = FakeKalshi()
        with mock.patch.object(job, "EXCLUDED_SERIES", frozenset({"KXNBAREB"})):
            run, w, st = self.run_full(fake=fake)
        qs = [dict(urllib.parse.parse_qsl(u.partition("?")[2])) for u in fake.urls if "/markets?" in u]
        self.assertFalse(any(kb.series_of(q.get("event_ticker") or q.get("series_ticker", "")) == "KXNBAREB"
                             for q in qs))
        self.assertIn("KXNBAREB", run.excluded)
        self.assertFalse(any(kb.series_of(t) == "KXNBAREB" for t in st.window))
        self.assertIn(f"excluded=1 (KXNBAREB {run.excluded['KXNBAREB']} events), overflowed=0", run.job_note)
        self.assertLess(run.job_note.index("excluded="), 200)  # inside job_health's 500 characters
        self.assertGreater(run.markets, 0)

    def test_a_series_over_the_page_cap_is_skipped_the_rest_written_then_the_run_fails(self) -> None:
        fake = FakeKalshi()

        def endless(url: str):
            q = dict(urllib.parse.parse_qsl(url.partition("?")[2]))
            if url.split("?")[0].endswith("/markets") and kb.series_of(
                    q.get("event_ticker") or q.get("series_ticker", "")) == "KXNBAREB":
                fake.urls.append(url)
                return 200, {"markets": [], "cursor": "more"}
            return fake(url)

        w, st = RecordingWriter(), job.BoardState()
        with self.assertRaisesRegex(job.BoardFailed,
                                    r"overflowed the page cap and were skipped: KXNBAREB \(\d+ events\)"):
            job.full_job(client=client(endless), now=lambda: NOW, state=st, writer=w)  # type: ignore[arg-type]
        self.assertIn("KXNBAGAME-26NOV06BOSCHA-BOS", w.sent("markets"))  # the rest was written
        self.assertEqual([k for k, _ in w.calls][-3:], ["touch", "link", "prune"])
        swept = {t for k, v in w.calls if k == "sweep" for t in v[0]}
        self.assertFalse(any(kb.series_of(t) == "KXNBAREB" for t in swept))  # never swept unread
        # The board was written, so the refresh has its window (minus the skipped series).
        self.assertTrue(st.window)
        self.assertFalse(any(kb.series_of(t) == "KXNBAREB" for t in st.window))
        clock = FakeClock()
        run = job.refresh_job(http=http_of(FakeKalshi(), clock), now=lambda: AT, state=st, writer=RecordingWriter(),
                              clock=clock, sleep=clock.sleep)
        self.assertIsNone(run.idle)
        self.assertGreater(run.markets, 0)

    def test_the_note_reads_excluded_and_overflowed(self) -> None:
        run = job.BoardRun(kind="full")
        self.assertIn("excluded=0, overflowed=0", run.job_note)
        run.overflowed["KXNFLX"] = 14
        self.assertIn("overflowed=1 (KXNFLX 14 events)", run.job_note)

    def test_a_series_not_yet_open_is_counted_not_failed(self) -> None:
        # Nov 7, 21:30 UTC: milestones listed KXATPMATCH before its markets
        # opened (open_time 21:35). Kalshi lists them as unopened: no failure.
        fake = FakeKalshi()
        asked_unopened: list[str] = []

        def waiting(url: str):
            q = dict(urllib.parse.parse_qsl(url.partition("?")[2]))
            if url.split("?")[0].endswith("/markets") and kb.series_of(
                    q.get("event_ticker") or q.get("series_ticker", "")) == "KXNBAREB":
                fake.urls.append(url)
                if q.get("status") == "unopened":
                    asked_unopened.append(q["event_ticker"])
                    return 200, {"markets": [{"ticker": q["event_ticker"] + "-X", "status": "initialized"}],
                                 "cursor": ""}
                return 200, {"markets": [], "cursor": ""}
            return fake(url)

        w, st = RecordingWriter(), job.BoardState()
        run = job.full_job(client=client(waiting), now=lambda: NOW, state=st, writer=w)  # type: ignore[arg-type]
        self.assertEqual(run.empty_series, {})
        self.assertEqual(run.not_yet_open, len(asked_unopened))
        self.assertGreater(run.not_yet_open, 0)
        self.assertTrue(all(kb.series_of(t) == "KXNBAREB" for t in asked_unopened))
        self.assertIn(f"empty_series=0, not_yet_open={run.not_yet_open}", run.job_note)
        self.assertFalse(any(kb.series_of(t) == "KXNBAREB" for t in st.window))  # nothing to refresh yet
        self.assertIn("KXNBAGAME-26NOV06BOSCHA-BOS", w.sent("markets"))

    def test_an_empty_series_with_nothing_unopened_still_fails(self) -> None:
        # The existing rule stands when Kalshi lists no unopened market either.
        fake = FakeKalshi()

        def gone(url: str):
            q = dict(urllib.parse.parse_qsl(url.partition("?")[2]))
            if url.split("?")[0].endswith("/markets") and kb.series_of(
                    q.get("event_ticker") or q.get("series_ticker", "")) == "KXNBAREB":
                fake.urls.append(url)
                return 200, {"markets": [], "cursor": ""}
            return fake(url)

        with self.assertRaisesRegex(job.BoardFailed, r"KXNBAREB \(\d+ events\)"):
            job.full_job(client=client(gone), now=lambda: NOW, state=job.BoardState(), writer=RecordingWriter())  # type: ignore[arg-type]
        self.assertTrue(any("status=unopened" in u for u in fake.urls))  # it did ask

    def test_a_changed_shape_fails_the_run(self) -> None:
        bad = load("collections")
        del bad["multivariate_contracts"][0]["collection_ticker"]
        with self.assertRaisesRegex(job.BoardFailed, "collection_ticker"):
            self.run_full(fake=FakeKalshi(overrides={"/multivariate_event_collections": bad}))

    def test_an_unmodelled_fee_fails_naming_the_series(self) -> None:
        raw = next(s for s in load("series")["series"] if s["ticker"] == "KXNBAREB")
        with self.assertRaisesRegex(job.BoardFailed, "KXNBAREB"):
            self.run_full(fake=FakeKalshi(overrides={"/series/KXNBAREB": {"series": dict(raw, fee_type="flat")}}))

    def test_series_are_read_one_by_one_once_a_chicago_day(self) -> None:
        fake = FakeKalshi()
        run1, _, st = self.run_full(fake=fake)
        paths = [u.split("/trade-api/v2", 1)[1].split("?")[0] for u in fake.urls]
        self.assertNotIn("/series", paths)  # never the 6.5 MB bulk list
        self.assertEqual(run1.series_fetched, run1.series)
        self.assertEqual(sum(p.startswith("/series/") for p in paths), run1.series)
        later = FakeKalshi()
        run2 = job.full_job(client=client(later), now=lambda: NOW + timedelta(hours=3), state=st,
                            writer=RecordingWriter())
        self.assertEqual(run2.series_fetched, 0)
        self.assertFalse(any("/series/" in u for u in later.urls))
        tomorrow = FakeKalshi()
        run3 = job.full_job(client=client(tomorrow), now=lambda: NOW + timedelta(days=1), state=st,
                            writer=RecordingWriter())
        self.assertEqual(run3.series_fetched, run3.series)

    def test_a_dry_run_writes_nothing_and_keeps_no_state(self) -> None:
        st = job.BoardState()
        run = job.full_job(client=client(FakeKalshi()), now=lambda: NOW, state=st, dry_run=True)
        self.assertGreater(run.markets, 30)
        self.assertEqual((st.markets, st.events, st.window, st.series_rows), ({}, {}, {}, {}))


def http_of(fake: Any, clock: "FakeClock | None" = None, per_call_s: float = 0.0) -> kb.Http:
    """FakeKalshi as raw HTTP (bytes), each call advancing the fake clock."""
    def http(url: str) -> tuple[int, bytes | None]:
        if clock is not None:
            clock.t += per_call_s
        status, body = fake(url)
        return status, (None if body is None else json.dumps(body).encode())
    return http


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


#: 15:30 Chicago: the recorded games start at 00:00 UTC Nov 7 (2.5 h out);
#: two others started at ~19:10 UTC Nov 6 (already under way).
AT = datetime(2026, 11, 6, 21, 30, tzinfo=UTC)


class Refresh(unittest.TestCase):
    """The rebuilt refresh (owner, Nov 7): per event, 3 h, not started,
    soonest first, <= 300 calls and <= 300 s, adaptive pacing."""

    def setUp(self) -> None:
        self.state = job.BoardState()
        job.full_job(client=client(FakeKalshi()), now=lambda: NOW, state=self.state, writer=RecordingWriter())

    def refresh(self, at: datetime = AT, fake: Any = None, writer: RecordingWriter | None = None,
                per_call_s: float = 0.0) -> tuple[job.BoardRun, RecordingWriter, FakeKalshi, FakeClock]:
        w = writer or RecordingWriter()
        f = fake if fake is not None else FakeKalshi()
        clock = FakeClock()
        run = job.refresh_job(http=http_of(f, clock, per_call_s), now=lambda: at, state=self.state, writer=w,
                              clock=clock, sleep=clock.sleep)
        return run, w, f, clock

    def market_queries(self, fake: FakeKalshi) -> list[dict[str, str]]:
        return [dict(urllib.parse.parse_qsl(u.partition("?")[2])) for u in fake.urls
                if u.split("?")[0].endswith("/markets")]

    def test_idle_outside_hours_and_before_a_full_pull(self) -> None:
        late = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)  # 00:00 Chicago
        self.assertIn("outside 08:00-23:00", self.refresh(late)[0].job_note)
        run = job.refresh_job(http=http_of(FakeKalshi()), now=lambda: NOW, state=job.BoardState())
        self.assertIn("no window yet", run.job_note)

    def test_always_per_event_within_3_h_never_started_soonest_first(self) -> None:
        # One window event already under way (started an hour ago).
        under_way = sorted(self.state.window)[0]
        self.state.window[under_way] = int((AT - timedelta(hours=1)).timestamp())
        run, w, fake, _ = self.refresh()
        qs = self.market_queries(fake)
        self.assertTrue(qs)
        self.assertTrue(all("event_ticker" in q and "series_ticker" not in q for q in qs), qs[:3])
        asked = [q["event_ticker"] for q in qs]
        self.assertEqual(len(asked), len(set(asked)))  # one call an event (each fits one page)
        lo, hi = AT.timestamp(), (AT + timedelta(hours=3)).timestamp()
        in_scope = {t for t, s in self.state.window.items() if lo <= s < hi}
        self.assertEqual(set(asked), in_scope)
        started = {t for t, s in self.state.window.items() if s < lo}
        self.assertTrue(started)  # the fixture's ~19:10 UTC games
        self.assertFalse(started & set(asked))
        self.assertEqual([self.state.window[t] for t in asked], sorted(self.state.window[t] for t in asked))
        self.assertEqual((run.scope, run.events, run.skipped), (len(in_scope), len(in_scope), 0))
        self.assertGreater(run.markets, 0)
        self.assertEqual((run.sent, w.sent("markets")), (0, []))  # nothing moved since the full pull
        self.assertEqual({t for k, v in w.calls if k == "sweep" for t in v[0]}, in_scope)
        self.assertIn(f"refresh: {run.events} of {run.scope} events within 3 h", run.job_note)
        nothing = self.refresh(datetime(2026, 11, 6, 14, 0, tzinfo=UTC))[0]  # 08:00 Chicago: games 10 h out
        self.assertIn("nothing starts within 3 h", nothing.job_note)

    def test_the_call_cap_stops_logs_the_skip_and_never_sweeps_an_unread_event(self) -> None:
        with mock.patch.object(job, "REFRESH_MAX_CALLS", 3):
            run, w, fake, _ = self.refresh()
        self.assertEqual(len(fake.urls), 3)
        self.assertEqual(run.calls, 3)
        self.assertEqual((run.events, run.cap), (3, "calls"))
        self.assertEqual(run.skipped, run.scope - 3)
        self.assertGreater(run.skipped, 0)
        asked = [q["event_ticker"] for q in self.market_queries(fake)]
        swept = [t for k, v in w.calls if k == "sweep" for t in v[0]]
        self.assertEqual(swept, asked)  # only the events read whole
        soonest_skipped = sorted((s for t, s in self.state.window.items()
                                  if AT.timestamp() <= s < (AT + timedelta(hours=3)).timestamp()
                                  and t not in asked))[0]
        self.assertEqual(run.skipped_from, datetime.fromtimestamp(soonest_skipped, UTC))
        self.assertIn(f"SKIPPED {run.skipped} (calls cap; from ", run.job_note)

    def test_the_time_cap_stops_before_300_s(self) -> None:
        # A call in flight may take its whole timeout: the last call starts
        # by MAX - MARGIN, so the run still ends inside MAX.
        with mock.patch.object(job, "REFRESH_MAX_S", 10), mock.patch.object(job, "REFRESH_MARGIN_S", 4):
            run, _, fake, clock = self.refresh(per_call_s=4.0)
        self.assertEqual(run.cap, "time")
        self.assertLessEqual(run.seconds, 10)
        self.assertGreaterEqual(job.REFRESH_MARGIN_S, job.REFRESH_CALL_TIMEOUT_S)
        self.assertLess(len(fake.urls), run.scope)
        self.assertIn("(time cap; from ", run.job_note)

    def test_the_real_caps(self) -> None:
        self.assertEqual((job.REFRESH_MAX_CALLS, job.REFRESH_MAX_S), (300, 300))
        self.assertEqual(kb.REFRESH_WITHIN, timedelta(hours=3))

    def test_429s_slow_the_pace_and_the_rate_is_in_the_note(self) -> None:
        fake = FakeKalshi()
        limited = {"n": 0}

        def flaky(url: str):
            if url.split("?")[0].endswith("/markets") and limited["n"] < 2:
                limited["n"] += 1
                fake.urls.append(url)
                return 429, None
            return fake(url)

        start = self.state.refresh_pace
        run, _, _, clock = self.refresh(fake=flaky)
        self.assertEqual(run.limited, 2)
        self.assertGreater(run.pace, start)  # doubled twice, then eased at most a little
        self.assertEqual(self.state.refresh_pace, run.pace)  # carried to the next run
        self.assertIn(f"2 429s ({100 * 2 / run.calls:.1f}%)", run.job_note)
        self.assertIn(f"pace {run.pace:.2f} s", run.job_note)
        self.assertTrue(clock.slept)  # paced: it waited between calls

    def test_stands_down_while_a_full_pull_runs(self) -> None:
        with job.PULL_LOCK:
            run, w, fake, _ = self.refresh()
        self.assertIn("full pull is running", run.job_note)
        self.assertEqual((w.calls, fake.urls), ([], []))

    def test_the_lock_is_released_after_a_failure(self) -> None:
        fake = FakeKalshi(overrides={"/markets": {"markets": [], "cursor": ""}})
        with self.assertRaisesRegex(job.BoardFailed, "no open market returned"):
            self.refresh(fake=fake)
        self.assertTrue(job.PULL_LOCK.acquire(blocking=False))
        job.PULL_LOCK.release()

    def test_an_event_over_the_page_cap_is_skipped_never_swept_and_the_rest_go_on(self) -> None:
        fake = FakeKalshi()
        asked: list[str] = []
        stuck: dict[str, str] = {}

        def endless(url: str):
            q = dict(urllib.parse.parse_qsl(url.partition("?")[2]))
            if url.split("?")[0].endswith("/markets") and "event_ticker" in q:
                asked.append(q["event_ticker"])
                stuck.setdefault("e", q["event_ticker"])  # the first event asked pages forever
                if q["event_ticker"] == stuck["e"]:
                    fake.urls.append(url)
                    return 200, {"markets": [], "cursor": "more"}
            return fake(url)

        with mock.patch.object(kb, "MAX_PAGES", 3):
            run, w, _, _ = self.refresh(fake=endless)
        self.assertEqual(run.overflowed, {kb.series_of(stuck["e"]): 1})
        self.assertEqual(run.events, run.scope - 1)
        self.assertNotIn(stuck["e"], {t for k, v in w.calls if k == "sweep" for t in v[0]})
        self.assertIn("overflowed=1", run.job_note)

    def test_a_moved_price_is_sent(self) -> None:
        fake = FakeKalshi()

        def moved(url: str):
            status, body = fake(url)
            if url.split("?")[0].endswith("/markets") and body and body["markets"]:
                body["markets"][0]["yes_ask_dollars"] = "0.9900"
            return status, body

        run, w, _, _ = self.refresh(fake=moved)
        self.assertEqual(run.sent, run.events)  # the first market of each event moved
        self.assertEqual(run.written, run.sent)


class Pacing(unittest.TestCase):
    """kb.Paced: AIMD pacing under a hard budget."""

    def paced(self, statuses: list[int], **kw: Any) -> tuple[kb.Paced, FakeClock]:
        clock = FakeClock()
        it = iter(statuses)

        def http(url: str):
            s = next(it)
            return s, (b"{}" if s == 200 else None)

        return kb.Paced(http=http, clock=clock, sleep=clock.sleep, **kw), clock

    def test_a_429_doubles_and_a_clean_run_eases_to_the_floor(self) -> None:
        p, _ = self.paced([429] + [200] * 400, interval=0.5)
        p("u")
        self.assertEqual(p.interval, 1.0)
        for _ in range(kb.PACE_EASE_AFTER):
            p("u")
        self.assertAlmostEqual(p.interval, 0.9)
        for _ in range(375):
            p("u")
        self.assertEqual(p.interval, kb.PACE_FLOOR)
        self.assertAlmostEqual(p.limited_pct, 100 / 401)

    def test_the_ceiling(self) -> None:
        p, _ = self.paced([429] * 10, interval=0.5)
        for _ in range(10):
            p("u")
        self.assertEqual(p.interval, kb.PACE_CEILING)

    def test_calls_are_spaced_by_the_interval(self) -> None:
        p, clock = self.paced([200] * 3, interval=0.5)
        for _ in range(3):
            p("u")
        self.assertEqual(clock.slept, [0.5, 0.5])

    def test_the_call_cap_and_the_deadline_raise_budget_spent(self) -> None:
        p, _ = self.paced([200] * 5, interval=0.3, max_calls=2)
        p("u"), p("u")
        with self.assertRaises(kb.BudgetSpent) as cm:
            p("u")
        self.assertEqual(cm.exception.reason, "calls")
        p, clock = self.paced([200] * 5, interval=0.3, deadline=1000.2)
        p("u")
        with self.assertRaises(kb.BudgetSpent) as cm:
            p("u")  # the 0.3 s wait would end past the deadline
        self.assertEqual(cm.exception.reason, "time")
        with self.assertRaises(kb.BudgetSpent):
            p.sleep(5.0)  # the client's own 429 backoff is held to it too

    def test_budget_spent_is_not_a_kalshi_error(self) -> None:
        # The client retries KalshiUnreachable; a spent budget must stop the run.
        from venues.kalshi.client import KalshiError
        self.assertFalse(issubclass(kb.BudgetSpent, KalshiError))
        p, _ = self.paced([429, 429, 429], interval=0.3, max_calls=2)
        c = KalshiClient(transport=p, min_interval=0, sleep=p.sleep)
        with self.assertRaises(kb.BudgetSpent):
            c.get("/markets")
        self.assertEqual(p.calls, 2)


class LadderAndEscalator(unittest.TestCase):
    """Nov 7: 467 parse misses on the 15:30 UTC pull, mostly these (rules text
    read from Kalshi the same day; shapes below are copied from it)."""

    LADDER = {"Payout Per Unit": "$0.05", "football_player": "x", "football_team": "y", "scalar_cap": "20",
              "scalar_floor": "0", "scalar_payout_type": "capped_cent", "statistic": "receptions"}
    FANTASY = {"football_player": "x", "football_team": "y", "scalar_cap": "100", "scalar_floor": "0",
               "scalar_payout_type": "capped_cent", "statistic": "fantasy points"}
    ESCALATOR = {"Payout formula": "floor(10000 x (min(s, 14) / 14)^3) / 10000", "exponent": "3",
                 "football_player": "x", "football_team": "y", "s": "official receptions", "scalar_cap": "14",
                 "scalar_floor": "0", "scalar_payout_type": "capped_centicent", "scalar_step": "1",
                 "statistic": "receptions"}
    NBA_ESCALATOR = {"exponent": "3", "basketball_player": "x", "basketball_team": "y", "scalar_cap": "50",
                     "scalar_floor": "0", "scalar_payout_type": "capped_centicent", "scalar_step": "2",
                     "statistic": "points"}

    def scalar(self, title: str, sub: str, strike: dict[str, Any], key: str = "football_player"):
        return parse("KXNBAREB", title=title, yes_sub_title=sub, custom_strike=strike, floor_strike=None,
                     strike_type="custom", primary_participant_key=key)

    def test_ladders_are_scalar(self) -> None:
        for title, strike, stat, cap in (("Emeka Egbuka Receptions Ladder", self.LADDER, "receptions", "20"),
                                         ("Emeka Egbuka fantasy scalar ladder", self.FANTASY, "fantasy points",
                                          "100")):
            with self.subTest(title):
                row, ex = self.scalar(title, "Emeka Egbuka", strike)
                self.assertEqual((row["market_kind"], row["payout"], row["variant"], row["subject"], row["stat"],
                                  row["line"]), ("prop", "scalar", "ladder", "Emeka Egbuka", stat, None))
                self.assertEqual(row["label"], f"Emeka Egbuka: {stat}, $1 at {cap}")
                self.assertFalse(ex["miss"])

    def test_escalators_every_sport(self) -> None:
        row, ex = self.scalar("Emeka Egbuka Receptions Escalator", "Emeka Egbuka", self.ESCALATOR)
        self.assertEqual((row["payout"], row["variant"], row["subject"], row["stat"], row["line"], row["label"]),
                         ("scalar", "escalator", "Emeka Egbuka", "receptions", None, "Emeka Egbuka: receptions, $1 at 14"))
        self.assertFalse(ex["miss"])
        row, ex = self.scalar("Victor Wembanyama player points escalator", "Victor Wembanyama",
                              self.NBA_ESCALATOR, key="basketball_player")
        self.assertEqual((row["market_kind"], row["payout"], row["variant"], row["stat"], row["line"]),
                         ("prop", "scalar", "escalator", "points", None))
        self.assertFalse(ex["miss"])

    def test_a_scalar_is_never_a_parse_miss_and_keeps_the_market(self) -> None:
        strike = {k: v for k, v in self.LADDER.items() if k != "statistic"}
        row, ex = self.scalar("Emeka Egbuka Receptions Ladder", "Emeka Egbuka", strike)
        self.assertFalse(ex["miss"])
        self.assertEqual((row["payout"], row["variant"], row["stat"], row["subject"]),
                         ("scalar", "ladder", None, "Emeka Egbuka"))
        self.assertIsNotNone(row["yes_ask"])

    def test_binary_markets_say_so(self) -> None:
        # Kalshi's own market_type reads "binary" on scalars too (Nov 7), so it
        # is never what decides; scalar_cap is.
        row, _ = parse("KXNBAREB")
        self.assertEqual(row["payout"], "binary")
        row, _ = self.scalar("X Ladder", "X", self.LADDER, )
        self.assertEqual(row["payout"], "scalar")
        row, _ = parse("KXNBAREB", market_type="binary", custom_strike=dict(self.LADDER))
        self.assertEqual(row["payout"], "scalar")  # market_type "binary" as Kalshi sends it on a ladder

    def test_a_bad_cap_is_a_shape_error(self) -> None:
        with self.assertRaisesRegex(KalshiShapeError, "scalar_cap"):
            self.scalar("X Ladder", "X", dict(self.LADDER, scalar_cap="lots"))

    def test_the_payout_and_variant_values_match_db_072(self) -> None:
        sql = (pathlib.Path(__file__).parents[2] / "db" / "072_kalshi_board_payout.sql").read_text("utf-8")
        self.assertIn("CHECK (payout IN ('binary', 'scalar'))", sql)
        self.assertIn("variant IN ('protected', 'ladder', 'escalator')", sql)


class TitleFixes(unittest.TestCase):
    def test_a_player_named_seth_is_not_a_set(self) -> None:
        row, ex = parse("KXNBAREB", title="Seth McGowan: 10+ rushing yards", yes_sub_title="Seth McGowan: 10+",
                        floor_strike=9.5)
        self.assertEqual((row["subject"], row["stat"], row["line"]), ("Seth McGowan", "rushing yards", "9.5"))
        self.assertFalse(ex["miss"])
        # A real set still strips.
        p = kb.parse_subject("game_line", "Set 1: Over 9.5 games", None, "greater", Decimal("9.5"))
        self.assertEqual(p.stat, "total")

    def test_a_tennis_games_spread_from_the_sub_title(self) -> None:
        p = kb.parse_subject("game_line", "Will Ben Shelton win at least 3.5 more games than Luca Van Assche?",
                             "Ben Shelton -3.5 games", "greater", Decimal("3.5"))
        self.assertEqual((p.subject, p.stat, p.miss), ("Ben Shelton", "spread", False))


class Notes(unittest.TestCase):
    def test_the_full_note_reads_empty_series_and_the_top_10_misses(self) -> None:
        run = job.BoardRun(kind="full", markets=5, calls=100, limited=3)
        run.misses.update({f"KXS{i:02d}": 20 - i for i in range(12)})
        note = run.job_note
        self.assertIn("empty_series=0", note)
        self.assertLess(note.index("empty_series=0"), 120)  # inside job_health's 500 characters
        self.assertIn("KXS09 11", note)
        self.assertNotIn("KXS10", note)
        self.assertIn("3 429s (3.0%)", note)
        run.empty_series["KXNBAREB"] = 4
        self.assertIn("empty_series=1", run.job_note)


class Leagues(unittest.TestCase):
    def test_league_by_label_then_prefix_else_none(self) -> None:
        self.assertEqual(job.league_of("KXNBAREB", ["NBA"]), "nba")
        self.assertEqual(job.league_of("KXNCAAF1H", [None]), "cfb")
        self.assertEqual(job.league_of("KXNCAAMBTOTAL", []), "mcbb")
        self.assertIsNone(job.league_of("KXLALIGAGAME", ["La Liga"]))
        self.assertIsNone(job.league_of("KXBULLETBRAWLTOP", []))

    def test_sport_from_the_tag_then_the_milestone(self) -> None:
        self.assertEqual(kb.sport_of(("Soccer",), None), "Soccer")
        self.assertEqual(kb.sport_of(("Olympics", "Hockey"), None), "Hockey")
        self.assertEqual(kb.sport_of((), "basketball_game"), "Basketball")
        self.assertEqual(kb.sport_of((), None), "Other")


class Registration(unittest.TestCase):
    def test_both_jobs_are_scheduled(self) -> None:
        from core import system_jobs
        jobs = {j.name: j for j in system_jobs.build_system_jobs({})}
        full, refresh = jobs[job.FULL_JOB], jobs[job.REFRESH_JOB]
        self.assertEqual(full.expected_interval_s, 18 * 3600)
        self.assertTrue(full.run_at_boot)
        self.assertEqual(refresh.expected_interval_s, 15 * 60)
        fires = []
        t = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)
        for _ in range(3):
            t = full.trigger.get_next_fire_time(None, t + timedelta(seconds=1))
            fires.append(t.astimezone(job.OWNER_TZ).strftime("%H:%M"))
        self.assertEqual(fires, ["09:30", "15:30", "09:30"])


class NoOverlap(SystemTestCase):
    """max_instances=1 and coalesce reach both board jobs (the scheduler's
    job_defaults, core/orchestrator.py), with the stubbed ledger."""

    def test_neither_job_can_overlap_itself(self) -> None:
        from core import system_jobs
        from core.orchestrator import Orchestrator
        orch = Orchestrator()
        for j in system_jobs.build_system_jobs({}):
            orch.register_system_job(j)
        orch._scheduler.start(paused=True)
        self.addCleanup(orch._scheduler.shutdown, False)
        orch._wire_system()
        for name in (job.FULL_JOB, job.REFRESH_JOB):
            with self.subTest(name):
                sj = orch._scheduler.get_job(f"system:{name}")
                self.assertEqual((sj.max_instances, sj.coalesce), (1, True))


if __name__ == "__main__":
    unittest.main()
