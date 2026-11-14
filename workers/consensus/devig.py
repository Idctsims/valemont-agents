"""De-vigging a book's prices: POWER (the consensus method, owner Nov 9) and
MULTIPLICATIVE (a reported comparison only). Pure stdlib, no I/O.

A book quotes every outcome of a market at prices whose implied
probabilities sum past 1 (the overround). De-vigging maps them back to
probabilities that sum to 1:

    multiplicative : p_i = q_i / sum(q)
    power          : p_i = q_i ** k, with k > 0 solving sum(q_i ** k) = 1

where q_i = 1 / decimal_odds_i. Power shades favourites less than
longshots, the observed favourite-longshot bias; it is what the
pre-registration freezes. Both are checked against penaltyblog (a dev-only
oracle, tests/test_consensus.py) to 1e-9.
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

__all__ = ["american_to_decimal", "implied", "multiplicative", "power", "DevigError"]

#: The power method's root is found to this tolerance on sum(q ** k) - 1.
TOLERANCE = 1e-15
MAX_ITER = 200


class DevigError(ValueError):
    """Prices that cannot be de-vigged (one outcome, a price below 1.0)."""


def american_to_decimal(price: int | float) -> float:
    """+150 -> 2.5; -160 -> 1.625. A price strictly between -100 and +100 does
    not exist in American odds and is refused."""
    p = float(price)
    if -100 < p < 100:
        raise DevigError(f"not an American price: {price!r}")
    return 1.0 + (p / 100.0 if p > 0 else 100.0 / -p)


def implied(decimal_odds: Sequence[float]) -> list[float]:
    if len(decimal_odds) < 2:
        raise DevigError("a market needs at least two outcomes to de-vig")
    out = []
    for o in decimal_odds:
        if not o > 1.0:
            raise DevigError(f"decimal odds must exceed 1.0, got {o!r}")
        out.append(1.0 / o)
    return out


def multiplicative(decimal_odds: Sequence[float]) -> list[float]:
    q = implied(decimal_odds)
    s = sum(q)
    return [x / s for x in q]


def power(decimal_odds: Sequence[float]) -> list[float]:
    """The k with sum(q_i ** k) = 1, by bisection then Newton polish. With an
    overround (sum q > 1) k > 1; with an underround (a rare arbitrage) k < 1."""
    q = implied(decimal_odds)

    def f(k: float) -> float:
        return sum(x ** k for x in q) - 1.0

    # f is decreasing in k (every q < 1); bracket the root.
    lo, hi = 1e-6, 1.0
    while f(hi) > 0:
        lo, hi = hi, hi * 2.0
        if hi > 1e6:
            raise DevigError(f"power de-vig found no root for {decimal_odds!r}")
    if f(lo) < 0:
        raise DevigError(f"power de-vig found no root for {decimal_odds!r}")
    k = (lo + hi) / 2.0
    for _ in range(MAX_ITER):
        k = (lo + hi) / 2.0
        v = f(k)
        if abs(v) < TOLERANCE:
            break
        if v > 0:
            lo = k
        else:
            hi = k
        if hi - lo < 1e-16:
            break
    # Newton steps from the bisection's k (f' = sum q^k ln q).
    for _ in range(5):
        d = sum((x ** k) * math.log(x) for x in q)
        if d == 0:
            break
        step = f(k) / d
        k -= step
        if abs(step) < 1e-17:
            break
    return [x ** k for x in q]


def additive(decimal_odds: Sequence[float]) -> list[float]:
    """The overround taken off every outcome equally. A longshot can land at
    or below zero; it is floored at 0.0 (the method has no opinion there, and
    the four-method test then claims nothing on it)."""
    q = implied(decimal_odds)
    cut = (sum(q) - 1.0) / len(q)
    return [max(0.0, x - cut) for x in q]


def shin(decimal_odds: Sequence[float]) -> list[float]:
    """Shin (1992): the z (share of insider money) at which
    p_i = (sqrt(z^2 + 4 (1 - z) q_i^2 / S) - z) / (2 (1 - z)) sums to 1, by
    bisection. With two outcomes it equals the additive method."""
    q = implied(decimal_odds)
    s = sum(q)

    def probs(z: float) -> list[float]:
        return [(math.sqrt(z * z + 4.0 * (1.0 - z) * x * x / s) - z) / (2.0 * (1.0 - z)) for x in q]

    if s <= 1.0:  # no overround to explain: nothing to remove
        return [x / s for x in q]
    lo, hi = 0.0, 1.0 - 1e-12  # sum(probs) falls from sqrt(S) > 1 at z = 0 toward < 1
    for _ in range(MAX_ITER):
        z = (lo + hi) / 2.0
        if sum(probs(z)) > 1.0:
            lo = z
        else:
            hi = z
        if hi - lo < 1e-16:
            break
    return probs((lo + hi) / 2.0)


#: The four methods an edge claim must survive (docs/consensus.md, "the
#: four-method test"; owner, 2026-11-13).
METHODS: tuple[tuple[str, Callable[[Sequence[float]], list[float]]], ...] = (
    ("power", power), ("multiplicative", multiplicative), ("shin", shin), ("additive", additive))
