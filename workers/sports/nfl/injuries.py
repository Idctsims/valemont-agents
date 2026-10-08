"""Starting quarterbacks and their injury status, as of a commit instant.

The `injury` factor (preregistration_nfl.md §3.1, amendment A1 and its
condition):

    qb_out(team) = 1 if the team's starting QB is Out or Doubtful on the team's
                   final injury report for the game week, else 0
    NULL         if the team has no report rows that week

nflverse injury files carry season and week but **no report date**, so the
fence is week-level: the final report for the game week, which the NFL
publishes at least ~28 h before kickoff in every standard slot and so before
the kickoff − 24 h commit instant. A status changed after publication cannot be
distinguished in a backtest (disclosed in the pre-registration).

**The publication time is verified per game, not assumed** (owner condition on
A1). nflverse carries no report timestamp, so what is verified is the NFL's
*latest permitted* release: 4:00 p.m. ET on the day the policy names for the
game's weekday. If that deadline precedes the commit instant, the report was
public by then. Where the policy names no deadline this module can rely on —
Tuesday, Wednesday and Friday games, and every neutral-site game (international
games, where teams in another time zone may report after the opponent's
practice; the Super Bowl) — the factor is **missing**, never assumed. A
Thanksgiving 12:30 p.m. kickoff fails too: its commit instant (Wednesday
12:30) precedes Wednesday's 4:00 p.m. deadline.

**Never read `games.csv` `away_qb_id` / `home_qb_id`.** Those are the QBs who
actually started, known only after the game. The starter here is inferred from
prior games' pass attempts.

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

from datetime import date, time, timedelta

from sports.nfl.schedule import NEW_YORK, Game, NflSchedule, ScheduleError

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

#: Days before an ET kickoff date on which the final report with game statuses
#: is due, by 4:00 p.m. ET (NFL personnel report policy: Wednesday for
#: Thursday games, Thursday for Saturday, Friday for Sunday, Saturday for
#: Monday). Any other weekday has no deadline this module relies on.
FINAL_REPORT_DAYS_BEFORE: Final[dict[int, int]] = {
    3: 1,   # Thursday → Wednesday
    5: 2,   # Saturday → Thursday
    6: 2,   # Sunday   → Friday
    0: 2,   # Monday   → Saturday
}
FINAL_REPORT_DEADLINE_ET: Final = time(16, 0)
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


def final_report_deadline(game: Game) -> datetime | None:
    """The latest moment the game's final injury report may be published, in
    UTC — or None when the policy gives no deadline this module relies on."""
    if game.location != "Home":
        return None
    kickoff_et = game.kickoff.astimezone(NEW_YORK)
    days = FINAL_REPORT_DAYS_BEFORE.get(kickoff_et.weekday())
    if days is None:
        return None
    report_day: date = kickoff_et.date() - timedelta(days=days)
    return datetime.combine(report_day, FINAL_REPORT_DEADLINE_ET, NEW_YORK).astimezone(game.kickoff.tzinfo)


def report_public_by(game: Game, before: datetime) -> bool:
    """True only when the final report was certainly public at `before`."""
    deadline = final_report_deadline(game)
    return deadline is not None and deadline <= before


def qb_out(
    game: Game,
    team: str,
    before: datetime,
    reports: InjuryReports,
    passing: Iterable[PassingRow],
    schedule: NflSchedule,
) -> int | None:
    """1 if the starting QB is Out/Doubtful on the week's final report, 0 if
    not, None if unknowable: the report's publication before `before` cannot
    be verified, no starter could be identified, or no report was filed."""
    if not report_public_by(game, before):
        return None
    season, week = game.season, game.week
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
