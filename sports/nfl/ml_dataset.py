"""Training rows for `nfl_ml`, rebuilt from Kalshi history exactly as live.

One row per settled game:

    t        = kickoff − 24 h                       (the commit instant)
    features = features_asof(..., t)                (the SAME function the agent calls)
    y        = close_mid_home − mid_home(t)         (the move the model predicts)

The close is fetched by a separate call and never passed to `features_asof`,
so it is a target and cannot be a feature (preregistration_nfl.md §4.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable, Mapping

from sports.nfl import injuries
from sports.nfl.ml_model import (
    ANCHOR_LEAD, LATE_WINDOW, STALE_AFTER, Features, features_asof, mid_at,
)
from sports.nfl.schedule import Game, NflSchedule
from venues.kalshi.client import KalshiClient, Quote

__all__ = ["TrainingRow", "build_rows", "COMMIT_LEAD", "CLOSE_LOOKBACK"]

COMMIT_LEAD = timedelta(hours=24)
CLOSE_LOOKBACK = timedelta(hours=3)


@dataclass(frozen=True, slots=True)
class TrainingRow:
    game_id: str
    week_key: int              # season * 100 + week: orders time for walk-forward validation
    kickoff: datetime
    features: Features
    y: float


def _close_mid(client: KalshiClient, series: str, home: Quote, kickoff: datetime) -> Decimal | None:
    candles = client.candles(series, home.ticker, kickoff - CLOSE_LOOKBACK, kickoff, 1,
                             settled_at=home.settlement_ts)
    return mid_at(candles, kickoff, None)


def build_rows(
    *,
    client: KalshiClient,
    series: str,
    markets: Iterable[Quote],
    schedule: NflSchedule,
    games: Iterable[Game],
    reports: injuries.InjuryReports,
    passing: list[injuries.PassingRow],
) -> tuple[list[TrainingRow], dict[str, str]]:
    """Rows for `games`, plus a reason for every game that produced none."""
    by_event: dict[str, list[Quote]] = {}
    for quote in markets:
        by_event.setdefault(quote.event_ticker, []).append(quote)
    event_for: dict[str, list[Quote]] = {}
    for event, quotes in by_event.items():
        game = schedule.for_event(event)
        if game is not None:
            event_for[game.game_id] = quotes

    rows: list[TrainingRow] = []
    skipped: dict[str, str] = {}
    for game in games:
        quotes = event_for.get(game.game_id)
        if not quotes:
            skipped[game.game_id] = "no Kalshi event"
            continue
        suffix = {q.ticker.rsplit("-", 1)[-1]: q for q in quotes}
        code = game.kalshi_code(game.home, suffix)
        if code is None:
            skipped[game.game_id] = "no home market"
            continue
        home = suffix[code]
        t = game.kickoff - COMMIT_LEAD
        hourly = client.candles(series, home.ticker, game.kickoff - ANCHOR_LEAD - timedelta(hours=2),
                                t, 60, settled_at=home.settlement_ts)
        minute = (
            client.candles(series, home.ticker, t - LATE_WINDOW - STALE_AFTER, t - LATE_WINDOW, 1,
                           settled_at=home.settlement_ts)
            + client.candles(series, home.ticker, t - STALE_AFTER, t, 1,
                             settled_at=home.settlement_ts)
        )
        features = features_asof(
            t=t, kickoff=game.kickoff, hourly=hourly, minute=minute,
            home_rest=game.home_rest, away_rest=game.away_rest,
            qb_out_home=injuries.qb_out(game, game.home, t, reports, passing, schedule),
            qb_out_away=injuries.qb_out(game, game.away, t, reports, passing, schedule),
        )
        if features.mid_t is None:
            skipped[game.game_id] = "stale price at t"
            continue
        close = _close_mid(client, series, home, game.kickoff)
        if close is None:
            skipped[game.game_id] = "no close"
            continue
        rows.append(TrainingRow(
            game_id=game.game_id, week_key=game.season * 100 + game.week,
            kickoff=game.kickoff, features=features, y=float(close - features.mid_t),
        ))
    return rows, skipped


def as_fit_rows(rows: Iterable[TrainingRow]) -> list[tuple[Mapping[str, Decimal | None], float]]:
    return [(r.features.values, r.y) for r in rows]
