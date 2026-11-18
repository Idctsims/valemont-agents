"""The M1 fill stages (scripts/research/kalshi_fills.py): cells, the sample,
trades and candles with their resume and the wall. No network: every fetch is
a fake."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from scripts.research import kalshi_fills as kf
from scripts.research.kalshi_history import Pacer


def combo(ticker: str, events: list[str], close: str = "2026-01-18T20:00:00Z") -> dict:
    return {"ticker": ticker, "close_time": close, "open_time": "2026-01-17T00:00:00Z", "volume_fp": "5.00",
            "mve_selected_legs": [{"event_ticker": e, "market_ticker": f"{e}-M{i}", "side": "yes"}
                                  for i, e in enumerate(events)]}


def pacer() -> Pacer:
    return Pacer(sleep=lambda s: None)


class Cells(unittest.TestCase):
    def test_one_game_whose_series_carry_dates_a_day_apart_is_same_game(self) -> None:
        legs = combo("C", ["KXNFLGAME-26JAN18SFSEA", "KXNFLPASSYDS-26JAN17SFSEA", "KXNFLRSHYDS-26JAN17SFSEA"])
        self.assertEqual(kf.cell_of(legs), ("same-game", "2-5"))

    def test_two_games_and_the_same_teams_weeks_apart_are_cross_game(self) -> None:
        self.assertEqual(kf.cell_of(combo("C", ["KXNBAGAME-26JAN15LALBOS", "KXNBATOTAL-26JAN15NYKMIA"])),
                         ("cross-game", "2-5"))
        self.assertEqual(kf.cell_of(combo("C", ["KXNFLGAME-25OCT05SFSEA", "KXNFLGAME-26JAN18SFSEA"])),
                         ("cross-game", "2-5"))

    def test_leg_bands_and_a_tournament_combo_has_no_cell(self) -> None:
        game = "KXNBAPTS-26JAN15LALBOS"
        self.assertEqual([kf.cell_of(combo("C", [game] * n))[1] for n in (2, 5, 6, 10, 11, 20)],
                         ["2-5", "2-5", "6-10", "6-10", "11+", "11+"])
        self.assertIsNone(kf.cell_of(combo("C", ["KXMARMAD-26", "KXMARMADROUND-26T2"])))
        self.assertIsNone(kf.cell_of(combo("C", [game, "KXOSCARPIC-26"])))
        self.assertIsNone(kf.cell_of(combo("C", [game])))  # one leg is not a combo


class Sample(unittest.TestCase):
    def listing(self, d: Path, rows: list[dict], name: str = "KXMVETEST") -> None:
        with (d / f"{name}.jsonl").open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_per_cell_cap_by_ticker_hash_and_a_rerun_or_reorder_picks_the_same(self) -> None:
        same = [combo(f"S{i}", ["KXNBAGAME-26JAN15LALBOS", "KXNBATOTAL-26JAN15LALBOS"]) for i in range(40)]
        cross = [combo(f"X{i}", ["KXNBAGAME-26JAN15LALBOS", "KXNBAGAME-26JAN15NYKMIA"]) for i in range(3)]
        odd = [combo("T1", ["KXMARMAD-26", "KXMARMADROUND-26T2"])]
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.listing(Path(a), same + cross + odd + same[:5])  # five repeated rows (a re-read page)
            self.listing(Path(b), list(reversed(cross + same)) + odd)
            sa, ca = kf.build_sample(Path(a), per_cell=10)
            sb, _ = kf.build_sample(Path(b), per_cell=10)
        self.assertEqual([r["ticker"] for r in sa], [r["ticker"] for r in sb])
        self.assertEqual(sum(r["kind"] == "same-game" for r in sa), 10)
        self.assertEqual(sorted(r["ticker"] for r in sa if r["kind"] == "cross-game"), ["X0", "X1", "X2"])
        self.assertEqual((ca["same-game 2-5 listed"], ca["same-game 2-5 sampled"], ca["cross-game 2-5 sampled"],
                          ca["not a game combo"]), (40, 10, 3, 1))
        self.assertEqual(sa[0]["legs"][0], {"event": "KXNBAGAME-26JAN15LALBOS", "market": "KXNBAGAME-26JAN15LALBOS-M0",
                                            "side": "yes"})
        self.assertNotIn("result", sa[0])  # the sample carries no outcome


class Trades(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        rows = [{"ticker": t, "kind": "same-game", "legs_band": "2-5", "legs": [{"market": "LEG-A"}, {"market": "LEG-B"}]}
                for t in ("C1", "C2", "C3")]
        kf._append(self.out / "sample.jsonl", rows)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def trade(i: int, ticker: str, when: str) -> dict:
        return {"trade_id": f"t{i}", "ticker": ticker, "count_fp": "1.00", "yes_price_dollars": "0.1200",
                "no_price_dollars": "0.8800", "taker_outcome_side": "yes", "taker_book_side": "ask",
                "created_time": when, "is_block_trade": False, "taker_side": "yes"}

    def test_pages_the_wall_a_404_and_the_resume(self) -> None:
        calls: list[str] = []

        def fetch(url: str) -> tuple[int, str]:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            calls.append(q["ticker"][0] + ("+" if "cursor" in q else ""))
            if q["ticker"][0] == "C1":
                if "cursor" not in q:
                    return 200, json.dumps({"trades": [self.trade(1, "C1", "2026-01-18T19:00:30Z")], "cursor": "p2"})
                return 200, json.dumps({"trades": [self.trade(2, "C1", "2026-01-18T19:05:10.5Z"),
                                                   self.trade(3, "C1", "2026-07-01T00:00:00Z")], "cursor": ""})
            if q["ticker"][0] == "C2":
                return 404, ""
            return 200, json.dumps({"trades": [], "cursor": ""})

        counts = kf.crawl_trades(self.out, pacer(), fetch, log=lambda s: None)
        self.assertEqual(calls, ["C1", "C1+", "C2", "C3"])
        self.assertEqual((counts["combos"], counts["trades"], counts["past the wall, dropped"], counts["missed"],
                          counts["combos with no trade"], counts["left"]), (3, 2, 1, 1, 2, 0))
        rows = list(kf._read(self.out / "trades.jsonl"))
        self.assertEqual([r["trade_id"] for r in rows], ["t1", "t2"])
        self.assertEqual(set(rows[0]), set(kf.TRADE_FIELDS))  # the deprecated field is left behind
        self.assertEqual([r["ticker"] for r in kf._read(self.out / "trades.miss.jsonl")], ["C2"])
        calls.clear()
        again = kf.crawl_trades(self.out, pacer(), fetch, log=lambda s: None)
        self.assertEqual((calls, again["combos"]), ([], 0))  # nothing is asked twice

    def test_a_deadline_stops_between_combos_and_the_rest_is_left(self) -> None:
        counts = kf.crawl_trades(self.out, pacer(), lambda url: (200, '{"trades": [], "cursor": ""}'), deadline=0.0,
                                 log=lambda s: None)
        self.assertEqual((counts["combos"], counts["left"]), (0, 3))


class Candles(unittest.TestCase):
    def test_windows_join_near_fills_split_far_ones_and_never_pass_5000_minutes(self) -> None:
        self.assertEqual(kf.windows([100, 100, 130]), [(95, 131)])
        self.assertEqual(kf.windows([100, 100 + kf.JOIN_GAP_MIN + kf.BEFORE_MIN + kf.AFTER_MIN + 1]),
                         [(95, 101), (100 + 67 - 5, 100 + 67 + 1)])
        long = kf.windows(range(0, 12000, 30))
        self.assertTrue(all(hi - lo < kf.MAX_SPAN_MIN for lo, hi in long))
        self.assertGreater(len(long), 2)
        self.assertEqual(kf.windows([]), [])

    def test_each_leg_once_only_minutes_near_a_fill_kept_and_no_window_past_the_wall(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            kf._append(out / "sample.jsonl", [
                {"ticker": "C1", "legs": [{"market": "LEG-A"}, {"market": "LEG-B"}]},
                {"ticker": "C2", "legs": [{"market": "LEG-A"}, {"market": "LEG-GONE"}]}])
            t0 = kf.ts_of("2026-01-18T19:00:30Z")
            kf._append(out / "trades.jsonl", [
                {"trade_id": "t1", "ticker": "C1", "created_time": "2026-01-18T19:00:30Z"},
                {"trade_id": "t1", "ticker": "C1", "created_time": "2026-01-18T19:00:30Z"},  # a re-read page
                {"trade_id": "t2", "ticker": "C2", "created_time": "2026-01-18T19:20:00Z"}])
            need = kf.leg_minutes(out)
            self.assertEqual({k: len(v) for k, v in need.items()}, {"LEG-A": 2, "LEG-B": 1, "LEG-GONE": 1})
            calls: list[tuple[str, int, int]] = []

            def fetch(url: str) -> tuple[int, str]:
                u = urllib.parse.urlparse(url)
                q = urllib.parse.parse_qs(u.query)
                market = u.path.split("/")[-2]
                lo, hi = int(q["start_ts"][0]), int(q["end_ts"][0])
                calls.append((market, lo, hi))
                self.assertEqual(q["period_interval"], ["1"])
                self.assertLess(hi, kf.WALL_TS)
                if market == "LEG-GONE":
                    return 404, ""
                ends = range(lo - lo % 60 + 60, hi + 1, 60)
                return 200, json.dumps({"ticker": market, "candlesticks": [
                    {"end_period_ts": e, "yes_bid": {"open": "0.40", "close": "0.41"},
                     "yes_ask": {"open": "0.43", "close": "0.44"}, "price": {"close": None}, "volume": "0.00"} for e in ends]})

            counts = kf.crawl_candles(out, pacer(), fetch, log=lambda s: None)
            self.assertEqual([c[0] for c in calls], ["LEG-A", "LEG-B", "LEG-GONE"])  # LEG-A's two fills share one call
            rows = list(kf._read(out / "candles.jsonl"))
            a = sorted(r["end"] for r in rows if r["market"] == "LEG-A")
            fill_min = t0 // 60
            # Around 19:00 and around 19:20: seven minutes each; the quiet minutes between are dropped.
            self.assertEqual(len(a), 2 * (kf.BEFORE_MIN + kf.AFTER_MIN + 1))
            self.assertIn((fill_min + 1) * 60, a)        # the candle that ends at 19:01:00 is the fill's minute
            self.assertNotIn((fill_min + 10) * 60, a)
            self.assertEqual(rows[0]["bid_close"], "0.41")
            self.assertEqual((counts["legs"], counts["missed"], counts["calls"], counts["left"]), (3, 1, 2, 0))
            calls.clear()
            self.assertEqual(kf.crawl_candles(out, pacer(), fetch, log=lambda s: None)["legs"], 0)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
