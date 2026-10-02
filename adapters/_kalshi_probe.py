"""`_kalshi_probe` — exercises Kalshi settlement and close capture on real data.

Owner decision (2026-09-30): the `nfl_ml` holdout passed with zero
commitments, so `resolve()` and `capture_close()` had never run against a real
Kalshi market. This agent exists only to make them run.

Once per NFL week it commits **one contract**, YES on the home team of the
week's **first game**, at kickoff − 24 h, at the live ask — **regardless of
edge**. It has no model and no thesis about the outcome.

**One per week, structurally.** Only the week's first game by kickoff is ever
eligible, and `open_subjects()` stops a second commitment on that game, so no
ledger history is needed to enforce the cadence.

**Never in a performance view.** Its `agents` row is `is_test = true`
(db/012), so `TRACK_RECORD_FILTER` drops every row it writes from every
track-record calculation, and per-agent reporting leaves it out like `_fake`
and `_test`. Its record is plumbing evidence: did the commitment resolve,
was the close captured, did the numbers match Kalshi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, ClassVar, Final, Sequence

from adapters.nfl_ml import COMMIT_LEAD, NFL_ML_CAPTURE_POLICY, NFL_ML_DEFER_POLICY, SERIES
from core.agent import DeferPolicy, Proposal
from core.ledger import PendingCommitment
from sports.nfl.schedule import KICKOFF_SOURCE, Game, NflSchedule
from venues.kalshi.client import KalshiClient, Quote
from venues.kalshi.contract_agent import KalshiContractAgent, contract_commitment
from venues.kalshi.edge import Gate, evaluate
from venues.kalshi.fees import FeeRegime, FeeSchedule, fetch_schedule

__all__ = ["KalshiProbe"]

#: Accepts any price: the probe commits regardless of edge. One contract.
PROBE_GATE: Final = Gate(margin=Decimal(-1), max_spread=Decimal(1), size=Decimal(1))

log = logging.getLogger("valemont.kalshi_probe")


@dataclass(frozen=True, slots=True)
class Observation:
    at: datetime
    game: Game | None
    home: Quote | None
    regime: FeeRegime | None
    schedule_fetched_at: datetime


def first_game_of_week(schedule: NflSchedule, game: Game) -> bool:
    week = [g for g in schedule.games if (g.season, g.week) == (game.season, game.week)]
    return game.kickoff == min(g.kickoff for g in week)


class KalshiProbe(KalshiContractAgent[Observation, Observation]):
    slug: ClassVar[str] = "_kalshi_probe"
    default_defer_policy: ClassVar[DeferPolicy] = NFL_ML_DEFER_POLICY
    default_capture_policy: ClassVar[DeferPolicy] = NFL_ML_CAPTURE_POLICY
    max_slate_size: ClassVar[int] = 1

    def __init__(
        self,
        *,
        client: KalshiClient | None = None,
        clock: Callable[[], datetime] | None = None,
        load_schedule: Callable[[Sequence[int]], NflSchedule] | None = None,
        load_fees: Callable[[KalshiClient, str], FeeSchedule] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(client=client, clock=clock, **kwargs)  # type: ignore[arg-type]
        self._load_schedule = load_schedule or (lambda seasons: NflSchedule.fetch(seasons))
        self._load_fees = load_fees or fetch_schedule
        self._schedule: NflSchedule | None = None

    def schedule(self, now: datetime) -> NflSchedule:
        season = now.year if now.month >= 3 else now.year - 1
        if self._schedule is None or (now - self._schedule.fetched_at).total_seconds() > 600:
            self._schedule = self._load_schedule((season,))
        return self._schedule

    def kickoff_now(self, pending: PendingCommitment) -> datetime | None:
        game = self.schedule(self.clock()).game(pending.payload["game_id"])
        return None if game is None else game.kickoff

    def observe(self) -> Observation:
        now = self.clock()
        schedule = self.schedule(now)
        empty = Observation(at=now, game=None, home=None, regime=None,
                            schedule_fetched_at=schedule.fetched_at)
        window = [g for g in schedule.commit_window(COMMIT_LEAD, now)
                  if first_game_of_week(schedule, g)]
        if not window:
            return empty
        game = window[0]
        held = self.open_subjects()
        for quote in self.client.markets(series_ticker=SERIES, status="open"):
            if schedule.for_event(quote.event_ticker) != game:
                continue
            if quote.ticker in held:
                return empty
            if game.kalshi_code(game.home, {quote.ticker.rsplit("-", 1)[-1]}):
                regime = self._load_fees(self.client, SERIES).regime_at(now)
                return Observation(at=now, game=game, home=quote, regime=regime,
                                   schedule_fetched_at=schedule.fetched_at)
        log.warning("%s: no open home market found for the week's first game", game.game_id)
        return empty

    def form_thesis(self, observation: Observation) -> Observation | None:
        if observation.game is None or observation.home is None or observation.regime is None:
            return None
        return observation

    def build_commitment(self, thesis: Observation) -> Proposal | None:
        home, game, regime = thesis.home, thesis.game, thesis.regime
        assert home is not None and game is not None and regime is not None
        if home.yes_bid is None or home.yes_ask is None or home.expected_expiration is None:
            log.warning("%s: no usable quote — probe skipped this week", home.ticker)
            return None
        mid = (home.yes_bid + home.yes_ask) / 2
        yes = next(c for c in evaluate(home, mid, regime, PROBE_GATE) if c.side == "yes")
        if not yes.passes:
            log.warning("%s: probe could not price (%s)", home.ticker, yes.reason)
            return None
        leg, payload = contract_commitment(
            candidate=yes, series=SERIES, regime=regime, p_market=mid, margin=PROBE_GATE.margin,
            kickoff=game.kickoff, kickoff_source=KICKOFF_SOURCE,
            kickoff_fetched_at=thesis.schedule_fetched_at, game_id=game.game_id,
            model_version=None, extra={"purpose": "plumbing_probe", "nfl_week": game.week},
        )
        return Proposal(
            kind="event_contract",
            quote_fetched_at=home.fetched_at,
            thesis=f"Plumbing probe, week {game.week}: 1 YES {home.ticker} at {yes.entry_price}. "
                   "No edge claimed; exercises settlement and close capture.",
            payload=payload, resolves_after=home.expected_expiration, legs=[leg],
            closes_at=game.kickoff,
        )
