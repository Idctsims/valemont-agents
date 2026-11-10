"""Matching book prices to Kalshi markets, and the outside-line tiers
(Chat 8 Phase 2; owner decisions, Nov 9). Pure: no I/O, no SQL.

    Odds API event  -> sports_events row  by team names (ODDS_NAMES aliases
                       the few the Odds API spells differently) and start
                       within 6 h; a miss is counted, never guessed.
    Kalshi market   -> its game           kalshi_board_events.sports_event_id
    Kalshi market   -> a book quote       EXACT equivalence only:
        moneyline  KX*GAME -HOU            book h2h, Houston (soccer: 3 ways)
        spread     KX*SPREAD -HOU5 (4.5)   book spread, Houston at -4.5
                                           ("wins by over 4.5")
        total      KX*TOTAL (L)            book total, Over L
        props      KXNFLREC/RECYDS/RSHYDS  book player Over L ("X+" = L + 0.5)
      A Kalshi rung with no exact book line stays kalshi tier: no rung
      interpolation without a pre-registration (owner, Nov 9).
    The side of a team market is its ticker's code (the last segment, digits
    dropped) against the game's two ESPN codes through sports_team_aliases.

    tier books : >= 2 Odds API books at the exact line; p_books = the median
                 of each book's POWER-de-vigged p (multiplicative's median
                 kept as a comparison).
    tier espn  : else ESPN's one book (DraftKings) at the exact line.
    The blend, the fees and the edges are SQL's (db/073 consensus_leg), the
    one implementation every reader shares.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from consensus import devig
from consensus.espn_odds import EspnOdds
from sports.kalshi_map import fold, kalshi_code

#: The Odds API's team names that differ from ESPN's (Odds API -> ESPN).
#: Add a line when a run's miss count names one; tests/test_consensus.py.
ODDS_NAMES: Mapping[str, str] = {
    "Los Angeles Clippers": "LA Clippers",
}
#: Full-game series per league: (moneyline, spread, total).
GAME_SERIES: Mapping[str, tuple[str, str, str]] = {
    "nfl": ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL"),
    "nba": ("KXNBAGAME", "KXNBASPREAD", "KXNBATOTAL"),
    "cfb": ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL"),
    "mcbb": ("KXNCAAMBGAME", "KXNCAAMBSPREAD", "KXNCAAMBTOTAL"),
    "nhl": ("KXNHLGAME", "KXNHLSPREAD", "KXNHLTOTAL"),
    "epl": ("KXEPLGAME", "KXEPLSPREAD", "KXEPLTOTAL"),
    "ucl": ("KXUCLGAME", "KXUCLSPREAD", "KXUCLTOTAL"),
}
#: NFL props pulled per game on NFL game days (owner, Nov 9: Option B).
PROP_SERIES: Mapping[str, str] = {
    "KXNFLREC": "player_receptions",
    "KXNFLRECYDS": "player_reception_yds",
    "KXNFLRSHYDS": "player_rush_yds",
}
#: book_lines' short market names.
SHORT: Mapping[str, str] = {
    "h2h": "ml", "spreads": "spread", "totals": "total", "player_receptions": "rec",
    "player_reception_yds": "rec_yds", "player_rush_yds": "rush_yds", "player_anytime_td": "anytime_td",
}
MIN_BOOKS = 2
MATCH_WINDOW = timedelta(hours=6)


# ------------------------------------------------------------------ inputs

@dataclass(frozen=True)
class Game:
    """A board game linked to sports_events (the ledger's read)."""

    sports_event_id: int
    league: str
    espn_id: str
    starts_at: datetime
    home: str
    away: str
    home_abbr: str | None
    away_abbr: str | None


@dataclass(frozen=True)
class Market:
    """A binary board market of a series the consensus prices."""

    market_ticker: str
    event_ticker: str
    series: str
    sports_event_id: int
    subject: str | None
    line: float | None


@dataclass
class BookPrices:
    """One game's prices from the Odds API, by book. Sides are 'home',
    'away', 'draw' (soccer); props key on the folded player name."""

    ml: dict[str, dict[str, int]] = field(default_factory=dict)
    #: book -> [(home point, home price, away price)]
    spread: dict[str, list[tuple[float, int, int]]] = field(default_factory=dict)
    #: book -> [(point, over price, under price)]
    total: dict[str, list[tuple[float, int, int]]] = field(default_factory=dict)
    #: market -> player -> book -> [(point, over price, under price)]
    props: dict[str, dict[str, dict[str, list[tuple[float, int, int | None]]]]] = field(default_factory=dict)
    #: The display name per folded player name (for book_lines).
    names: dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------ events

def espn_name(odds_name: str) -> str:
    return ODDS_NAMES.get(odds_name, odds_name)


@dataclass
class EventMatch:
    by_game: dict[int, dict[str, Any]] = field(default_factory=dict)
    #: A team name no sports_events row of the league has: an ODDS_NAMES gap
    #: (the run fails loud on these).
    misses: list[str] = field(default_factory=list)
    #: Both teams known, but no linked board game within 6 h (not listed on
    #: Kalshi yet, or not linked): counted, not a failure.
    unboarded: list[str] = field(default_factory=list)
    #: Odds API events outside the window (counted, not misses).
    outside: int = 0


def match_events(events: Iterable[Mapping[str, Any]], games: Sequence[Game], since: datetime,
                 until: datetime, known: frozenset[str] | None = None) -> EventMatch:
    """Odds API events starting in [since, until) -> board games. An event in
    the window with no game of the same two teams within 6 h is unboarded
    when both names are `known` (folded sports_events names of the league),
    else a miss."""
    out = EventMatch()
    by_pair: dict[frozenset[str], list[Game]] = {}
    for g in games:
        by_pair.setdefault(frozenset({fold(g.home), fold(g.away)}), []).append(g)
    for ev in events:
        try:
            start = datetime.fromisoformat(str(ev["commence_time"]).replace("Z", "+00:00"))
            home, away = espn_name(ev["home_team"]), espn_name(ev["away_team"])
        except (KeyError, TypeError, ValueError):
            out.misses.append(f"unreadable event {str(ev.get('id'))[:40]}")
            continue
        if not (since <= start < until):
            out.outside += 1
            continue
        cands = [g for g in by_pair.get(frozenset({fold(home), fold(away)}), [])
                 if abs(g.starts_at - start) <= MATCH_WINDOW]
        if len(cands) != 1:
            label = f"{away} at {home} {start:%m-%d %H:%M}Z"
            unknown = [n for n in (home, away) if known is not None and fold(n) not in known]
            if unknown:
                out.misses.append(f"{label}: unknown name {', '.join(unknown)}")
            elif len(cands) > 1:
                out.misses.append(f"{label}: {len(cands)} board games")
            else:
                out.unboarded.append(label)
            continue
        out.by_game[cands[0].sports_event_id] = dict(ev) | {"_home": home, "_away": away}
    return out


# ------------------------------------------------------------------ prices

def _side(name: str, home: str, away: str) -> str | None:
    n = fold(espn_name(name))
    if n == fold(home):
        return "home"
    if n == fold(away):
        return "away"
    if n in ("draw", "tie"):
        return "draw"
    return None


def parse_event(ev: Mapping[str, Any], into: BookPrices | None = None) -> BookPrices:
    """One Odds API event (bulk or event-odds body) into BookPrices. An
    outcome it cannot place is skipped (books list odd extras)."""
    bp = into or BookPrices()
    home, away = ev.get("_home") or espn_name(ev["home_team"]), ev.get("_away") or espn_name(ev["away_team"])
    for b in ev.get("bookmakers") or []:
        book = b.get("key")
        if not isinstance(book, str):
            continue
        for m in b.get("markets") or []:
            key, outs = m.get("key"), m.get("outcomes") or []
            if key == "h2h":
                sides = {s: o.get("price") for o in outs if (s := _side(str(o.get("name")), home, away))}
                if all(isinstance(v, int) for v in sides.values()) and {"home", "away"} <= set(sides):
                    bp.ml[book] = sides
            elif key == "spreads":
                pts = {s: (o.get("point"), o.get("price")) for o in outs
                       if (s := _side(str(o.get("name")), home, away)) in ("home", "away")}
                if set(pts) == {"home", "away"} and pts["home"][0] is not None \
                        and float(pts["home"][0]) == -float(pts["away"][0]):
                    bp.spread.setdefault(book, []).append(
                        (float(pts["home"][0]), int(pts["home"][1]), int(pts["away"][1])))
            elif key == "totals":
                ov = [o for o in outs if o.get("name") == "Over"]
                un = [o for o in outs if o.get("name") == "Under"]
                for o in ov:
                    u = next((x for x in un if x.get("point") == o.get("point")), None)
                    if u is not None and o.get("point") is not None:
                        bp.total.setdefault(book, []).append((float(o["point"]), int(o["price"]), int(u["price"])))
            elif key in PROP_SERIES.values() or key == "player_anytime_td":
                by: dict[tuple[str, float | None], dict[str, int]] = {}
                for o in outs:
                    who = o.get("description")
                    if not isinstance(who, str) or not isinstance(o.get("price"), int):
                        continue
                    k = (player_key(who, home, away), None if o.get("point") is None else float(o["point"]))
                    bp.names.setdefault(k[0], who)
                    by.setdefault(k, {})[str(o.get("name"))] = o["price"]
                for (who, pt), sides in by.items():
                    if key == "player_anytime_td" and "Yes" in sides:
                        # Yes-only (owner, Nov 9): stored, never de-vigged.
                        bp.props.setdefault(key, {}).setdefault(who, {}).setdefault(book, []).append(
                            (0.5, sides["Yes"], sides.get("No")))
                    elif pt is not None and "Over" in sides and "Under" in sides:
                        bp.props.setdefault(key, {}).setdefault(who, {}).setdefault(book, []).append(
                            (pt, sides["Over"], sides["Under"]))
    return bp


_DST = re.compile(r"\s*(d/st|dst|defense)\s*$", re.I)


def player_key(name: str, home: str = "", away: str = "") -> str:
    """A player's folded name, suffixes dropped ('James Cook III' ->
    'james cook'). A team defense keys on its team: Kalshi's 'BUF Bills D/ST'
    and a book's 'Buffalo Bills D/ST' are both 'dst bills'."""
    if _DST.search(name):
        words = fold(_DST.sub("", name)).split()
        return "dst " + (words[-1] if words else "")
    words = [w for w in fold(name).split() if w not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(words)


# ------------------------------------------------------------------ tiers

@dataclass(frozen=True)
class Priced:
    """One book's view of one Kalshi market: p of the market's YES side."""

    book: str
    p_power: float
    p_mult: float
    prices: tuple[int, ...]


def _two_way(a: int, b: int) -> tuple[float, float]:
    odds = [devig.american_to_decimal(a), devig.american_to_decimal(b)]
    return devig.power(odds)[0], devig.multiplicative(odds)[0]


def team_side(m: Market, g: Game, aliases: Mapping[tuple[str, str], str]) -> str | None:
    """'home' | 'away' | 'draw' from the market ticker's last segment."""
    code = re.sub(r"\d+$", "", m.market_ticker.rsplit("-", 1)[-1])
    if code == "TIE":
        return "draw"
    if g.home_abbr and code == kalshi_code(g.league, g.home_abbr, aliases):
        return "home"
    if g.away_abbr and code == kalshi_code(g.league, g.away_abbr, aliases):
        return "away"
    return None


def kind_of(series: str, league: str) -> str | None:
    ml, sp, tot = GAME_SERIES.get(league, ("", "", ""))
    return {ml: "ml", sp: "spread", tot: "total"}.get(series) or (
        "prop" if series in PROP_SERIES else None)


def book_views(m: Market, g: Game, bp: BookPrices, aliases: Mapping[tuple[str, str], str]) -> list[Priced]:
    """Every book's de-vigged p for the market's YES, at the EXACT line only."""
    kind = kind_of(m.series, g.league)
    out: list[Priced] = []
    if kind in ("ml", "spread"):
        side = team_side(m, g, aliases)
        if side is None:
            return out
        if kind == "ml":
            for book, sides in bp.ml.items():
                outcomes = ["home", "away"] + (["draw"] if "draw" in sides else [])
                if side not in sides:
                    continue
                odds = [devig.american_to_decimal(sides[o]) for o in outcomes]
                i = outcomes.index(side)
                out.append(Priced(book, devig.power(odds)[i], devig.multiplicative(odds)[i],
                                  tuple(sides[o] for o in outcomes)))
        else:
            if side == "draw" or m.line is None:
                return out
            # "wins by over L": the side at -L; home point = -L, or L for the away side.
            want = -m.line if side == "home" else m.line
            for book, rows in bp.spread.items():
                for hp, hprice, aprice in rows:
                    if hp == want:
                        a, b = (hprice, aprice) if side == "home" else (aprice, hprice)
                        pp, pm = _two_way(a, b)
                        out.append(Priced(book, pp, pm, (a, b)))
                        break
    elif kind == "total" and m.line is not None:
        for book, rows in bp.total.items():
            for pt, over, under in rows:
                if pt == m.line:
                    pp, pm = _two_way(over, under)
                    out.append(Priced(book, pp, pm, (over, under)))
                    break
    elif kind == "prop" and m.line is not None and m.subject:
        who = player_key(m.subject, g.home, g.away)
        for book, rows in bp.props.get(PROP_SERIES[m.series], {}).get(who, {}).items():
            for pt, over, under in rows:
                if pt == m.line and under is not None:
                    pp, pm = _two_way(over, under)
                    out.append(Priced(book, pp, pm, (over, under)))
                    break
    return out


def espn_view(m: Market, g: Game, e: EspnOdds | None, aliases: Mapping[tuple[str, str], str]) -> Priced | None:
    """ESPN's one book for the market, at the exact line, or None."""
    if e is None:
        return None
    kind = kind_of(m.series, g.league)
    try:
        if kind == "ml":
            side = team_side(m, g, aliases)
            sides = {"home": e.ml_home, "away": e.ml_away} | ({"draw": e.ml_draw} if e.ml_draw else {})
            if side is None or any(v is None for v in sides.values()) or side not in sides:
                return None
            outcomes = list(sides)
            odds = [devig.american_to_decimal(sides[o]) for o in outcomes]
            i = outcomes.index(side)
            return Priced("espn_dk", devig.power(odds)[i], devig.multiplicative(odds)[i],
                          tuple(int(sides[o]) for o in outcomes))  # type: ignore[arg-type]
        if kind == "spread" and m.line is not None and e.spread_home is not None:
            side = team_side(m, g, aliases)
            if side not in ("home", "away") or e.spread_home_price is None or e.spread_away_price is None:
                return None
            if e.spread_home != (-m.line if side == "home" else m.line):
                return None
            a, b = ((e.spread_home_price, e.spread_away_price) if side == "home"
                    else (e.spread_away_price, e.spread_home_price))
            pp, pm = _two_way(a, b)
            return Priced("espn_dk", pp, pm, (a, b))
        if kind == "total" and m.line is not None and e.total == m.line \
                and e.over_price is not None and e.under_price is not None:
            pp, pm = _two_way(e.over_price, e.under_price)
            return Priced("espn_dk", pp, pm, (e.over_price, e.under_price))
    except devig.DevigError:
        return None
    return None


def r6(x: float) -> float:
    return round(x, 6)


def consensus_row(m: Market, views: Sequence[Priced], espn: Priced | None) -> dict[str, Any] | None:
    """The leg_consensus row (db/073), or None: the market stays kalshi tier."""
    if len(views) >= MIN_BOOKS:
        return {"market_ticker": m.market_ticker, "tier": "books", "n_books": len(views),
                "p_books": r6(statistics.median(v.p_power for v in views)),
                "p_books_mult": r6(statistics.median(v.p_mult for v in views)),
                "p_espn": None if espn is None else r6(espn.p_power),
                "inputs": {"m": "power", "b": {v.book: round(v.p_power, 4) for v in views}}}
    if espn is not None:
        return {"market_ticker": m.market_ticker, "tier": "espn", "n_books": 1, "p_books": None,
                "p_books_mult": None, "p_espn": r6(espn.p_power),
                "inputs": {"m": "power", "src": "espn", "b": {"draftkings": round(espn.p_power, 4)},
                           "px": list(espn.prices)}}
    return None


# ------------------------------------------------------------------ main lines

def _main(rows: Sequence[tuple[float, int, int | None]]) -> tuple[float, int, int | None] | None:
    """A book's main line among several: its prices closest to even."""
    best, gap = None, 9.0
    for r in rows:
        if r[2] is None:
            continue
        try:
            p = _two_way(r[1], r[2])[0]
        except devig.DevigError:
            continue
        if abs(p - 0.5) < gap:
            best, gap = r, abs(p - 0.5)
    return best


def book_lines(g: Game, bp: BookPrices | None, e: EspnOdds | None, fetched_at: datetime,
               fetched_espn: datetime | None) -> list[dict[str, Any]]:
    """book_lines rows (db/073): one per game, market, subject and book, the
    main line only. Odds API main lines priced at >= 2 books (owner, Nov 9);
    ESPN's one book where it has the market."""
    base = {"sports_event_id": g.sports_event_id, "starts_at": g.starts_at.isoformat(), "league": g.league}
    out: list[dict[str, Any]] = []

    def add(source: str, at: datetime, market: str, subject: str | None, book: str, line: float | None,
            a: int, b: int | None, p: float | None) -> None:
        out.append(base | {"fetched_at": at.isoformat(), "source": source, "market": market, "subject": subject,
                           "book": book, "line": line, "price_a": a, "price_b": b,
                           "p_a": None if p is None or b is None else round(p, 6)})

    def p2(a: int, b: int | None) -> float | None:
        try:
            return None if b is None else _two_way(a, b)[0]
        except devig.DevigError:
            return None

    if bp is not None:
        if len(bp.ml) >= MIN_BOOKS:
            for book, sides in bp.ml.items():
                if "draw" in sides:
                    for s, name in (("home", g.home), ("away", g.away), ("draw", "Draw")):
                        add("o", fetched_at, "ml", name, book, None, sides[s], None, None)
                else:
                    add("o", fetched_at, "ml", g.home, book, None, sides["home"], sides["away"],
                        p2(sides["home"], sides["away"]))
        for mkt, by_book, subject in (("spread", bp.spread, g.home), ("total", bp.total, None)):
            mains = {book: _main(rows) for book, rows in by_book.items()}
            lines = [m[0] for m in mains.values() if m]
            for book, mrow in mains.items():
                if mrow and lines.count(mrow[0]) >= MIN_BOOKS:
                    add("o", fetched_at, mkt, subject, book, mrow[0], mrow[1], mrow[2], p2(mrow[1], mrow[2]))
        for key, players in bp.props.items():
            for who, by_book in players.items():
                if key == "player_anytime_td":
                    for book, rows in by_book.items():
                        add("o", fetched_at, SHORT[key], bp.names.get(who, who), book, None, rows[0][1], None, None)
                    continue
                mains = {book: _main(rows) for book, rows in by_book.items()}
                lines = [m[0] for m in mains.values() if m]
                for book, mrow in mains.items():
                    if mrow and lines.count(mrow[0]) >= MIN_BOOKS:
                        add("o", fetched_at, SHORT[key], bp.names.get(who, who), book, mrow[0], mrow[1], mrow[2],
                            p2(mrow[1], mrow[2]))
    if e is not None and fetched_espn is not None:
        if e.ml_home is not None and e.ml_away is not None:
            if e.ml_draw is not None:
                for name, a in ((g.home, e.ml_home), (g.away, e.ml_away), ("Draw", e.ml_draw)):
                    add("e", fetched_espn, "ml", name, "draftkings", None, a, None, None)
            else:
                add("e", fetched_espn, "ml", g.home, "draftkings", None, e.ml_home, e.ml_away,
                    p2(e.ml_home, e.ml_away))
        if e.spread_home is not None and e.spread_home_price is not None and e.spread_away_price is not None:
            add("e", fetched_espn, "spread", g.home, "draftkings", e.spread_home, e.spread_home_price,
                e.spread_away_price, p2(e.spread_home_price, e.spread_away_price))
        if e.total is not None and e.over_price is not None and e.under_price is not None:
            add("e", fetched_espn, "total", None, "draftkings", e.total, e.over_price, e.under_price,
                p2(e.over_price, e.under_price))
    return out
