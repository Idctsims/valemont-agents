"""E1: build the slip for a target payout (owner, Nov 25; docs/payout_target.md).

The owner gives a stake S and a payout Y, the total paid back if every leg
hits. A Kalshi contract pays $1, so Y contracts at a combo price c cost
Y x (c + fee(c)): the target is the c with c + fee(c) = S / Y. This finds up to
three tickets priced at it or up to 10% under it (so the payout is at least
Y, up to ~10% more), from everything on the board.

Its own search (owner, Nov 25 evening, after "the slips aren't even trying"
on $1 -> $1,000), not the Parlay Generator's pre-registered one:
- **One leg per game.** Legs from different games are independent, so the
  product of their asks is an honest estimate of the combo's price and the
  correlated fair chance is the product of theirs. Same-game legs gave a 0%
  ticket (two legs that cannot both hit) and a fake +245% (nested legs priced
  as their product). One prop per player follows.
- **Legs sized to the target.** For k legs each leg aims near c^(1/k) (6 legs to
  0.09c: about 31c each), within a band of it, cheapest to trade first (ask /
  the raw Kalshi side mid, the generator's order); the last leg is the one
  that lands the price. Asks 10c-90c on a multi-leg ticket (no 95c filler, no
  5c lottery leg); a single bet may ask 5c-95c.
- **No 1c floor.** Kalshi quotes combos below 1c on a $0.0001 grid (the RFQ
  guide); the generator's rule 11 tick floor does not apply here.
- Ranked by cost over fair (the best chance to hit for the price), fewer legs
  on a tie. When nothing lands in the window, the nearest price it could
  build, marked `nearest`.

It reuses `pt.candidates` (the order) and `pt.price_ticket` (the pricer) and
never changes the generator. Pure: no SQL and no network. Nothing here bets
or commits (CLAUDE.md §1, §2).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Mapping, Sequence

from consensus import correlation as cr
from consensus import parlay_legs as pl
from consensus import parlay_ticket as pt

FEE_RATE = Decimal("0.07")
#: A leg's ask: 5c to 95c as a single bet; 10c to 90c on a multi-leg ticket.
ASK_LO, ASK_HI = Decimal("0.05"), Decimal("0.95")
MULTI_LO, MULTI_HI = Decimal("0.10"), Decimal("0.90")
#: A leg's ask may sit this far either side of its share of the target (c^(1/k)).
BAND = Decimal("1.15")
#: Kalshi's finest combo price step below 1c (the RFQ guide): the lowest target searched.
PRICE_FLOOR = Decimal("0.0001")
FRESH = timedelta(minutes=20)
MAX_LEGS = 12
#: The window: the target or up to 10% under it (the payout at least what was asked).
CHEAPER = Decimal("0.10")
TOP = 3
#: Past this multiple (payout / stake), the page warns: the longshot corner.
WARN_MULTIPLE = 100


def fee(c: Decimal) -> Decimal:
    """Kalshi's taker fee per contract at price c, rounded up at $0.000001 (db/073, price.ts)."""
    return (FEE_RATE * c * (1 - c) * 1_000_000).to_integral_value(rounding=ROUND_CEILING) / 1_000_000


def target_price(stake: Decimal, payout: Decimal) -> Decimal:
    """The c with c + 0.07 c (1 - c) = stake / payout: the smaller root of
    0.07 c^2 - 1.07 c + r = 0. Rounded to 1e-8."""
    r = float(stake / payout)
    c = (1.07 - math.sqrt(1.07 ** 2 - 4 * 0.07 * r)) / (2 * 0.07)
    return Decimal(str(round(c, 8)))


def contracts(stake: Decimal, price: Decimal) -> int:
    """Whole contracts the stake buys at the combo price, fee included."""
    return int((stake / (price + fee(price))).to_integral_value(rounding=ROUND_FLOOR))


def window(c: Decimal, rel: Decimal) -> tuple[Decimal, Decimal]:
    return c / (1 + rel), c * (1 + rel)


@dataclass(frozen=True)
class Pick:
    legs: list[pt.Cand]
    priced: pt.Priced


@dataclass(frozen=True)
class Answer:
    target: Decimal
    picks: list[Pick]
    nearest: bool
    candidates: int
    seconds: float


def legs_from(markets: Sequence[pl.Market], at: Mapping[str, datetime], now: datetime) -> list[pl.Leg]:
    """Both sides of every market, priced at the board's quote and its read
    time, that keep the generator's universe rule (binary, not started,
    combo-eligible; YES-only takes YES), are fresh (20 min), and ask 5c-95c."""
    out = []
    for m in markets:
        read = at.get(m.market_ticker)
        if read is None or now - read > FRESH:
            continue
        q = pl.Quote(m.yes_bid, m.yes_ask, read)
        for side in pl.SIDES:
            leg = pl.Leg(m, side, q)
            ask = leg.ask
            if ask is None or not (ASK_LO <= ask <= ASK_HI) or not pl.universe(leg, now):
                continue
            out.append(leg)
    return out


def _game(c: pt.Cand) -> str:
    return c.m.game_ticker


def _player(c: pt.Cand) -> tuple[str, str] | None:
    m = c.m
    return ((m.team or "?"), m.subject.strip().lower()) if m.market_kind == "prop" and m.subject else None


def _fits(legs: Sequence[pt.Cand], c: pt.Cand) -> bool:
    """One leg per game, one prop per player."""
    if any(_game(x) == _game(c) for x in legs):
        return False
    me = _player(c)
    return me is None or all(_player(x) != me for x in legs)


def build(cands: Sequence[pt.Cand], k: int, target: Decimal) -> list[pt.Cand] | None:
    """A k-leg ticket priced as near the target as the board allows, never above
    it when it can be in the window. Each next leg aims at what is left of the
    price spread evenly over the legs left ((target / price so far)^(1 / legs
    left)), the cheapest to trade within BAND of that aim, so no leg is left to
    make up the difference; the last leg is the one that lands the price.
    None when k legs cannot be found."""
    lo, hi = (ASK_LO, ASK_HI) if k == 1 else (MULTI_LO, MULTI_HI)
    pool = [c for c in cands if lo <= c.ask <= hi]
    legs: list[pt.Cand] = []
    for i in range(k - 1):
        aim = Decimal(math.exp(math.log(float(target / pt._product(legs))) / (k - i)))
        pick = next((c for c in pool if aim / BAND <= c.ask <= aim * BAND and _fits(legs, c)), None)
        if pick is None:
            return None
        legs.append(pick)
    need = target / pt._product(legs)
    # The last leg: in the window (at most the target, at least 10% under) and cheapest to
    # trade; else the one whose price lands nearest the target.
    best, best_d = None, None
    for c in pool:
        if not _fits(legs, c):
            continue
        if need / (1 + CHEAPER) <= c.ask <= need:
            return [*legs, c]
        d = abs(math.log(float(c.ask / need)))
        if best_d is None or d < best_d:
            best, best_d = c, d
    return None if best is None else [*legs, best]


def search(stake: Decimal, payout: Decimal, legs: Sequence[pl.Leg], t: cr.RhoTable, *,
           draws: int = cr.MC_DRAWS, draws_max: int = cr.MC_DRAWS_MAX) -> Answer:
    t0 = time.perf_counter()
    target = target_price(stake, payout)
    cands, _ = pt.candidates(legs)
    aim = max(target, PRICE_FLOOR)
    win = (aim / (1 + CHEAPER), aim)
    built: list[Pick] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for k in range(1, MAX_LEGS + 1):
        ticket = build(cands, k, aim)
        if ticket is None:
            continue
        key = tuple(sorted(c.key for c in ticket))
        if key in seen:
            continue
        seen.add(key)
        built.append(Pick(ticket, pt.price_ticket(ticket, t, draws=draws, draws_max=draws_max)))
    inside = [p for p in built if pt.in_window(p.priced.scored_price, win)]
    nearest = target < PRICE_FLOOR or not inside
    if inside and not nearest:
        picks = sorted(inside, key=lambda p: (p.priced.rel_cost, len(p.legs),
                                              sorted(c.m.market_ticker for c in p.legs)))
    else:
        # The nearest payout first, then the best chance among those as near.
        picks = sorted(built, key=lambda p: (abs(math.log(float(p.priced.scored_price / aim))),
                                             p.priced.rel_cost, len(p.legs)))
    return Answer(target, picks[:TOP], nearest, len(cands), time.perf_counter() - t0)


def _n(d: Decimal | None, places: int = 6) -> float | None:
    return None if d is None else round(float(d), places)


def ticket_json(p: Pick, stake: Decimal, periods: Mapping[str, str | None], at: datetime) -> dict[str, Any]:
    """One ticket as payout_answers.tickets stores it (the page reads this)."""
    pr = p.priced
    n = contracts(stake, pr.scored_price)
    fair = pr.fair_p
    legs = []
    for c in p.legs:
        m, q = c.m, c.leg.quote
        assert q is not None
        legs.append({k: v for k, v in {
            "m": m.market_ticker, "side": c.leg.side, "k": m.market_kind, "label": m.label, "who": m.subject,
            "stat": m.stat, "line": _n(m.line, 2), "team": m.team, "per": periods.get(m.series_ticker),
            "game": m.game_title, "sport": m.sport, "start": int(m.starts_at.timestamp()),
            "ask": _n(c.ask, 4), "p": _n(c.p), "tier": m.tier, "ya": _n(q.yes_ask, 4),
            "na": None if q.yes_bid is None else _n(1 - q.yes_bid, 4),
            "yo": True if m.combo_yes_only else None, "age": max(0, int((at - q.at).total_seconds())),
        }.items() if v is not None})
    return {
        "k": len(p.legs), "price": _n(pr.scored_price, 8), "fee": _n(fee(pr.scored_price), 6),
        "contracts": n, "fair": round(fair, 6), "se": None if pr.fair_se is None else round(pr.fair_se, 6),
        "naive": _n(pr.naive_p, 8), "rel": round(pr.rel_cost, 6) if math.isfinite(pr.rel_cost) else None,
        "exp": round((fair * n - float(stake)) / float(stake), 4),
        "combo": pr.combo_status, "combo_reason": pr.combo_reason,
        "pairs": pr.pairs, "measured": pr.measured_pairs, "prior": pr.prior_pairs, "legs": legs,
    }
