"""Fetch every model-independent input for the props variants once, to disk.

    python -m jobs.props_cache

The variants in docs/dev/props_variants.md differ only in the model, so the
Kalshi prices they are scored against are fetched once:

  - every eligible 2025 yardage-prop player-game (same matching and
    eligibility as the first dev run) with its rungs' bid, ask and settlement
    at t = kickoff − 75 min;
  - for every game with props, the total and spread ladders at t, fitted to
    Normal(μ, σ), for V3's implied team points.

Development data only (2025, historical tier). Written to `.cache/` (git-
ignored): a convenience copy of exchange data, not a record.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import timedelta

from core.paths import REPO_ROOT
from jobs.dev_nfl_props import (
    COMMIT_LEAD, SERIES, STALE_AFTER, load_player_games, player_name_from_title,
    recently_active, team_code_of,
)
from sports.nfl import injuries
from sports.nfl.injuries import OUT_STATUSES, report_public_by
from sports.nfl.props_model import PlayerHistory, match_player, normalize_name
from sports.nfl.schedule import NflSchedule, kalshi_codes
from sports.nfl.spread_model import Rung, fit_ladder, rung_from_ticker
from venues.kalshi.client import KalshiClient

CACHE = REPO_ROOT / ".cache/props_2025.json"
log = logging.getLogger("valemont.props_cache")


def quote_at(client: KalshiClient, series: str, q, t):
    candles = client.candles(series, q.ticker, t - STALE_AFTER, t, 1, settled_at=q.settlement_ts)
    quoted = [c for c in candles if c.end <= t and c.yes_bid_close is not None and c.yes_ask_close is not None]
    if not quoted:
        return None
    last = max(quoted, key=lambda c: c.end)
    if t - last.end > STALE_AFTER:
        return None
    return float(last.yes_bid_close), float(last.yes_ask_close)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    schedule = NflSchedule.fetch([2024, 2025])
    games = {g.game_id: g for g in schedule.games if g.season == 2025}
    reports, _ = injuries.load([2025])
    player_games = load_player_games(schedule, [2024, 2025])
    client = KalshiClient(min_interval=0.25)

    rungs, skipped, games_with_props = [], {}, set()
    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for stat, series in SERIES.items():
        history = PlayerHistory(player_games[stat])
        roster: dict[str, dict[str, str]] = {}
        positions = {}
        for g in player_games[stat]:
            roster.setdefault(g.team, {})[normalize_name(g.name)] = g.player_id
            positions[g.player_id] = g.position
        groups: dict[tuple[str, str, str], list] = {}
        for q in client.historical_markets(series_ticker=series):
            game = schedule.for_event(q.event_ticker)
            if game is None or game.game_id not in games or q.floor_strike is None:
                continue
            parts = q.ticker.split("-")
            team = team_code_of(parts[2], game) if len(parts) >= 3 else None
            pid = match_player(player_name_from_title(q.title), roster.get(team, {})) if team else None
            if pid is None:
                skip("player not matched")
                continue
            groups.setdefault((game.game_id, pid, team), []).append(q)
        for (game_id, pid, team), quotes in groups.items():
            game = games[game_id]
            t = game.kickoff - COMMIT_LEAD
            if not report_public_by(game, t):
                skip("A1c unverifiable"); continue
            status = reports.status(game.season, game.week, team, pid)
            if status is None or status in OUT_STATUSES:
                skip("no report / Out / Doubtful"); continue
            if not recently_active(pid, team, t, schedule, history):
                skip("not recently active"); continue
            games_with_props.add(game_id)
            for q in quotes:
                ba = quote_at(client, series, q, t)
                if ba is None:
                    continue
                rungs.append({
                    "stat": stat, "series": series, "ticker": q.ticker, "game_id": game_id,
                    "week": game.week, "kickoff": game.kickoff.isoformat(), "t": t.isoformat(),
                    "player_id": pid, "team": team,
                    "opponent": game.away if team == game.home else game.home,
                    "position": positions.get(pid, ""), "floor": float(q.floor_strike),
                    "bid": ba[0], "ask": ba[1],
                    "settle": None if q.settlement_value is None else float(q.settlement_value),
                    "settlement_ts": None if q.settlement_ts is None else q.settlement_ts.isoformat(),
                })
        log.info("%s: %d rungs cached so far", stat, len(rungs))

    env: dict[str, dict] = {}
    total_q = {}
    for q in client.historical_markets(series_ticker="KXNFLTOTAL"):
        g = schedule.for_event(q.event_ticker)
        if g is not None and g.game_id in games_with_props and q.floor_strike is not None:
            total_q.setdefault(g.game_id, []).append(q)
    spread_q = {}
    for q in client.historical_markets(series_ticker="KXNFLSPREAD"):
        g = schedule.for_event(q.event_ticker)
        if g is not None and g.game_id in games_with_props and q.floor_strike is not None:
            spread_q.setdefault(g.game_id, []).append(q)
    for game_id in sorted(games_with_props):
        game = games[game_id]
        t = game.kickoff - COMMIT_LEAD
        tot_rungs = []
        for q in total_q.get(game_id, []):
            ba = quote_at(client, "KXNFLTOTAL", q, t)
            if ba is not None:
                tot_rungs.append(Rung(tau=float(q.floor_strike), p_over=(ba[0] + ba[1]) / 2))
        home, away = set(kalshi_codes(game.home)), set(kalshi_codes(game.away))
        mar_rungs = []
        for q in spread_q.get(game_id, []):
            ba = quote_at(client, "KXNFLSPREAD", q, t)
            if ba is not None:
                from decimal import Decimal
                r = rung_from_ticker(q.ticker, q.floor_strike, Decimal(str((ba[0] + ba[1]) / 2)), home, away)
                if r is not None:
                    mar_rungs.append(r)
        ft, fm = fit_ladder(tot_rungs), fit_ladder(mar_rungs)
        env[game_id] = {"total_mu": None if ft is None else ft.mu,
                        "margin_mu": None if fm is None else fm.mu,
                        "home": game.home, "away": game.away}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"rungs": rungs, "env": env, "skipped": skipped}), encoding="utf-8")
    log.info("cached %d rungs, %d games with environment, skipped %s", len(rungs), len(env), skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
