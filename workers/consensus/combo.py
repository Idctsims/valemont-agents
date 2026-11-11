"""Price a set of board legs as one parlay (Chat 8 Phase 3; docs/correlation.md).

    legs -> per-game blocks -> each block's joint (correlation.py) -> the
    product over games (games are independent: owner, Nov 11)

plus the naive product, Kalshi's naive cost (each leg's ask + taker fee,
multiplied) and the Kalshi combo status. Pure: the caller (combo_job.py)
reads the legs' board rows and consensus p, and writes the quote.

Legs on one latent are merged before the copula (owner decision 4):
one player's rungs of one stat (its protected twin included), one series'
spread rungs (both clubs: the canonical club's margin), total rungs, one
club's team-total rungs. Each leg is a threshold on that latent; the merged
event is an interval whose probability comes from the legs' own prices:
same direction, the harder rung (rho = 1); opposite directions, exactly
p_low - p_high; a pair that cannot both hit is refused.

Refused, never priced: a market twice, two outcomes of a mutually exclusive
event (db/073's flag), rungs that cannot both hit, one player's stat beside a
sum that holds it, a scalar market, a leg off the board or with no price, a
game already started.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Mapping, Sequence

from consensus import correlation as cr

#: A period line (1H, 2Q, 3P, ...) right before the line's kind: its own
#: latent family, never a table row.
_PERIOD: Final = re.compile(r"(?:[1-4]Q|[12]H|[1-9]P)(?:SPREAD|TOTAL|TEAMTOTAL|BTTS)?$")
_CODE: Final = re.compile(r"^([A-Z]{1,6})\d*$")


@dataclass(frozen=True)
class LegIn:
    """One requested leg with its board row and consensus (combo_job reads
    them through leg_consensus_for and the board)."""

    market_ticker: str
    side: str                      # yes | no
    event_ticker: str
    game_ticker: str
    series_ticker: str
    sport: str | None
    league: str | None
    market_kind: str
    payout: str
    subject: str | None
    stat: str | None
    line: float | None
    label: str | None
    team: str | None
    tier: str
    p_yes: float | None            # the consensus p of YES
    yes_ask: float | None
    no_ask: float | None
    fee_yes: float | None
    fee_no: float | None
    stale: bool
    mutually_exclusive: bool | None
    combo_eligible: bool
    combo_yes_only: bool
    combo_size_max: int | None
    starts_at: datetime | None

    @property
    def p(self) -> float:
        assert self.p_yes is not None
        return self.p_yes if self.side == "yes" else 1.0 - self.p_yes

    @property
    def cost(self) -> float | None:
        ask, fee = (self.yes_ask, self.fee_yes) if self.side == "yes" else (self.no_ask, self.fee_no)
        if ask is None or fee is None or not 0.0 < ask < 1.0:
            return None
        return ask + fee


@dataclass
class _Latent:
    key: tuple[Any, ...]
    role: cr.Role
    # (threshold, P(latent > threshold), the leg hits above it?)
    cuts: list[tuple[float, float, bool, LegIn]] = field(default_factory=list)


def _period(series: str) -> bool:
    return bool(_PERIOD.search(series))


def _club(leg: LegIn) -> str | None:
    """The club of a team market: the board's `team` (db/076), else its
    ticker's code (a game line's ticker ends in the club's code; never used
    for props: "NONE" reads as "NO")."""
    if leg.team:
        return leg.team
    if leg.market_kind != "game_line":
        return None
    m = _CODE.match(leg.market_ticker.rsplit("-", 1)[-1])
    return m.group(1) if m else None


def _latent_key(leg: LegIn, t: cr.RhoTable, table: str | None) -> tuple[tuple[Any, ...], cr.Role, str | None]:
    """(the latent's key, its role, the leg's own club) for one leg."""
    s = leg.series_ticker
    if leg.market_kind == "prop":
        kind = t.kind_of(table, leg.league, s)
        player = f"{leg.team or '?'}|{(leg.subject or '').strip().lower()}"
        role = cr.Role(kind=kind, club=leg.team, player=player)
        if leg.line is None:
            return ("M", leg.market_ticker), role, leg.team
        base = s[:-4] if s.endswith("PROT") else s
        return ("P", player, kind or base), role, leg.team
    if leg.market_kind == "game_line":
        club = _club(leg)
        period = _period(s)
        stat = leg.stat
        if stat == "winner":
            role = cr.Role(line=None if period else "win", club=club)
            return ("W", s, club or leg.market_ticker), role, club
        if stat == "spread" and leg.line is not None and club:
            return ("S", s), cr.Role(line=None if period else "cover", club=club), club
        if stat == "total" and leg.line is not None:
            return ("T", s), cr.Role(line=None if period else "over"), None
        if stat == "team_total" and leg.line is not None and club:
            return ("TT", s, club), cr.Role(club=club), club
        return ("M", leg.market_ticker), cr.Role(), club
    if s.endswith("BTTS") and not _period(s):
        return ("M", leg.market_ticker), cr.Role(line="btts"), None
    return ("M", leg.market_ticker), cr.Role(), None


def _cut(leg: LegIn, key: tuple[Any, ...], canonical: str | None) -> tuple[float, float, bool]:
    """The leg as (threshold t, P(latent > t), hits above t?) on its latent,
    oriented so higher means the canonical club's margin / the stat over."""
    p_yes = leg.p_yes
    assert p_yes is not None
    if key[0] in ("P", "T", "TT") or (key[0] == "S" and leg.line is not None):
        line = float(leg.line)  # type: ignore[arg-type]
        if key[0] == "S" and _club(leg) != canonical:
            # "B wins by over L" is "the canonical margin is under -L".
            return -line, 1.0 - p_yes, leg.side == "no"
        return line, p_yes, leg.side == "yes"
    # A single market (a winner, a btts, anything else): over 0 is its YES.
    return 0.0, p_yes, leg.side == "yes"


def _interval(lat: _Latent) -> tuple[float, float, float]:
    """(lo, hi, probability) in z on the latent, from its legs' own prices."""
    above = [(t, s) for t, s, up, _ in lat.cuts if up]
    below = [(t, s) for t, s, up, _ in lat.cuts if not up]
    lo_t, s_lo = max(above, key=lambda x: (x[0], -x[1])) if above else (-math.inf, 1.0)
    hi_t, s_hi = min(below, key=lambda x: (x[0], -x[1])) if below else (math.inf, 0.0)
    labels = " and ".join(sorted(leg.label or leg.market_ticker for *_, leg in lat.cuts))
    if lo_t >= hi_t:
        raise cr.Refused(f"can't both hit: {labels}")
    q = s_lo - s_hi
    if q <= 0.0:
        raise cr.Refused(f"the rungs' prices disagree (no room between them): {labels}")
    return cr.Phi_inv(1.0 - s_lo), cr.Phi_inv(1.0 - s_hi), q


@dataclass(frozen=True)
class Block:
    game: str
    legs: list[LegIn]
    p: float
    se: float | None
    method: str
    draws: int
    shrink: float
    latents: int
    pairs: list[dict[str, Any]]
    nested: list[list[str]]

    @property
    def unmodeled(self) -> int:
        return sum(1 for x in self.pairs if x["cls"] == cr.UNMODELED)

    @property
    def priors(self) -> int:
        return sum(1 for x in self.pairs if x["cls"] in ("prior", "borrowed"))


@dataclass(frozen=True)
class Quote:
    naive_p: float
    fair_p: float
    se: float | None
    blocks: list[Block]
    kalshi_naive_cost: float | None
    combo_status: str
    combo_reason: str | None

    @property
    def unmodeled_pairs(self) -> int:
        return sum(b.unmodeled for b in self.blocks)

    @property
    def prior_pairs(self) -> int:
        return sum(b.priors for b in self.blocks)


def validate(requested: Sequence[Mapping[str, str]], rows: Mapping[str, LegIn], now: datetime) -> list[LegIn]:
    """The legs in request order, or Refused with the reason."""
    seen: dict[str, str] = {}
    for r in requested:
        t, side = r["market_ticker"], r["side"]
        if t in seen:
            raise cr.Refused(f"both sides of one market: {t}" if seen[t] != side else f"the same leg twice: {t}")
        seen[t] = side
    legs: list[LegIn] = []
    for r in requested:
        row = rows.get(r["market_ticker"])
        if row is None:
            raise cr.Refused(f"not on the board (gone, or never listed): {r['market_ticker']}")
        if row.payout != "binary":
            raise cr.Refused(f"scalar markets are never legs: {row.label or row.market_ticker}")
        if row.p_yes is None:
            raise cr.Refused(f"no price: {row.label or row.market_ticker}")
        if row.starts_at is not None and row.starts_at <= now:
            raise cr.Refused(f"the game has started: {row.label or row.market_ticker}")
        legs.append(LegIn(**{**row.__dict__, "side": r["side"]}))
    by_event: dict[str, list[LegIn]] = {}
    for leg in legs:
        by_event.setdefault(leg.event_ticker, []).append(leg)
    for ev, group in by_event.items():
        if len(group) > 1 and group[0].mutually_exclusive:
            raise cr.Refused(f"two outcomes of one result ({ev}): "
                             + " and ".join(g.label or g.market_ticker for g in group))
    return legs


def price_block(game: str, legs: Sequence[LegIn], t: cr.RhoTable, *, draws: int = cr.MC_DRAWS,
                draws_max: int = cr.MC_DRAWS_MAX) -> Block:
    """One game's legs: merge latents, build Sigma, the joint."""
    league, sport = legs[0].league, legs[0].sport
    table, borrowed = t.table_for(league, sport)
    no_draw = t.no_draw(league)
    latents: dict[tuple[Any, ...], _Latent] = {}
    for leg in legs:
        key, role, _club_of_leg = _latent_key(leg, t, table)
        lat = latents.setdefault(key, _Latent(key, role))
        lat.cuts.append((0.0, 0.0, True, leg))  # placeholder, oriented below
    # Spread latents: the canonical club is the smallest code among its legs.
    for lat in latents.values():
        canonical = min((_club(c[3]) or "" for c in lat.cuts), default=None) if lat.key[0] == "S" else None
        if canonical is not None:
            lat.role = cr.Role(line=lat.role.line, club=canonical)
        lat.cuts = [(*_cut(c[3], lat.key, canonical), c[3]) for c in lat.cuts]
        if lat.key[0] == "W" and len({c[3].market_ticker for c in lat.cuts}) > 1:
            raise cr.Refused("two outcomes of one result: "
                             + " and ".join(c[3].label or c[3].market_ticker for c in lat.cuts))
    # Two clubs' winners of one series are two outcomes of one result, flagged
    # mutually exclusive or not (a soccer draw is its own market: the same).
    winners: dict[str, list[LegIn]] = {}
    for lat in latents.values():
        if lat.key[0] == "W":
            winners.setdefault(lat.key[1], []).extend(c[3] for c in lat.cuts)
    for group in winners.values():
        if len({x.market_ticker for x in group}) > 1:
            raise cr.Refused("two outcomes of one result: " + " and ".join(x.label or x.market_ticker for x in group))
    order = list(latents.values())
    los, his, nested = [], [], []
    for lat in order:
        lo, hi, _ = _interval(lat)
        los.append(lo)
        his.append(hi)
        if len(lat.cuts) > 1:
            nested.append(sorted(c[3].market_ticker for c in lat.cuts))
    k = len(order)
    sigma = [[1.0 if i == j else 0.0 for j in range(k)] for i in range(k)]
    pairs: list[dict[str, Any]] = []
    for i in range(k):
        for j in range(i + 1, k):
            pr = cr.pair_rho(t, table, borrowed, no_draw, order[i].role, order[j].role)
            sigma[i][j] = sigma[j][i] = pr.rho
            pairs.append({"a": i, "b": j, "rho": round(pr.rho, 4), "cls": pr.cls, "key": pr.key})
    sigma, shrink = cr.psd_shrink(sigma)
    keys = [f"{leg.market_ticker}:{leg.side}" for leg in legs]
    j = cr.joint(los, his, sigma, keys, draws=draws, draws_max=draws_max)
    return Block(game, list(legs), j.p, j.se, j.method, j.draws, shrink, k, pairs, nested)


def combo_status(legs: Sequence[LegIn]) -> tuple[str, str | None]:
    """'fits' when Kalshi can take the legs as one combo, else
    ('build_manually', why): every leg's event combo-eligible, at most its
    event's combo_size_max legs from it (NULL: no stated cap), a YES-only
    event's legs on YES."""
    by_event: dict[str, list[LegIn]] = {}
    for leg in legs:
        by_event.setdefault(leg.event_ticker, []).append(leg)
    for ev, group in sorted(by_event.items()):
        g0 = group[0]
        if not g0.combo_eligible:
            return "build_manually", f"not combo-eligible on Kalshi: {g0.label or g0.market_ticker}"
        if g0.combo_size_max is not None and len(group) > g0.combo_size_max:
            return "build_manually", f"Kalshi takes at most {g0.combo_size_max} leg(s) from {ev}"
        if g0.combo_yes_only and any(x.side != "yes" for x in group):
            return "build_manually", f"YES only on Kalshi: {ev}"
    return "fits", None


def price(legs: Sequence[LegIn], t: cr.RhoTable, *, draws: int = cr.MC_DRAWS,
          draws_max: int = cr.MC_DRAWS_MAX) -> Quote:
    """Validated legs -> the quote. Games are independent: the fair p is the
    product of the blocks', the SE by the delta method over MC blocks."""
    by_game: dict[str, list[LegIn]] = {}
    for leg in legs:
        by_game.setdefault(leg.game_ticker, []).append(leg)
    blocks = [price_block(g, by_game[g], t, draws=draws, draws_max=draws_max) for g in sorted(by_game)]
    fair = math.prod(b.p for b in blocks)
    se: float | None = None
    mc = [b for b in blocks if b.se is not None]
    if mc:
        rel = math.sqrt(sum((b.se / b.p) ** 2 for b in mc if b.p > 0))  # type: ignore[operator]
        se = fair * rel if fair > 0 else max(b.se for b in mc)  # type: ignore[type-var]
    naive = math.prod(leg.p for leg in legs)
    costs = [leg.cost for leg in legs]
    cost = math.prod(costs) if all(c is not None for c in costs) else None  # type: ignore[misc]
    status, reason = combo_status(legs)
    return Quote(naive, fair, se, blocks, cost, status, reason)


def block_json(b: Block) -> dict[str, Any]:
    """A block as combo_quotes.blocks stores it (the sheet reads this)."""
    return {
        "game": b.game, "p": round(b.p, 8), "se": None if b.se is None else round(b.se, 8), "method": b.method,
        "draws": b.draws, "shrink": round(b.shrink, 6), "latents": b.latents, "nested": b.nested,
        "pairs": b.pairs,
        "legs": [{"t": x.market_ticker, "side": x.side, "label": x.label, "p": round(x.p, 6), "tier": x.tier,
                  "stale": x.stale, "cost": None if x.cost is None else round(x.cost, 6)} for x in b.legs],
    }
