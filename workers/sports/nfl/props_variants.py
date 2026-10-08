"""The props development variants of docs/dev/props_variants.md.

Each function reads only data that existed before t; callers pass games
already fenced by `PlayerHistory.before(t)` / `player_before(pid, t)`, and
every function re-checks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Iterable, Mapping, Sequence

from sports.nfl.props_model import HALF_LIFE_DAYS, LeagueContext, PlayerGame

__all__ = [
    "V1_PSEUDO_OPPS", "VACATED_MIN_SHARE",
    "usage_mean", "environment_factor", "opportunity_shares", "vacated_factor",
    "Calibrator", "fit_calibrator",
]

V1_PSEUDO_OPPS: Final = 20.0
VACATED_MIN_SHARE: Final = 0.15
POOL_POSITIONS: Final[dict[str, frozenset[str]]] = {
    "rec": frozenset({"WR", "TE", "RB"}),
    "rush": frozenset({"RB"}),
}


def _w(t: datetime, g: PlayerGame) -> float:
    return 0.5 ** ((t - g.kickoff).total_seconds() / 86400 / HALF_LIFE_DAYS)


def _fenced(games: Iterable[PlayerGame], t: datetime) -> list[PlayerGame]:
    out = list(games)
    if any(g.kickoff >= t for g in out):
        raise ValueError("a game at or after t reached a props variant — leakage")
    return out


def usage_mean(
    *, player_games: Sequence[PlayerGame], league_before: Sequence[PlayerGame],
    context: LeagueContext, position: str, opponent: str, opp_scale: float = 1.0,
) -> float | None:
    """V1: opportunities × yards-per-opportunity × opponent factor.

    Volume is the player's own weighted opportunities per game, not shrunk
    toward an all-player average; only efficiency is shrunk (20 pseudo-opps).
    `opp_scale` lets V4 redistribute vacated usage.
    """
    t = context.t
    player_games = _fenced(player_games, t)
    league_before = _fenced(league_before, t)
    pos = [g for g in league_before if g.position == position]
    pos_opps = sum(g.opportunities for g in pos)
    if not player_games or pos_opps <= 0:
        return None
    ypo_pos = sum(g.yards for g in pos) / pos_opps
    wsum = sum(_w(t, g) for g in player_games)
    w_opps = sum(_w(t, g) * g.opportunities for g in player_games)
    w_yards = sum(_w(t, g) * g.yards for g in player_games)
    opp_per_game = w_opps / wsum * opp_scale
    ypo = (w_yards + V1_PSEUDO_OPPS * ypo_pos) / (w_opps + V1_PSEUDO_OPPS)
    league = context.league_per_team_game.get(position)
    if not league:
        return None
    tot, n_opp = context.opp_allowed.get((position, opponent), (0.0, 0))
    opp_factor = ((tot + league * 6.0) / (n_opp + 6.0)) / league
    return max(0.1, opp_per_game * ypo * opp_factor)


def environment_factor(implied_team_points: float | None, league_mean_points: float | None) -> float:
    """V3: implied team points at t ÷ league mean team points before t; 1.0 if
    either is missing."""
    if not implied_team_points or not league_mean_points or implied_team_points <= 0:
        return 1.0
    return implied_team_points / league_mean_points


def opportunity_shares(
    team: str, stat: str, team_games_before: Sequence[PlayerGame], t: datetime,
) -> dict[str, float]:
    """Each player's weighted share of the team's pool opportunities before t."""
    pool = POOL_POSITIONS.get(stat)
    if pool is None:
        return {}
    games = [g for g in _fenced(team_games_before, t) if g.team == team and g.position in pool]
    totals: dict[str, float] = {}
    for g in games:
        totals[g.player_id] = totals.get(g.player_id, 0.0) + _w(t, g) * g.opportunities
    s = sum(totals.values())
    return {} if s <= 0 else {p: v / s for p, v in totals.items()}


def vacated_factor(player_id: str, shares: Mapping[str, float], out_players: set[str]) -> float:
    """V4: 1 + vacated × own share / Σ shares of remaining players, where
    vacated counts only out players holding ≥ 15% of the pool."""
    vacated = sum(s for p, s in shares.items() if p in out_players and s >= VACATED_MIN_SHARE)
    if vacated <= 0 or player_id in out_players:
        return 1.0
    remaining = sum(s for p, s in shares.items() if p not in out_players)
    own = shares.get(player_id, 0.0)
    if remaining <= 0 or own <= 0:
        return 1.0
    return 1.0 + vacated * own / remaining


# ---------------------------------------------------------------------------
# V2 calibration layer
# ---------------------------------------------------------------------------

def _logit(p: float) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


@dataclass(frozen=True, slots=True)
class Calibrator:
    a: float = 0.0
    b: float = 1.0

    def __call__(self, p: float) -> float:
        z = self.a + self.b * _logit(p)
        return 1.0 / (1.0 + math.exp(-z))


def fit_calibrator(pairs: Sequence[tuple[float, float]], ridge: float = 1e-3) -> Calibrator:
    """Maximum likelihood logistic on (p, outcome ∈ {0,1}): σ(a + b·logit p).
    Newton–Raphson with a small ridge toward (0, 1). Identity when too few."""
    data = [(_logit(p), y) for p, y in pairs if y in (0.0, 1.0)]
    if len(data) < 50:
        return Calibrator()
    a, b = 0.0, 1.0
    for _ in range(50):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in data:
            q = 1.0 / (1.0 + math.exp(-(a + b * x)))
            r = q - y
            ga += r; gb += r * x
            w = q * (1 - q)
            haa += w; hab += w * x; hbb += w * x * x
        ga += ridge * a; gb += ridge * (b - 1.0)
        haa += ridge; hbb += ridge
        det = haa * hbb - hab * hab
        if det <= 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break
    return Calibrator(a, b)
