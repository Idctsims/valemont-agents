"""Kalshi trading fees, per series, as of a moment.

**What comes from where.** The fee *formula* is schedule-wide; the fee
*regime* is per series and fetched:

    taker fee / contract = 0.07 × multiplier × P × (1 − P)
    maker fee / contract = maker_share(fee_type) × taker fee

- `0.07` is pinned by Kalshi's own worked example (a $0.055 contract has model
  fee $0.00363825 = 0.07 × 0.055 × 0.945; docs.kalshi.com fee_rounding).
- `maker_share` comes from the series' `fee_type`: `quadratic` charges makers
  nothing; `quadratic_with_maker_fees` charges a quarter of the taker rate
  (0.0175); `quadratic_with_combo_maker_fees` a half (docs.kalshi.com
  get-series). **Confirmed by the owner 2026-09-30** against multiple
  sources; the per-series API data remains authoritative. `flat` uses a different table and is refused rather than
  guessed.
- `fee_type` and `fee_multiplier` are **never hard-coded per series**: they are
  read from `GET /series/{ticker}`, with their history from
  `GET /series/fee_changes?show_historical=true`, and stamped `fetched_at`.

**Fees are rounded per fill to $0.000001** (docs fee_rounding), so the
per-contract figure here is rounded up at the sixth decimal.

**The regime changes.** `KXNFLGAME` became `quadratic_with_maker_fees` on
2026-01-01. A fee applied to a 2025 trade must be the 2025 regime, so every
lookup is `regime_at(t)`. Before the earliest recorded change the prior regime
is not published; the lookup returns the earliest known regime and marks it
`assumed`, and that flag travels into the commitment payload.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from typing import Any, Final

from venues.kalshi.client import KalshiClient, KalshiError, ts

__all__ = [
    "TAKER_COEFFICIENT",
    "MAKER_SHARE",
    "FeeRegime",
    "FeeSchedule",
    "UnsupportedFeeType",
    "SNAPSHOT_2026_09_30",
    "fetch_schedule",
]

log = logging.getLogger("valemont.kalshi.fees")

TAKER_COEFFICIENT: Final = Decimal("0.07")

MAKER_SHARE: Final[dict[str, Decimal]] = {
    "quadratic": Decimal("0"),
    "quadratic_with_maker_fees": Decimal("0.25"),
    "quadratic_with_combo_maker_fees": Decimal("0.5"),
}

_SIX_DP: Final = Decimal("0.000001")

#: As fetched from the live API at 2026-09-30T20:45:47Z. A record and a drift
#: check, **not** the source of truth: `fetch_schedule` reads the API, and logs
#: loudly if a series no longer matches this.
SNAPSHOT_2026_09_30: Final[dict[str, tuple[str, Decimal]]] = {
    "KXNFLGAME":    ("quadratic_with_maker_fees", Decimal(1)),
    "KXNFLSPREAD":  ("quadratic_with_maker_fees", Decimal(1)),
    "KXNFLTOTAL":   ("quadratic_with_maker_fees", Decimal(1)),
    "KXNFLANYTD":   ("quadratic_with_maker_fees", Decimal(1)),
    "KXNFLPASSYDS": ("quadratic", Decimal(1)),
    "KXNFLRSHYDS":  ("quadratic", Decimal(1)),
    "KXNFLRECYDS":  ("quadratic", Decimal(1)),
    "KXNFLREC":     ("quadratic", Decimal(1)),
    "KXNFLPASSTDS": ("quadratic", Decimal(1)),
}


class UnsupportedFeeType(KalshiError):
    """A fee type this module does not model. Refuse rather than guess."""


@dataclass(frozen=True, slots=True)
class FeeRegime:
    """One series' fee structure from `effective_from` onward."""

    series: str
    fee_type: str
    multiplier: Decimal
    effective_from: datetime | None
    fetched_at: datetime
    #: True when this regime is applied to a moment before its own
    #: `effective_from` because nothing earlier is published.
    assumed: bool = False

    def __post_init__(self) -> None:
        if self.fee_type not in MAKER_SHARE:
            raise UnsupportedFeeType(
                f"{self.series}: fee_type {self.fee_type!r} is not modelled "
                f"(known: {sorted(MAKER_SHARE)}). Refusing to price it."
            )

    def taker_fee(self, price: Decimal) -> Decimal:
        """Per-contract taker fee at `price`, rounded up at $0.000001."""
        _check_price(price)
        raw = TAKER_COEFFICIENT * self.multiplier * price * (1 - price)
        return raw.quantize(_SIX_DP, rounding=ROUND_CEILING)

    def maker_fee(self, price: Decimal) -> Decimal:
        """Per-contract maker fee at `price`. Zero on `quadratic` series."""
        _check_price(price)
        raw = MAKER_SHARE[self.fee_type] * TAKER_COEFFICIENT * self.multiplier * price * (1 - price)
        return raw.quantize(_SIX_DP, rounding=ROUND_CEILING)

    def as_payload(self) -> dict[str, Any]:
        """What a commitment records about the fees it was priced under."""
        return {
            "series": self.series,
            "fee_type": self.fee_type,
            "fee_multiplier": str(self.multiplier),
            "effective_from": None if self.effective_from is None else self.effective_from.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "assumed": self.assumed,
        }


def _check_price(price: Decimal) -> None:
    if not (Decimal(0) < price < Decimal(1)):
        raise ValueError(f"a contract price must be strictly between 0 and 1, not {price}")


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """Every known regime for one series, oldest first."""

    series: str
    regimes: tuple[FeeRegime, ...]

    def regime_at(self, when: datetime) -> FeeRegime:
        if when.tzinfo is None:
            raise ValueError("fee lookups need a timezone-aware moment")
        applicable = [
            r for r in self.regimes
            if r.effective_from is None or r.effective_from <= when
        ]
        if applicable:
            return applicable[-1]
        earliest = self.regimes[0]
        log.warning(
            "%s: no fee regime published before %s; assuming the earliest "
            "known (%s from %s)", self.series, when.isoformat(),
            earliest.fee_type, earliest.effective_from,
        )
        return FeeRegime(
            series=earliest.series, fee_type=earliest.fee_type,
            multiplier=earliest.multiplier, effective_from=earliest.effective_from,
            fetched_at=earliest.fetched_at, assumed=True,
        )


def fetch_schedule(client: KalshiClient, series: str) -> FeeSchedule:
    """Build a series' schedule from the live API, stamped with the fetch time.

    The current regime comes from `GET /series/{ticker}`; its history from
    `GET /series/fee_changes`. A change without `scheduled_ts` is refused, and
    a series that has drifted from `SNAPSHOT_2026_09_30` is logged loudly.
    """
    fetched_at = datetime.now(timezone.utc)
    current = client.series(series)
    changes = client.series_fee_changes(series)

    regimes: list[FeeRegime] = []
    for change in sorted(changes, key=lambda c: c.get("scheduled_ts") or ""):
        scheduled = ts(change.get("scheduled_ts"))
        if scheduled is None:
            raise KalshiError(f"{series}: fee change without scheduled_ts: {change!r}")
        regimes.append(FeeRegime(
            series=series, fee_type=change["fee_type"],
            multiplier=Decimal(str(change["fee_multiplier"])),
            effective_from=scheduled, fetched_at=fetched_at,
        ))

    live = FeeRegime(
        series=series, fee_type=current["fee_type"],
        multiplier=Decimal(str(current["fee_multiplier"])),
        effective_from=None, fetched_at=fetched_at,
    )
    if not regimes:
        regimes = [live]
    elif (regimes[-1].fee_type, regimes[-1].multiplier) != (live.fee_type, live.multiplier):
        # The newest change should describe the current state; if it does not,
        # a pending (future) change is listed. Current state wins for now.
        log.warning(
            "%s: current fee (%s×%s) differs from the latest change record "
            "(%s×%s from %s)", series, live.fee_type, live.multiplier,
            regimes[-1].fee_type, regimes[-1].multiplier, regimes[-1].effective_from,
        )

    snapshot = SNAPSHOT_2026_09_30.get(series)
    if snapshot is not None and snapshot != (live.fee_type, live.multiplier):
        log.error(
            "%s fee regime drifted from the 2026-09-30 snapshot: was %s×%s, now "
            "%s×%s. Using the live value; update SNAPSHOT_2026_09_30 and check "
            "the pre-registration's cost assumptions.", series, *snapshot,
            live.fee_type, live.multiplier,
        )
    return FeeSchedule(series=series, regimes=tuple(regimes))
