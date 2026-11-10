"""The Kalshi board's worker jobs (Chat 8 Phase 1; db/071; docs/kalshi_board.md).

    kalshi_board          09:30 and 15:30 America/Chicago, and at boot: the
                          full pull. Every open market of every sports event
                          starting in the next 36 h (venues/kalshi/board.py:
                          milestones -> /markets by event, never the raw
                          open list), the series' fees, combo eligibility.
                          Then the link to sports_events, the sweep of
                          markets Kalshi no longer lists, and the prune.
    kalshi_board_refresh  every 15 min; works 08:00-23:00 Chicago only (idle
                          otherwise, so the health monitor still sees it on
                          time): re-prices the events starting within 3 h,
                          not yet started, from the full pull's in-memory
                          window: one event per call, events with an outside
                          price first (db/074), then soonest, at most
                          300 calls and 300 s a run, paced to keep 429s
                          under 2% (owner, Nov 7).

Changed-only: a market, event or series row is sent only when its content
differs from what this process last had confirmed (a compact map of 64-bit
hashes, rebuilt by every full pull; a restart re-sends every row once, and
the database writes only a row whose stored columns differ). Nothing is read
back from the database: the job's Supabase egress is statement replies only.

The board is the PRE-GAME leg universe (owner, Nov 6): an event and its
markets are deleted once coalesce(starts_at, close_time) + 6 h has passed.
It is an app table, upserted in place; never the immutable kalshi_markets
archive. Read-only to Kalshi: no key, no order (CLAUDE.md §1).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from core import ledger, memory
from consensus import priority
from venues.kalshi import board as kb
from venues.kalshi.client import KalshiClient

log = logging.getLogger("valemont.kalshi_board")

FULL_JOB = "kalshi_board"
REFRESH_JOB = "kalshi_board_refresh"
#: The longest gap between full pulls is 15:30 -> 09:30 (18 h).
FULL_INTERVAL_S = 18 * 60 * 60
REFRESH_INTERVAL_S = 15 * 60
OWNER_TZ = ZoneInfo("America/Chicago")
#: The refresh works from 08:00 up to and including the 23:00 run.
REFRESH_HOURS = (8, 23)
MIN_INTERVAL_S = 0.25
#: Market rows buffered before a write (ten events' markets at most past it).
FLUSH_ROWS = 1500
#: Series never read, by exact name (owner, Nov 7; docs/kalshi_board.md):
#: KXNFLPASSYDSPRECISE is a per-yard duplicate of KXNFLPASSYDS, 1,204 markets a
#: game (16,856 in 14 events, ~44 MB from Kalshi), and overflowed the page cap.
EXCLUDED_SERIES: frozenset[str] = frozenset({"KXNFLPASSYDSPRECISE"})
#: The refresh's hard caps per run (owner, Nov 7). Past either it stops and
#: logs what it skipped; the soonest-starting events are read first.
REFRESH_MAX_CALLS = 300
REFRESH_MAX_S = 300
#: No call STARTS later than REFRESH_MAX_S minus this, so one in flight (at
#: most REFRESH_CALL_TIMEOUT_S) and the last write still end inside the cap.
REFRESH_MARGIN_S = 20
REFRESH_CALL_TIMEOUT_S = 15.0

#: Milestone league/tour label -> sports_leagues.code.
LABELS: Mapping[str, str] = {
    "NFL": "nfl", "NCAAFB": "cfb", "NBA": "nba", "NCAAMB": "mcbb", "NHL": "nhl", "MLB": "mlb",
    "EPL": "epl", "MLS": "mls", "Champions League": "ucl", "UEFA Champions League": "ucl",
    "UFC": "ufc", "ATP": "atp", "WTA": "wta",
}
#: Series ticker prefix -> code, for a series no milestone label maps.
PREFIXES: tuple[tuple[str, str], ...] = tuple(sorted({
    "KXNFL": "nfl", "KXNCAAF": "cfb", "KXNBA": "nba", "KXNCAAMB": "mcbb", "KXNHL": "nhl", "KXMLB": "mlb",
    "KXEPL": "epl", "KXMLS": "mls", "KXUCL": "ucl", "KXUFC": "ufc", "KXATP": "atp", "KXWTA": "wta",
    "KXF1": "f1", "KXPGA": "pga",
}.items(), key=lambda kv: -len(kv[0])))


def league_of(series: str, labels: Iterable[str | None]) -> str | None:
    """Our league code, or None (most of Kalshi's sports: on the board all
    the same, with Kalshi's sport tag)."""
    for label in labels:
        if label in LABELS:
            return LABELS[label]
    for prefix, code in PREFIXES:
        if series.startswith(prefix):
            return code
    return None


def digest(row: Mapping[str, Any]) -> int:
    """A process-local hash of a row's values (a skip decision only: the
    database still compares every column before it writes)."""
    return hash(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str))


# ------------------------------------------------------------------ state

@dataclass
class BoardState:
    """Process memory, never read back from the database."""

    #: hash(ticker) -> digest of the row last confirmed written or unchanged.
    markets: dict[int, int] = field(default_factory=dict)
    events: dict[str, int] = field(default_factory=dict)
    series_rows: dict[str, int] = field(default_factory=dict)
    #: The last full pull's window: event ticker -> start (epoch s); and the
    #: series it needs (fees) for the refresh.
    window: dict[str, int] = field(default_factory=dict)
    series: dict[str, kb.Series] = field(default_factory=dict)
    #: The Chicago day `series` was first read on: the first pull of a new
    #: day reads every series again (the current fee type, daily).
    series_day: str | None = None
    #: Kalshi's mutually_exclusive per event (db/073), read with the series
    #: once per Chicago day: the series already read on `flags_day`.
    flags: dict[str, bool] = field(default_factory=dict)
    flags_series: set[str] = field(default_factory=set)
    flags_day: str | None = None
    #: The refresh's pace (s between calls) where its last run ended.
    refresh_pace: float = kb.PACE_START


STATE = BoardState()
#: Held by a full pull; a refresh that finds it held stands down (15:30 fires
#: both), so the two never share Kalshi's rate or the state maps.
PULL_LOCK = threading.Lock()


@dataclass
class BoardRun:
    kind: str = "full"
    markets: int = 0
    events: int = 0
    series: int = 0
    series_fetched: int = 0
    #: mutually_exclusive read (db/073): series asked today, window events with a flag.
    flag_series: int = 0
    flag_events: int = 0
    by_sport: dict[str, Counter] = field(default_factory=dict)
    combo_events: int = 0
    combo_markets: int = 0
    misses: Counter = field(default_factory=Counter)
    no_checked: int = 0
    no_mismatch: int = 0
    sent: int = 0
    written: int = 0
    events_written: int = 0
    series_written: int = 0
    linked: int = 0
    swept: int = 0
    pruned: dict[str, int] = field(default_factory=dict)
    calls: int = 0
    limited: int = 0
    kalshi_bytes: int = 0
    seconds: float = 0.0
    idle: str | None = None
    dry_run: bool = False
    #: Window events with NO market of any status, by series (owner, Nov 9:
    #: Kalshi lists an event before its markets exist). Counted; the run
    #: fails only past NO_MARKETS_MAX_SHARE or on an owner-league series
    #: whose every window event is here.
    no_markets: dict[str, int] = field(default_factory=dict)
    #: Window events per series (after exclusion), for the rule above.
    window_by_series: dict[str, int] = field(default_factory=dict)
    #: Events of a window series with no open market whose markets Kalshi
    #: lists in another status: status ("initialized", "closed+settled") ->
    #: events, and series -> events. Counted, never a failure (owner, Nov 9).
    not_open: dict[str, int] = field(default_factory=dict)
    not_open_series: dict[str, int] = field(default_factory=dict)

    @property
    def no_markets_note(self) -> str:
        n = sum(self.no_markets.values())
        if not n:
            return "no_markets=0"
        names = sorted(self.no_markets)
        return (f"no_markets={n} (" + ", ".join(f"{s} {self.no_markets[s]}" for s in names[:6])
                + (f" +{len(names) - 6} more" if len(names) > 6 else "") + ")")

    @property
    def not_open_note(self) -> str:
        n = sum(self.not_open.values())
        if not n:
            return "not_open=0"
        by = ", ".join(f"{k} {v}" for k, v in sorted(self.not_open.items(), key=lambda kv: (-kv[1], kv[0])))
        names = sorted(self.not_open_series)
        return (f"not_open={n} ({by}; " + ", ".join(names[:6])
                + (f" +{len(names) - 6} more" if len(names) > 6 else "") + ")")
    #: EXCLUDED_SERIES met in the window: series -> its window events (never read).
    excluded: dict[str, int] = field(default_factory=dict)
    #: Series whose /markets query hit the page cap: series -> its events (skipped).
    overflowed: dict[str, int] = field(default_factory=dict)
    #: Markets that broke the parser contract: skipped, the rest written, the
    #: full pull fails at the end naming them (owner, Nov 10).
    contract_misses: list[str] = field(default_factory=list)

    @property
    def contract_note(self) -> str:
        n = self.contract_misses
        return (f"contract_misses={len(n)}"
                + (" (" + ", ".join(n[:6]) + (f" +{len(n) - 6} more" if len(n) > 6 else "") + ")" if n else ""))
    #: The refresh: events in scope (start within REFRESH_WITHIN, not started),
    #: those left unread when a cap was hit, which cap, the first skipped
    #: start, and the pace it ended on (s between calls).
    scope: int = 0
    skipped: int = 0
    cap: str | None = None
    skipped_from: datetime | None = None
    pace: float | None = None

    @property
    def limited_pct(self) -> float:
        return 100.0 * self.limited / self.calls if self.calls else 0.0

    @property
    def job_note(self) -> str:
        if self.idle:
            return f"idle: {self.idle}"
        by = "; ".join(f"{s} " + "/".join(f"{k} {c[k]}" for k in ("game_line", "prop", "other") if c[k])
                       for s, c in sorted(self.by_sport.items(), key=lambda kv: -sum(kv[1].values()))[:8])
        head = (f"{self.kind}{' (dry run)' if self.dry_run else ''}: {self.markets} markets, "
                f"{self.events} events, {self.series} series ({self.series_fetched} read from /series)")
        overflowed = (f"overflowed={len(self.overflowed)}"
                      + (" (" + ", ".join(f"{s} {n} events" for s, n in sorted(self.overflowed.items())) + ")"
                         if self.overflowed else ""))
        if self.kind == "full":
            # Early in the note, so job_health's 500-character cut keeps them:
            # counts read, never an absence inferred (owner, Nov 7).
            head += (f"; {self.no_markets_note}, {self.not_open_note}; "
                     f"excluded={len(self.excluded)}"
                     + (" (" + ", ".join(f"{s} {n} events" for s, n in sorted(self.excluded.items())) + ")"
                        if self.excluded else "")
                     + f", {overflowed}, {self.contract_note}")
            head += f"; flags {self.flag_events} events ({self.flag_series} series read)"
        else:
            head = (f"refresh: {self.events} of {self.scope} events within "
                    f"{kb.REFRESH_WITHIN.total_seconds() / 3600:.0f} h, {self.markets} markets")
            if self.skipped:
                head += (f"; SKIPPED {self.skipped} ({self.cap} cap; from "
                         f"{self.skipped_from:%H:%M} UTC)" if self.skipped_from else f" ({self.cap} cap)")
            if self.overflowed:
                head += f"; {overflowed}"
            if self.contract_misses:
                head += f"; {self.contract_note}"
        rate = (f"{self.calls} Kalshi calls, {self.limited} 429s ({self.limited_pct:.1f}%)"
                + (f", pace {self.pace:.2f} s" if self.pace is not None else "")
                + f", ~{self.kalshi_bytes // 1024} KB from Kalshi, {self.seconds:.0f} s")
        if self.kind != "full":
            return "; ".join(p for p in [
                head, rate,
                f"sent {self.sent}, written {self.written} markets; swept {self.swept}",
                f"parse misses {sum(self.misses.values())}"
                + (" (" + ", ".join(f"{k} {v}" for k, v in self.misses.most_common(10)) + ")" if self.misses else ""),
                f"NO complement mismatches {self.no_mismatch}/{self.no_checked}"] if p)
        parts = [head,
                 by,
                 f"combo-eligible {self.combo_markets} markets / {self.combo_events} events",
                 f"sent {self.sent}, written {self.written} markets, {self.events_written} events, "
                 f"{self.series_written} series",
                 f"linked {self.linked}, swept {self.swept}, pruned "
                 + (", ".join(f"{k} {v}" for k, v in self.pruned.items()) or "0"),
                 f"parse misses {sum(self.misses.values())}"
                 + (" (" + ", ".join(f"{k} {v}" for k, v in self.misses.most_common(10)) + ")" if self.misses else ""),
                 f"NO complement mismatches {self.no_mismatch}/{self.no_checked}",
                 rate]
        return "; ".join(p for p in parts if p)


class BoardFailed(RuntimeError):
    """The pull failed (a changed shape, an unmodelled fee, Kalshi down)."""


# ------------------------------------------------------------------ writes

class Writer:
    """The database side, through core/ledger.py. A dry run swaps in NoWrite."""

    def series(self, rows: list[dict[str, Any]]) -> int:
        return ledger.kalshi_board_series_upsert(rows)

    def events(self, rows: list[dict[str, Any]], at: datetime) -> int:
        return ledger.kalshi_board_events_upsert(rows, at)

    def markets(self, rows: list[dict[str, Any]], at: datetime) -> int:
        return ledger.kalshi_board_markets_upsert(rows, at)

    def touch(self, events: list[str], series: list[str], at: datetime) -> None:
        ledger.kalshi_board_touch(events, series, at)

    def sweep(self, events: list[str], markets: list[str]) -> int:
        return ledger.kalshi_board_sweep(events, markets)

    def link(self) -> int:
        return ledger.kalshi_board_link()

    def prune(self) -> dict[str, int]:
        return ledger.kalshi_board_prune()

    def game_series(self) -> frozenset[str]:
        """The owner's five leagues' game series, from the sports_leagues
        registry (the kalshi_series Chat 7 maps: KXNFLGAME, KXNBAGAME, …).
        A read; asked only when a real run has zero-market events."""
        return frozenset(lg.kalshi_series for lg in ledger.sports_leagues()
                         if lg.code in OWNER_LEAGUES and lg.kalshi_series)


class NoWrite(Writer):
    def series(self, rows: list[dict[str, Any]]) -> int:
        return 0

    def events(self, rows: list[dict[str, Any]], at: datetime) -> int:
        return 0

    def markets(self, rows: list[dict[str, Any]], at: datetime) -> int:
        return 0

    def touch(self, events: list[str], series: list[str], at: datetime) -> None:
        return None

    def sweep(self, events: list[str], markets: list[str]) -> int:
        return 0

    def link(self) -> int:
        return 0

    def prune(self) -> dict[str, int]:
        return {}


# ------------------------------------------------------------------ rows

def series_row(s: kb.Series, league: str | None, combo: Iterable[str]) -> dict[str, Any]:
    colls = sorted(set(combo))
    return {"series_ticker": s.ticker, "title": s.title, "sport": s.sport, "league": league,
            "category": s.category or None, "tags": list(s.tags), "fee_type": s.regime.fee_type,
            "fee_multiplier": str(s.regime.multiplier), "fee_regime": s.regime.as_payload() | {"fetched_at": None},
            "combo_eligible": bool(colls), "collection_tickers": colls}


def event_row(ticker: str, game: kb.Game, times: tuple[datetime | None, datetime | None],
              combo: kb.Combo | None, exclusive: bool | None = None) -> dict[str, Any]:
    close, expiry = times
    return {"mutually_exclusive": exclusive,
            "event_ticker": ticker, "series_ticker": kb.series_of(ticker), "game_ticker": game.game_ticker,
            "game_title": game.title, "milestone_start": game.starts_at.isoformat(),
            "close_time": None if close is None else close.isoformat(),
            "expected_expiration_time": None if expiry is None else expiry.isoformat(),
            "combo_eligible": combo is not None, "combo_yes_only": bool(combo and combo.yes_only),
            "combo_collections": list(combo.collections) if combo else [],
            # None stays None: absent on the event is "no stated cap", never 0 or 1.
            "combo_size_max": combo.size_max if combo else None,
            "combo_quoters": combo.quoters if combo else None}


# ------------------------------------------------------------------ runs

def _owner_hour(t: datetime) -> int:
    return t.astimezone(OWNER_TZ).hour


def _client(client: KalshiClient | None) -> tuple[KalshiClient, kb.Counting | None]:
    if client is not None:
        counter = client._transport if isinstance(client._transport, kb.Counting) else None
        return client, counter
    return kb.make_client(min_interval=MIN_INTERVAL_S)


class _Flusher:
    """Buffers one run's rows and writes them in bounded batches: events
    before their markets (the foreign key), then the sweep of each written
    group's markets Kalshi no longer lists."""

    def __init__(self, run: BoardRun, writer: Writer, state: BoardState, at: datetime,
                 new_markets: dict[int, int], new_events: dict[str, int]) -> None:
        self.run, self.writer, self.state, self.at = run, writer, state, at
        self.new_markets, self.new_events = new_markets, new_events
        self.events: list[tuple[dict[str, Any], int]] = []
        self.markets: list[tuple[dict[str, Any], int, int]] = []
        self.seen_events: list[str] = []
        self.seen_markets: list[str] = []

    def add(self, group: list[str], ev_rows: list[dict[str, Any]], m_rows: list[dict[str, Any]]) -> None:
        # Every event asked for is swept, one with no open market left included.
        self.seen_events.extend(group)
        for r in ev_rows:
            d = digest(r)
            self.new_events[r["event_ticker"]] = d
            if self.state.events.get(r["event_ticker"]) != d:
                self.events.append((r, d))
        for r in m_rows:
            k, d = hash(r["market_ticker"]), digest(r)
            self.new_markets[k] = d
            self.seen_markets.append(r["market_ticker"])
            if self.state.markets.get(k) != d:
                self.markets.append((r, k, d))
        if len(self.markets) >= FLUSH_ROWS or len(self.seen_markets) >= 4 * FLUSH_ROWS:
            self.flush()

    def flush(self) -> None:
        if self.events:
            self.run.events_written += self.writer.events([r for r, _ in self.events], self.at)
        if self.markets:
            self.run.sent += len(self.markets)
            self.run.written += self.writer.markets([r for r, _, _ in self.markets], self.at)
        if self.seen_events:
            self.run.swept += self.writer.sweep(self.seen_events, self.seen_markets)
        # Confirmed: what was written (or found unchanged) is now the state.
        for r, d in self.events:
            self.state.events[r["event_ticker"]] = d
        for _, k, d in self.markets:
            self.state.markets[k] = d
        self.events, self.markets, self.seen_events, self.seen_markets = [], [], [], []


def _pull(client: KalshiClient, run: BoardRun, writer: Writer, state: BoardState, at: datetime,
          tickers: list[str], window: Mapping[str, kb.Game], series: Mapping[str, kb.Series],
          combos: Mapping[str, kb.Combo], new_markets: dict[int, int], new_events: dict[str, int],
          flags: Mapping[str, bool] | None = None) -> None:
    flags = flags or {}
    fl = _Flusher(run, writer, state, at, new_markets, new_events)
    for group, batch in kb.market_batches(client, tickers, dict(series)):
        if batch.overflow:
            # Nothing of it is written, swept or kept in the window; the run
            # fails at the end naming it (owner, Nov 7).
            run.overflowed[kb.series_of(group[0])] = len(group)
            continue
        run.contract_misses.extend(batch.contract_misses)
        if not batch.rows and not batch.contract_misses:
            # A window series with events but no open market. Each event is
            # asked once with NO status filter (owner, Nov 9): one with markets
            # in any other status (unopened, closed, paused, settled, whatever
            # Kalshi returns) counts as not_open by status; one with zero
            # markets of any status counts as no_markets. Neither fails the run
            # on its own: _full applies the broken-fetch rule after the rest
            # is written.
            s = kb.series_of(group[0])
            for t in group:
                status = kb.market_statuses(client, t)
                if status is None:
                    run.no_markets[s] = run.no_markets.get(s, 0) + 1
                else:
                    run.not_open[status] = run.not_open.get(status, 0) + 1
                    run.not_open_series[s] = run.not_open_series.get(s, 0) + 1
        ev_rows = [event_row(t, window[t], batch.times[t], combos.get(t), flags.get(t))
                   for t in group if t in batch.times]
        run.events += len(ev_rows)
        run.markets += len(batch.rows)
        run.no_checked += batch.checked
        run.no_mismatch += batch.no_mismatch
        run.misses.update(batch.misses)
        for r in batch.rows:
            sport = series[kb.series_of(r["event_ticker"])].sport
            run.by_sport.setdefault(sport, Counter())[r["market_kind"]] += 1
            if r["event_ticker"] in combos:
                run.combo_markets += 1
        run.combo_events += sum(1 for r in ev_rows if r["combo_eligible"])
        fl.add(group, ev_rows, batch.rows)
        del batch
    fl.flush()


def full_job(*, client: KalshiClient | None = None, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
             state: BoardState = STATE, writer: Writer | None = None, dry_run: bool = False) -> BoardRun:
    """The kalshi_board system job: the full 36 h pull."""
    with PULL_LOCK:
        return _full(client, now, state, writer, dry_run)


def _full(client: KalshiClient | None, now: Callable[[], datetime], state: BoardState, writer: Writer | None,
          dry_run: bool) -> BoardRun:
    t0 = time.monotonic()
    if dry_run:
        # A dry run must never mark anything as sent for the next real run.
        state = BoardState()
    w = NoWrite() if dry_run else (writer or Writer())
    c, counter = _client(client)
    run = BoardRun(kind="full", dry_run=dry_run)
    at = now()
    try:
        games = kb.milestones(c, at, at + kb.WINDOW)
        window = kb.window_events(games)
        for t in [t for t in window if kb.series_of(t) in EXCLUDED_SERIES]:
            s = kb.series_of(t)
            run.excluded[s] = run.excluded.get(s, 0) + 1
            del window[t]
        wanted = {kb.series_of(t) for t in window}
        sport_hint = {kb.series_of(t): g.kind for t, g in window.items()}
        day = at.astimezone(OWNER_TZ).date().isoformat()
        known = state.series if state.series_day == day else {}
        series = kb.series_info(c, wanted, sport_hint, at, known)
        run.series_fetched = len(set(series) - set(known))
        # mutually_exclusive (db/073): once per Chicago day, with the series.
        same_day = state.flags_day == day
        flag_series = wanted - (state.flags_series if same_day else set())
        flags = dict(state.flags) if same_day else {}
        flags.update(kb.event_flags(c, flag_series))
        run.flag_series, run.flag_events = len(flag_series), sum(1 for t in window if t in flags)
        combos = kb.collections(c, keep=set(window)).by_event
        labels: dict[str, set[str | None]] = {}
        for t, g in window.items():
            labels.setdefault(kb.series_of(t), set()).add(g.label)
        coll_by_series: dict[str, set[str]] = {}
        for t, combo in combos.items():
            coll_by_series.setdefault(kb.series_of(t), set()).update(combo.collections)
        s_rows = [series_row(s, league_of(s.ticker, sorted(labels.get(s.ticker, ()), key=str)),
                             coll_by_series.get(s.ticker, ())) for s in series.values()]
        run.series = len(s_rows)
        changed = [r for r in s_rows if state.series_rows.get(r["series_ticker"]) != digest(r)]
        run.series_written = w.series(changed)
        if not dry_run:
            for r in changed:
                state.series_rows[r["series_ticker"]] = digest(r)

        for t in window:
            s = kb.series_of(t)
            run.window_by_series[s] = run.window_by_series.get(s, 0) + 1
        new_markets: dict[int, int] = {}
        new_events: dict[str, int] = {}
        _pull(c, run, w, state, at, kb.order_events(window), window, series, combos, new_markets, new_events,
              flags)
        if window and not run.markets:
            raise BoardFailed(f"kalshi board: no open market returned for {len(window)} window events "
                              "(has the /markets filter changed?)")
        w.touch(sorted(new_events), sorted(series), at)
        run.linked = w.link()
        run.pruned = w.prune()
        # The window just read is the whole state now (what left it is pruned
        # or swept in the database).
        if not dry_run:
            state.markets, state.events = new_markets, new_events
            state.window = {t: int(g.starts_at.timestamp()) for t, g in window.items() if t in new_events}
            # Kept for the refresh and for later pulls the same day (series
            # that left the window drop out).
            state.series = dict(series)
            if state.series_day != day:
                state.series_day = day
            state.flags = {t: f for t, f in flags.items() if t in window}
            state.flags_series = (state.flags_series if same_day else set()) | flag_series
            state.flags_day = day
    except kb.KalshiError as exc:
        raise BoardFailed(f"kalshi board: {exc}") from exc
    finally:
        _finish(run, counter, t0)
    log.info("kalshi board: %s", run.job_note)
    # Both fail AFTER the rest was written and the state (the refresh's
    # window) was set: the refresh keys on "the board was written", not on
    # "the last pull was ok" (owner, Nov 7).
    broken = broken_fetch(run, w.game_series()) if run.no_markets and not dry_run else []
    if (broken or run.overflowed or run.contract_misses) and not dry_run:
        why = []
        if run.overflowed:
            why.append(f"{len(run.overflowed)} series overflowed the page cap and were skipped: "
                       + ", ".join(f"{s} ({n} events)" for s, n in sorted(run.overflowed.items())))
        if run.contract_misses:
            why.append(f"{len(run.contract_misses)} markets broke the parser contract and were skipped: "
                       + ", ".join(run.contract_misses[:20])
                       + (f" +{len(run.contract_misses) - 20} more" if len(run.contract_misses) > 20 else ""))
        why.extend(broken)
        raise BoardFailed(f"kalshi board: {'; '.join(why)} (the rest was written: {run.job_note})")
    return run


#: The broken-fetch rule (owner, Nov 9). A zero-market event is Kalshi listing
#: an event before its markets exist; a broken fetch path (Nov 6's comma batch,
#: 200 and empty) empties MANY events at once. So the run fails only when
#: zero-market events exceed this share of the window's events, or when a
#: GAME series of the owner's five leagues (the registry's kalshi_series:
#: KXNFLGAME, KXNBAGAME, KXNCAAFGAME, KXF1RACE, KXPGATOUR) has every window
#: event at zero markets. Props and other series of those leagues fall under
#: the share only (owner, Nov 9).
NO_MARKETS_MAX_SHARE = 0.02
OWNER_LEAGUES: frozenset[str] = frozenset({"cfb", "nfl", "nba", "f1", "pga"})


def broken_fetch(run: BoardRun, game_series: Iterable[str]) -> list[str]:
    """Why the zero-market events look like a broken fetch, or [] when they
    look like events listed before their markets exist."""
    game_series = frozenset(game_series)
    why: list[str] = []
    total, window = sum(run.no_markets.values()), sum(run.window_by_series.values())
    if window and total > NO_MARKETS_MAX_SHARE * window:
        why.append(f"{total} of {window} window events have no market of any status "
                   f"(> {NO_MARKETS_MAX_SHARE:.0%}): "
                   + ", ".join(f"{s} ({n} events)" for s, n in sorted(run.no_markets.items())))
    whole = sorted(s for s, n in run.no_markets.items() if s in game_series and n == run.window_by_series.get(s))
    if whole:
        why.append("every window event of an owner-league game series has no market of any status: "
                   + ", ".join(f"{s} ({run.no_markets[s]} events)" for s in whole))
    return why


def refresh_job(*, http: kb.Http | None = None,
                now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                state: BoardState = STATE, writer: Writer | None = None,
                clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep) -> BoardRun:
    """The kalshi_board_refresh system job (rebuilt, owner, Nov 7): re-price
    the events starting within 3 h and not yet started, ONE event per call,
    events with an outside price (consensus/priority.py) first, then soonest,
    under REFRESH_MAX_CALLS and REFRESH_MAX_S. A run never
    overlaps itself (the scheduler's max_instances=1 and coalesce) or a full
    pull (PULL_LOCK: it idles while one runs)."""
    at = now()
    hour = _owner_hour(at)
    if not (REFRESH_HOURS[0] <= hour <= REFRESH_HOURS[1]) or (hour == REFRESH_HOURS[1] and
                                                             at.astimezone(OWNER_TZ).minute >= 15):
        return BoardRun(kind="refresh", idle="outside 08:00-23:00 Chicago")
    if not state.window:
        return BoardRun(kind="refresh", idle="no window yet (the full pull has not run in this process)")
    lo, hi = at.timestamp(), (at + kb.REFRESH_WITHIN).timestamp()
    # Events with an outside price first (consensus/priority.py; owner, Nov 9),
    # then the soonest start; the cap and the window are unchanged.
    prio = priority.events()
    soon = sorted((t for t, s in state.window.items() if lo <= s < hi),
                  key=lambda t: (t not in prio, state.window[t], t))
    if not soon:
        return BoardRun(kind="refresh", idle=f"nothing starts within {kb.REFRESH_WITHIN.total_seconds() / 3600:.0f} h")
    if not PULL_LOCK.acquire(blocking=False):
        return BoardRun(kind="refresh", idle="the full pull is running")
    try:
        return _refresh(http, at, soon, state, writer, clock, sleep)
    finally:
        PULL_LOCK.release()


def _refresh(http: kb.Http | None, at: datetime, soon: list[str], state: BoardState, writer: Writer | None,
             clock: Callable[[], float], sleep: Callable[[float], None]) -> BoardRun:
    t0 = clock()
    w = writer or Writer()
    # The pace carries over from the last run in this process, so a rate
    # limit learned once is not re-learned every 15 min.
    paced = kb.Paced(interval=state.refresh_pace, max_calls=REFRESH_MAX_CALLS,
                     deadline=t0 + REFRESH_MAX_S - REFRESH_MARGIN_S, http=http, clock=clock, sleep=sleep,
                     timeout=REFRESH_CALL_TIMEOUT_S)
    c = KalshiClient(transport=paced, min_interval=0, sleep=paced.sleep)
    run = BoardRun(kind="refresh", scope=len(soon))
    changed: list[tuple[dict[str, Any], int, int]] = []
    done: list[str] = []
    seen: list[str] = []

    def flush() -> None:
        nonlocal changed, done, seen
        if changed:
            run.sent += len(changed)
            run.written += w.markets([r for r, _, _ in changed], at)
            for _, k, d in changed:
                state.markets[k] = d
        if done:
            # Only events read whole are swept: a capped run never deletes
            # the markets of an event it did not finish reading.
            run.swept += w.sweep(done, seen)
            w.touch(done, [], at)
        changed, done, seen = [], [], []

    try:
        for i, t in enumerate(soon):
            try:
                batch = kb.event_markets(c, state.series, t)
            except kb.BudgetSpent as cap:
                run.skipped, run.cap = len(soon) - i, cap.reason
                run.skipped_from = datetime.fromtimestamp(state.window[t], timezone.utc)
                log.warning("kalshi board refresh: %s cap hit; %d of %d events skipped, the first starting %s",
                            cap.reason, run.skipped, len(soon), run.skipped_from.isoformat())
                break
            except kb.PageCapExceeded:
                # One event over the page cap: skipped, never swept (that would
                # delete its markets); the others go on (owner, Nov 7).
                s = kb.series_of(t)
                run.overflowed[s] = run.overflowed.get(s, 0) + 1
                continue
            run.events += 1
            run.markets += len(batch.rows)
            run.no_checked += batch.checked
            run.no_mismatch += batch.no_mismatch
            run.misses.update(batch.misses)
            run.contract_misses.extend(batch.contract_misses)
            for r in batch.rows:
                k, d = hash(r["market_ticker"]), digest(r)
                seen.append(r["market_ticker"])
                if state.markets.get(k) != d:
                    changed.append((r, k, d))
            done.append(t)
            del batch
            if len(changed) >= FLUSH_ROWS or len(seen) >= 4 * FLUSH_ROWS:
                flush()
        flush()
        if run.events and not run.markets:
            raise BoardFailed(f"kalshi board refresh: no open market returned for {run.events} events "
                              f"starting within {kb.REFRESH_WITHIN.total_seconds() / 3600:.0f} h "
                              "(has the /markets filter changed?)")
    except kb.KalshiError as exc:
        raise BoardFailed(f"kalshi board refresh: {exc}") from exc
    finally:
        state.refresh_pace = paced.interval
        run.pace = paced.interval
        run.calls, run.limited, run.kalshi_bytes = paced.calls, paced.limited, paced.bytes
        run.seconds = clock() - t0
        memory.release()
    return run


def _finish(run: BoardRun, counter: kb.Counting | None, t0: float) -> None:
    run.seconds = time.monotonic() - t0
    if counter is not None:
        run.calls, run.limited, run.kalshi_bytes = counter.calls, counter.limited, counter.bytes
    memory.release()
