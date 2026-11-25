"""E1: build the slip for a target payout (owner, Nov 25; docs/payout_target.md).

The owner gives a stake S and a payout Y, the total paid back if every leg
hits. A Kalshi contract pays $1, so Y contracts at a combo price c cost
Y x (c + fee(c)): the target is the c with c + fee(c) = S / Y. This finds up to
three tickets priced at it or up to 10% under it (so the payout is at least
Y, up to ~10% more), from everything on the board.

It reuses the Parlay Generator's parts and never changes them
(consensus/parlay_ticket.py is pre-registered, P2.1):
- `pt.candidates`: the legs in the generator's order, ask / the raw Kalshi side
  mid, cheapest to trade first. (Not consensus p per dollar: on Nov 20 that
  order selected on ESPN's single-book p sitting above Kalshi's ask.)
- `pt.greedy`: one pass per leg count, keeping rules 5-11 (event cap, refused
  pairs, one prop per player, no unmodeled same-game pair, 3 per game, the 1c
  tick floor) and skipping a leg once the rest could no longer reach the window.
- `pt.price_ticket`: the correlated fair chance (copula within a game,
  independent across games).
The tickets are ranked as the generator ranks them, by cost over fair (the
highest chance to hit for the price), fewer legs on a tie.

When nothing lands there, the window widens both ways in steps and the answer is
the nearest payout it can reach, marked `nearest`. A target under 1c (about
99x and up) is never reachable: rule 11's tick floor.

Pure: no SQL and no network. Nothing here bets or commits (CLAUDE.md §1, §2).
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
#: A leg's ask: 5c to 95c (the Props tab's bands, end to end).
ASK_LO, ASK_HI = Decimal("0.05"), Decimal("0.95")
FRESH = timedelta(minutes=20)
MAX_LEGS = 12
#: The window: +-10% of the target price, then wider steps for the nearest answer.
STEPS = (Decimal("0.10"), Decimal("0.25"), Decimal("0.5"), Decimal("1"), Decimal("3"), Decimal("9"))
#: The search prices at most this many tickets (the 20 s budget).
MAX_PRICED = 12
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


def search(stake: Decimal, payout: Decimal, legs: Sequence[pl.Leg], t: cr.RhoTable, *,
           draws: int = cr.MC_DRAWS, draws_max: int = cr.MC_DRAWS_MAX) -> Answer:
    t0 = time.perf_counter()
    target = target_price(stake, payout)
    cands, _ = pt.candidates(legs)
    aim = max(target, pt.TICK_FLOOR)
    picks: list[Pick] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    priced = 0
    nearest = target < pt.TICK_FLOOR
    for i, rel in enumerate(STEPS):
        # First: the target price or up to 10% cheaper, so the payout is at least
        # what was asked (up to ~10% more). A price above it buys fewer contracts:
        # the Nov 25 dry run's +-10% window paid 91-93 on a $100 ask. The nearest
        # answer's wider steps look both ways.
        win = (aim / (1 + rel), aim) if i == 0 and not nearest else window(aim, rel)
        for k in range(1, MAX_LEGS + 1):
            if priced >= MAX_PRICED or len(cands) < k:
                break
            b = pt.greedy(cands, k, t, win)
            if len(b.legs) < k or not pt.in_window(pt._product(b.legs), win):
                continue
            key = tuple(sorted(c.key for c in b.legs))
            if key in seen:
                continue
            seen.add(key)
            picks.append(Pick(list(b.legs), pt.price_ticket(b.legs, t, draws=draws, draws_max=draws_max)))
            priced += 1
        if picks:
            nearest = nearest or i > 0
            break
    if nearest and picks:
        # The nearest payout first, then the best chance among those as near.
        picks.sort(key=lambda p: (abs(p.priced.scored_price - target), p.priced.rel_cost, len(p.legs)))
    else:
        picks.sort(key=lambda p: (p.priced.rel_cost, len(p.legs), sorted(c.m.market_ticker for c in p.legs)))
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
