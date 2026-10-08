"""Weekly store-only archive of the Kalshi data F1 and F2 will need.

    python -m jobs.archive_nfl_props --week 5

docs/preregistration_nfl.md §8: F1 and F2 are evaluated once, in January,
on 2026 weeks 5–18, from 1-minute candles and trade prints. Kalshi does not
document how long it keeps them, so this job copies them into the db/011 and
db/014 tables as the weeks happen.

**Store only.** It fetches, inserts and counts rows. It computes no feature,
no price at t, no R, and never reads the archive back: every insert is
`ON CONFLICT DO NOTHING`, so a rerun (for example, to pick up settlements
that were pending) is harmless. Printing a quantity F1 or F2 evaluates would
be an interim look (§8.1), and the job does not do it.

What is stored, per yardage-prop market (`KXNFLPASSYDS`, `KXNFLRSHYDS`,
`KXNFLRECYDS`) of a regular-season game in the week:

* weeks 1–18: the market row (kickoff from nflverse) and, once settled, its
  settlement. F2's base-rate pool needs every 2026 settlement, weeks 1–4
  included;
* weeks 5–18 only: 1-minute candles from kickoff − 3 h to kickoff, plus the
  single latest candle before that window, and every trade print in
  [kickoff − 3 h, kickoff). That keeps "the latest candle at or before x"
  exact for every x ≥ kickoff − 3 h, which covers the quote at
  t = kickoff − 75 min (≤ 60 min old) and the close at kickoff, and keeps
  every print in the maker window [t, kickoff).

Run weekly, by hand for now, after Monday night's game: the Tuesday after a
week. Kalshi jobs run one at a time.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Final, Sequence

from core import ledger
from sports.nfl.schedule import Game, NflSchedule
from venues.kalshi.client import Candle, KalshiClient, Quote

log = logging.getLogger("valemont.archive_nfl_props")

SEASON: Final = 2026
SERIES: Final = ("KXNFLPASSYDS", "KXNFLRSHYDS", "KXNFLRECYDS")
FIRST_PRICE_WEEK: Final = 5
LAST_WEEK: Final = 18
CANDLE_FETCH: Final = timedelta(hours=24)
KEEP_WINDOW: Final = timedelta(hours=3)
#: A game is archived only once this long after kickoff, so the window is complete.
SETTLE_GRACE: Final = timedelta(hours=5)


def keep_candles(candles: Sequence[Candle], kickoff: datetime) -> list[Candle]:
    """Candles ending in [kickoff − 3 h, kickoff], plus the latest one before
    that window. Nothing after kickoff (db/011 would refuse it anyway)."""
    start = kickoff - KEEP_WINDOW
    pre = [c for c in candles if c.end < start]
    window = [c for c in candles if start <= c.end <= kickoff]
    return ([max(pre, key=lambda c: c.end)] if pre else []) + sorted(window, key=lambda c: c.end)


def stores_prices(week: int) -> bool:
    return FIRST_PRICE_WEEK <= week <= LAST_WEEK


def ready(game: Game, now: datetime) -> bool:
    return game.kickoff + SETTLE_GRACE <= now


def week_games(schedule: NflSchedule, week: int) -> list[Game]:
    if not 1 <= week <= LAST_WEEK:
        raise SystemExit(f"week {week} is outside 1–{LAST_WEEK}")
    return [g for g in schedule.games if g.season == SEASON and g.week == week and g.game_type == "REG"]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--week", type=int, required=True)
    week = ap.parse_args(argv).week

    now = datetime.now(timezone.utc)
    schedule = NflSchedule.fetch([SEASON])
    games = week_games(schedule, week)
    by_id = {g.game_id: g for g in games}
    pending = [g.game_id for g in games if not ready(g, now)]
    client = KalshiClient(min_interval=0.3)
    cutoff = client.historical_cutoff()

    counts = {"markets": 0, "settled": 0, "unsettled": 0, "candles": 0, "trades": 0,
              "games_pending": len(pending)}
    for series in SERIES:
        quotes: list[Quote] = client.markets(series_ticker=series)
        if any(g.kickoff < cutoff for g in games):
            quotes += client.historical_markets(series_ticker=series)
        for q in quotes:
            game = schedule.for_event(q.event_ticker)
            if game is None or game.game_id not in by_id or not ready(game, now):
                continue
            ledger.archive_market(
                ticker=q.ticker, event_ticker=q.event_ticker, series_ticker=series, sport="nfl",
                game_id=game.game_id, kickoff=game.kickoff, kickoff_source="nflverse",
                floor_strike=q.floor_strike, title=q.title,
            )
            counts["markets"] += 1
            if q.settlement_value is not None and q.settlement_ts is not None:
                ledger.archive_settlement(ticker=q.ticker, result=q.result,
                                          value=q.settlement_value, settled_at=q.settlement_ts)
                counts["settled"] += 1
            else:
                counts["unsettled"] += 1
            if not stores_prices(week):
                continue
            source = "historical" if q.settlement_ts is not None and q.settlement_ts < cutoff else "live"
            k = game.kickoff
            kept = keep_candles(
                client.candles(series, q.ticker, k - CANDLE_FETCH, k, 1, settled_at=q.settlement_ts), k)
            ledger.archive_candles(ticker=q.ticker, candles=kept, source=source)
            prints = client.trades(q.ticker, k - KEEP_WINDOW, k, settled_at=q.settlement_ts)
            ledger.archive_trades(ticker=q.ticker, trades=prints, source=source)
            counts["candles"] += len(kept)
            counts["trades"] += len(prints)

    log.info("week %d archived: %s", week, counts)
    if pending:
        log.warning("not yet archivable (kickoff + %s not reached): %s — rerun later",
                    SETTLE_GRACE, pending)
    if counts["unsettled"]:
        log.warning("%d markets unsettled — rerun this week later to store their settlements",
                    counts["unsettled"])
    ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
