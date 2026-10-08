"""NFL schedule, kickoff, and the Kalshi ↔ nflverse game mapping.

**Kickoff comes from here, never from Kalshi.** Kalshi's `close_time` is the
final whistle (game markets trade in-play) and `occurrence_datetime` is not
kickoff either (verified 2026-09-30). nflverse `games.csv` gives `gameday` and
`gametime` as an **America/New_York wall clock**; it is converted to UTC
exactly once, here, with `zoneinfo`. A naive local datetime relabelled as UTC
moved one reference builder's feature fence by four hours
(docs/reference-analysis.md §5); nothing downstream of this module ever sees a
naive time.

**Matching tickers.** A Kalshi event ticker is `{SERIES}-{YY}{MON}{DD}{AWAY}
{HOME}` with the team codes run together, so it cannot be split by position.
It is matched against the schedule instead: for each game within ±1 day of the
ticker's date (some tickers carry a date a day off the game), build every
spelling Kalshi might use and compare. Kalshi's codes differ from nflverse's
in two known places: `JAC` for `JAX`, and the Rams as `LA` in 2025 but `LAR`
in 2026.
"""

from __future__ import annotations

import csv
import io
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Final, Iterable
from zoneinfo import ZoneInfo

__all__ = [
    "Game",
    "NflSchedule",
    "ScheduleError",
    "GAMES_CSV_URL",
    "kalshi_codes",
    "parse_event_date",
]

GAMES_CSV_URL: Final = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
KICKOFF_SOURCE: Final = "nflverse"

NEW_YORK: Final = ZoneInfo("America/New_York")

#: nflverse code → every code Kalshi has been seen to use for that team.
_KALSHI_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "JAX": ("JAX", "JAC"),
    "LA": ("LA", "LAR"),
}

_MONTHS: Final = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                  "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


class ScheduleError(RuntimeError):
    """The schedule source failed or returned something unusable."""


def kalshi_codes(nflverse_code: str) -> tuple[str, ...]:
    return _KALSHI_ALIASES.get(nflverse_code, (nflverse_code,))


def parse_event_date(event_ticker: str) -> tuple[date, str]:
    """`KXNFLGAME-26SEP27BALDAL` → (2026-09-27, 'BALDAL')."""
    try:
        body = event_ticker.split("-", 1)[1]
        yy, mon, dd, teams = body[:2], body[2:5], body[5:7], body[7:]
        return date(2000 + int(yy), _MONTHS.index(mon) + 1, int(dd)), teams
    except (IndexError, ValueError) as exc:
        raise ScheduleError(f"unparseable Kalshi event ticker {event_ticker!r}") from exc


@dataclass(frozen=True, slots=True)
class Game:
    game_id: str
    season: int
    week: int
    game_type: str
    away: str
    home: str
    kickoff: datetime          # UTC, aware
    away_rest: int | None
    home_rest: int | None
    #: nflverse `location`: 'Home', or 'Neutral' for international and other
    #: neutral-site games (London, the Super Bowl, a season opener abroad).
    location: str = "Home"

    def kalshi_spellings(self) -> set[str]:
        return {a + h for a in kalshi_codes(self.away) for h in kalshi_codes(self.home)}

    def kalshi_code(self, nflverse_code: str, candidates: Iterable[str]) -> str | None:
        """Which of `candidates` (market suffixes) is this team."""
        options = set(kalshi_codes(nflverse_code))
        return next((c for c in candidates if c in options), None)


def _kickoff(gameday: str, gametime: str) -> datetime:
    wall = datetime.fromisoformat(f"{gameday}T{gametime}")
    if wall.tzinfo is not None:
        raise ScheduleError(f"expected a wall-clock time, got {wall.isoformat()}")
    return wall.replace(tzinfo=NEW_YORK).astimezone(timezone.utc)


def _int_or_none(raw: str | None) -> int | None:
    return int(raw) if raw not in (None, "", "NA") else None


class NflSchedule:
    """The season's games, loaded once per fetch, stamped with the fetch time."""

    def __init__(self, games: Iterable[Game], fetched_at: datetime) -> None:
        self.games = tuple(games)
        self.fetched_at = fetched_at
        self._by_id = {g.game_id: g for g in self.games}

    @classmethod
    def from_csv(cls, text: str, fetched_at: datetime, seasons: Iterable[int]) -> "NflSchedule":
        wanted = set(seasons)
        games = []
        for row in csv.DictReader(io.StringIO(text)):
            if int(row["season"]) not in wanted or not row.get("gametime"):
                continue
            games.append(Game(
                game_id=row["game_id"],
                season=int(row["season"]),
                week=int(row["week"]),
                game_type=row["game_type"],
                away=row["away_team"],
                home=row["home_team"],
                kickoff=_kickoff(row["gameday"], row["gametime"]),
                away_rest=_int_or_none(row.get("away_rest")),
                home_rest=_int_or_none(row.get("home_rest")),
                location=row.get("location") or "Home",
            ))
        if not games:
            raise ScheduleError(f"no games for seasons {sorted(wanted)} in the schedule")
        return cls(games, fetched_at)

    @classmethod
    def fetch(
        cls,
        seasons: Iterable[int],
        *,
        get_text: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> "NflSchedule":
        fetched_at = clock()
        text = (get_text or _get_text)(GAMES_CSV_URL)
        return cls.from_csv(text, fetched_at, seasons)

    def game(self, game_id: str) -> Game | None:
        return self._by_id.get(game_id)

    def for_event(self, event_ticker: str) -> Game | None:
        """The game a Kalshi event ticker refers to, or None. Never guesses:
        two candidate games is an error, not a choice."""
        when, teams = parse_event_date(event_ticker)
        hits = [
            g for g in self.games
            if abs((g.kickoff.astimezone(NEW_YORK).date() - when).days) <= 1
            and teams in g.kalshi_spellings()
        ]
        if len(hits) > 1:
            raise ScheduleError(f"{event_ticker} matches {len(hits)} games: {[g.game_id for g in hits]}")
        return hits[0] if hits else None

    def commit_window(self, lead: timedelta, now: datetime) -> list[Game]:
        """Games whose commit instant (kickoff − lead) has passed and whose
        kickoff has not."""
        return [g for g in self.games if g.kickoff - lead <= now < g.kickoff]


def _get_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "valemont-agents/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8")
    except OSError as exc:
        raise ScheduleError(f"{url} failed: {exc}") from exc
