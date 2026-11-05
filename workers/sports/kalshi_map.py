"""ESPN events -> their Kalshi events (Chat 7 Phase 2; db/066;
docs/kalshi_mapping.md, decided from docs/kalshi_mapping_probe.md).

    kalshi_map   chained after every sports_schedule run (04:00 Chicago and at
                 boot), and at 10:00, 16:00 and 22:00 Chicago: every 6 h.

Each run:

  1. reads the registry (each league's Kalshi series and match method), the
     alias exceptions, and the events inside the window (starting from 6 h
     ago; out-of-scope rows are never read again) in narrow columns;
  2. once per Chicago day, checks every registered series exists
     (GET /series/{ticker}): the registry is verified at run time;
  3. for each league with rows, reads its series' open events once
     (series -> events -> markets, never the keyword search or the raw
     open-markets list), every page shape-checked (the contract test);
  4. decides every unlinked row, and writes only rows whose state changed.

The rules (docs/kalshi_mapping.md §2):

  * team_pair: the same series, the UNORDERED pair of Kalshi codes (ESPN's
    code through sports_team_aliases, exceptions only), the Kalshi event's
    ticker date within ±1 US-Eastern day of ESPN's date. Never the start time,
    never the ticker date alone.
  * player_pair (tennis, UFC): the two names (accents, case and word order
    folded; the surnames when the full names differ), ±1 day.
  * A row that has started with no open candidate keeps the state it had
    (its event closed at the start: no new evidence). One never checked
    before it started is not_linked, saying so.
  * by_date: an F1 Race session -> the KXF1RACE event whose start is within
    ±1 day; a golf round -> the KXPGATOUR event whose start is 1 day after to
    4 days before it. Best effort: ambiguous is not linked.
  * Exactly one candidate, claimed by no other row -> linked (with
    home_away_swapped when Kalshi's order disagrees; NULL for soccer, whose
    home side is out of scope). Zero or several -> not linked, with the
    reason. No candidate and starting after the series' latest listed event
    (futures over 30 days out excluded) -> not_listed.
  * A linked row is NEVER relinked or unlinked. If a later run finds a
    different unique candidate, the row keeps its link and is flagged
    (link_conflict), logged once. The database refuses a relink anyway
    (sports_events_link_guard).
  * Out of scope: a league whose kalshi_map is off (MLB, off season), F1
    sessions other than the race. FCS games never reach sports_events.

Kalshi failures (HTTP, 429 after the client's backoff, a changed shape) fail
that league only; the others are written, then the run fails naming each.
"""

from __future__ import annotations

import logging
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from core import ledger, memory
from venues.kalshi.client import KalshiClient, KalshiError
from venues.kalshi.events import KalshiEvent, check_series, open_pages

log = logging.getLogger("valemont.kalshi_map")

JOB = "kalshi_map"
INTERVAL_S = 6 * 60 * 60
EASTERN = ZoneInfo("America/New_York")
OWNER_TZ = ZoneInfo("America/Chicago")
#: Rows starting from this long ago are decided (an in-play game is still an
#: open Kalshi event); an older row keeps the state it last had.
EVAL_BACK = timedelta(hours=6)
DAY_TOLERANCE = 1
#: A listed event starting further out than this is a future (the Masters
#: next April), not part of the rolling listing: it never sets the horizon.
FUTURES_BEYOND = timedelta(days=30)
#: A golf round on day D belongs to a tournament starting D-4 .. D+1.
GOLF_START_BEFORE, GOLF_START_AFTER = 4, 1
#: The F1 sessions Kalshi lists an event for (KXF1RACE: the race winner).
F1_LINKED_SESSIONS = frozenset({"Race"})
#: Pacing between Kalshi calls (public, unauthenticated; the probe ran at
#: 0.25 s: 703 calls, 0 429s).
MIN_INTERVAL_S = 0.25

LINKED, NOT_LISTED, NOT_LINKED, OUT_OF_SCOPE = "linked", "not_listed", "not_linked", "out_of_scope"
STATES = (LINKED, NOT_LISTED, NOT_LINKED, OUT_OF_SCOPE)


def eastern_day(t: datetime) -> date:
    return t.astimezone(EASTERN).date()


# ----------------------------------------------------------------- inputs

@dataclass(frozen=True)
class MapLeague:
    code: str
    kind: str
    sport: str
    series: str | None
    method: str  # team_pair | player_pair | by_date | off

    @classmethod
    def from_row(cls, row: ledger.SportsLeagueRow) -> "MapLeague":
        return cls(row.code, row.kind, row.sport, row.kalshi_series, row.kalshi_map)


@dataclass(frozen=True)
class Decision:
    id: int
    state: str
    reason: str | None = None
    method: str | None = None
    ticker: str | None = None
    swapped: bool | None = None

    def row(self) -> dict[str, Any]:
        """What ledger.sports_link_write sends for one row."""
        return {"id": self.id, "link_state": self.state, "link_reason": self.reason, "link_method": self.method,
                "kalshi_event_ticker": self.ticker, "home_away_swapped": self.swapped}

    @property
    def key(self) -> tuple[Any, ...]:
        return (self.state, self.reason, self.method, self.ticker, self.swapped)


@dataclass
class LeagueResult:
    decisions: list[Decision] = field(default_factory=list)
    #: (row id, the different unique candidate) for linked rows.
    conflicts: list[tuple[int, str]] = field(default_factory=list)
    #: Every row this run saw, by state (linked rows included).
    counts: Counter = field(default_factory=Counter)
    #: Open Kalshi events inside ESPN's dates that no ESPN row claims.
    unmatched: list[str] = field(default_factory=list)


# --------------------------------------------------------------- matching

def fold(name: str) -> str:
    """Accents and case folded, punctuation to spaces: 'Tomás Martín
    Etcheverry' -> 'tomas martin etcheverry'."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in s).split())


def player(name: str) -> frozenset[str]:
    """A name as its folded words, order ignored: ESPN's 'Wang Xinyu' is
    Kalshi's 'Xinyu Wang'."""
    return frozenset(fold(name).split())


def surname(name: str) -> str:
    parts = fold(name).split()
    return parts[-1] if parts else ""


def kalshi_code(league: str, espn_code: str, aliases: Mapping[tuple[str, str], str]) -> str:
    """ESPN's team code as Kalshi spells it: the alias, else the same code."""
    return aliases.get((league, espn_code)) or espn_code.upper()


def _event_day(e: KalshiEvent) -> date | None:
    return e.day if e.day is not None else (eastern_day(e.start) if e.start else None)


def _near(e: KalshiEvent, d: date) -> bool:
    ed = _event_day(e)
    return ed is not None and abs((ed - d).days) <= DAY_TOLERANCE


def candidates(lg: MapLeague, row: ledger.MapRow, events: Sequence[KalshiEvent],
               aliases: Mapping[tuple[str, str], str]) -> tuple[list[KalshiEvent], str | None]:
    """(the Kalshi events this row could be, or [] with why it cannot be
    matched at all)."""
    d = eastern_day(row.starts_at)
    if lg.method == "team_pair":
        if not row.home_abbr or not row.away_abbr:
            return [], "ESPN gives no team code for one side"
        pair = frozenset({kalshi_code(lg.code, row.home_abbr, aliases), kalshi_code(lg.code, row.away_abbr, aliases)})
        if len(pair) != 2:
            return [], "both sides map to one Kalshi code"
        return [e for e in events if e.codes == pair and _near(e, d)], None
    if lg.method == "player_pair":
        if len(row.names) != 2:
            return [], "ESPN does not give two named players"
        near = [e for e in events if len(e.sides) == 2 and _near(e, d)]
        full = frozenset(player(n) for n in row.names)
        hits = [e for e in near if frozenset(player(s.name) for s in e.sides) == full]
        if hits:
            return hits, None
        last = frozenset(surname(n) for n in row.names)
        if len(last) != 2:
            return [], None
        return [e for e in near if frozenset(surname(s.name) for s in e.sides) == last], None
    if lg.method == "by_date":
        dated = [e for e in events if e.start is not None]
        if lg.kind == "golf_round":
            return [e for e in dated
                    if -GOLF_START_AFTER <= (d - eastern_day(e.start)).days <= GOLF_START_BEFORE], None
        return [e for e in dated if abs((eastern_day(e.start) - d).days) <= DAY_TOLERANCE], None
    return [], f"no match rule {lg.method!r}"


def out_of_scope(lg: MapLeague, row: ledger.MapRow) -> str | None:
    if lg.method == "off":
        return f"out of scope: {lg.code} is not mapped (kalshi_map off)"
    if lg.kind == "race_session" and row.session not in F1_LINKED_SESSIONS:
        return f"out of scope: {lg.series} lists the race winner only, no event for {row.session or 'this session'}"
    return None


def horizon(events: Iterable[KalshiEvent], now: datetime) -> datetime | None:
    """The series' current listing horizon: its latest listed event start,
    futures over FUTURES_BEYOND out excluded."""
    starts = [e.start for e in events if e.start is not None and e.start <= now + FUTURES_BEYOND]
    return max(starts) if starts else None


def swapped(lg: MapLeague, row: ledger.MapRow, e: KalshiEvent, aliases: Mapping[tuple[str, str], str]) -> bool | None:
    """True when Kalshi's home (the ticker's second code) is ESPN's away team.
    NULL for soccer (home side out of scope) and when the order is unreadable."""
    if lg.method != "team_pair" or lg.sport == "soccer" or not row.home_abbr:
        return None
    order = e.order
    if order is None:
        return None
    return order[1] != kalshi_code(lg.code, row.home_abbr, aliases)


def _names(e: Sequence[KalshiEvent]) -> str:
    tickers = sorted(x.ticker for x in e)
    return ", ".join(tickers[:3]) + (f" (+{len(tickers) - 3})" if len(tickers) > 3 else "")


def no_candidate(lg: MapLeague, row: ledger.MapRow, aliases: Mapping[tuple[str, str], str]) -> str:
    if lg.method == "team_pair":
        a = kalshi_code(lg.code, row.away_abbr or "?", aliases)
        h = kalshi_code(lg.code, row.home_abbr or "?", aliases)
        return f"no {lg.series} event for {a}/{h} within ±{DAY_TOLERANCE} ET day"
    if lg.method == "player_pair":
        return f"no {lg.series} event for {' / '.join(row.names)[:120]} within ±{DAY_TOLERANCE} ET day"
    span = "its tournament's dates" if lg.kind == "golf_round" else f"±{DAY_TOLERANCE} ET day"
    return f"no {lg.series} event dated within {span}"


def decide_league(lg: MapLeague, rows: Sequence[ledger.MapRow], events: Sequence[KalshiEvent],
                  aliases: Mapping[tuple[str, str], str], now: datetime) -> LeagueResult:
    """Every row of one league against its series' open events. Pure: no
    I/O. Linked rows are never decided again (only checked for a conflict)."""
    res = LeagueResult()
    live: list[ledger.MapRow] = []
    for r in rows:
        why = None if r.ticker else out_of_scope(lg, r)
        if why:
            res.decisions.append(Decision(r.id, OUT_OF_SCOPE, why))
            res.counts[OUT_OF_SCOPE] += 1
        else:
            live.append(r)
    found = {r.id: candidates(lg, r, events, aliases) for r in live}
    claims: dict[str, list[int]] = defaultdict(list)
    for r in live:
        for e in found[r.id][0]:
            claims[e.ticker].append(r.id)
    linked_to = {r.ticker: r.id for r in live if r.ticker}
    shared = lg.kind == "golf_round"  # a tournament's rounds share its event
    hz = horizon(events, now)
    for r in live:
        cands, cannot = found[r.id]
        if r.ticker:
            res.counts[LINKED] += 1
            if len(cands) == 1 and cands[0].ticker != r.ticker and r.link_conflict != cands[0].ticker:
                res.conflicts.append((r.id, cands[0].ticker))
            continue
        if cannot:
            d = Decision(r.id, NOT_LINKED, cannot)
        elif len(cands) == 1:
            e = cands[0]
            holder = linked_to.get(e.ticker)
            if holder is not None and not shared:
                d = Decision(r.id, NOT_LINKED, f"{e.ticker} is already linked to sports_events {holder}")
            elif len(claims[e.ticker]) > 1 and not shared:
                d = Decision(r.id, NOT_LINKED, f"{e.ticker} matches {len(claims[e.ticker])} ESPN events")
            else:
                d = Decision(r.id, LINKED, None, lg.method, e.ticker, swapped(lg, r, e, aliases))
        elif cands:
            d = Decision(r.id, NOT_LINKED, f"{len(cands)} Kalshi candidates: {_names(cands)}")
        elif r.starts_at <= now and r.link_state is not None:
            # Started, and its Kalshi event (if it had one) has closed: no
            # new evidence, so the state it had before the start stands.
            res.counts[r.link_state] += 1
            continue
        elif r.starts_at <= now:
            d = Decision(r.id, NOT_LINKED, f"had started before its first check; no open {lg.series} event")
        elif hz is None:
            d = Decision(r.id, NOT_LISTED, f"{lg.series} lists no open event")
        elif r.starts_at > hz:
            d = Decision(r.id, NOT_LISTED, f"starts after {lg.series}'s latest listed event")
        else:
            d = Decision(r.id, NOT_LINKED, no_candidate(lg, r, aliases))
        res.decisions.append(d)
        res.counts[d.state] += 1
    if live and lg.method in ("team_pair", "player_pair"):
        first = min(eastern_day(r.starts_at) for r in live)
        last = max(eastern_day(r.starts_at) for r in live)
        res.unmatched = sorted(e.ticker for e in events if e.ticker not in claims and e.ticker not in linked_to
                               and (d := _event_day(e)) is not None and first <= d <= last)
    return res


# -------------------------------------------------------------------- run

class Meter:
    """Counts Kalshi calls and 429s through the client's transport (the
    client backs off on a 429 itself; this only sees them)."""

    def __init__(self, client: KalshiClient) -> None:
        self.calls = 0
        self.limited = 0
        inner = client._transport

        def transport(url: str) -> tuple[int, Any]:
            status, body = inner(url)
            self.calls += 1
            if status == 429:
                self.limited += 1
            return status, body

        client._transport = transport


@dataclass
class MapState:
    """Process memory, never read back from the database. `sent` is each
    row's last decision the database confirmed, so an unchanged row is not
    sent again; a restart sends every row once (the database still writes
    only a changed one)."""

    contract_day: date | None = None
    sent: dict[int, tuple[Any, ...]] = field(default_factory=dict)


STATE = MapState()


@dataclass
class MapRun:
    leagues: dict[str, Counter] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    sent: int = 0
    written: int = 0
    conflicts: int = 0
    calls: int = 0
    rate_limited: int = 0
    seconds: float = 0.0
    contract: str | None = None
    unmatched: dict[str, int] = field(default_factory=dict)
    no_milestone: int = 0

    @property
    def job_note(self) -> str:
        per = "; ".join(f"{code} " + ", ".join(f"{s} {c[s]}" for s in STATES if c[s])
                        for code, c in self.leagues.items() if sum(c.values())) or "no rows in the window"
        parts = [per, f"sent {self.sent}, written {self.written}, conflicts {self.conflicts}",
                 f"{self.calls} Kalshi calls, {self.rate_limited} 429s, {self.seconds:.1f} s"]
        if any(self.unmatched.values()):
            parts.append("Kalshi unmatched " + ", ".join(f"{k} {v}" for k, v in self.unmatched.items() if v))
        if self.no_milestone:
            parts.append(f"{self.no_milestone} Kalshi event(s) with no milestone")
        if self.contract:
            parts.append(self.contract)
        if self.failed:
            parts.append("FAILED " + ", ".join(sorted(self.failed)))
        return "; ".join(p for p in parts if p)


class KalshiMapFailed(RuntimeError):
    """One or more leagues (or series checks) failed; the others were written."""


def fetch_open(client: KalshiClient, series: str, run: MapRun) -> list[KalshiEvent]:
    out: list[KalshiEvent] = []
    for page in open_pages(client, series):
        out.extend(page.events)
        run.no_milestone += page.no_milestone
    return out


def _check_contract(client: KalshiClient, leagues: Sequence[MapLeague], run: MapRun) -> set[str]:
    """GET /series/{ticker} for every registered series. Returns the series
    that failed (their leagues are skipped this run)."""
    bad: set[str] = set()
    series = sorted({lg.series for lg in leagues if lg.series})
    for s in series:
        try:
            check_series(s, client.get(f"/series/{s}"))
        except KalshiError as exc:
            bad.add(s)
            run.failed[f"series {s}"] = str(exc)
            log.error("kalshi map: series %s: %s", s, exc)
    run.contract = f"contract: {len(series) - len(bad)}/{len(series)} series ok"
    return bad


def map_job(*, client: KalshiClient | None = None, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
            state: MapState = STATE, is_test: bool = False) -> MapRun:
    """The kalshi_map system job."""
    t0 = time.monotonic()
    t = now()
    leagues = [MapLeague.from_row(r) for r in ledger.sports_leagues()]
    if not leagues:
        raise RuntimeError("sports_leagues has no active league (db/065 not pasted?)")
    aliases = ledger.sports_team_aliases()
    rows = ledger.kalshi_map_rows(t - EVAL_BACK, [lg.code for lg in leagues], is_test=is_test)
    by_league: dict[str, list[ledger.MapRow]] = defaultdict(list)
    for r in rows:
        by_league[r.league].append(r)
    del rows
    client = client or KalshiClient(min_interval=MIN_INTERVAL_S)
    meter = Meter(client)
    run = MapRun()
    decisions: list[Decision] = []
    conflicts: list[tuple[int, str]] = []
    try:
        today = t.astimezone(OWNER_TZ).date()
        bad_series: set[str] = set()
        if state.contract_day != today:
            bad_series = _check_contract(client, leagues, run)
            if not bad_series:
                state.contract_day = today
        for lg in leagues:
            lrows = by_league.get(lg.code)
            if not lrows:
                continue
            if lg.series in bad_series:
                continue
            events: list[KalshiEvent] = []
            if lg.method != "off":
                try:
                    events = fetch_open(client, lg.series or "", run)
                except KalshiError as exc:
                    run.failed[lg.code] = str(exc)
                    log.error("kalshi map: %s: %s", lg.code, exc)
                    continue
            res = decide_league(lg, lrows, events, aliases, t)
            del events
            run.leagues[lg.code] = res.counts
            run.unmatched[lg.code] = len(res.unmatched)
            decisions.extend(res.decisions)
            conflicts.extend(res.conflicts)
        _write(run, decisions, conflicts, state, is_test, {r.id for v in by_league.values() for r in v})
    finally:
        run.calls, run.rate_limited = meter.calls, meter.limited
        run.seconds = time.monotonic() - t0
        memory.release()
    log.info("kalshi map: %s", run.job_note)
    if run.failed:
        raise KalshiMapFailed(f"{len(run.failed)} failed: "
                              + "; ".join(f"{k}: {run.failed[k]}" for k in sorted(run.failed))
                              + f" ({run.job_note})")
    return run


def _write(run: MapRun, decisions: Sequence[Decision], conflicts: Sequence[tuple[int, str]], state: MapState,
           is_test: bool, seen: set[int]) -> None:
    """Send only decisions that differ from the last one the database
    confirmed (the database also writes only a changed row), then the
    conflict flags, each logged once (it is written once)."""
    changed = [d for d in decisions if state.sent.get(d.id) != d.key]
    run.sent = len(changed)
    if changed:
        written = {i for i, _ in ledger.sports_link_write([d.row() for d in changed], is_test=is_test)}
        run.written = len(written)
        for d in changed:
            state.sent[d.id] = d.key
            if d.state == LINKED and d.id in written:
                log.info("kalshi map: sports_events %d linked to %s (%s%s)", d.id, d.ticker, d.method,
                         ", home/away swapped" if d.swapped else "")
    if conflicts:
        flagged = set(ledger.sports_link_conflicts([{"id": i, "link_conflict": c} for i, c in conflicts],
                                                   is_test=is_test))
        run.conflicts = len(flagged)
        for i, c in conflicts:
            if i in flagged:
                log.warning("kalshi map: sports_events %d is linked, but a later run found a different unique "
                            "candidate %s; the link is kept and flagged", i, c)
    # Forget rows that left the window or were decided elsewhere.
    for k in [k for k in state.sent if k not in seen]:
        del state.sent[k]
