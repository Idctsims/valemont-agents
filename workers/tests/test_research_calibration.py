"""The M2 crawler (scripts/research/kalshi_calibration.py): the reserve gate on the
listing, the 70/30 date split, the candle window, and no network without --live."""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.research import kalshi_calibration as cal
from scripts.research.kalshi_history import Counts

#: A reserve-dated listing line whose other fields are NOT valid JSON: if
#: anything decoded it before the close_time check, the test would raise.
POISON = ('{"ticker": "KXNBAPTS-26JUL02NYKSAS-X-10", "result": POISON_NOT_JSON, "volume_fp": NOT_A_NUMBER, '
          '"close_time": "2026-07-02T03:00:00Z", "event_ticker": "KXNBAPTS-26JUL02NYKSAS"}')


def line(ticker, event, close="2026-03-01T03:00:00Z", volume="10.00", **kw):
    return json.dumps({"ticker": ticker, "event_ticker": event, "close_time": close, "volume_fp": volume,
                       "result": "yes", "last_price_dollars": "0.42", "settlement_value_dollars": "1.0000"} | kw)


class Gate(unittest.TestCase):
    def test_a_reserve_line_is_dropped_before_any_other_field_is_decoded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "KXNBAPTS.jsonl"
            p.write_text("\n".join([line("KXNBAPTS-26MAR01NYKSAS-A-10", "KXNBAPTS-26MAR01NYKSAS"), POISON,
                                    line("KXNBAPTS-26MAR02NYKSAS-B-10", "KXNBAPTS-26MAR02NYKSAS")]) + "\n",
                         encoding="utf-8")
            counts = Counts()
            rows = list(cal.gated_rows([p], counts))
            self.assertEqual([r["ticker"] for _, r in rows], ["KXNBAPTS-26MAR01NYKSAS-A-10", "KXNBAPTS-26MAR02NYKSAS-B-10"])
            self.assertEqual((counts.seen, counts.kept, counts.reserve), (3, 0, 1))

    def test_the_boundary_day_and_undated_lines_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "KXNBAGAME.jsonl"
            p.write_text("\n".join([line("A", "KXNBAGAME-26JUN30NYKSAS", close="2026-07-01T00:00:00Z"),
                                    '{"ticker": "B", "result": NOPE}']) + "\n", encoding="utf-8")
            counts = Counts()
            self.assertEqual(list(cal.gated_rows([p], counts)), [])
            self.assertEqual((counts.reserve, counts.undated), (1, 1))


class Sample(unittest.TestCase):
    def build(self, lines_by_series, per_cell=100):
        with tempfile.TemporaryDirectory() as d:
            for s, lines in lines_by_series.items():
                (Path(d) / f"{s}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
            return cal.build_sample(Path(d), per_cell)

    def test_no_outcome_or_price_field_reaches_the_sample_and_untraded_is_out(self):
        sample, info = self.build({"KXNBAPTS": [line(f"KXNBAPTS-26MAR{i:02d}NYKSAS-P-10", f"KXNBAPTS-26MAR{i:02d}NYKSAS")
                                                for i in range(1, 11)]
                                   + [line("KXNBAPTS-26MAR11NYKSAS-Q-10", "KXNBAPTS-26MAR11NYKSAS", volume="0.00"), POISON],
                                   "KXNBA1HWINNER": [line("KXNBA1HWINNER-26MAR01NYKSAS-NYK", "KXNBA1HWINNER-26MAR01NYKSAS")]})
        self.assertEqual(len(sample), 10)
        for r in sample:
            self.assertEqual(set(r), set(cal.SAMPLE_FIELDS))
            self.assertFalse({"result", "last_price_dollars", "settlement_value_dollars", "volume_fp"} & set(r))
        self.assertEqual(info["skipped"]["untraded"], 1)
        self.assertEqual(info["gate"]["reserve"], 1)

    def test_seventy_thirty_by_date_and_the_cap_is_by_ticker_hash(self):
        lines = [line(f"KXNBAGAME-26MAR{i:02d}NYKSAS-NYK", f"KXNBAGAME-26MAR{i:02d}NYKSAS") for i in range(1, 11)]
        sample, info = self.build({"KXNBAGAME": lines})
        self.assertEqual(info["first_test_date"]["nba"], "2026-03-08")
        self.assertEqual(sum(r["split"] == "fit" for r in sample), 7)
        capped, _ = self.build({"KXNBAGAME": lines}, per_cell=4)
        again, _ = self.build({"KXNBAGAME": list(reversed(lines))}, per_cell=4)
        self.assertEqual([r["ticker"] for r in capped], [r["ticker"] for r in again])
        self.assertEqual(len(capped), 4)


class Candles(unittest.TestCase):
    def test_the_window_never_reaches_the_wall(self):
        start = int(dt.datetime(2026, 6, 13, 0, 30, tzinfo=dt.timezone.utc).timestamp())
        q = cal.candle_query(start)
        self.assertEqual((q["end_ts"] - q["start_ts"], q["end_ts"]), (3600, start))
        self.assertLess(cal.candle_query(cal.WALL_TS - 10)["end_ts"], cal.WALL_TS)
        self.assertIsNone(cal.candle_query(cal.WALL_TS))

    def test_the_last_candle_at_or_before_the_start(self):
        cs = [{"end_period_ts": t, "yes_bid": {"close": b}, "yes_ask": {"close": b + 2}} for t, b in
              ((100, 40), (160, 41), (220, 43))]
        self.assertEqual(cal.last_before(cs, 200), {"end": 160, "bid_close": 41, "ask_close": 43})
        self.assertIsNone(cal.last_before(cs, 50))

    def test_start_match_takes_either_order_within_a_day_and_the_nearer_date(self):
        starts = {"nba": [{"league": "nba", "start": "2026-03-02T00:30:00+00:00", "eastern_date": "2026-03-01",
                           "away": "NYK", "home": "SAS"},
                          {"league": "nba", "start": "2026-03-03T00:30:00+00:00", "eastern_date": "2026-03-02",
                           "away": "SAS", "home": "NYK"}]}
        first = int(dt.datetime(2026, 3, 2, 0, 30, tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(cal.match_start("nba", "NYKSAS", dt.date(2026, 3, 1), starts), first)
        self.assertEqual(cal.match_start("nba", "SASNYK", dt.date(2026, 3, 1), starts), first)
        self.assertEqual(cal.match_start("nba", "NYKSAS", dt.date(2026, 3, 2), starts), first + 86400)
        self.assertIsNone(cal.match_start("nba", "NYKSAS", dt.date(2026, 3, 5), starts))
        self.assertIsNone(cal.match_start("nba", "NYKBOS", dt.date(2026, 3, 1), starts))


class EspnStarts(unittest.TestCase):
    def ev(self, away, home, when, league="nba"):
        from sports.espn import SportsEvent
        return SportsEvent(league=league, kind="game", espn_id=f"{away}{home}", event_name="x", starts_at=when,
                           status="final", espn_status="STATUS_FINAL",
                           participants={"away": {"abbr": away}, "home": {"abbr": home}})

    def test_codes_go_through_the_aliases_and_the_date_is_eastern(self):
        late = dt.datetime(2026, 3, 2, 3, 0, tzinfo=dt.timezone.utc)  # 10 pm Eastern on Mar 1
        rows = cal.start_rows([self.ev("GS", "NY", late), self.ev("BOS", None, late)], {("nba", "GS"): "GSW", ("nba", "NY"): "NYK"})
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["away"], rows[0]["home"], rows[0]["eastern_date"]), ("GSW", "NYK", "2026-03-01"))

    def test_fetch_starts_one_call_per_date_and_a_failed_date_is_recorded(self):
        from sports.espn import EspnFetchError
        calls = []

        class FakeEspn:
            def events(self, lg, days):
                calls.append((lg.code, days[0]))
                if days[0] == dt.date(2026, 3, 2):
                    raise EspnFetchError("nba", "HTTP 503")
                return [EspnStarts.ev(self_, "BOS", "TOR", dt.datetime(2026, 3, 2, 0, 0, tzinfo=dt.timezone.utc))]

        self_ = self
        rows = cal.fetch_starts(FakeEspn(), {"nba": [dt.date(2026, 3, 1), dt.date(2026, 3, 2), dt.date(2026, 3, 1)]}, {},
                                log=lambda s: None)
        self.assertEqual(calls, [("nba", dt.date(2026, 3, 1)), ("nba", dt.date(2026, 3, 2))])
        self.assertEqual(sum("error" in r for r in rows), 1)

    def test_dry_run_dates_refuse_the_reserve_and_unknown_leagues(self):
        self.assertEqual(cal.parse_dates("nba:2025-11-20,2026-02-10;nhl:2025-12-01"),
                         {"nba": {dt.date(2025, 11, 20), dt.date(2026, 2, 10)}, "nhl": {dt.date(2025, 12, 1)}})
        with self.assertRaises(ValueError):
            cal.parse_dates("nba:2026-07-01")
        with self.assertRaises(ValueError):
            cal.parse_dates("nfl:2025-11-20")


class NoNetworkWithoutLive(unittest.TestCase):
    def test_candles_and_starts_make_no_call_without_live(self):
        with tempfile.TemporaryDirectory() as d,                 mock.patch.object(cal, "DATA_DIR", Path(d)),                 mock.patch.object(cal, "http_fetch", side_effect=AssertionError("network")),                 mock.patch.object(cal, "httpx_get", side_effect=AssertionError("network")),                 mock.patch("builtins.print"):
            self.assertEqual(cal.main(["candles"]), 0)
            self.assertEqual(cal.main(["starts"]), 0)


if __name__ == "__main__":
    unittest.main()
