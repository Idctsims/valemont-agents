"""Market-efficiency scan: scope guards, schedule matching, base rate and the
blend weight, on synthetic data. Never reaches the network."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from jobs import scan_market_efficiency as s
from venues.kalshi.client import Candle

T = datetime(2025, 7, 8, 22, 40, tzinfo=timezone.utc)


def candle(end: datetime, bid: str | None, ask: str | None) -> Candle:
    return Candle(end=end, yes_bid_close=None if bid is None else Decimal(bid),
                  yes_ask_close=None if ask is None else Decimal(ask),
                  trade_low=None, trade_high=None, trade_close=None, volume=Decimal(10), open_interest=None)


class Scope(unittest.TestCase):
    def test_nfl_is_refused(self) -> None:
        with self.assertRaises(s.ScopeViolation):
            s.assert_in_scope("KXNFLGAME", "KXNFLGAME-25NOV30LACAR")

    def test_the_reserve_is_refused_from_its_first_day(self) -> None:
        s.assert_in_scope("KXMLBHIT", "KXMLBHIT-26JUN301910NYYBOS")
        with self.assertRaises(s.ScopeViolation):
            s.assert_in_scope("KXMLBHIT", "KXMLBHIT-26JUL011910NYYBOS")

    def test_no_cell_is_nfl(self) -> None:
        self.assertFalse([c for c in s.CELLS if c.series.startswith("KXNFL")])

    def test_frozen_constants_match_the_spec(self) -> None:
        self.assertEqual((s.WINDOW_END, s.LEAD, s.POOL_EVENTS, s.PRICED_EVENTS, s.RUNGS_PER_EVENT,
                          s.BASE_MIN_N, s.SEED, s.DRAWS),
                         (date(2026, 6, 30), timedelta(minutes=60), 300, 150, 5, 20, 20261001, 2000))


class Tickers(unittest.TestCase):
    def test_game_code_with_and_without_a_start_time(self) -> None:
        a = s.parse_game_code("KXMLBGAME-26AUG011507STLTOR")
        self.assertEqual((a.day, a.hhmm, a.teams), (date(2026, 8, 1), "1507", "STLTOR"))
        b = s.parse_game_code("KXNCAAFGAME-25NOV12TOLM-OH")
        self.assertEqual((b.day, b.hhmm, b.teams), (date(2025, 11, 12), None, "TOLM-OH"))

    def test_participant_and_order(self) -> None:
        ev = "KXNHLSPREAD-25DEC02MINEDM"
        self.assertEqual(s.yes_participant(f"{ev}-EDM2", ev), "EDM")
        self.assertIs(s.second_listed("MINEDM", "EDM"), True)
        self.assertIs(s.second_listed("MINEDM", "MIN"), False)
        self.assertIsNone(s.second_listed("LALAL", "LAL"))   # both ends: cannot tell
        self.assertIsNone(s.second_listed("MINEDM", ""))     # a total has no participant


def at(h: int, m: int = 0, d: int = 8) -> datetime:
    return datetime(2025, 7, d, h, m, tzinfo=timezone.utc)


class TeamSchedule(unittest.TestCase):
    def test_codes_split_with_aliases(self) -> None:
        code = s.parse_game_code("KXNHLGAME-25DEC02LAVGK")
        g = s.Scheduled(datetime(2025, 12, 3, 3, tzinfo=timezone.utc), "LAK", "VGK")
        self.assertEqual(s.match_team_game(code, [g]), g)

    def test_a_series_picks_the_listed_et_day_not_its_neighbours(self) -> None:
        code = s.parse_game_code("KXMLBGAME-25JUL08CHCMIN")
        games = [s.Scheduled(at(23, 40, d), "CHC", "MIN") for d in (7, 8, 9)]
        self.assertEqual(s.match_team_game(code, games), games[1])

    def test_a_doubleheader_without_a_listed_time_is_dropped(self) -> None:
        code = s.parse_game_code("KXMLBGAME-25JUL08CHCMIN")
        games = [s.Scheduled(at(17, 10), "CHC", "MIN"), s.Scheduled(at(23, 40), "CHC", "MIN")]
        self.assertIsNone(s.match_team_game(code, games))

    def test_a_listed_time_resolves_a_doubleheader(self) -> None:
        code = s.parse_game_code("KXMLBGAME-25JUL081940CHCMIN")    # 19:40 ET = 23:40Z
        games = [s.Scheduled(at(17, 10), "CHC", "MIN"), s.Scheduled(at(23, 40), "CHC", "MIN")]
        self.assertEqual(s.match_team_game(code, games), games[1])

    def test_a_late_game_rolling_into_the_next_et_day_still_matches(self) -> None:
        code = s.parse_game_code("KXNCAAFGAME-25OCT11USUHAW")
        g = s.Scheduled(datetime(2025, 10, 12, 9, tzinfo=timezone.utc), "USU", "HAW")  # 05:00 ET on the 12th
        self.assertEqual(s.match_team_game(code, [g]), g)


class Tennis(unittest.TestCase):
    def test_surname_prefixes_either_order(self) -> None:
        code = s.parse_game_code("KXATPMATCH-25JUL05TSIRIC")
        m = s.Scheduled(datetime(2025, 7, 5, 12, tzinfo=timezone.utc), "Keegan Rice", "Stefanos Tsitsipas")
        self.assertEqual(s.match_tennis(code, [m]), m)

    def test_a_given_name_never_stands_in_for_a_surname(self) -> None:
        code = s.parse_game_code("KXATPMATCH-25JUL05STERIC")
        m = s.Scheduled(datetime(2025, 7, 5, 12, tzinfo=timezone.utc), "Keegan Rice", "Stefanos Tsitsipas")
        self.assertIsNone(s.match_tennis(code, [m]))

    def test_compound_and_accented_surnames(self) -> None:
        self.assertIn("DEMINAUR", s.surname_forms("Alex de Minaur"))
        self.assertIn("MONFILS", s.surname_forms("Gaël Monfils"))


class Prices(unittest.TestCase):
    def test_quote_is_the_last_fresh_two_sided_candle_at_or_before_t(self) -> None:
        cs = [candle(T - timedelta(minutes=30), "0.40", "0.44"), candle(T, "0.41", "0.43"),
              candle(T + timedelta(minutes=1), "0.90", "0.95")]
        self.assertEqual(s.quote_at(cs, T), (0.41, 0.43))

    def test_stale_or_one_sided_is_no_quote(self) -> None:
        self.assertIsNone(s.quote_at([candle(T - timedelta(minutes=61), "0.4", "0.5")], T))
        self.assertIsNone(s.quote_at([candle(T, "0.00", "0.05")], T))
        self.assertIsNone(s.quote_at([candle(T, None, "0.05")], T))


def pool(n: int, settle: float, *, floor: float | None = 1.5, flag: bool | None = None,
         when: datetime = T - timedelta(days=1)) -> list[s.PoolRung]:
    return [s.PoolRung(flag, floor, settle, when) for _ in range(n)]


class BaseRate(unittest.TestCase):
    def test_only_settled_before_t_counts(self) -> None:
        p = pool(20, 1.0) + pool(50, 0.0, when=T)
        self.assertEqual(s.base_rate(p, kind="prop", flag=None, floor=1.5, window=0, t=T), (1.0, False))

    def test_fewer_than_twenty_falls_back_to_half(self) -> None:
        self.assertEqual(s.base_rate(pool(19, 1.0), kind="prop", flag=None, floor=1.5, window=0, t=T),
                         (0.5, True))

    def test_floor_window(self) -> None:
        p = pool(20, 1.0, floor=44.5) + pool(20, 0.0, floor=48.5)
        self.assertEqual(s.base_rate(p, kind="total", flag=None, floor=48.5, window=3, t=T)[0], 0.0)   # 44.5 is 4 away
        self.assertEqual(s.base_rate(p, kind="total", flag=None, floor=46.5, window=3, t=T)[0], 0.5)

    def test_winner_keys_on_listing_order_and_ignores_floor(self) -> None:
        p = pool(30, 1.0, floor=None, flag=True) + pool(30, 0.0, floor=None, flag=False)
        self.assertEqual(s.base_rate(p, kind="winner", flag=True, floor=None, window=0, t=T)[0], 1.0)
        self.assertEqual(s.base_rate(p, kind="winner", flag=None, floor=None, window=0, t=T), (0.5, True))


class Weight(unittest.TestCase):
    def rows(self, base: float, settles: list[float]) -> list[dict]:
        return [{"event": f"e{i}", "mid": 0.8, "settle": y, "base": base} for i, y in enumerate(settles)]

    def test_an_overconfident_mid_earns_the_base_rate_weight(self) -> None:
        # mid 0.8, truth 0.5: the Brier-optimal blend with b = 0.5 is all b.
        r = s.weight_with_ci(self.rows(0.5, [1.0, 0.0] * 20), "base")
        self.assertAlmostEqual(r["w"], 1.0)

    def test_a_calibrated_mid_earns_none_and_is_not_positive(self) -> None:
        r = s.weight_with_ci(self.rows(0.5, [1.0] * 32 + [0.0] * 8), "base")
        self.assertAlmostEqual(r["w"], 0.0)
        self.assertFalse(r["positive"])

    def test_fair_value_settlements_are_excluded(self) -> None:
        r = s.weight_with_ci(self.rows(0.5, [0.5, 0.37]), "base")
        self.assertEqual(r["n_events"], 0)


if __name__ == "__main__":
    unittest.main()
