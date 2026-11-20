"""Phase 4.3: the ticket search, its prices and the leg reasons (P1).

docs/preregistration_parlays.md §2-§4 (stamped, db/085). Pure: no SQL, no
network, and no clock that decides anything (a search only measures its own
pricing time, for the report). Given the same candidates (the legs that passed rules 1-4
in `consensus.parlay_legs`), the same rho table and the same draw count, it
builds the same tickets and the same reason text, byte for byte. Nothing here
commits; Phase 4.4's adapter does.

Ticket rules (§3), checked in this order for each candidate the greedy
search meets; the first one it breaks is its reason:

    5  leg count        the ticket already holds the count being built
    6  event cap        more legs from one event than its combo_size_max
    7  refused pair     the pricer's refusals (consensus.combo.structure),
                        plus two legs of one market and two outcomes of one
                        mutually exclusive event
    8  one prop/player  a second prop for one player (team + name)
    9  unmodeled pair   a same-game pair with no rho row (measured, prior or
                        borrowed): never priced at an assumed 0
   10  3 per game       a fourth leg from one game
   11  tick floor       the product of the side asks below 1c

Readings of the text where it is silent, each the stricter one (§0), stated
here so the owner can see them:

- A passing leg with no consensus p (or p of 0) cannot be ordered by
  ask / p, so it is not a candidate; the slot counts it (`no_p`).
- "Share of measured pairs" is measured pairs / same-game pairs; a ticket
  with no same-game pair has share 1.0 (nothing on it is a prior).
- "The lexicographically first list of market tickers" compares the
  tickets' market tickers sorted ascending.
- A candidate's reason is its latest evaluation. One ranked above a taken leg
  that the search never reached (the ticket was full) counts as rule 5.
- On a cross-sport ticket, a candidate of the ticket's one sport that the
  replacement scan passes over was never a rule break: it counts as rule 5
  (the count was full when the greedy pass reached it).
- A leg's market label is its Kalshi title (the board's `label`, owner
  Nov 20); a row with none falls back to the market ticker. The ticker stays
  the key everywhere.

These readings were approved as written by the owner on 2026-11-20.

**Consensus p is recomputed from the slot's fresh quote before ordering and
pricing** (owner, 2026-11-20). This is the stamped rule's stricter reading
(§0), not a change to it: the owner's Nov 9 decision, "the generator re-quotes
every candidate leg and recomputes its edge before selecting". The caller
passes legs whose markets went through `parlay_legs.with_fresh_p`, the
consensus engine's own blend and normalization on the fresh mids (kalshi
tier: the mid, normalized across a mutually exclusive event; books and espn:
0.7 x outside + 0.3 x that mid). The stored p from the board's last read
never orders, prices or names a leg. `fair_p`'s marginals are those p. The
engine's 12 h outside flag blocks a claim, never the blend, and that is
kept here exactly.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
from typing import Any, Mapping, Sequence

from consensus import combo
from consensus import correlation as cr
from consensus import parlay_legs as pl

RULE_VERSION = "P1"
#: Tier -> (agent, the leg-count range), §1.
TIERS: Mapping[str, tuple[str, int, int]] = {
    "safer": ("parlay_safer", 3, 5),
    "mid": ("parlay_mid", 6, 10),
    "lotto": ("parlay_lotto", 11, 20),
}
MAX_PER_GAME = 3
TICK_FLOOR = Decimal("0.01")
NOMINAL_CONTRACTS = 100
TAKER_RATE = Decimal("0.07")
MAKER_SHARE = Decimal("0.5")
#: The edge-leg margin under the least favourable de-vig method, net of fee (audit M4).
EDGE_MARGIN = Decimal("0.02")
FILL_NOTE = "extrapolated fill"
EXPECTED_QUOTE_PENDING = "pending (M1)"
RULE_LABELS: Mapping[int, str] = {
    5: "leg count", 6: "event cap", 7: "refused pair", 8: "one prop per player",
    9: "unmodeled pair", 10: "3 per game", 11: "tick floor",
}
#: A leg the cross-sport replacement step removed (§3).
REPLACED = "replaced for a second sport"
MEASURED = "measured_external"


# --------------------------------------------------------------------------- candidates
@dataclass(frozen=True)
class Cand:
    """A leg that passed rules 1-4, with its side's consensus p and ask."""

    leg: pl.Leg
    p: Decimal
    ask: Decimal

    @property
    def m(self) -> pl.Market:
        return self.leg.market

    @property
    def key(self) -> tuple[str, str]:
        return self.m.market_ticker, self.leg.side

    @property
    def ratio(self) -> Decimal:
        return self.ask / self.p

    @property
    def order(self) -> tuple[Decimal, str, str]:
        return self.ratio, self.m.market_ticker, self.leg.side


def side_p(leg: pl.Leg) -> Decimal | None:
    p = leg.market.p_yes
    if p is None:
        return None
    return p if leg.side == "yes" else Decimal(1) - p


def candidates(passed: Sequence[pl.Leg]) -> tuple[list[Cand], int]:
    """The slot's candidates in §3's order (ask / p lowest first; ties by
    market ticker, then side), and how many passing legs had no usable p."""
    out, no_p = [], 0
    for leg in passed:
        p, ask = side_p(leg), leg.ask
        if p is None or p <= 0 or ask is None:
            no_p += 1
            continue
        out.append(Cand(leg, p, ask))
    out.sort(key=lambda c: c.order)
    return out, no_p


def leg_in(c: Cand) -> combo.LegIn:
    """A candidate as the pricer's leg (prices from the slot's re-quote)."""
    m, q = c.m, c.leg.quote
    assert q is not None
    return combo.LegIn(
        market_ticker=m.market_ticker, side=c.leg.side, event_ticker=m.event_ticker, game_ticker=m.game_ticker,
        series_ticker=m.series_ticker, sport=m.sport, league=m.league, market_kind=m.market_kind, payout=m.payout,
        subject=m.subject, stat=m.stat, line=None if m.line is None else float(m.line), label=m.label, team=m.team,
        tier=m.tier, p_yes=None if m.p_yes is None else float(m.p_yes),
        yes_ask=None if q.yes_ask is None else float(q.yes_ask),
        no_ask=None if q.yes_bid is None else float(Decimal(1) - q.yes_bid),
        fee_yes=None, fee_no=None, stale=False, mutually_exclusive=m.mutually_exclusive,
        combo_eligible=m.combo_eligible, combo_yes_only=m.combo_yes_only, combo_size_max=m.combo_size_max,
        starts_at=m.starts_at, stale_kalshi=False, kalshi_at=q.at)


def _player(c: Cand) -> tuple[str, str] | None:
    m = c.m
    if m.market_kind != "prop" or not m.subject:
        return None
    return (m.team or "?"), m.subject.strip().lower()


# --------------------------------------------------------------------------- rules 5-11
def first_broken(ticket: Sequence[Cand], c: Cand, n: int, t: cr.RhoTable) -> int | None:
    """The first of rules 5-11 that adding `c` to `ticket` (building an
    n-leg ticket) breaks, or None if it keeps them all."""
    if len(ticket) >= n:
        return 5
    m = c.m
    same_event = [x for x in ticket if x.m.event_ticker == m.event_ticker]
    if m.combo_size_max is not None and len(same_event) + 1 > m.combo_size_max:
        return 6
    if any(x.m.market_ticker == m.market_ticker for x in ticket):
        return 7
    if same_event and m.mutually_exclusive:
        return 7
    game = [x for x in ticket if x.m.game_ticker == m.game_ticker]
    s: combo.Structure | None = None
    if game:
        try:
            s = combo.structure([leg_in(x) for x in (*game, c)], t)
        except cr.Refused:
            return 7
    me = _player(c)
    if me is not None and any(_player(x) == me for x in ticket):
        return 8
    if s is not None and s.unmodeled:
        return 9
    if len(game) + 1 > MAX_PER_GAME:
        return 10
    if math.prod((x.ask for x in ticket), start=Decimal(1)) * c.ask < TICK_FLOOR:
        return 11
    return None


@dataclass
class Build:
    """One greedy pass: the ticket, and each candidate's reason for not being in it."""

    legs: list[Cand]
    why: dict[tuple[str, str], str] = field(default_factory=dict)
    removed: Cand | None = None


def greedy(cands: Sequence[Cand], n: int, t: cr.RhoTable) -> Build:
    b = Build([])
    for c in cands:
        if len(b.legs) >= n:
            break
        r = first_broken(b.legs, c, n, t)
        if r is None:
            b.legs.append(c)
        else:
            b.why[c.key] = RULE_LABELS[r]
    return b


def replace_for_second_sport(b: Build, cands: Sequence[Cand], n: int, t: cr.RhoTable) -> Build | None:
    """§3's cross-sport step: when every leg shares one sport, the last leg
    added gives way to the first remaining candidate, in order, from another
    sport that keeps rules 5-11. None when there is none."""
    sports = {x.m.sport for x in b.legs}
    if len(sports) != 1:
        return b
    last = b.legs[-1]
    base = b.legs[:-1]
    held = {x.key for x in b.legs}
    why = dict(b.why)
    why[last.key] = REPLACED
    (only,) = sports
    for c in cands:
        if c.key in held or c.m.sport == only:
            continue
        r = first_broken(base, c, n, t)
        if r is None:
            return Build([*base, c], why, removed=last)
        why[c.key] = RULE_LABELS[r]
    return None


# --------------------------------------------------------------------------- prices
def taker_fee_order(price: Decimal, contracts: int = NOMINAL_CONTRACTS) -> Decimal:
    """Kalshi's taker fee on one order at the combo price, rounded up to the cent (§4, §5)."""
    raw = TAKER_RATE * price * (Decimal(1) - price) * contracts
    return (raw * 100).to_integral_value(rounding=ROUND_CEILING) / 100


def side_mid(c: Cand) -> Decimal:
    q = c.leg.quote
    assert q is not None and q.yes_bid is not None and q.yes_ask is not None
    mid = (q.yes_bid + q.yes_ask) / 2
    return mid if c.leg.side == "yes" else Decimal(1) - mid


@dataclass(frozen=True)
class Span:
    """A books-tier leg's de-vig span (db/078) and the board's fee at each side's ask."""

    p_lo: Decimal
    p_hi: Decimal
    fee_yes: Decimal
    fee_no: Decimal


def is_edge_leg(c: Cand, span: Span | None) -> bool:
    """Audit M4: books tier, fresh (rule 2 already held), positive under all
    four methods and >= 2c net of fee under the least favourable one. The span
    is the four methods' lowest and highest p, so the least favourable side p
    is p_lo for YES and 1 - p_hi for NO; >= 2c there implies positive under all."""
    if span is None or c.m.tier != "books":
        return False
    least = span.p_lo if c.leg.side == "yes" else Decimal(1) - span.p_hi
    fee = span.fee_yes if c.leg.side == "yes" else span.fee_no
    return least - (c.ask + fee) >= EDGE_MARGIN


@dataclass(frozen=True)
class Priced:
    scored_price: Decimal
    taker_fee_order: Decimal
    taker_fee: Decimal              # per contract
    maker_fee_assumed: Decimal      # per contract: 50% of the taker fee (audit J2), labelled an assumption
    naive_p: Decimal
    fair_p: float
    fair_se: float | None
    rel_cost: float
    pairs: int
    measured_pairs: int
    prior_pairs: int
    combo_status: str
    combo_reason: str | None

    @property
    def measured_share(self) -> float:
        return 1.0 if self.pairs == 0 else self.measured_pairs / self.pairs


def price_ticket(legs: Sequence[Cand], t: cr.RhoTable, *, draws: int = cr.MC_DRAWS,
                 draws_max: int = cr.MC_DRAWS_MAX) -> Priced:
    scored = math.prod((c.ask for c in legs), start=Decimal(1))
    fee_order = taker_fee_order(scored)
    fee = fee_order / NOMINAL_CONTRACTS
    q = combo.price([leg_in(c) for c in legs], t, draws=draws, draws_max=draws_max)
    pairs = [p for b in q.blocks for p in b.pairs]
    rel = float(scored) / q.fair_p - 1.0 if q.fair_p > 0 else math.inf
    return Priced(
        scored_price=scored, taker_fee_order=fee_order, taker_fee=fee, maker_fee_assumed=fee * MAKER_SHARE,
        naive_p=math.prod((side_mid(c) for c in legs), start=Decimal(1)), fair_p=q.fair_p, fair_se=q.se,
        rel_cost=rel, pairs=len(pairs), measured_pairs=sum(1 for p in pairs if p["cls"] == MEASURED),
        prior_pairs=sum(1 for p in pairs if p["cls"] in ("prior", "borrowed")),
        combo_status=q.combo_status, combo_reason=q.combo_reason)


# --------------------------------------------------------------------------- reasons
def _num(d: Decimal, places: int) -> str:
    return str(d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN))


def _cents(ask: Decimal) -> str:
    s = f"{ask * 100:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


#: A prop's stat in words, after its Kalshi title (the title names the player
#: and the line, not the stat). Unknown stats are written as the board has them.
STAT_WORDS: Mapping[str, str] = {
    "points": "pts", "rebounds": "reb", "assists": "ast", "threes": "threes", "steals": "stl", "blocks": "blk",
    "combined points, rebounds, and assists": "pts+reb+ast", "combined steals and blocks": "stl+blk",
    "double double": "double-double", "triple double": "triple-double",
}


def label_of(m: pl.Market) -> str:
    """The leg in words (owner, Nov 20): the Kalshi title, the stat for a prop
    (its title does not say it), "wins" for a winner market, then the game's
    teams in parentheses. The ticker when the row has no title; the ticker
    stays the key in the payload."""
    title = m.label.strip() if m.label and m.label.strip() else None
    if title is None:
        return m.market_ticker
    if m.market_kind == "prop" and m.stat:
        title = f"{title} {STAT_WORDS.get(m.stat, m.stat)}"
    elif m.market_kind == "game_line" and m.stat == "winner":
        title = f"{title} wins"
    game = (m.game_title or "").strip()
    return f"{title} ({game})" if game else title


def leg_reasons(legs: Sequence[Cand], scope: Sequence[Cand], why: Mapping[tuple[str, str], str]) -> list[str]:
    """§3's one line per leg, in the ticket's order:
    `<side> <label>: <tier> consensus p <p>, ask <ask>c, ask/p <ratio>, ranked <n> of <N>;
    <k> ranked higher were not taken (<rule>: <count>, ...)`."""
    rank = {c.key: i for i, c in enumerate(scope, 1)}
    held = {c.key for c in legs}
    out = []
    for c in legs:
        n = rank[c.key]
        higher = [x for x in scope[: n - 1] if x.key not in held]
        counts = Counter(why.get(x.key, RULE_LABELS[5]) for x in higher)
        line = (f"{c.leg.side.upper()} {label_of(c.m)}: {c.m.tier} consensus p {_num(c.p, 3)}, "
                f"ask {_cents(c.ask)}¢, ask÷p {_num(c.ratio, 3)}, ranked {n} of {len(scope)}; "
                f"{len(higher)} ranked higher were not taken")
        if higher:
            line += " (" + ", ".join(f"{r}: {counts[r]}" for r in REASON_ORDER if counts[r]) + ")"
        out.append(line)
    return out


# --------------------------------------------------------------------------- the search
@dataclass(frozen=True)
class Ticket:
    tier: str
    agent: str
    scope: str                      # sport | cross_sport
    sport: str | None               # None on a cross-sport ticket
    legs: list[Cand]
    priced: Priced
    leg_reasons: list[str]
    edge_legs: list[str]            # "<ticker>:<side>"
    rho_version: str | None
    rho_sha256: str
    fill_note: str | None
    expected_quote: None = None     # M1 not frozen: absent, shown EXPECTED_QUOTE_PENDING, never scored
    rule_version: str = RULE_VERSION

    @property
    def tickers(self) -> list[str]:
        return sorted(c.m.market_ticker for c in self.legs)

    def payload(self) -> dict[str, Any]:
        """§4's fields as the commitment's payload will hold them (4.4 adds fetch times' context)."""
        p = self.priced
        return {
            "rule_version": self.rule_version, "tier": self.tier, "scope": self.scope, "sport": self.sport,
            "scored_price": str(p.scored_price), "taker_fee": str(p.taker_fee),
            "taker_fee_order": str(p.taker_fee_order), "contracts": NOMINAL_CONTRACTS,
            "maker_fee_assumed": str(p.maker_fee_assumed), "maker_fee_note": "assumption: 50% of the taker fee",
            "fair_p": p.fair_p, "fair_se": p.fair_se, "rho_version": self.rho_version, "rho_sha256": self.rho_sha256,
            "naive_p": str(p.naive_p), "expected_quote": None, "expected_quote_note": EXPECTED_QUOTE_PENDING,
            "rel_cost_over_fair": p.rel_cost, "pairs": p.pairs, "measured_pairs": p.measured_pairs,
            "prior_pairs": p.prior_pairs, "edge_legs": self.edge_legs, "combo_status": p.combo_status,
            "combo_reason": p.combo_reason, "leg_reasons": self.leg_reasons, "fill_note": self.fill_note,
            "legs": [{"market_ticker": c.m.market_ticker, "side": c.leg.side, "event_ticker": c.m.event_ticker,
                      "game_ticker": c.m.game_ticker, "sport": c.m.sport, "tier": c.m.tier, "p": str(c.p),
                      "ask": str(c.ask), "mid": str(side_mid(c)), "fetched_at": c.leg.quote.at.isoformat()  # type: ignore[union-attr]
                      } for c in self.legs],
        }


@dataclass(frozen=True)
class Result:
    """One tier's (or the cross-sport Lotto's) answer for one scope in one slot."""

    tier: str
    scope: str
    sport: str | None
    candidates: int
    ticket: Ticket | None
    binding: str | None             # why the slot is empty
    tried: dict[int, str]           # leg count -> "rel <x>" or the reason it built nothing
    #: Measurement only (never an input): seconds pricing each leg count's
    #: ticket (the copula, Monte Carlo past two latents a game), and the whole search.
    price_seconds: dict[int, float] = field(default_factory=dict)
    seconds: float = 0.0


def _choose(priced: list[tuple[Build, Priced]]) -> tuple[Build, Priced]:
    return min(priced, key=lambda bp: (bp[1].rel_cost, -bp[1].measured_share, len(bp[0].legs),
                                        sorted(c.m.market_ticker for c in bp[0].legs)))


REASON_ORDER = (*RULE_LABELS.values(), REPLACED)


def _binding(skips: Counter) -> str:
    """The empty slot's binding rule: the rule that skipped the most
    candidates at the tier's smallest leg count (ties: the lower rule)."""
    if not skips:
        return "no candidates"
    top = max(skips.values())
    return min((r for r, k in skips.items() if k == top), key=REASON_ORDER.index)


def search(tier: str, cands: Sequence[Cand], t: cr.RhoTable, *, sport: str | None, cross_sport: bool = False,
           spans: Mapping[str, Span] | None = None, draws: int = cr.MC_DRAWS,
           draws_max: int = cr.MC_DRAWS_MAX) -> Result:
    """§3's deterministic search for one tier over one scope's candidates
    (already in §3's order): a greedy ticket per leg count, each priced, the
    lowest relative cost over fair wins (ties: more measured pairs, fewer
    legs, the first sorted ticker list)."""
    t0 = time.perf_counter()
    agent, lo, hi = TIERS[tier]
    scope = "cross_sport" if cross_sport else "sport"
    tried: dict[int, str] = {}
    timing: dict[int, float] = {}
    built: list[tuple[Build, Priced]] = []
    first_skips: Counter | None = None
    if len(cands) < lo:
        return Result(tier, scope, sport, len(cands), None, f"rules 1-4: {len(cands)} candidates < {lo}", tried,
                      timing, time.perf_counter() - t0)
    for n in range(lo, hi + 1):
        b: Build | None = greedy(cands, n, t)
        if first_skips is None:
            first_skips = Counter(b.why.values())  # type: ignore[union-attr]
        if len(b.legs) < n:  # type: ignore[union-attr]
            tried[n] = f"short: {len(b.legs)} legs"  # type: ignore[union-attr]
            continue
        if cross_sport:
            b = replace_for_second_sport(b, cands, n, t)  # type: ignore[arg-type]
            if b is None:
                tried[n] = "no second sport"
                continue
        tp = time.perf_counter()
        pr = price_ticket(b.legs, t, draws=draws, draws_max=draws_max)
        timing[n] = time.perf_counter() - tp
        tried[n] = f"rel {pr.rel_cost:.6f}"
        built.append((b, pr))
    if not built:
        why = "no second sport" if cross_sport and any(v == "no second sport" for v in tried.values()) \
            else _binding(first_skips or Counter())
        return Result(tier, scope, sport, len(cands), None, why, tried, timing, time.perf_counter() - t0)
    b, pr = _choose(built)
    reasons = leg_reasons(b.legs, cands, b.why)
    edges = [f"{c.m.market_ticker}:{c.leg.side}" for c in b.legs
             if is_edge_leg(c, (spans or {}).get(c.m.market_ticker))]
    ticket = Ticket(tier, agent, scope, None if cross_sport else sport, list(b.legs), pr, reasons, edges,
                    t.data.get("version"), t.sha256, FILL_NOTE if cross_sport else None)
    return Result(tier, scope, sport, len(cands), ticket, None, tried, timing, time.perf_counter() - t0)


@dataclass(frozen=True)
class Slot:
    results: list[Result]           # per tier per sport, then the cross-sport Lotto
    no_p: int


def slot(passed: Sequence[pl.Leg], t: cr.RhoTable, *, spans: Mapping[str, Span] | None = None,
         draws: int = cr.MC_DRAWS, draws_max: int = cr.MC_DRAWS_MAX) -> Slot:
    """Every tier, at most one ticket per sport (§2), plus Lotto's one
    cross-sport ticket over every sport's candidates pooled."""
    cands, no_p = candidates(passed)
    sports = sorted({c.m.sport for c in cands})
    results = []
    for tier in TIERS:
        for s in sports:
            mine = [c for c in cands if c.m.sport == s]
            results.append(search(tier, mine, t, sport=s, spans=spans, draws=draws, draws_max=draws_max))
    results.append(search("lotto", cands, t, sport=None, cross_sport=True, spans=spans, draws=draws,
                          draws_max=draws_max))
    return Slot(results, no_p)
