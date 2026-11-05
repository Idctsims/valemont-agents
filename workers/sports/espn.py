"""ESPN's site API (unofficial, no key): fetch, validate, parse into
SportsEvent rows. docs/sports_data.md is the contract this module keeps.

Schema-validated: every field the parser reads is checked for presence and
type. A missing or renamed required field raises EspnShapeError(league,
path) instead of becoming a NULL; optional fields (broadcasts, venue, a score
before the start, a rank, line scores) may be absent, but a present one of
the wrong type is still an error. An unfamiliar status NAME maps by its state
and is kept verbatim; an unfamiliar STATE is a shape error.

No SQL here (CLAUDE.md §6): rows go to core/ledger.sports_events_upsert.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

BASE = "https://site.api.espn.com/apis/site/v2/sports/"
TIMEOUT_S = 20
USER_AGENT = "valemont-worker"
RETRY_AFTER_S = 2.0

KINDS = ("game", "race_session", "golf_round", "bout", "match")
#: Owner, 2026-11-04: the followed golfers' positions while live.
FOLLOWED_GOLFERS = ("Scottie Scheffler", "Brooks Koepka")
GOLF_LEADERS = 5
F1_RESULTS = 10
TENNIS_SINGLES = ("mens-singles", "womens-singles")
#: F1 session types by ESPN abbreviation, as probed 2026-11-04 (FP1-3, Qual,
#: Race). Practice is skipped; anything else (a sprint session: its shape is
#: pending the first sprint weekend, docs/sports_data.md §8) fails loudly.
F1_PRACTICE = frozenset({"FP1", "FP2", "FP3"})
F1_STORED = frozenset({"Qual", "Race"})
#: Tennis's placeholder for an undecided player.
TBD_NAME = "TBD"

STATUSES = ("scheduled", "live", "delayed", "suspended", "final", "postponed", "canceled")
_DELAYS = frozenset({"STATUS_DELAYED", "STATUS_RAIN_DELAY"})


class EspnError(Exception):
    """A league's fetch or parse failed; the run carries on with the others."""

    def __init__(self, league: str, detail: str) -> None:
        super().__init__(f"{league}: {detail}")
        self.league = league


class EspnShapeError(EspnError):
    """A required field is missing, renamed or of the wrong type."""

    def __init__(self, league: str, path: str, detail: str) -> None:
        super().__init__(league, f"shape at {path}: {detail}")
        self.path = path


class EspnFetchError(EspnError):
    """HTTP failure, timeout or a body that is not JSON."""


@dataclass(frozen=True)
class League:
    code: str
    path: str
    params: Mapping[str, str]
    fetch_mode: str  # per_day | range
    kind: str
    owner_five: bool = False
    sort: int = 0
    #: The schedule pull's horizon in days (db/066: 14 for NFL and CFB).
    days_ahead: int = 7


@dataclass(frozen=True)
class SportsEvent:
    league: str
    kind: str
    espn_id: str
    event_name: str
    starts_at: datetime
    status: str
    espn_status: str
    parent_espn_id: str | None = None
    session: str | None = None
    time_tbd: bool = False
    home: str | None = None
    away: str | None = None
    participants: Any = None
    broadcast: tuple[str, ...] = ()
    national_tv: bool = False
    venue: str | None = None
    status_detail: str | None = None
    period: int | None = None
    clock: str | None = None
    score: Mapping[str, Any] | None = field(default=None)
    #: ESPN's competitions[].neutralSite (db/066), games only; None where
    #: ESPN does not say (soccer carries no flag).
    neutral_site: bool | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.league, self.espn_id)

    def row(self) -> dict[str, Any]:
        """The JSON object ledger.sports_events_upsert sends (one per row)."""
        d = asdict(self)
        d["starts_at"] = self.starts_at.astimezone(timezone.utc).isoformat()
        d["broadcast"] = list(self.broadcast)
        return d

    def content_hash(self) -> str:
        """sha256 of every stored field: equal hashes, nothing to write."""
        body = json.dumps(self.row(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ------------------------------------------------------------- validation

_MISSING = object()


def _get(obj: Any, key: str, types: type | tuple[type, ...], league: str, path: str, *, required: bool) -> Any:
    if not isinstance(obj, dict):
        raise EspnShapeError(league, path, f"expected an object, got {type(obj).__name__}")
    value = obj.get(key, _MISSING)
    if value is _MISSING or value is None:
        if required:
            raise EspnShapeError(league, f"{path}.{key}", "missing")
        return None
    # bool is an int in Python; never accept one where a number is meant.
    if isinstance(value, bool) and bool not in (types if isinstance(types, tuple) else (types,)):
        raise EspnShapeError(league, f"{path}.{key}", "is a boolean")
    if not isinstance(value, types):
        raise EspnShapeError(league, f"{path}.{key}", f"is {type(value).__name__}")
    return value


def req(obj: Any, key: str, types: type | tuple[type, ...], league: str, path: str) -> Any:
    return _get(obj, key, types, league, path, required=True)


def opt(obj: Any, key: str, types: type | tuple[type, ...], league: str, path: str) -> Any:
    return _get(obj, key, types, league, path, required=False)


def parse_time(value: str, league: str, path: str) -> datetime:
    """ESPN's '2026-11-05T00:00Z' (minutes) or with seconds; always UTC."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EspnShapeError(league, path, f"not a timestamp: {value[:40]!r}") from None
    if dt.tzinfo is None:
        raise EspnShapeError(league, path, "timestamp has no zone")
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------- status

@dataclass(frozen=True)
class Status:
    status: str
    espn: str
    state: str
    detail: str | None
    period: int | None
    clock: str | None


def map_status(name: str, state: str) -> str:
    """ESPN (name, state) -> ours (docs/sports_data.md §4). The caller has
    already refused a state other than pre/in/post."""
    if state == "pre":
        if name == "STATUS_POSTPONED":
            return "postponed"
        if name == "STATUS_CANCELED":
            return "canceled"
        return "delayed" if name in _DELAYS else "scheduled"
    if state == "in":
        return "delayed" if name in _DELAYS | {"STATUS_SUSPENDED"} else "live"
    if name == "STATUS_POSTPONED":
        return "postponed"
    if name in ("STATUS_CANCELED", "STATUS_ABANDONED"):
        return "canceled"
    if name == "STATUS_SUSPENDED":
        return "suspended"
    return "final"


def read_status(obj: Any, league: str, path: str) -> Status:
    st = req(obj, "status", dict, league, path)
    t = req(st, "type", dict, league, f"{path}.status")
    name = req(t, "name", str, league, f"{path}.status.type")
    state = req(t, "state", str, league, f"{path}.status.type")
    if state not in ("pre", "in", "post"):
        raise EspnShapeError(league, f"{path}.status.type.state", f"unknown state {state[:20]!r}")
    if not re.fullmatch(r"[A-Z0-9_]{1,60}", name):
        raise EspnShapeError(league, f"{path}.status.type.name", f"unexpected name {name[:40]!r}")
    period = opt(st, "period", int, league, f"{path}.status")
    clock = opt(st, "displayClock", str, league, f"{path}.status")
    detail = opt(t, "shortDetail", str, league, f"{path}.status.type")
    return Status(map_status(name, state), name, state, detail[:120] if detail else None,
                  period if period is None or period >= 0 else None, clock[:30] if clock else None)


# ------------------------------------------------------------ small parts

def broadcasts(comp: Any, league: str, path: str) -> tuple[tuple[str, ...], bool]:
    """Every broadcaster name in ESPN's order, deduplicated; national if any
    entry's market is 'national'."""
    names: list[str] = []
    national = False
    for i, b in enumerate(opt(comp, "broadcasts", list, league, path) or []):
        p = f"{path}.broadcasts[{i}]"
        market = opt(b, "market", str, league, p)
        national = national or market == "national"
        for j, n in enumerate(opt(b, "names", list, league, p) or []):
            if not isinstance(n, str):
                raise EspnShapeError(league, f"{p}.names[{j}]", f"is {type(n).__name__}")
            n = n.strip()
            if n and n not in names:
                names.append(n[:60])
    return tuple(names), national


def venue_name(obj: Any, key: str, league: str, path: str) -> str | None:
    v = opt(obj, key, dict, league, path)
    if v is None:
        return None
    name = opt(v, "fullName", str, league, f"{path}.{key}")
    return name[:200] if name else None


def number(s: str | None) -> int | str | None:
    if s is None:
        return None
    s = s.strip()
    return int(s) if re.fullmatch(r"-?\d+", s) else s


def _list(obj: Any, key: str, league: str, path: str) -> list[Any]:
    return req(obj, key, list, league, path)


# ------------------------------------------------------------ team games

#: Sports whose every game carries competitions[].neutralSite (checked in
#: every NFL, CFB, NBA, NCAAB and NHL fixture, 2026-11-04): a missing flag is
#: a shape failure there. Soccer carries none; MLB is unverified (off
#: season), so it is optional there and stored NULL when absent.
NEUTRAL_REQUIRED = frozenset({"football", "basketball", "hockey"})

def _team(cp: Any, league: str, path: str) -> tuple[str, dict[str, Any], int | str | None]:
    side = req(cp, "homeAway", str, league, path)
    if side not in ("home", "away"):
        raise EspnShapeError(league, f"{path}.homeAway", f"unexpected {side[:20]!r}")
    team = req(cp, "team", dict, league, path)
    info: dict[str, Any] = {
        "id": req(team, "id", str, league, f"{path}.team"),
        "name": req(team, "displayName", str, league, f"{path}.team")[:120],
    }
    abbr = opt(team, "abbreviation", str, league, f"{path}.team")
    if abbr:
        info["abbr"] = abbr[:12]
    conf = opt(team, "conferenceId", str, league, f"{path}.team")
    if conf:
        info["conference_id"] = conf
    rank = opt(cp, "curatedRank", dict, league, path)
    if rank is not None:
        current = opt(rank, "current", int, league, f"{path}.curatedRank")
        if current is not None and 0 < current < 99:
            info["rank"] = current
    return side, info, number(opt(cp, "score", str, league, path))


def parse_games(lg: League, events: Sequence[Any]) -> list[SportsEvent]:
    out: list[SportsEvent] = []
    for i, e in enumerate(events):
        p = f"events[{i}]"
        eid = req(e, "id", str, lg.code, p)
        name = req(e, "name", str, lg.code, p)
        comps = _list(e, "competitions", lg.code, p)
        if not comps:
            raise EspnShapeError(lg.code, f"{p}.competitions", "empty")
        cp = f"{p}.competitions[0]"
        c = comps[0]
        competitors = _list(c, "competitors", lg.code, cp)
        if len(competitors) != 2:
            raise EspnShapeError(lg.code, f"{cp}.competitors", f"{len(competitors)} competitors, not 2")
        sides: dict[str, tuple[dict[str, Any], int | str | None]] = {}
        for j, comp in enumerate(competitors):
            side, info, score = _team(comp, lg.code, f"{cp}.competitors[{j}]")
            sides[side] = (info, score)
        if set(sides) != {"home", "away"}:
            raise EspnShapeError(lg.code, f"{cp}.competitors", "not one home and one away")
        st = read_status(c, lg.code, cp)
        names, national = broadcasts(c, lg.code, cp)
        valid = opt(c, "timeValid", bool, lg.code, cp)
        neutral = (req if lg.path.split("/")[0] in NEUTRAL_REQUIRED else opt)(c, "neutralSite", bool, lg.code, cp)
        score = None
        if st.state != "pre" and sides["home"][1] is not None and sides["away"][1] is not None:
            score = {"home": sides["home"][1], "away": sides["away"][1]}
        out.append(SportsEvent(
            league=lg.code, kind="game", espn_id=eid, event_name=name[:200],
            starts_at=parse_time(req(c, "date", str, lg.code, cp), lg.code, f"{cp}.date"),
            time_tbd=valid is False, home=sides["home"][0]["name"], away=sides["away"][0]["name"],
            participants={"home": sides["home"][0], "away": sides["away"][0]},
            broadcast=names, national_tv=national, venue=venue_name(c, "venue", lg.code, cp),
            status=st.status, espn_status=st.espn, status_detail=st.detail, period=st.period,
            clock=st.clock, score=score, neutral_site=neutral))
    return out


# --------------------------------------------------------------------- F1

def parse_f1(lg: League, events: Sequence[Any]) -> list[SportsEvent]:
    out: list[SportsEvent] = []
    for i, e in enumerate(events):
        p = f"events[{i}]"
        eid = req(e, "id", str, lg.code, p)
        name = req(e, "name", str, lg.code, p)
        venue = venue_name(e, "circuit", lg.code, p)
        for k, c in enumerate(_list(e, "competitions", lg.code, p)):
            cp = f"{p}.competitions[{k}]"
            abbr = req(req(c, "type", dict, lg.code, cp), "abbreviation", str, lg.code, f"{cp}.type")
            if abbr in F1_PRACTICE:
                continue
            if abbr not in F1_STORED:
                raise EspnShapeError(lg.code, f"{cp}.type.abbreviation",
                                     f"unknown F1 session type {abbr[:20]!r} (sprint shape pending the first "
                                     "sprint weekend: docs/sports_data.md §8)")
            st = read_status(c, lg.code, cp)
            names, national = broadcasts(c, lg.code, cp)
            score = None
            if st.state != "pre":
                ranked = []
                for j, comp in enumerate(opt(c, "competitors", list, lg.code, cp) or []):
                    q = f"{cp}.competitors[{j}]"
                    order = req(comp, "order", int, lg.code, q)
                    driver = req(req(comp, "athlete", dict, lg.code, q), "displayName", str, lg.code, f"{q}.athlete")
                    ranked.append((order, driver[:80]))
                ranked.sort()
                score = {"results": [{"pos": o, "name": n} for o, n in ranked[:F1_RESULTS]]}
            out.append(SportsEvent(
                league=lg.code, kind="race_session", espn_id=req(c, "id", str, lg.code, cp), parent_espn_id=eid,
                event_name=name[:200], session=abbr[:60],
                starts_at=parse_time(req(c, "date", str, lg.code, cp), lg.code, f"{cp}.date"),
                time_tbd=opt(c, "timeValid", bool, lg.code, cp) is False, broadcast=names, national_tv=national,
                venue=venue, status=st.status, espn_status=st.espn, status_detail=st.detail, period=st.period,
                clock=st.clock, score=score))
    return out


# ------------------------------------------------------------------- golf

def _golf_standings(competitors: Sequence[Any], rnd: int, league: str, path: str) -> dict[str, Any]:
    players = []
    for j, comp in enumerate(competitors):
        q = f"{path}.competitors[{j}]"
        name = req(req(comp, "athlete", dict, league, q), "displayName", str, league, f"{q}.athlete")
        order = opt(comp, "order", int, league, q)
        to_par = opt(comp, "score", str, league, q)
        today = thru = None
        for k, ls in enumerate(opt(comp, "linescores", list, league, q) or []):
            lq = f"{q}.linescores[{k}]"
            if opt(ls, "period", int, league, lq) == rnd:
                today = opt(ls, "displayValue", str, league, lq)
                thru = len(opt(ls, "linescores", list, league, lq) or [])
        players.append({"name": name[:80], "order": order if order is not None else j + 1,
                        "to_par": to_par, "today": today, "thru": thru})
    players.sort(key=lambda x: x["order"])
    # Ties: the same total shares the first order among them, shown as "T<n>".
    groups: dict[str, list[dict[str, Any]]] = {}
    for pl in players:
        if pl["to_par"] is not None:
            groups.setdefault(pl["to_par"], []).append(pl)
    for pl in players:
        same = groups.get(pl["to_par"]) if pl["to_par"] is not None else None
        first = same[0]["order"] if same else pl["order"]
        pl["pos"] = f"T{first}" if same and len(same) > 1 else str(first)

    def entry(pl: dict[str, Any]) -> dict[str, Any]:
        return {k: pl[k] for k in ("name", "pos", "to_par", "today", "thru") if pl[k] is not None}

    followed = {n.lower() for n in FOLLOWED_GOLFERS}
    return {"round": rnd,
            "leaders": [entry(pl) for pl in players[:GOLF_LEADERS]],
            "followed": [entry(pl) for pl in players if pl["name"].lower() in followed]}


def parse_golf(lg: League, events: Sequence[Any]) -> list[SportsEvent]:
    """One row per round, derived from the tournament (docs/sports_data.md §3)."""
    out: list[SportsEvent] = []
    for i, e in enumerate(events):
        p = f"events[{i}]"
        eid = req(e, "id", str, lg.code, p)
        name = req(e, "name", str, lg.code, p)
        start = parse_time(req(e, "date", str, lg.code, p), lg.code, f"{p}.date")
        end_s = opt(e, "endDate", str, lg.code, p)
        end = parse_time(end_s, lg.code, f"{p}.endDate") if end_s else start + timedelta(days=3)
        rounds = max(1, min(5, (end.date() - start.date()).days + 1))
        comps = _list(e, "competitions", lg.code, p)
        if not comps:
            raise EspnShapeError(lg.code, f"{p}.competitions", "empty")
        cp = f"{p}.competitions[0]"
        c = comps[0]
        competitors = _list(c, "competitors", lg.code, cp)
        st = read_status(c, lg.code, cp)
        names, national = broadcasts(c, lg.code, cp)
        venue = venue_name(c, "venue", lg.code, cp)
        current = st.period or 0
        complete = "PLAY_COMPLETE" in st.espn
        standings = None
        if st.state != "pre" and current >= 1:
            standings = _golf_standings(competitors, current, lg.code, cp)
        for r in range(1, rounds + 1):
            if st.state == "in":
                if r < current or (r == current and complete):
                    status, espn = "final", "STATUS_FINAL"
                elif r == current:
                    status, espn = st.status, st.espn
                else:
                    status, espn = "scheduled", "STATUS_SCHEDULED"
            else:
                status, espn = st.status, st.espn
            out.append(SportsEvent(
                league=lg.code, kind="golf_round", espn_id=f"{eid}-r{r}", parent_espn_id=eid,
                event_name=name[:200], session=f"R{r}", starts_at=start + timedelta(days=r - 1), time_tbd=True,
                broadcast=names, national_tv=national, venue=venue, status=status, espn_status=espn,
                status_detail=st.detail if r == current else None, period=r,
                score=standings if r == current else None))
    return out


# -------------------------------------------------------------------- UFC

def parse_ufc(lg: League, events: Sequence[Any]) -> list[SportsEvent]:
    out: list[SportsEvent] = []
    for i, e in enumerate(events):
        p = f"events[{i}]"
        eid = req(e, "id", str, lg.code, p)
        name = req(e, "name", str, lg.code, p)
        for k, c in enumerate(_list(e, "competitions", lg.code, p)):
            cp = f"{p}.competitions[{k}]"
            st = read_status(c, lg.code, cp)
            fighters = []
            for j, comp in enumerate(_list(c, "competitors", lg.code, cp)):
                q = f"{cp}.competitors[{j}]"
                fighter = {"name": req(req(comp, "athlete", dict, lg.code, q), "displayName", str, lg.code,
                                       f"{q}.athlete")[:80]}
                won = opt(comp, "winner", bool, lg.code, q)
                if st.state == "post" and won is not None:
                    fighter["winner"] = won
                fighters.append(fighter)
            if len(fighters) != 2:
                raise EspnShapeError(lg.code, f"{cp}.competitors", f"{len(fighters)} fighters, not 2")
            weight = opt(c, "type", dict, lg.code, cp)
            session = opt(weight, "abbreviation", str, lg.code, f"{cp}.type") if weight else None
            winners = [f["name"] for f in fighters if f.get("winner")]
            names, national = broadcasts(c, lg.code, cp)
            out.append(SportsEvent(
                league=lg.code, kind="bout", espn_id=req(c, "id", str, lg.code, cp), parent_espn_id=eid,
                event_name=name[:200], session=session[:60] if session else None,
                starts_at=parse_time(req(c, "date", str, lg.code, cp), lg.code, f"{cp}.date"),
                time_tbd=opt(c, "timeValid", bool, lg.code, cp) is False, participants=fighters,
                broadcast=names, national_tv=national, venue=venue_name(c, "venue", lg.code, cp),
                status=st.status, espn_status=st.espn, status_detail=st.detail, period=st.period, clock=st.clock,
                score={"winner": winners[0]} if st.status == "final" and len(winners) == 1 else None))
    return out


# ----------------------------------------------------------------- tennis

def parse_tennis(lg: League, events: Sequence[Any]) -> list[SportsEvent]:
    """Every tour event ESPN lists (owner, 2026-11-04), singles matches only."""
    out: list[SportsEvent] = []
    for i, e in enumerate(events):
        p = f"events[{i}]"
        eid = req(e, "id", str, lg.code, p)
        name = req(e, "name", str, lg.code, p)
        opt(e, "major", bool, lg.code, p)  # validated, not a filter
        for g, grp in enumerate(opt(e, "groupings", list, lg.code, p) or []):
            gp = f"{p}.groupings[{g}]"
            slug = req(req(grp, "grouping", dict, lg.code, gp), "slug", str, lg.code, f"{gp}.grouping")
            if slug not in TENNIS_SINGLES:
                continue
            for k, c in enumerate(_list(grp, "competitions", lg.code, gp)):
                cp = f"{gp}.competitions[{k}]"
                players = []
                for j, comp in enumerate(_list(c, "competitors", lg.code, cp)):
                    q = f"{cp}.competitors[{j}]"
                    pid = req(comp, "id", str, lg.code, q)
                    pname = req(req(comp, "athlete", dict, lg.code, q), "displayName", str, lg.code, f"{q}.athlete")
                    sets = []
                    for s, ls in enumerate(opt(comp, "linescores", list, lg.code, q) or []):
                        v = opt(ls, "value", (int, float), lg.code, f"{q}.linescores[{s}]")
                        if v is not None:
                            sets.append(int(v))
                    players.append({"id": pid, "name": pname[:80], "winner": opt(comp, "winner", bool, lg.code, q),
                                    "sets": sets})
                if len(players) != 2:
                    raise EspnShapeError(lg.code, f"{cp}.competitors", f"{len(players)} players, not 2")
                if any(pl["id"].startswith("-") or pl["name"] == TBD_NAME for pl in players):
                    continue  # stored once both players are known
                st = read_status(c, lg.code, cp)
                rnd = opt(c, "round", dict, lg.code, cp)
                session = opt(rnd, "displayName", str, lg.code, f"{cp}.round") if rnd else None
                score = None
                if st.state != "pre" and (players[0]["sets"] or players[1]["sets"]):
                    score = {"sets": [list(x) for x in zip(players[0]["sets"], players[1]["sets"])]}
                names, national = broadcasts(c, lg.code, cp)
                out.append(SportsEvent(
                    league=lg.code, kind="match", espn_id=req(c, "id", str, lg.code, cp), parent_espn_id=eid,
                    event_name=name[:200], session=session[:60] if session else None,
                    starts_at=parse_time(req(c, "date", str, lg.code, cp), lg.code, f"{cp}.date"),
                    time_tbd=opt(c, "timeValid", bool, lg.code, cp) is False,
                    participants=[{k2: v for k2, v in pl.items() if k2 != "id" and v is not None
                                   and (k2 != "winner" or st.state == "post")} for pl in players],
                    broadcast=names, national_tv=national, venue=venue_name(c, "venue", lg.code, cp),
                    status=st.status, espn_status=st.espn, status_detail=st.detail, period=st.period,
                    clock=st.clock, score=score))
    return out


PARSERS: dict[str, Callable[[League, Sequence[Any]], list[SportsEvent]]] = {
    "game": parse_games, "race_session": parse_f1, "golf_round": parse_golf, "bout": parse_ufc,
    "match": parse_tennis,
}


def parse(lg: League, data: Any) -> list[SportsEvent]:
    """One scoreboard response -> rows. The top level is the daily contract:
    `events` and `leagues` are lists on every league, even an empty day."""
    if not isinstance(data, dict):
        raise EspnShapeError(lg.code, "$", f"expected an object, got {type(data).__name__}")
    events = req(data, "events", list, lg.code, "$")
    req(data, "leagues", list, lg.code, "$")
    try:
        parser = PARSERS[lg.kind]
    except KeyError:
        raise EspnShapeError(lg.code, "$", f"no parser for kind {lg.kind!r}") from None
    return parser(lg, events)


# ------------------------------------------------------------------- HTTP

@dataclass(frozen=True)
class Resp:
    status: int
    data: Any
    wire_bytes: int


Http = Callable[[str, Mapping[str, str]], Resp]


def httpx_get() -> tuple[Http, Callable[[], None]]:
    import httpx

    client = httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})

    def get(url: str, params: Mapping[str, str]) -> Resp:
        r = client.get(url, params=dict(params))
        try:
            data = r.json()
        except ValueError:
            data = None
        return Resp(r.status_code, data, r.num_bytes_downloaded)

    return get, client.close


def dates_param(days: Sequence[date]) -> str:
    lo, hi = min(days), max(days)
    return lo.strftime("%Y%m%d") if lo == hi else f"{lo:%Y%m%d}-{hi:%Y%m%d}"


class Espn:
    """Counts calls and bytes over the wire; one retry on 429, 5xx or a
    transport error, then EspnFetchError."""

    def __init__(self, http: Http, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.http = http
        self.sleep = sleep
        self.calls = 0
        self.wire_bytes = 0

    def scoreboard(self, lg: League, days: Sequence[date]) -> Any:
        params = dict(lg.params, dates=dates_param(days))
        url = BASE + lg.path + "/scoreboard"
        last = ""
        for attempt in (1, 2):
            self.calls += 1
            try:
                r = self.http(url, params)
            except Exception as exc:  # transport: timeout, DNS, reset
                last = type(exc).__name__
            else:
                self.wire_bytes += r.wire_bytes
                if r.status == 200:
                    if r.data is None:
                        raise EspnFetchError(lg.code, f"dates={params['dates']}: body is not JSON")
                    return r.data
                last = f"HTTP {r.status}"
                if r.status != 429 and r.status < 500:
                    break
            if attempt == 1:
                self.sleep(RETRY_AFTER_S)
        raise EspnFetchError(lg.code, f"dates={params['dates']}: {last}")

    def events(self, lg: League, days: Sequence[date]) -> list[SportsEvent]:
        """Every event for `days`: one call per date for a per_day league
        (ESPN refuses a team-sport range), one call for a range league."""
        days = sorted(set(days))
        if not days:
            return []
        batches = [[d] for d in days] if lg.fetch_mode == "per_day" else [days]
        seen: dict[str, SportsEvent] = {}
        for batch in batches:
            for ev in parse(lg, self.scoreboard(lg, batch)):
                seen[ev.espn_id] = ev
        return list(seen.values())
