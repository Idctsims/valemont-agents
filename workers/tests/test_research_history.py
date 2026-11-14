"""The Step 1 crawler (scripts/research/kalshi_history.py): the reserve wall,
the resume, the pacing. No network: every fetch is a fake."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.research import kalshi_history as kh


def row(ticker: str, close: str, **more: object) -> str:
    return json.dumps({"ticker": ticker, "volume_fp": "10.00", "result": "yes"} | more | {"close_time": close})


#: A reserve-dated row whose other fields are NOT valid JSON. Anything that
#: decodes it (json.loads) raises; only a reader that stops at close_time passes.
POISON = ('{"ticker": "KXMVE-RESERVE", "result": POISON_NOT_JSON, "legs": [{"close_time": "2025-01-01T00:00:00Z"}], '
          '"note": "a } brace and a \\" quote in a string", "close_time": "2026-07-01T00:00:00Z", "price": 0.5.5}')


def page(rows: list[str], cursor: str = "") -> str:
    return '{"markets": [' + ", ".join(rows) + '], "cursor": ' + json.dumps(cursor) + "}"


class TheWall(unittest.TestCase):
    def test_a_reserve_row_is_dropped_before_any_other_field_is_decoded(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            json.loads(POISON)  # the fixture really is undecodable
        seen: list[str] = []

        def parse(r: dict) -> dict:
            seen.append(r["ticker"])
            return r

        counts = kh.Counts()
        rows, cursor = kh.read_page(page([row("A", "2026-06-30T23:59:59Z"), POISON, row("B", "2025-11-18T04:44:21Z")],
                                         "next"), counts, traded_only=False, parse=parse)
        self.assertEqual([r["ticker"] for r in rows], ["A", "B"])
        self.assertEqual(seen, ["A", "B"])  # the parser never saw the reserve row
        self.assertEqual((counts.seen, counts.kept, counts.reserve, counts.undated), (3, 2, 1, 0))
        self.assertEqual(cursor, "next")

    def test_the_boundary_and_a_later_row_whatever_its_other_fields_say(self) -> None:
        counts = kh.Counts()
        self.assertIsNotNone(kh.admit(row("A", "2026-06-30T23:59:59.999Z"), counts))
        for close in ("2026-07-01T00:00:00Z", "2026-09-12T23:56:52Z", "2027-01-01T00:00:00Z"):
            # A nested close_time before the wall must not admit it.
            self.assertIsNone(kh.admit(row("R", close, mve_selected_legs=[{"close_time": "2025-01-01T00:00:00Z"}]),
                                       counts), close)
        self.assertEqual((counts.kept, counts.reserve), (0, 3))

    def test_an_undated_row_is_dropped_too(self) -> None:
        counts = kh.Counts()
        for raw in ('{"ticker": "X", "result": "yes"}', '{"ticker": "X", "close_time": null}',
                    '{"ticker": "X", "close_time": ""}', '{"ticker": "X", "close_time": 20260101}'):
            self.assertIsNone(kh.admit(raw, counts), raw)
        self.assertEqual((counts.undated, counts.reserve, counts.seen), (4, 0, 4))

    def test_a_reserve_row_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            state = kh.crawl_series("KXMVETEST", out, kh.Pacer(sleep=lambda s: None),
                                    lambda url: (200, page([POISON, POISON])), traded_only=True,
                                    log=lambda m: None)
            self.assertFalse((out / "KXMVETEST.jsonl").exists())
            self.assertEqual((state.counts.reserve, state.counts.kept, state.done), (2, 0, True))
            self.assertEqual(json.loads((out / "KXMVETEST.state.json").read_text())["counts"]["reserve"], 2)


class Scanner(unittest.TestCase):
    def test_top_level_returns_raw_values_and_survives_awkward_strings(self) -> None:
        text = ' {"a": "x\\"}{[", "b": {"a": 1, "c": [1, {"d": "]"}]}, "c": [1, 2], "d": -1.5e3, "e": null, "f": true} '
        got = kh.top_level(text)
        self.assertEqual(list(got), ["a", "b", "c", "d", "e", "f"])
        self.assertEqual({k: json.loads(v) for k, v in got.items()}, json.loads(text))
        self.assertEqual([json.loads(x) for x in kh.elements(' [ {"a": "]"} , 2, "x,y", [3, [4]] ] ')],
                         [{"a": "]"}, 2, "x,y", [3, [4]]])
        self.assertEqual(list(kh.elements("[]")), [])


class Crawl(unittest.TestCase):
    def test_pages_append_untraded_combos_are_counted_and_a_stop_resumes_at_the_cursor(self) -> None:
        pages = {"": page([row("A", "2026-06-01T00:00:00Z"), row("B", "2026-06-01T00:00:00Z", volume_fp="0.00")], "c1"),
                 "c1": page([row("C", "2026-05-01T00:00:00Z", rules_primary="long text")], "c2"),
                 "c2": page([row("D", "2026-04-01T00:00:00Z")], "")}
        asked: list[str] = []

        def fetch(url: str) -> tuple[int, str]:
            cur = url.split("cursor=")[1] if "cursor=" in url else ""
            asked.append(cur)
            return 200, pages[cur]

        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            pacer = kh.Pacer(sleep=lambda s: None)
            s = kh.crawl_series("S", out, pacer, fetch, traded_only=True, max_pages=1, log=lambda m: None)
            self.assertEqual((s.pages, s.cursor, s.done, s.counts.untraded), (1, "c1", False, 1))
            s = kh.crawl_series("S", out, pacer, fetch, traded_only=True, log=lambda m: None)  # the resume
            self.assertEqual(asked, ["", "c1", "c2"])
            self.assertEqual((s.pages, s.done, s.counts.kept), (3, True, 3))
            rows = [json.loads(x) for x in (out / "S.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([r["ticker"] for r in rows], ["A", "C", "D"])
            self.assertNotIn("rules_primary", rows[1])
            self.assertEqual(s.counts.census, {"2026-06|0": 1, "2026-05|0": 1, "2026-04|0": 1})
            # Finished: another run asks nothing.
            kh.crawl_series("S", out, pacer, fetch, traded_only=True, log=lambda m: None)
            self.assertEqual(len(asked), 3)


    def test_the_sample_is_by_ticker_so_a_rerun_keeps_the_same_rows_and_the_census_counts_all(self) -> None:
        rows = [row(f"KXMVE-{i}", "2026-05-01T00:00:00Z", mve_selected_legs=[{}, {}, {}]) for i in range(400)]
        a, b = kh.Counts(), kh.Counts()
        kept_a, _ = kh.read_page(page(rows), a, traded_only=True, sample_pct=10)
        kept_b, _ = kh.read_page(page(rows), b, traded_only=True, sample_pct=10)
        self.assertEqual([r["ticker"] for r in kept_a], [r["ticker"] for r in kept_b])
        self.assertTrue(15 <= len(kept_a) <= 70, len(kept_a))
        self.assertEqual((a.kept + a.sampled_out, a.census), (400, {"2026-05|3": 400}))


class Pacing(unittest.TestCase):
    def test_a_429_halves_the_speed_and_is_retried_and_success_speeds_up(self) -> None:
        answers = iter([(429, ""), (200, "ok"), (200, "ok")])
        slept: list[float] = []
        p = kh.Pacer(interval=1.0, sleep=slept.append)
        self.assertEqual(p.get(lambda u: next(answers), "u"), "ok")
        self.assertEqual((p.calls, p.r429), (2, 1))
        self.assertAlmostEqual(p.interval, 2.0 * 0.97)
        self.assertEqual(slept[:3], [1.0, 2.0, 2.0])  # the pace, the backoff, the slower pace
        p.get(lambda u: next(answers), "u")
        self.assertLess(p.interval, 2.0 * 0.97)

    def test_the_run_stops_when_429s_pass_two_percent(self) -> None:
        p = kh.Pacer(sleep=lambda s: None)
        p.calls, p.r429 = 500, 9   # 10 of 501 would be 2.0%: still inside
        with self.assertRaises(kh.TooMany429s):
            for _ in range(5):
                p.get(lambda u: (429, ""), "u")
        self.assertGreater(p.r429 / p.calls, kh.MAX_429_SHARE)

    def test_a_404_is_not_retried_and_a_dead_line_is(self) -> None:
        p = kh.Pacer(sleep=lambda s: None)
        with self.assertRaises(RuntimeError):
            p.get(lambda u: (404, ""), "u")
        self.assertEqual(p.calls, 1)
        answers = iter([OSError("reset"), (200, "ok")])

        def flaky(u: str) -> tuple[int, str]:
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a

        self.assertEqual(p.get(flaky, "u"), "ok")


class Discover(unittest.TestCase):
    def test_m1_is_the_combo_series_and_m2_is_nba_and_nhl_singles_only(self) -> None:
        sports = {"series": [{"ticker": t} for t in ("KXNBAGAME", "KXNBAPTSPROT", "KXNHLGAME", "KXNFLGAME",
                                                      "KXNCAAFTOTAL", "KXMVENBASINGLEGAME", "KXMLBGAME")]}
        exotics = {"series": [{"ticker": "KXMVESPORTSMULTIGAMEEXTENDED"}, {"ticker": "KXOTHER"}]}

        def fetch(url: str) -> tuple[int, str]:
            return 200, json.dumps(exotics if "Exotics" in url else sports)

        p = kh.Pacer(sleep=lambda s: None)
        self.assertEqual(kh.discover("m1", p, fetch), ["KXMVENBASINGLEGAME", "KXMVESPORTSMULTIGAMEEXTENDED"])
        self.assertEqual(kh.discover("m2", p, fetch), ["KXNBAGAME", "KXNHLGAME"])


if __name__ == "__main__":
    unittest.main()
