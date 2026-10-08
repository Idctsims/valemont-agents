"""`nfl_props` yardage model (preregistration_nfl.md §3.3), development phase.

Per player-stat, a **negative binomial** on integer yards (owner direction for
the dev phase; the gamma alternative in §3.3 stays open until an amendment):

    mean  = player's exponentially weighted yards per game before t
            (weight 0.5^(days/90)), shrunk toward the position mean with a
            4-game pseudo-count
          × opponent factor: the opponent's yards allowed per game to the
            position ÷ league mean, shrunk with a 6-game pseudo-count
    size  = r per stat × position, fitted walk-forward (earlier weeks only)
    P(over rung) = P(X > floor_strike) = 1 − CDF(floor(floor_strike))

Yards are modelled as max(0, yards): every Kalshi rung is a positive
threshold, so negative rushing yards count as "under" either way.

**As-of discipline.** `PlayerHistory.before(t)` is the only way a game reaches
the model, and it admits only games that kicked off before t. The environment
term of §3.3 (implied team points) is 1.0 in this phase: its inputs are not
fetched yet (disclosed in the dev report).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Iterable, Mapping, Sequence

__all__ = [
    "PlayerGame",
    "PlayerHistory",
    "nb_logpmf",
    "nb_sf",
    "estimate_mean",
    "LeagueContext",
    "normalize_name",
    "match_player",
    "fit_size",
    "HALF_LIFE_DAYS",
    "PLAYER_PSEUDO_GAMES",
    "OPPONENT_PSEUDO_GAMES",
    "SIZE_GRID",
]

HALF_LIFE_DAYS: Final = 90.0
PLAYER_PSEUDO_GAMES: Final = 4.0
OPPONENT_PSEUDO_GAMES: Final = 6.0
SIZE_GRID: Final = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)


@dataclass(frozen=True, slots=True)
class PlayerGame:
    player_id: str
    name: str
    position: str
    team: str
    opponent: str
    game_id: str
    kickoff: datetime
    stat: str
    yards: int              # already max(0, ·)
    opportunities: int      # targets / carries / attempts


@dataclass
class PlayerHistory:
    """Every player-game for one stat, indexed for as-of reads."""

    games: Sequence[PlayerGame]
    _by_player: dict[str, list[PlayerGame]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        for g in sorted(self.games, key=lambda g: g.kickoff):
            self._by_player.setdefault(g.player_id, []).append(g)

    def before(self, t: datetime) -> list[PlayerGame]:
        """All games that kicked off strictly before t. The only door in."""
        return [g for g in self.games if g.kickoff < t]

    def player_before(self, player_id: str, t: datetime) -> list[PlayerGame]:
        return [g for g in self._by_player.get(player_id, []) if g.kickoff < t]


def _weight(age_days: float) -> float:
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


@dataclass(frozen=True, slots=True)
class LeagueContext:
    """League aggregates from games strictly before `t`, built once per kickoff
    instant and shared by every player priced at that instant."""

    t: datetime
    pos_mean: Mapping[str, float]                          # yards per player-game
    league_per_team_game: Mapping[str, float]              # position yards per team-game
    opp_allowed: Mapping[tuple[str, str], tuple[float, int]]  # (pos, opp) → (yards, games)

    @classmethod
    def build(cls, games_before: Sequence[PlayerGame], t: datetime) -> "LeagueContext":
        if any(g.kickoff >= t for g in games_before):
            raise ValueError("a game at or after t reached the league context — leakage")
        pos_sum: dict[str, float] = {}
        pos_n: dict[str, int] = {}
        team_game: dict[tuple[str, str, str], float] = {}
        allowed: dict[tuple[str, str, str], float] = {}
        for g in games_before:
            pos_sum[g.position] = pos_sum.get(g.position, 0.0) + g.yards
            pos_n[g.position] = pos_n.get(g.position, 0) + 1
            team_game[(g.position, g.game_id, g.team)] = team_game.get((g.position, g.game_id, g.team), 0.0) + g.yards
            allowed[(g.position, g.opponent, g.game_id)] = allowed.get((g.position, g.opponent, g.game_id), 0.0) + g.yards
        per_team: dict[str, list[float]] = {}
        for (pos, _, _), y in team_game.items():
            per_team.setdefault(pos, []).append(y)
        opp: dict[tuple[str, str], tuple[float, int]] = {}
        for (pos, o, _), y in allowed.items():
            tot, n = opp.get((pos, o), (0.0, 0))
            opp[(pos, o)] = (tot + y, n + 1)
        return cls(
            t=t,
            pos_mean={p: pos_sum[p] / pos_n[p] for p in pos_sum},
            league_per_team_game={p: sum(v) / len(v) for p, v in per_team.items()},
            opp_allowed=opp,
        )


def estimate_mean(
    *,
    player_games: Sequence[PlayerGame],
    context: LeagueContext,
    position: str,
    opponent: str,
) -> float | None:
    """The pre-registered mean for one player-stat at `context.t`."""
    t = context.t
    if any(g.kickoff >= t for g in player_games):
        raise ValueError("a game at or after t reached the prop mean — leakage")
    pos_mean = context.pos_mean.get(position)
    league = context.league_per_team_game.get(position)
    if pos_mean is None or not league:
        return None
    wsum = sum(_weight((t - g.kickoff).total_seconds() / 86400) for g in player_games)
    wyards = sum(_weight((t - g.kickoff).total_seconds() / 86400) * g.yards for g in player_games)
    player_mean = (wyards + PLAYER_PSEUDO_GAMES * pos_mean) / (wsum + PLAYER_PSEUDO_GAMES)
    tot, n_opp = context.opp_allowed.get((position, opponent), (0.0, 0))
    opp_factor = ((tot + league * OPPONENT_PSEUDO_GAMES) / (n_opp + OPPONENT_PSEUDO_GAMES)) / league
    return max(0.1, player_mean * opp_factor)


def nb_logpmf(k: int, mean: float, size: float) -> float:
    """log P(X = k), NB with mean `mean` and size r: Var = μ + μ²/r."""
    p = size / (size + mean)
    return (math.lgamma(k + size) - math.lgamma(size) - math.lgamma(k + 1)
            + size * math.log(p) + k * math.log1p(-p))


def nb_sf(threshold: float, mean: float, size: float) -> float:
    """P(X > threshold) for a half-integer or integer threshold."""
    k_max = math.floor(threshold)
    if k_max < 0:
        return 1.0
    cdf = sum(math.exp(nb_logpmf(k, mean, size)) for k in range(k_max + 1))
    return min(1.0, max(0.0, 1.0 - cdf))


def fit_size(pairs: Iterable[tuple[int, float]], grid: Sequence[float] = SIZE_GRID) -> float:
    """The r on `grid` maximizing log-likelihood of (yards, predicted mean)."""
    data = list(pairs)
    if not data:
        return 2.0
    return max(grid, key=lambda r: sum(nb_logpmf(y, m, r) for y, m in data))


def normalize_name(name: str) -> str:
    """'Amon-Ra St. Brown Jr.' → 'amonrastbrown'. For matching Kalshi titles
    to nflverse display names within a team."""
    cleaned = name.lower()
    for suffix in (" jr.", " jr", " sr.", " iii", " ii", " iv", " v"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return "".join(ch for ch in cleaned if ch.isalpha())


def match_player(title_name: str, candidates: Mapping[str, str]) -> str | None:
    """`candidates` maps normalized name → player_id for one team."""
    return candidates.get(normalize_name(title_name))
