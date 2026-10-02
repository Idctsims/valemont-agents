"""`nfl_ml` — NFL game-winner contracts on Kalshi (`KXNFLGAME`).

Pre-registered in docs/preregistration_nfl.md (v1 + A1). This module does only
what is NFL-moneyline specific; settlement, the close and the fee/edge
arithmetic are shared (`venues/kalshi/`).

    observe()          games whose commit instant (kickoff − 24 h) has passed
                       and whose kickoff has not; both teams' live quotes; the
                       home market's candles; rest; starting-QB status; the fee
                       regime in force; the latest usable model fit
    form_thesis()      p_home = mid_home + Σ β·x; price YES on both teams'
                       markets through the shared gate; at most one per game
    build_commitment() one event_contract per surviving game

**The prior is that this agent loses** (CLAUDE.md §8). It is scored net of fees
on its own record, and it commits only when its disagreement with the market
pays for the spread and the fee with 2¢ to spare.

It stands down — loudly, as an idle run with the reason — when there is no
usable model fit whose data ends before now. It never guesses coefficients.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, ClassVar, Final, Sequence

from core import ledger
from core.agent import DeferPolicy, Proposal
from core.ledger import Factor, PendingCommitment
from sports.nfl import injuries
from sports.nfl.ml_model import (
    ANCHOR_LEAD, LATE_WINDOW, STALE_AFTER, FactorModel, Features, features_asof,
)
from sports.nfl.schedule import KICKOFF_SOURCE, Game, NflSchedule
from venues.kalshi.client import Candle, KalshiClient, Quote
from venues.kalshi.contract_agent import KalshiContractAgent, contract_commitment
from venues.kalshi.edge import Candidate, Gate, evaluate
from venues.kalshi.fees import FeeRegime, FeeSchedule, fetch_schedule

__all__ = ["NflMoneylineAgent", "NFL_ML_DEFER_POLICY", "NFL_ML_CAPTURE_POLICY",
           "SERIES", "GATE", "COMMIT_LEAD", "MODEL"]

SERIES: Final = "KXNFLGAME"
MODEL: Final = "nfl_ml_factor_ridge"

#: preregistration_nfl.md §2.2 / §2.3 / §2.6.
GATE: Final = Gate(margin=Decimal("0.02"), max_spread=Decimal("0.03"), size=Decimal(100))
COMMIT_LEAD: Final = timedelta(hours=24)

#: Clamp so a large adjustment can never produce an impossible probability.
P_FLOOR: Final = Decimal("0.001")
P_CEIL: Final = Decimal("0.999")

#: §2.7, from FOOTBALLGAMEWIN: expiration ≤ one week after the game, settlement
#: the next day, plus possible outcome review. Hourly sweep → ~24 attempts/day.
NFL_ML_DEFER_POLICY: Final = DeferPolicy(max_attempts=264, max_overdue=timedelta(days=10))
#: A game postponed within Kalshi's 48 h window, plus margin. Capture every
#: 10 minutes → 6 attempts/hour.
NFL_ML_CAPTURE_POLICY: Final = DeferPolicy(max_attempts=450, max_overdue=timedelta(hours=72))

#: Refresh the schedule at most this often (postponements are rare and slow).
SCHEDULE_TTL: Final = timedelta(minutes=10)

log = logging.getLogger("valemont.nfl_ml")


@dataclass(frozen=True, slots=True)
class GameObservation:
    game: Game
    home: Quote
    away: Quote
    hourly: tuple[Candle, ...]
    minute: tuple[Candle, ...]
    qb_out_home: int | None
    qb_out_away: int | None


@dataclass(frozen=True, slots=True)
class Observation:
    at: datetime
    games: tuple[GameObservation, ...]
    model_version: int | None
    model: FactorModel | None
    regime: FeeRegime | None
    schedule_fetched_at: datetime
    skipped: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Pick:
    obs: GameObservation
    candidate: Candidate
    p_market: Decimal
    contributions: dict[str, Decimal]
    features: Features


@dataclass(frozen=True, slots=True)
class Thesis:
    picks: tuple[Pick, ...]
    at: datetime
    model_version: int
    regime: FeeRegime
    schedule_fetched_at: datetime


class NflMoneylineAgent(KalshiContractAgent[Observation, Thesis]):
    slug: ClassVar[str] = "nfl_ml"
    default_defer_policy: ClassVar[DeferPolicy] = NFL_ML_DEFER_POLICY
    default_capture_policy: ClassVar[DeferPolicy] = NFL_ML_CAPTURE_POLICY
    #: One commitment per game; a full NFL window holds at most 16.
    max_slate_size: ClassVar[int] = 16
    #: Live commits require displayed depth. A backtest replay sets this False:
    #: candles carry no book sizes, and preregistration §2.2.4 discloses that
    #: replay assumes the displayed size was sufficient.
    check_depth: bool = True

    def __init__(
        self,
        *,
        client: KalshiClient | None = None,
        clock: Callable[[], datetime] | None = None,
        load_schedule: Callable[[Sequence[int]], NflSchedule] | None = None,
        load_injuries: Callable[[Sequence[int]], tuple[injuries.InjuryReports, list[injuries.PassingRow]]] | None = None,
        load_fees: Callable[[KalshiClient, str], FeeSchedule] | None = None,
        load_model: Callable[[int, str, datetime], ledger.ModelVersion | None] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(client=client, clock=clock, **kwargs)  # type: ignore[arg-type]
        self._load_schedule = load_schedule or (lambda seasons: NflSchedule.fetch(seasons))
        self._load_injuries = load_injuries or injuries.load
        self._load_fees = load_fees or fetch_schedule
        self._load_model = load_model or ledger.latest_model_version
        self._schedule: NflSchedule | None = None

    # -- data ---------------------------------------------------------------

    @staticmethod
    def seasons_for(now: datetime) -> tuple[int, int]:
        """The NFL season in progress and the one before (for week-1 starters)."""
        season = now.year if now.month >= 3 else now.year - 1
        return (season - 1, season)

    def schedule(self, now: datetime) -> NflSchedule:
        if self._schedule is None or now - self._schedule.fetched_at > SCHEDULE_TTL:
            self._schedule = self._load_schedule(self.seasons_for(now))
        return self._schedule

    def kickoff_now(self, pending: PendingCommitment) -> datetime | None:
        game = self.schedule(self.clock()).game(pending.payload["game_id"])
        return None if game is None else game.kickoff

    # -- the hooks ----------------------------------------------------------

    def observe(self) -> Observation:
        now = self.clock()
        schedule = self.schedule(now)
        window = schedule.commit_window(COMMIT_LEAD, now)
        skipped: dict[str, str] = {}

        empty = Observation(at=now, games=(), model_version=None, model=None,
                            regime=None, schedule_fetched_at=schedule.fetched_at,
                            skipped=skipped)
        if not window:
            return empty

        fit = self._load_model(self.agent_id, MODEL, now)
        if fit is None:
            log.warning("no usable %s fit with data before %s — standing down", MODEL, now)
            skipped["*"] = "no usable model fit"
            return empty

        held = self.open_subjects()
        regime = self._load_fees(self.client, SERIES).regime_at(now)
        reports, passing = self._load_injuries(self.seasons_for(now))

        by_event: dict[str, list[Quote]] = {}
        for quote in self.client.markets(series_ticker=SERIES, status="open"):
            by_event.setdefault(quote.event_ticker, []).append(quote)

        observed: list[GameObservation] = []
        for event, quotes in by_event.items():
            game = schedule.for_event(event)
            if game is None or game not in window:
                continue
            if any(q.ticker in held for q in quotes):
                skipped[game.game_id] = "already committed"
                continue
            suffix = {q.ticker.rsplit("-", 1)[-1]: q for q in quotes}
            home_code = game.kalshi_code(game.home, suffix)
            away_code = game.kalshi_code(game.away, suffix)
            if home_code is None or away_code is None:
                skipped[game.game_id] = f"markets {sorted(suffix)} do not match {game.away}@{game.home}"
                continue
            home = suffix[home_code]
            hourly = self.client.candles(
                SERIES, home.ticker, game.kickoff - ANCHOR_LEAD - timedelta(hours=2), now, 60)
            minute = (
                self.client.candles(SERIES, home.ticker, now - LATE_WINDOW - STALE_AFTER,
                                    now - LATE_WINDOW, 1)
                + self.client.candles(SERIES, home.ticker, now - STALE_AFTER, now, 1)
            )
            observed.append(GameObservation(
                game=game, home=home, away=suffix[away_code],
                hourly=tuple(hourly), minute=tuple(minute),
                qb_out_home=injuries.qb_out(game, game.home, now,
                                            reports, passing, schedule),
                qb_out_away=injuries.qb_out(game, game.away, now,
                                            reports, passing, schedule),
            ))
        return Observation(at=now, games=tuple(observed), model_version=fit.id,
                           model=FactorModel.from_params(fit.params), regime=regime,
                           schedule_fetched_at=schedule.fetched_at, skipped=skipped)

    def form_thesis(self, observation: Observation) -> Thesis | None:
        if observation.model is None or observation.regime is None or observation.model_version is None:
            return None
        picks: list[Pick] = []
        for g in observation.games:
            features = features_asof(
                t=observation.at, kickoff=g.game.kickoff,
                hourly=g.hourly, minute=g.minute,
                home_rest=g.game.home_rest, away_rest=g.game.away_rest,
                qb_out_home=g.qb_out_home, qb_out_away=g.qb_out_away,
            )
            if features.mid_t is None:
                log.info("%s: stale or missing price at t — no commitment", g.game.game_id)
                self._record_gate(observation, g, None, None, None, "stale or missing price at t")
                continue
            if g.home.yes_bid is None or g.home.yes_ask is None:
                self._record_gate(observation, g, None, None, None, "no two-sided home quote")
                continue
            p_market = (g.home.yes_bid + g.home.yes_ask) / 2
            contributions = observation.model.contributions(features)
            p_home = min(P_CEIL, max(P_FLOOR, p_market + sum(contributions.values(), Decimal(0))))

            # Four ways to hold a team: YES on its book or NO on the other's.
            # Same position, two prices; the gate picks the cheaper by edge.
            candidates = (
                evaluate(g.home, p_home, observation.regime, GATE, check_depth=self.check_depth)
                + evaluate(g.away, 1 - p_home, observation.regime, GATE, check_depth=self.check_depth)
            )
            passing = [c for c in candidates if c.passes]
            nearest = max(passing or candidates, key=lambda c: c.edge)
            self._record_gate(observation, g, nearest, p_market, contributions, None)
            if not passing:
                continue
            best = max(passing, key=lambda c: c.edge)
            picks.append(Pick(obs=g, candidate=best, p_market=p_market,
                              contributions=contributions, features=features))
        if not picks:
            return None
        return Thesis(picks=tuple(picks), at=observation.at,
                      model_version=observation.model_version,
                      regime=observation.regime,
                      schedule_fetched_at=observation.schedule_fetched_at)

    def _record_gate(self, observation: Observation, g: Any, cand: Any, p_market: Decimal | None,
                     contributions: dict[str, Decimal] | None, skipped: str | None) -> None:
        """One `gate_evaluated` event per game per tick in the commit window:
        the best candidate (passing if any, else the nearest miss), the model
        probability on that side, the mid, the model's adjustment, the
        threshold, and the verdict. Home-signed adjustment and mid are kept
        alongside the side-held values, so the row cannot be misread."""
        adjustment = None if contributions is None else sum(contributions.values(), Decimal(0))
        holds_home = None if cand is None else ((cand.ticker == g.home.ticker) == (cand.side == "yes"))
        side_mid = None if p_market is None or holds_home is None else (p_market if holds_home else 1 - p_market)
        s = lambda x: None if x is None else str(x)
        self.record_gate_evaluation({
            "game_id": g.game.game_id, "kickoff": g.game.kickoff.isoformat(),
            "model_version": observation.model_version,
            "ticker": None if cand is None else cand.ticker, "side": None if cand is None else cand.side,
            "holds_home": holds_home,
            "p_model_side": None if cand is None else s(cand.p_model),
            "mid_side": s(side_mid), "mid_home": s(p_market),
            "adjustment_home": s(adjustment),
            "contributions_home": None if contributions is None else {k: str(v) for k, v in contributions.items()},
            "edge": None if cand is None else s(cand.edge),
            "threshold_margin": str(GATE.margin), "max_spread": str(GATE.max_spread),
            "spread": None if cand is None else s(cand.spread),
            "passes": False if cand is None else cand.passes,
            "reason": skipped if cand is None else cand.reason,
        }, message=f"{g.game.game_id}: {'PASS' if cand is not None and cand.passes else 'fail'}")

    def build_commitment(self, thesis: Thesis) -> list[Proposal]:
        proposals: list[Proposal] = []
        for pick in thesis.picks:
            game, cand = pick.obs.game, pick.candidate
            quote = pick.obs.home if cand.ticker == pick.obs.home.ticker else pick.obs.away
            if quote.expected_expiration is None or quote.expected_expiration <= game.kickoff:
                log.warning("%s: no usable expected_expiration_time — skipping", cand.ticker)
                continue
            # Does this position hold the home team? YES on home, or NO on away.
            holds_home = (quote is pick.obs.home) == (cand.side == "yes")
            p_market_side = pick.p_market if holds_home else 1 - pick.p_market
            leg, payload = contract_commitment(
                candidate=cand, series=SERIES, regime=thesis.regime,
                p_market=p_market_side, margin=GATE.margin,
                kickoff=game.kickoff, kickoff_source=KICKOFF_SOURCE,
                kickoff_fetched_at=thesis.schedule_fetched_at,
                game_id=game.game_id, model_version=thesis.model_version,
                extra={
                    "features": {k: None if v is None else str(v)
                                 for k, v in pick.features.values.items()},
                    "quote": {"yes_bid": str(quote.yes_bid), "yes_ask": str(quote.yes_ask),
                              "no_bid": str(quote.no_bid), "no_ask": str(quote.no_ask),
                              "fetched_at_worker": quote.fetched_at.isoformat()},
                },
            )
            # Factors are signed toward HOME in the model; flip when the position
            # holds the away team, so each value adjusts what we actually hold.
            sign = Decimal(1) if holds_home else Decimal(-1)
            factors = [Factor(name, sign * value) for name, value in pick.contributions.items()
                       if value != 0]
            proposals.append(Proposal(
                kind="event_contract",
                quote_fetched_at=quote.fetched_at,
                thesis=(f"{game.away}@{game.home} {game.game_id}: {cand.side.upper()} {cand.ticker} at "
                        f"{cand.entry_price} (+{cand.fee} fee); p_model {cand.p_model} vs "
                        f"cost {cand.entry_cost}, edge {cand.edge}"),
                payload=payload,
                resolves_after=quote.expected_expiration,
                legs=[leg],
                confidence=cand.p_model,
                closes_at=game.kickoff,
                factors=factors,
            ))
        return proposals
