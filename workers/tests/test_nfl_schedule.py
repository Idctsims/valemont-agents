"""Kickoff time and Kalshi ↔ nflverse matching (preregistration §1.2, §4.4)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sports.nfl.schedule import NflSchedule, ScheduleError, parse_event_date
from tests.kalshi_fakes import game, schedule

UTC = timezone.utc

CSV = """game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,home_score,away_rest,home_rest
2026_03_BAL_DAL,2026,REG,3,2026-09-27,Sunday,16:25,BAL,,DAL,,7,7
2026_15_JAX_TEN,2026,REG,15,2026-12-13,Sunday,13:00,JAX,,TEN,,6,7
2026_03_LA_DEN,2026,REG,3,2026-09-27,Sunday,20:20,LA,,DEN,,7,7
2025_13_LA_CAR,2025,REG,13,2025-11-30,Sunday,13:00,LA,,CAR,,7,7
"""


def sched() -> NflSchedule:
    return NflSchedule.from_csv(CSV, datetime(2026, 9, 30, tzinfo=UTC), [2025, 2026])


class Kickoff(unittest.TestCase):
    def test_eastern_daylight_time(self) -> None:
        """§4.4: 2026-09-27 16:25 ET is 20:25Z — verified against the live price."""
        self.assertEqual(sched().game("2026_03_BAL_DAL").kickoff,
                         datetime(2026, 9, 27, 20, 25, tzinfo=UTC))

    def test_eastern_standard_time_in_december(self) -> None:
        self.assertEqual(sched().game("2026_15_JAX_TEN").kickoff,
                         datetime(2026, 12, 13, 18, 0, tzinfo=UTC))

    def test_every_kickoff_is_aware_utc(self) -> None:
        for g in sched().games:
            self.assertEqual(g.kickoff.utcoffset(), timedelta(0))


class TickerMatching(unittest.TestCase):
    def test_plain_codes(self) -> None:
        self.assertEqual(sched().for_event("KXNFLGAME-26SEP27BALDAL").game_id, "2026_03_BAL_DAL")

    def test_jac_is_jax(self) -> None:
        self.assertEqual(sched().for_event("KXNFLGAME-26DEC13JACTEN").game_id, "2026_15_JAX_TEN")

    def test_rams_are_lar_in_2026_and_la_in_2025(self) -> None:
        self.assertEqual(sched().for_event("KXNFLGAME-26SEP27LARDEN").game_id, "2026_03_LA_DEN")
        self.assertEqual(sched().for_event("KXNFLGAME-25NOV30LACAR").game_id, "2025_13_LA_CAR")

    def test_a_ticker_dated_a_day_off_still_matches(self) -> None:
        self.assertEqual(sched().for_event("KXNFLGAME-26SEP28BALDAL").game_id, "2026_03_BAL_DAL")

    def test_two_days_off_does_not(self) -> None:
        self.assertIsNone(sched().for_event("KXNFLGAME-26SEP29BALDAL"))

    def test_market_suffix_resolves_to_the_team(self) -> None:
        g = sched().game("2026_03_LA_DEN")
        self.assertEqual(g.kalshi_code("LA", {"LAR", "DEN"}), "LAR")

    def test_ambiguity_is_an_error_not_a_choice(self) -> None:
        k = datetime(2026, 10, 4, 17, tzinfo=UTC)
        s = schedule(game(k, game_id="a"), game(k, game_id="b"))
        with self.assertRaises(ScheduleError):
            s.for_event("KXNFLGAME-26OCT04BALDAL")

    def test_unparseable_ticker(self) -> None:
        with self.assertRaises(ScheduleError):
            parse_event_date("KXNFLGAME-garbage")


class CommitWindow(unittest.TestCase):
    def test_only_games_past_their_commit_instant_and_before_kickoff(self) -> None:
        now = datetime(2026, 10, 3, 17, tzinfo=UTC)
        soon = game(now + timedelta(hours=23), game_id="in")
        early = game(now + timedelta(hours=30), game_id="early")
        started = game(now - timedelta(minutes=1), game_id="started")
        s = schedule(soon, early, started)
        self.assertEqual([g.game_id for g in s.commit_window(timedelta(hours=24), now)], ["in"])


if __name__ == "__main__":
    unittest.main()
