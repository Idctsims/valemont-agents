"""`nfl_spread` ladder model (preregistration_nfl.md §3.2).

A Kalshi spread event is a ladder of "TEAM wins by over X.5?" contracts for
both teams. Each rung is a statement about the home margin M = home − away:

    HOME wins by over X  →  P(M >  X) = mid
    AWAY wins by over X  →  P(M > −X) = 1 − mid

Fit `M ~ Normal(μ, σ)` by least squares on Φ⁻¹(P(M > τ)) = (μ − τ)/σ over rungs
with 0.10 ≤ mid ≤ 0.90, at least 3 of them. The model then adjusts μ by the
same four factors as `nfl_ml`, fitted on the target μ_close − μ_t.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from statistics import NormalDist
from typing import Final, Sequence

__all__ = ["Rung", "LadderFit", "fit_ladder", "rung_from_ticker", "MIN_RUNGS"]

MIN_RUNGS: Final = 3
P_LOW: Final = 0.10
P_HIGH: Final = 0.90
_SUFFIX: Final = re.compile(r"^([A-Z]+)(\d+)$")
_STD_NORMAL: Final = NormalDist()


@dataclass(frozen=True, slots=True)
class Rung:
    """One rung, restated as P(M > tau) for the home margin M."""

    tau: float
    p_over: float


@dataclass(frozen=True, slots=True)
class LadderFit:
    mu: float
    sigma: float
    n_rungs: int

    def p_over(self, tau: float) -> float:
        return 1.0 - _STD_NORMAL.cdf((tau - self.mu) / self.sigma)

    def slope_at(self, tau: float) -> float:
        """dP(M > tau)/dμ: how many probability points one point of μ moves."""
        return _STD_NORMAL.pdf((tau - self.mu) / self.sigma) / self.sigma


def rung_from_ticker(
    ticker: str, floor_strike: Decimal, mid: Decimal, home_codes: set[str], away_codes: set[str]
) -> Rung | None:
    """`KXNFLSPREAD-26SEP28PHICHI-CHI31` with floor 30.5 → a Rung on M."""
    m = _SUFFIX.match(ticker.rsplit("-", 1)[-1])
    if m is None:
        return None
    team, x, p = m.group(1), float(floor_strike), float(mid)
    if team in home_codes:
        return Rung(tau=x, p_over=p)
    if team in away_codes:
        return Rung(tau=-x, p_over=1.0 - p)
    return None


def fit_ladder(rungs: Sequence[Rung]) -> LadderFit | None:
    """Least squares on z = Φ⁻¹(p) = a + b·τ, with b = −1/σ and a = μ/σ.
    None when fewer than 3 usable rungs or the ladder slopes the wrong way."""
    usable = [r for r in rungs if P_LOW <= r.p_over <= P_HIGH]
    if len(usable) < MIN_RUNGS:
        return None
    xs = [r.tau for r in usable]
    zs = [_STD_NORMAL.inv_cdf(r.p_over) for r in usable]
    n = len(xs)
    mx, mz = sum(xs) / n, sum(zs) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (z - mz) for x, z in zip(xs, zs)) / sxx
    if b >= 0:
        return None
    a = mz - b * mx
    sigma = -1.0 / b
    if not math.isfinite(sigma) or sigma <= 0:
        return None
    return LadderFit(mu=a * sigma, sigma=sigma, n_rungs=n)
