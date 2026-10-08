"""crypto adapter — domain specifics only. Keep it thin.

Implements exactly four things against core.agent.BaseAgent:
    observe()             fetch domain data
    form_thesis(obs)      reason about it
    build_commitment(t)   produce the payload + legs
    resolve(commitment)   determine what actually happened

Everything else is core/'s job.

---

THE RULE (mechanical, deterministic, no model)

Volatility-normalized mean reversion on hourly candles:

    z = (price - SMA24) / STDEV24

    z <= -2.0   ->  LONG   (stretched below the mean)
    z >= +2.0   ->  SHORT  (stretched above it)
    otherwise   ->  stand down

The target is SMA24 either way. The invalidation is `ATR_STOP_MULT * ATR14`
away from entry, below for a long and above for a short — symmetric, so the
same favorable move scores the same in both directions.

Both directions matter. Long-only mean reversion is structurally idle through
a rally: every symbol sits above its mean and the agent collects nothing for
the duration of an uptrend, which is a coverage hole rather than caution.

Deterministic on purpose. This is the first adapter, and when it is wrong we
need to be able to tell a data bug from a reasoning bug. A model in the loop
makes those indistinguishable, so there isn't one. `form_thesis()` is where it
would go, and its signature does not change to accommodate one: it already
takes an `Observation` and returns a `Thesis | None`, and `Thesis` already
carries the free-text `rationale` and the `confidence` a model would produce.
Swapping the body is the whole change.

THE STOP IS THE DENOMINATOR (CLAUDE.md §9). `capital_at_risk` is
`|entry - invalidation| * size` — what the agent declared, not what the
instrument permits — so a stop the rule could tighten at will would be a stop
the rule could use to inflate its own multiple. Hence `_stop_distance()`:
volatility-derived by one documented rule, floored at `max(1.0*ATR, 0.5% of
price)`, and the whole thesis is abandoned rather than widened if the result
will not fit inside §9.0's band.

DATA: Coinbase Exchange public API, no key, no account.
  * `/products/{id}/ticker`  last trade + a real trade timestamp
  * `/products/{id}/candles` up to 350 hourly candles (~14 days)

Chosen over the alternatives for specific reasons. CoinGecko's free tier now
gates most endpoints behind a key and returns cross-exchange aggregates, which
are not a price anything could have been filled at. Binance geo-blocks US
egress, which Railway would hit. Kraken works and is a reasonable fallback,
but Coinbase gives a real per-trade timestamp on the ticker, and that is what
makes the staleness check below possible at all. Documented public limit is
10 req/s; this adapter uses 2 calls per symbol per tick — six calls per
quarter-hour against a budget of thirty-six thousand.

STALENESS is the failure this is built around. A feed that is down raises and
is visible; a feed quietly serving a price from an hour ago produces confident
garbage that looks exactly like a real commitment. So freshness is taken from
the ticker's own trade timestamp, never from the candle bucket — a fresh
in-progress hourly candle is stamped with its *bucket start*, up to an hour
ago, and treating that as staleness would reject good data while doing nothing
about the actual risk.
"""

from __future__ import annotations

import json
import statistics
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, ClassVar, Final, Literal, Sequence

from core.agent import (
    AgentError, BaseAgent, DeferPolicy, Proposal, Verdict, declared_risk,
    directional_return,
)
from core.ledger import Leg, LegOutcome, PendingCommitment

__all__ = ["CryptoAgent", "CoinbaseFeed", "FeedError", "Thesis",
           "CRYPTO_DEFER_POLICY"]


# ---------------------------------------------------------------------------
# Tunables. All in one place so the rule can be read off without hunting.
# ---------------------------------------------------------------------------

UNIVERSE: Final[tuple[str, ...]] = ("BTC-USD", "ETH-USD", "SOL-USD")

#: Entry threshold, applied to |z|. 2.0 is roughly a 2.3% tail per side under
#: normality; crypto's fatter tails make it somewhat more frequent in practice.
#: Symmetric: -2.0 goes long, +2.0 goes short.
Z_ENTRY: Final = Decimal("2.0")

#: Lookback for the mean and standard deviation, in hourly candles.
LOOKBACK: Final = 24

#: Refuse to act without this much history — a short window makes STDEV
#: unstable and z meaningless.
MIN_CANDLES: Final = 48

#: Skip a symbol whose recent volatility is this small relative to price.
#: Without it, a quiet tape produces a huge z from a rounding-scale move.
MIN_VOL_FRACTION: Final = Decimal("0.0015")   # 0.15% of price

#: How far from entry the thesis is considered wrong, in ATR units. Applied
#: below entry for a long and above it for a short. The number is deliberately
#: a module constant and not a per-thesis choice — see §9.0.
ATR_STOP_MULT: Final = Decimal("1.5")
ATR_PERIOD: Final = 14

#: Volatility floor on the stop distance. A stop inside one average true range
#: sits inside ordinary hourly noise: it gets hit by a random walk rather than
#: by the thesis being wrong, and it would shrink the §9 denominator for free.
#: This is the guard that matters; core's MIN_STOP_FRACTION is the backstop
#: beneath it for degenerate-ATR regimes.
MIN_STOP_ATR_MULT: Final = Decimal("1.0")

#: Absolute floor on the stop distance as a fraction of price, for the case
#: where ATR itself has collapsed — a halted pair, a rounding-scale range.
#: Matches core's MIN_STOP_FRACTION so a stop that clears this one cannot then
#: be rejected by the contract.
MIN_VOL_FRACTION_STOP: Final = Decimal("0.005")   # 0.5% of price

#: Fixed notional per position, so sizes are comparable across symbols.
NOTIONAL_USD: Final = Decimal("1000")

#: Hours until the outcome is judged. Crypto is 24/7, so this is a choice and
#: not a market constraint. Long enough that hourly noise is not the signal and
#: a reversion has room to happen; short enough to accumulate a track record at
#: a useful rate. Minutes would make the sweep churn on noise.
HORIZON: Final = timedelta(hours=6)

#: Max age of the ticker's own trade timestamp. Coinbase's BTC-USD ticker is
#: typically sub-second; two minutes is generous and still catches a stuck feed.
MAX_PRICE_AGE: Final = timedelta(seconds=120)

#: Max age of the newest CLOSED candle. Two hours means at most one missing
#: bucket, which is a gap worth standing down over.
MAX_CANDLE_AGE: Final = timedelta(hours=2)

HTTP_TIMEOUT: Final = 15
USER_AGENT: Final = "valemont-agents/0.1 (paper trading, no orders)"

#: Crypto never closes, so there is no legitimate reason a price is unavailable
#: twelve hours after the resolution time — that is an outage or a delisting,
#: not a delay. Long enough to ride out a multi-hour exchange incident, short
#: enough that a dead pair does not sit in the due set for days. With a 30-min
#: sweep the two thresholds trip at roughly the same moment, so the recorded
#: void reason is not an arbitrary race between them. Contrast CLAUDE.md §9.3's
#: postponed-game case, where days of patience are correct.
CRYPTO_DEFER_POLICY: Final = DeferPolicy(
    max_attempts=24,
    max_overdue=timedelta(hours=12),
)


class FeedError(RuntimeError):
    """The market data source failed, or returned something unusable."""


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Candle:
    start: datetime
    low: Decimal
    high: Decimal
    open: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One symbol, priced and measured. Everything the rule needs."""

    symbol: str
    price: Decimal
    price_at: datetime
    #: When this worker received the price, on the worker's clock. `price_at`
    #: is the venue's own timestamp; they are different clocks.
    fetched_at: datetime
    sma: Decimal
    sigma: Decimal
    atr: Decimal
    z: Decimal
    candles: int

    @property
    def vol_fraction(self) -> Decimal:
        return self.sigma / self.price


@dataclass(frozen=True, slots=True)
class Observation:
    """What the market looked like this tick, including what we could not see.

    The two failure buckets are kept apart on purpose, because they mean
    opposite things:

        broken   the data is wrong or absent — HTTP failure, malformed body,
                 a stale quote, a timestamp from the future. Something is
                 defective upstream.
        quiet    the data arrived and is fine, it just doesn't qualify —
                 not enough history, volatility too low to measure against.
                 Nothing is wrong; there is simply nothing to do.

    Collapsing them would make a stuck feed indistinguishable from a calm
    market, which is precisely the confusion this adapter is built to avoid.
    """

    at: datetime
    snapshots: tuple[Snapshot, ...] = ()
    broken: dict[str, str] = field(default_factory=dict)
    quiet: dict[str, str] = field(default_factory=dict)

    @property
    def healthy(self) -> int:
        return len(self.snapshots)


@dataclass(frozen=True, slots=True)
class Thesis:
    """A committed view, before it becomes a row.

    An LLM-driven version of `form_thesis` fills exactly these fields —
    `rationale` and `confidence` included — which is why swapping the rule for
    a model needs no restructuring anywhere else.
    """

    symbol: str
    direction: Literal["long", "short"]
    entry: Decimal
    size: Decimal
    target: Decimal
    stop: Decimal
    z: Decimal
    atr: Decimal
    confidence: Decimal
    rationale: str
    quote_fetched_at: datetime | None = None

    @property
    def market(self) -> str:
        return f"spot_{self.direction}"


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------

class CoinbaseFeed:
    """Coinbase Exchange public endpoints. No key, no account, no orders.

    Every method either returns validated data or raises `FeedError`. Nothing
    here returns a partially-trusted value — a price this class hands back has
    already been checked for type, sign, finiteness and age.
    """

    BASE: ClassVar[str] = "https://api.exchange.coinbase.com"

    def __init__(self, base: str | None = None, timeout: int = HTTP_TIMEOUT) -> None:
        self.base = base or self.BASE
        self.timeout = timeout

    def _get(self, path: str) -> Any:
        url = f"{self.base}{path}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise FeedError(f"{path} returned HTTP {response.status}")
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise FeedError(f"{path} returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise FeedError(f"{path} unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise FeedError(f"{path} timed out after {self.timeout}s") from exc

        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FeedError(f"{path} returned non-JSON ({raw[:80]!r})") from exc

    def ticker(self, symbol: str) -> tuple[Decimal, datetime]:
        """Last trade price and the time it happened. Raises on anything off."""
        data = self._get(f"/products/{symbol}/ticker")
        if not isinstance(data, dict):
            raise FeedError(f"{symbol} ticker was {type(data).__name__}, not an object")

        price = _decimal(data.get("price"), f"{symbol} ticker price")
        if price <= 0:
            raise FeedError(f"{symbol} ticker price was {price}")

        raw_time = data.get("time")
        if not isinstance(raw_time, str):
            raise FeedError(f"{symbol} ticker has no usable time field")
        try:
            # Coinbase sends nanosecond precision, which fromisoformat rejects
            # on older Pythons; truncate to microseconds rather than trust it.
            cleaned = raw_time.replace("Z", "+00:00")
            if "." in cleaned:
                head, _, tail = cleaned.partition(".")
                digits = tail[:-6] if tail.endswith("+00:00") else tail
                offset = tail[-6:] if tail.endswith("+00:00") else ""
                cleaned = f"{head}.{digits[:6]}{offset}"
            at = datetime.fromisoformat(cleaned)
        except ValueError as exc:
            raise FeedError(f"{symbol} ticker time {raw_time!r} unparseable") from exc

        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return price, at

    def candles(self, symbol: str, granularity: int = 3600) -> list[Candle]:
        """Hourly candles, oldest first, with the in-progress bucket dropped.

        Coinbase returns `[time, low, high, open, close, volume]`, newest
        first. The newest entry is the *current* bucket and is still forming,
        so it is excluded — a statistic computed over a partial hour is not the
        statistic it claims to be.
        """
        data = self._get(f"/products/{symbol}/candles?granularity={granularity}")
        if not isinstance(data, list):
            raise FeedError(f"{symbol} candles was {type(data).__name__}, not a list")
        if not data:
            raise FeedError(f"{symbol} returned no candles")

        now = datetime.now(timezone.utc)
        parsed: list[Candle] = []
        for row in data:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise FeedError(f"{symbol} candle row malformed: {row!r}")
            start = datetime.fromtimestamp(int(row[0]), timezone.utc)
            if start + timedelta(seconds=granularity) > now:
                continue                      # still forming
            parsed.append(
                Candle(
                    start=start,
                    low=_decimal(row[1], f"{symbol} candle low"),
                    high=_decimal(row[2], f"{symbol} candle high"),
                    open=_decimal(row[3], f"{symbol} candle open"),
                    close=_decimal(row[4], f"{symbol} candle close"),
                    volume=_decimal(row[5], f"{symbol} candle volume"),
                )
            )

        parsed.sort(key=lambda c: c.start)
        if not parsed:
            raise FeedError(f"{symbol} had no closed candles")
        return parsed


def _decimal(value: Any, label: str) -> Decimal:
    """Parse a number strictly. Rejects NaN, infinity and junk by name."""
    if isinstance(value, bool) or value is None:
        raise FeedError(f"{label} was {value!r}")
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise FeedError(f"{label} was not numeric: {value!r}") from exc
    if not parsed.is_finite():
        raise FeedError(f"{label} was {parsed}")
    return parsed


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class CryptoAgent(BaseAgent[Observation, Thesis]):
    """Paper long positions on volatility-normalized dislocation.

    Commits nothing on most ticks, by design — see `form_thesis`.
    """

    slug: ClassVar[str] = "crypto"
    default_defer_policy: ClassVar[DeferPolicy] = CRYPTO_DEFER_POLICY

    def __init__(
        self,
        feed: CoinbaseFeed | None = None,
        universe: Sequence[str] = UNIVERSE,
        defer_policy: DeferPolicy | None = None,
    ) -> None:
        super().__init__(defer_policy)
        self.feed = feed or CoinbaseFeed()
        self.universe = tuple(universe)

    # -- 1. observe ---------------------------------------------------------

    def observe(self) -> Observation:
        """Price and measure every symbol. Partial failure is not total failure.

        One bad symbol is skipped with a recorded reason; the tick continues on
        the rest. The tick raises only when *every* symbol is `broken` — that
        is the feed being down, and an agent that silently goes idle whenever
        its data source dies looks identical to one that is merely being
        selective. The two must never be confusable.

        A universe that is entirely `quiet` does NOT raise. Nothing is wrong
        with a calm market; that is an ordinary idle tick.
        """
        snapshots: list[Snapshot] = []
        broken: dict[str, str] = {}
        quiet: dict[str, str] = {}

        for symbol in self.universe:
            try:
                snapshot = self._snapshot(symbol)
            except FeedError as exc:
                broken[symbol] = str(exc)
                self.log.warning("%s data is defective: %s", symbol, exc)
                continue
            if isinstance(snapshot, str):
                quiet[symbol] = snapshot
                self.log.info("%s does not qualify: %s", symbol, snapshot)
                continue
            snapshots.append(snapshot)

        if self.universe and len(broken) == len(self.universe):
            raise FeedError(
                f"every symbol is defective ({len(broken)}/{len(self.universe)}): "
                + "; ".join(f"{k}: {v}" for k, v in broken.items())
            )

        return Observation(
            at=datetime.now(timezone.utc),
            snapshots=tuple(snapshots),
            broken=broken,
            quiet=quiet,
        )

    def _snapshot(self, symbol: str) -> Snapshot | str:
        """Build one snapshot, or say why not.

        Raises `FeedError` when the data is *defective*; returns a reason
        string when the data is *fine but unqualifying*. Which side a check
        falls on is a judgement about what it indicates upstream:

          defective   HTTP failure, malformed body, stale quote, future
                      timestamp, gap in the candle history
          unqualifying  thin history, volatility too low to divide by

        Staleness is defective, not unqualifying. A price that has not moved
        in ten minutes on a 24/7 market is a broken pipeline, and it is the
        single most dangerous input here: well-formed, plausible, and capable
        of producing a confident commitment at a price nobody could have
        traded at.
        """
        price, price_at = self.feed.ticker(symbol)
        fetched_at = datetime.now(timezone.utc)

        age = datetime.now(timezone.utc) - price_at
        if age > MAX_PRICE_AGE:
            raise FeedError(
                f"{symbol} price is {_secs(age)} old (max {_secs(MAX_PRICE_AGE)}) "
                f"— stale quote, refusing to price against it"
            )
        if age < -MAX_PRICE_AGE:
            raise FeedError(
                f"{symbol} price is timestamped {_secs(-age)} in the future "
                f"— clock skew between us and the venue"
            )

        candles = self.feed.candles(symbol)
        if len(candles) < MIN_CANDLES:
            return f"only {len(candles)} closed candles (need {MIN_CANDLES})"

        newest = candles[-1]
        candle_age = datetime.now(timezone.utc) - newest.start
        if candle_age > MAX_CANDLE_AGE + timedelta(hours=1):
            raise FeedError(
                f"{symbol} newest closed candle is {_secs(candle_age)} old "
                f"— gap in history"
            )

        closes = [c.close for c in candles[-LOOKBACK:]]
        sma = sum(closes) / Decimal(len(closes))
        sigma = Decimal(str(statistics.stdev([float(c) for c in closes])))
        if sigma <= 0:
            return "zero volatility over the lookback — z undefined"
        if sigma / price < MIN_VOL_FRACTION:
            return (
                f"volatility {sigma / price:.5f} below floor "
                f"{MIN_VOL_FRACTION} — z would amplify noise"
            )

        return Snapshot(
            symbol=symbol,
            price=price,
            price_at=price_at,
            fetched_at=fetched_at,
            sma=sma,
            sigma=sigma,
            atr=_atr(candles, ATR_PERIOD),
            z=(price - sma) / sigma,
            candles=len(candles),
        )

    # -- 2. form_thesis -----------------------------------------------------

    def form_thesis(self, observation: Observation) -> Thesis | None:
        """Pick the most dislocated symbol, or stand down.

        Returning None is the ordinary outcome. Four gates have to pass, and
        most ticks fail the first:

          1. `|z| >= 2.0` on at least one symbol. Below the mean goes long,
             above it goes short — symmetric, because a rule that only buys
             dips collects nothing for the length of an uptrend.
          2. That symbol is not already held — otherwise a three-hour selloff
             would open the same position on every tick until the first one
             resolved. This is what `open_subjects()` is for.
          3. At most one commitment per tick, the most extreme |z|, so a
             correlated market-wide move does not become three near-identical
             bets recorded as three independent views.
          4. The volatility-derived stop lands inside §9.0's band. If it does
             not, stand down — never widen or tighten it to fit, because the
             stop is the pnl denominator and adjusting it to suit is exactly
             the gaming the band exists to prevent.
        """
        candidates = [s for s in observation.snapshots if abs(s.z) >= Z_ENTRY]
        if not candidates:
            return None

        held = self.open_subjects()
        fresh = [s for s in candidates if s.symbol not in held]
        if not fresh:
            self.log.info(
                "%d dislocated symbol(s) but all already held: %s",
                len(candidates), ", ".join(sorted(s.symbol for s in candidates)),
            )
            return None

        pick = max(fresh, key=lambda s: abs(s.z))
        direction: Literal["long", "short"] = "long" if pick.z < 0 else "short"

        distance = _stop_distance(pick)
        stop = (
            pick.price - distance if direction == "long"
            else pick.price + distance
        )
        if stop <= 0:
            return None

        # Ask core whether this declaration is admissible BEFORE committing to
        # it. Failing here is a stand-down, not an error: the rule produced a
        # stop the contract will not accept, and the honest response is to
        # skip the trade rather than reshape the stop until it passes.
        try:
            declared_risk(pick.price, stop, 1)
        except AgentError as exc:
            self.log.info(
                "%s %s: stop %.4f outside the §9.0 band (%s) — standing down",
                pick.symbol, direction, stop, exc,
            )
            return None

        side = "below" if direction == "long" else "above"
        void_side = "below" if direction == "long" else "above"
        return Thesis(
            symbol=pick.symbol,
            direction=direction,
            entry=pick.price,
            size=NOTIONAL_USD / pick.price,
            target=pick.sma,
            stop=stop,
            z=pick.z,
            atr=pick.atr,
            quote_fetched_at=pick.fetched_at,
            confidence=_confidence(pick.z),
            rationale=(
                f"{pick.symbol} at {pick.price} is {abs(pick.z):.2f}σ {side} its "
                f"{LOOKBACK}h mean of {pick.sma:.2f} (σ={pick.sigma:.2f}). "
                f"Mean-reversion {direction}, target {pick.sma:.2f}, thesis void "
                f"{void_side} {stop:.2f} "
                f"({ATR_STOP_MULT}×ATR{ATR_PERIOD}={pick.atr:.2f}). "
                f"Judged after {int(HORIZON.total_seconds() // 3600)}h."
            ),
        )

    # -- 3. build_commitment ------------------------------------------------

    def build_commitment(self, thesis: Thesis) -> Proposal | None:
        """Freeze the thesis at the price available right now.

        No re-fetch. The entry recorded is the price the thesis was formed on,
        which is the only price that was actually available at commit time
        (CLAUDE.md §2). Re-pricing here would be a small, comfortable lie.
        """
        return Proposal(
            kind="paper_position",
            quote_fetched_at=thesis.quote_fetched_at,
            thesis=thesis.rationale,
            confidence=thesis.confidence,
            resolves_after=datetime.now(timezone.utc) + HORIZON,
            legs=[
                Leg(
                    subject=thesis.symbol,
                    market=thesis.market,
                    line=thesis.entry,
                    direction=thesis.direction,
                    size=thesis.size,
                )
            ],
            payload={
                "venue": "coinbase",
                "rule": "zscore_reversion_v1",
                "unit": "usd",
                "direction": thesis.direction,
                "entry": str(thesis.entry),
                "target": str(thesis.target),
                # THE pnl denominator (§9), not a footnote. capital_at_risk is
                # |entry - invalidation| * size, so this number is what the
                # result will be measured against — which is why the rule that
                # produced it is recorded alongside it and is not per-thesis.
                "invalidation": str(thesis.stop),
                "stop_rule": (
                    f"{ATR_STOP_MULT}xATR{ATR_PERIOD}, floored at "
                    f"max({MIN_STOP_ATR_MULT}xATR, {MIN_VOL_FRACTION_STOP} of price)"
                ),
                "capital_at_risk": str(
                    declared_risk(thesis.entry, thesis.stop, thesis.size)
                ),
                "notional": str(NOTIONAL_USD),
                "z": str(thesis.z),
                "atr": str(thesis.atr),
                "lookback_hours": LOOKBACK,
                "horizon_hours": int(HORIZON.total_seconds() // 3600),
            },
        )

    # -- 4. resolve ---------------------------------------------------------

    def resolve(self, pending: PendingCommitment) -> Verdict | None:
        """Mark the position to market. Defer rather than guess.

        Every path that cannot produce a trustworthy exit price returns None:
        the feed being down, the price being stale, the leg being unreadable,
        the commitment carrying no invalidation to measure against. Core's
        bounded defer turns a permanent failure into a void after
        `CRYPTO_DEFER_POLICY`; nothing here ever invents a number to fill a row.

        **The stop is checked before the horizon price is used** (§9.1). A
        declared invalidation is a live exit: if price traded through it during
        the holding window, the position ended there, and marking instead to
        whatever the price happens to be six hours later would record `-2.5` on
        something that was only ever able to lose `-1.0`. The candle highs and
        lows over the window answer this from data rather than assumption.
        """
        if not pending.legs:
            self.log.error("commitment #%s has no legs — cannot resolve", pending.id)
            return None

        leg = pending.legs[0]
        if leg.line is None or leg.size is None:
            self.log.error("commitment #%s leg is missing line or size", pending.id)
            return None
        if leg.direction not in ("long", "short"):
            self.log.error(
                "commitment #%s has direction %r, not long/short",
                pending.id, leg.direction,
            )
            return None

        stop = _payload_decimal(pending.payload, "invalidation")
        if stop is None:
            # §9: no declared invalidation means no denominator and no score.
            # Deferring lets the bounded-defer policy void it rather than this
            # adapter inventing a risk number after the fact.
            self.log.error(
                "commitment #%s declares no invalidation — unscoreable under §9",
                pending.id,
            )
            return None

        direction: Literal["long", "short"] = leg.direction
        entry = Decimal(str(leg.line))
        size = Decimal(str(leg.size))

        try:
            spot, price_at = self.feed.ticker(leg.subject)
        except FeedError as exc:
            self.log.warning("cannot price %s: %s — deferring", leg.subject, exc)
            return None

        age = datetime.now(timezone.utc) - price_at
        if age > MAX_PRICE_AGE:
            self.log.warning(
                "%s price is %s old — deferring rather than scoring on stale data",
                leg.subject, _secs(age),
            )
            return None

        # Did the position hit its stop while we were waiting?
        try:
            stopped = self._stop_was_hit(
                leg.subject, direction, stop, pending.committed_at
            )
        except FeedError as exc:
            self.log.warning(
                "cannot check the stop on %s: %s — deferring rather than "
                "scoring a position that may already have been closed",
                leg.subject, exc,
            )
            return None

        exit_price = stop if stopped else spot

        try:
            capital_at_risk = declared_risk(entry, stop, size)
            pnl = directional_return(entry, stop, exit_price, direction)
        except AgentError as exc:
            self.log.error(
                "commitment #%s has an unscoreable declaration: %s",
                pending.id, exc,
            )
            return None

        if pnl > 0:
            outcome: Any = "hit"
        elif pnl < 0:
            outcome = "miss"
        else:
            outcome = "push"

        target = _payload_decimal(pending.payload, "target")
        if target is None:
            target_reached = None
        elif direction == "long":
            target_reached = exit_price >= target
        else:
            target_reached = exit_price <= target

        return Verdict(
            outcome=outcome,
            leg_outcomes=[LegOutcome(leg_index=0, outcome=outcome, actual=exit_price)],
            pnl=pnl,
            detail={
                "unit": "usd",
                "direction": direction,
                "entry": str(entry),
                "invalidation": str(stop),
                "exit": str(exit_price),
                "exit_at": price_at.isoformat(),
                "spot_at_horizon": str(spot),
                "capital_at_risk": str(capital_at_risk),
                "gross": str(
                    (exit_price - entry) * size if direction == "long"
                    else (entry - exit_price) * size
                ),
                "stop_hit": stopped,
                # §9.1: we record the modeled fill AT the declared level. A real
                # gap through it fills worse, and this flag is what keeps a
                # resolution that leaned on the assumption distinguishable from
                # one that never needed it.
                "fill": "assumed_at_stop" if stopped else "spot",
                "held_hours": round(
                    (price_at - pending.committed_at).total_seconds() / 3600, 3
                ),
                # Whether the reversion actually happened, separately from
                # whether the position made money. Scoring is unresolved
                # (CLAUDE.md §8); keep both so it can be settled later.
                "target": None if target is None else str(target),
                "target_reached": target_reached,
                "rule": "zscore_reversion_v1",
            },
        )

    def _stop_was_hit(
        self,
        symbol: str,
        direction: Literal["long", "short"],
        stop: Decimal,
        since: datetime,
    ) -> bool:
        """Did price trade through the invalidation during the holding window?

        Answered from candle lows and highs rather than assumed. A long is
        stopped when any low reaches the level; a short when any high does.

        Hourly candles are the resolution limit: a spike that reversed inside
        one hour is caught, but the exact sequence within that hour is not
        known. That is a bounded and stated imprecision, and it errs toward
        recording the stop — which is the conservative direction, since the
        stop is the worse outcome.
        """
        candles = self.feed.candles(symbol)
        window = [c for c in candles if c.start + timedelta(hours=1) > since]
        if not window:
            return False
        if direction == "long":
            return any(c.low <= stop for c in window)
        return any(c.high >= stop for c in window)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _atr(candles: Sequence[Candle], period: int) -> Decimal:
    """Average true range. Volatility in price units, for the stop distance."""
    window = candles[-(period + 1):]
    if len(window) < 2:
        return Decimal(0)
    ranges: list[Decimal] = []
    for previous, current in zip(window, window[1:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(ranges) / Decimal(len(ranges))


def _stop_distance(snapshot: Snapshot) -> Decimal:
    """How far from entry the thesis is wrong, in price units.

    This is the §9 denominator, so it is computed by one documented rule for
    every commitment and never adjusted per-thesis. Tightening it would inflate
    the recorded multiple without taking any more risk, which is precisely what
    §9.0 exists to prevent.

        max( ATR_STOP_MULT × ATR,           the rule
             MIN_STOP_ATR_MULT × ATR,       noise floor
             MIN_VOL_FRACTION_STOP × price ) degenerate-ATR backstop

    The floors only ever widen the stop, which can only *reduce* the reported
    multiple. A guard that could tighten it would be a guard pointing the wrong
    way.
    """
    return max(
        ATR_STOP_MULT * snapshot.atr,
        MIN_STOP_ATR_MULT * snapshot.atr,
        MIN_VOL_FRACTION_STOP * snapshot.price,
    )


def _confidence(z: Decimal) -> Decimal:
    """Map dislocation to a bounded confidence. Deterministic, and capped.

    A 4σ move is not four times more likely to revert than a 2σ one, and the
    cap is a reminder of that. Calibration is an open problem (CLAUDE.md §8) —
    this is a placeholder that is at least monotonic and honest about its range.
    """
    excess = abs(z) - abs(Z_ENTRY)
    raw = min(Decimal("0.50") + (excess * Decimal("0.10")), Decimal("0.75"))
    # Quantize to the column's own precision (NUMERIC(4,3)) so the value we
    # record is the value we computed, rather than whatever Postgres rounds a
    # 28-digit float-derived Decimal to.
    return raw.quantize(Decimal("0.001"))


def _payload_decimal(payload: dict[str, Any], key: str) -> Decimal | None:
    raw = payload.get(key)
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except Exception:
        return None


def _secs(delta: timedelta) -> str:
    return f"{delta.total_seconds():.0f}s"


def build(feed: CoinbaseFeed | None = None) -> CryptoAgent:
    """Construct the live crypto agent. One line in the orchestrator registry."""
    return CryptoAgent(feed=feed)
