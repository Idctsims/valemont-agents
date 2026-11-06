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
from decimal import Decimal
from typing import Any

from sports import kalshi_board as job
from venues.kalshi import board as kb
from venues.kalshi.client import KalshiClient
from venues.kalshi.events import KalshiShapeError
from venues.kalshi.fees import FeeRegime, UnsupportedFeeType

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
            return 200, load("milestones")
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


class Refresh(unittest.TestCase):
    def setUp(self) -> None:
        self.state = job.BoardState()
        job.full_job(client=client(FakeKalshi()), now=lambda: NOW, state=self.state, writer=RecordingWriter())

    def refresh(self, at: datetime, writer: RecordingWriter | None = None) -> tuple[job.BoardRun, RecordingWriter]:
        w = writer or RecordingWriter()
        return job.refresh_job(client=client(FakeKalshi()), now=lambda: at, state=self.state, writer=w), w

    def test_idle_outside_hours_and_before_a_full_pull(self) -> None:
        late = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)  # 00:00 Chicago
        self.assertIn("outside 08:00-23:00", self.refresh(late)[0].job_note)
        run = job.refresh_job(client=client(FakeKalshi()), now=lambda: NOW, state=job.BoardState())
        self.assertIn("no window yet", run.job_note)

    def test_re_prices_only_what_starts_within_6_h_and_sends_only_changes(self) -> None:
        at = datetime(2026, 11, 6, 20, 0, tzinfo=UTC)  # 14:00 Chicago; games at 00:00 UTC
        run, w = self.refresh(at)
        self.assertGreater(run.markets, 0)
        self.assertEqual(run.sent, 0)
        self.assertEqual(w.sent("markets"), [])
        swept = {t for k, v in w.calls if k == "sweep" for t in v[0]}
        self.assertTrue(swept)
        starts = {t: self.state.window[t] for t in swept}
        self.assertTrue(all(at.timestamp() <= s < (at + kb.REFRESH_WITHIN).timestamp() for s in starts.values()))
        nothing = self.refresh(datetime(2026, 11, 6, 14, 0, tzinfo=UTC))[0]  # 08:00 Chicago: games 10 h out
        self.assertIn("nothing starts within 6 h", nothing.job_note)

    def test_stands_down_while_a_full_pull_runs(self) -> None:
        at = datetime(2026, 11, 6, 20, 0, tzinfo=UTC)
        with job.PULL_LOCK:
            run, w = self.refresh(at)
        self.assertIn("full pull is running", run.job_note)
        self.assertEqual(w.calls, [])

    def test_the_lock_is_released_after_a_failure(self) -> None:
        at = datetime(2026, 11, 6, 20, 0, tzinfo=UTC)
        fake = FakeKalshi(overrides={"/markets": {"markets": [], "cursor": ""}})
        with self.assertRaises(job.BoardFailed):
            job.refresh_job(client=client(fake), now=lambda: at, state=self.state, writer=RecordingWriter())
        self.assertTrue(job.PULL_LOCK.acquire(blocking=False))
        job.PULL_LOCK.release()


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


if __name__ == "__main__":
    unittest.main()
