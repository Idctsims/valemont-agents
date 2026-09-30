"""Starting quarterbacks and their injury status, as of a commit instant.

The `injury` factor (preregistration_nfl.md §3.1, amendment A1):

    qb_out(team) = 1 if the team's starting QB is Out or Doubtful on the team's
                   final injury report for the game week, else 0
    NULL         if the team has no report rows that week

nflverse injury files carry season and week but **no report date**, so the
fence is week-level: the final report for the game week, which the NFL
publishes at least ~28 h before kickoff in every standard slot and so before
the kickoff − 24 h commit instant. A status changed after publication cannot be
distinguished in a backtest (disclosed in the pre-registration).

Starting QB = most pass attempts over the team's previous 3 games with kickoff
before t. Only games that *started before t* are read, so a stats row from the
game being predicted can never leak in.
"""

from __future__ import annotations

import csv
import io
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Final, Iterable

from sports.nfl.schedule import NflSchedule, ScheduleError

__all__ = [
    "InjuryRow",
    "PassingRow",
    "InjuryReports",
    "starting_qb",
    "qb_out",
    "INJURIES_URL",
    "STATS_URL",
]

INJURIES_URL: Final = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv"
STATS_URL: Final = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"

OUT_STATUSES: Final = frozenset({"Out", "Doubtful"})
QB_LOOKBACK_GAMES: Final = 3


@dataclass(frozen=True, slots=True)
class InjuryRow:
    season: int
    week: int
    team: str
    player_id: str
    position: str
    status: str


@dataclass(frozen=True, slots=True)
class PassingRow:
    player_id: str
    team: str
    game_id: str
    attempts: int


class InjuryReports:
    """Final weekly reports, indexed by (season, week, team)."""

    def __init__(self, rows: Iterable[InjuryRow]) -> None:
        self._by_team_week: dict[tuple[int, int, str], dict[str, str]] = {}
        for row in rows:
            key = (row.season, row.week, row.team)
            self._by_team_week.setdefault(key, {})[row.player_id] = row.status

    def status(self, season: int, week: int, team: str, player_id: str) -> str | None:
        """The player's final status, '' if listed without one, None if the
        team filed no rows that week."""
        report = self._by_team_week.get((season, week, team))
        if report is None:
            return None
        return report.get(player_id, "")

    @classmethod
    def from_csv(cls, text: str) -> "InjuryReports":
        return cls(injury_rows_from_csv(text))


def injury_rows_from_csv(text: str) -> list[InjuryRow]:
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        if not r.get("gsis_id"):
            continue
        rows.append(InjuryRow(
            season=int(r["season"]), week=int(r["week"]), team=r["team"],
            player_id=r["gsis_id"], position=r.get("position", ""),
            status=r.get("report_status", "") or "",
        ))
    return rows


def passing_rows_from_csv(text: str) -> list[PassingRow]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("position") != "QB" or r.get("season_type") not in ("REG", "POST"):
            continue
        out.append(PassingRow(
            player_id=r["player_id"], team=r["team"], game_id=r["game_id"],
            attempts=int(float(r.get("attempts") or 0)),
        ))
    return out


def starting_qb(
    team: str,
    before: datetime,
    passing: Iterable[PassingRow],
    schedule: NflSchedule,
) -> str | None:
    """Most pass attempts over `team`'s last 3 games that kicked off before
    `before`. None when there is no such game."""
    prior = sorted(
        (g for g in schedule.games
         if g.kickoff < before and team in (g.home, g.away)),
        key=lambda g: g.kickoff,
    )[-QB_LOOKBACK_GAMES:]
    game_ids = {g.game_id for g in prior}
    totals: dict[str, int] = {}
    for row in passing:
        if row.team == team and row.game_id in game_ids:
            totals[row.player_id] = totals.get(row.player_id, 0) + row.attempts
    if not totals:
        return None
    return max(sorted(totals), key=lambda pid: totals[pid])


def qb_out(
    team: str,
    season: int,
    week: int,
    before: datetime,
    reports: InjuryReports,
    passing: Iterable[PassingRow],
    schedule: NflSchedule,
) -> int | None:
    """1 if the starting QB is Out/Doubtful on the week's final report, 0 if
    not, None if unknowable (no starter identified, or no report filed)."""
    qb = starting_qb(team, before, passing, schedule)
    if qb is None:
        return None
    status = reports.status(season, week, team, qb)
    if status is None:
        return None
    return 1 if status in OUT_STATUSES else 0


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "valemont-agents/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.read().decode("utf-8")
    except OSError as exc:
        raise ScheduleError(f"{url} failed: {exc}") from exc


def load(
    seasons: Iterable[int],
    get_text: Callable[[str], str] = fetch_text,
) -> tuple[InjuryReports, list[PassingRow]]:
    """Reports and passing rows for `seasons` (include the prior season so a
    week-1 starter can be identified)."""
    report_rows: list[InjuryRow] = []
    passing: list[PassingRow] = []
    for season in seasons:
        report_rows.extend(injury_rows_from_csv(get_text(INJURIES_URL.format(season=season))))
        passing.extend(passing_rows_from_csv(get_text(STATS_URL.format(season=season))))
    return InjuryReports(report_rows), passing
