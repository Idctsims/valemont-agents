"""Owner condition on amendment A1: the final injury report's publication must
be verified to precede the commit instant, per game. Where it cannot be, the
injury factor is MISSING, never assumed."""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sports.nfl import injuries
from sports.nfl.schedule import Game
from tests.kalshi_fakes import game, schedule

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def at_et(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ET).astimezone(UTC)


def commit_instant(g: Game) -> datetime:
    return g.kickoff - timedelta(hours=24)


class Deadlines(unittest.TestCase):
    def check(self, kickoff: datetime, *, public: bool, location: str = "Home") -> None:
        g = dataclasses.replace(game(kickoff), location=location)
        self.assertEqual(injuries.report_public_by(g, commit_instant(g)), public,
                         f"{kickoff.astimezone(ET):%a %H:%M} ET, {location}: "
                         f"deadline {injuries.final_report_deadline(g)}")

    def test_sunday_early_game_friday_report_precedes_saturday_commit(self) -> None:
        self.check(at_et(2026, 10, 4, 13), public=True)

    def test_sunday_night(self) -> None:
        self.check(at_et(2026, 10, 4, 20, 20), public=True)

    def test_monday_night_saturday_report(self) -> None:
        self.check(at_et(2026, 10, 5, 20, 15), public=True)

    def test_thursday_night_wednesday_report_precedes_wednesday_evening_commit(self) -> None:
        self.check(at_et(2026, 10, 8, 20, 15), public=True)

    def test_thanksgiving_early_kickoff_is_missing(self) -> None:
        """12:30 ET Thursday → commit Wednesday 12:30, before the 4 p.m. deadline."""
        self.check(at_et(2026, 11, 26, 12, 30), public=False)

    def test_thanksgiving_late_kickoff_is_verified(self) -> None:
        self.check(at_et(2026, 11, 26, 20, 20), public=True)

    def test_saturday_late_season_thursday_report(self) -> None:
        self.check(at_et(2026, 12, 19, 16, 30), public=True)

    def test_friday_game_has_no_deadline_to_rely_on(self) -> None:
        self.check(at_et(2026, 11, 27, 15), public=False)

    def test_wednesday_christmas_game_is_missing(self) -> None:
        self.check(at_et(2026, 12, 23, 13), public=False)

    def test_international_game_is_missing_even_on_a_sunday(self) -> None:
        self.check(at_et(2026, 10, 11, 9, 30), public=False, location="Neutral")

    def test_deadline_is_four_pm_eastern_across_a_dst_change(self) -> None:
        g = game(at_et(2026, 11, 2, 20, 15))       # Monday after DST ends (Nov 1)
        self.assertEqual(injuries.final_report_deadline(g), at_et(2026, 10, 31, 16))


class QbOutRespectsTheGate(unittest.TestCase):
    def setUp(self) -> None:
        self.prior = game(at_et(2026, 9, 27, 13), game_id="2026_03_BAL_DAL", week=3)
        self.reports = injuries.InjuryReports([
            injuries.InjuryRow(2026, 5, "BAL", "qb_bal", "QB", "Out"),
            injuries.InjuryRow(2026, 5, "DAL", "qb_dal", "QB", ""),
        ])
        self.passing = [injuries.PassingRow("qb_bal", "BAL", self.prior.game_id, 30)]

    def qb(self, g: Game) -> int | None:
        return injuries.qb_out(g, "BAL", commit_instant(g), self.reports, self.passing,
                               schedule(g, self.prior))

    def test_verified_report_is_read(self) -> None:
        self.assertEqual(self.qb(game(at_et(2026, 10, 4, 13))), 1)

    def test_unverifiable_report_is_missing_not_assumed(self) -> None:
        g = dataclasses.replace(game(at_et(2026, 10, 4, 9, 30)), location="Neutral")
        self.assertIsNone(self.qb(g))

    def test_the_post_game_starter_columns_are_never_loaded(self) -> None:
        """games.csv away_qb_id/home_qb_id name who actually started: a leak."""
        fields = {f.name for f in dataclasses.fields(Game)}
        self.assertFalse(fields & {"away_qb_id", "home_qb_id", "away_qb_name", "home_qb_name"})


if __name__ == "__main__":
    unittest.main()
