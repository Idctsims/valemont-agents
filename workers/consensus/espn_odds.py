"""ESPN's odds: the one book (DraftKings) ESPN carries on its scoreboard,
`competitions[].odds[0]`, for the leagues that have it (Step 0, Nov 9: cfb,
nfl, nba, mcbb, nhl, epl, ucl; none for f1, pga, atp, wta, ufc, mls). The
`close` price is the current one before the game (owner, Nov 9). No I/O: the
consensus job hands it the scoreboard bodies sports/espn.py's client fetched.

A missing or reshaped field leaves that market out (ESPN drops a market it
has no line for); a game with no odds object is simply not priced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

#: Leagues whose scoreboard carries odds (Step 0, Nov 9).
ODDS_LEAGUES: frozenset[str] = frozenset({"cfb", "nfl", "nba", "mcbb", "nhl", "epl", "ucl"})
BOOK = "draftkings"


@dataclass(frozen=True)
class EspnOdds:
    """American prices, home/away as ESPN orders the competitors."""

    espn_id: str
    provider: str
    ml_home: int | None = None
    ml_away: int | None = None
    ml_draw: int | None = None
    #: The home side's spread (e.g. -3.5) and both prices.
    spread_home: float | None = None
    spread_home_price: int | None = None
    spread_away_price: int | None = None
    total: float | None = None
    over_price: int | None = None
    under_price: int | None = None


def price(v: Any) -> int | None:
    """'+160' -> 160, '-115' -> -115, 'EVEN' -> 100; anything else None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n = int(v)
    elif isinstance(v, str):
        s = v.strip().upper()
        if s in ("EVEN", "EV"):
            return 100
        if not re.fullmatch(r"[+-]?\d{3,6}", s):
            return None
        n = int(s)
    else:
        return None
    return n if abs(n) >= 100 else None


def line(v: Any) -> float | None:
    """'+3.5' -> 3.5, 'o45.5' -> 45.5, 'u45.5' -> 45.5, 'PK' -> 0.0."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    if s in ("pk", "pick", "even"):
        return 0.0
    m = re.fullmatch(r"[ou]?([+-]?\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def _close(side: Any, key: str) -> Any:
    if not isinstance(side, Mapping):
        return None
    c = side.get("close")
    return c.get(key) if isinstance(c, Mapping) else None


def parse(scoreboard: Any) -> dict[str, EspnOdds]:
    """espn_id -> its odds, for every competition with a DraftKings odds object."""
    out: dict[str, EspnOdds] = {}
    events = scoreboard.get("events") if isinstance(scoreboard, Mapping) else None
    for ev in events or []:
        if not isinstance(ev, Mapping) or not isinstance(ev.get("id"), str):
            continue
        for comp in ev.get("competitions") or []:
            if not isinstance(comp, Mapping):
                continue
            odds = [o for o in (comp.get("odds") or []) if isinstance(o, Mapping)]
            if not odds:
                continue
            o = odds[0]
            prov = o.get("provider")
            name = prov.get("name") if isinstance(prov, Mapping) else None
            if not isinstance(name, str) or name.lower().replace(" ", "") != BOOK:
                continue
            ml = o.get("moneyline") if isinstance(o.get("moneyline"), Mapping) else {}
            ps = o.get("pointSpread") if isinstance(o.get("pointSpread"), Mapping) else {}
            tot = o.get("total") if isinstance(o.get("total"), Mapping) else {}
            draw = o.get("drawOdds")
            over_line = line(_close(tot.get("over"), "line"))
            under_line = line(_close(tot.get("under"), "line"))
            out[ev["id"]] = EspnOdds(
                espn_id=ev["id"], provider=BOOK,
                ml_home=price(_close(ml.get("home"), "odds")), ml_away=price(_close(ml.get("away"), "odds")),
                ml_draw=price(draw.get("moneyLine")) if isinstance(draw, Mapping) else None,
                spread_home=line(_close(ps.get("home"), "line")),
                spread_home_price=price(_close(ps.get("home"), "odds")),
                spread_away_price=price(_close(ps.get("away"), "odds")),
                # Both sides must quote the same total, or the market is left out.
                total=over_line if over_line is not None and over_line == under_line else None,
                over_price=price(_close(tot.get("over"), "odds")),
                under_price=price(_close(tot.get("under"), "odds")))
    return out
