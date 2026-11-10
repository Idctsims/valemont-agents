"""The consensus job (Chat 8 Phase 2; db/073; docs/consensus.md).

    consensus   10:15 and 16:15 America/Chicago. Never at boot: a redeploy
                must not spend Odds API credits (owner's budget, Nov 9).

Each run prices the board's linked games starting in the next 36 h:

  1. The Odds API (credits: core/odds_credits.py's guard on every paid call)
     - NBA mainlines (h2h, spreads, totals; 3 credits) every run that has
       an NBA game on the board.
     - NFL mainlines (3 credits) and NFL props (receptions, receiving yards,
       rushing yards; 3 credits a game) on NFL game days only: games whose
       Chicago date is today.
     - 16:15 pays only for games not yet covered today: the free /events
       first, a paid call only when it lists a board game this process has
       not priced since midnight. (Process memory: a restart between the
       runs can cost one repeat, at most 6 credits plus props.)
  2. ESPN's one book (DraftKings, `close`) for cfb, nfl, nba, mcbb, nhl,
     epl, ucl: the scoreboard per Eastern date of the games (sports/espn.py's
     client).
  3. Every binary market of the full-game moneyline/spread/total series and
     the three NFL prop series, matched EXACTLY (consensus/match.py): books
     (>= 2 Odds API books at the line), else espn, else no row (kalshi tier,
     derived in SQL). Changed-only upsert; per game, a sweep of rows this run
     no longer confirms, except a books row of a game this process has no
     Odds API prices for today (it stays until the next paid pull).
  4. book_lines: the main lines, changed-only; then the collapse prune.

A Kalshi-only recompute after each board refresh is not needed: the kalshi
tier and every edge are computed at read time from kalshi_board's live
prices (leg_consensus_for), so they follow every refresh with no write.

Fails loud, after the rest is written: an Odds API event in the window that
matches no board game (a missing ODDS_NAMES alias, or a schedule gap), and
the month's first credit refusal.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from consensus import espn_odds, match, priority
from consensus.match import BookPrices, Game, Market
from core import ledger, odds_credits
from core.odds_credits import CreditGuard, CreditRefused
from sports import espn
from sports.kalshi_map import eastern_day
from venues.odds import client as odds

log = logging.getLogger("valemont.consensus")

JOB = "consensus"
#: The longest gap between runs is 16:15 -> 10:15 (18 h).
INTERVAL_S = 18 * 60 * 60
OWNER_TZ = ZoneInfo("America/Chicago")
WINDOW = timedelta(hours=36)
PROP_MARKETS = ",".join(match.PROP_SERIES.values())
#: The afternoon run starts at this Chicago hour or later.
AFTERNOON_HOUR = 13


class ConsensusFailed(RuntimeError):
    pass


@dataclass
class DayState:
    """Process memory for one Chicago day, never read back from the database."""

    day: date | None = None
    #: sports_event_id -> (its Odds API prices, when fetched).
    prices: dict[int, tuple[BookPrices, datetime]] = field(default_factory=dict)
    props: set[int] = field(default_factory=set)


STATE = DayState()


@dataclass
class Run:
    kind: str = "morning"
    games: int = 0
    markets: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    written: int = 0
    swept: int = 0
    kept_books: int = 0
    odds_events: dict[str, tuple[int, int]] = field(default_factory=dict)
    misses: list[str] = field(default_factory=list)
    unboarded: list[str] = field(default_factory=list)
    props_games: int = 0
    espn_games: tuple[int, int] = (0, 0)
    espn_calls: int = 0
    side_misses: int = 0
    lines_written: int = 0
    lines_pruned: int = 0
    credits_run: int = 0
    credits_month: int = 0
    remaining: int | None = None
    refused: str | None = None
    tiers: dict[str, dict[str, int]] = field(default_factory=dict)
    claimed: int = 0
    kalshi_positive: int = 0
    stale_positive: int = 0
    #: stale_positive by tier, books vs espn (owner, Nov 10).
    stale_by_tier: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def job_note(self) -> str:
        t = {k: sum(v.get(k, 0) for v in self.tiers.values()) for k in ("books", "espn", "kalshi")}
        by = "; ".join(f"{s} " + "/".join(f"{k[0]} {v[k]}" for k in ("books", "espn", "kalshi") if v.get(k))
                       for s, v in sorted(self.tiers.items(), key=lambda kv: -sum(kv[1].values()))
                       if v.get("books") or v.get("espn"))
        oa = ", ".join(f"{s} {m}/{n}" for s, (m, n) in sorted(self.odds_events.items()))
        return "; ".join(p for p in [
            f"{self.kind}: legs books {t['books']}, espn {t['espn']}, kalshi {t['kalshi']} "
            f"(claimed {self.claimed}; stale, not claimed {self.stale_positive} "
            f"(books {self.stale_by_tier.get('books', 0)}, espn {self.stale_by_tier.get('espn', 0)}); "
            f"kalshi-tier positive {self.kalshi_positive})",
            f"credits {self.credits_run} this run, month {self.credits_month}, remaining {self.remaining}"
            + (f", REFUSED ({self.refused})" if self.refused else ""),
            f"odds events matched {oa or 'none'}" + (f", MISSES {len(self.misses)}" if self.misses else "")
            + (f", unboarded {len(self.unboarded)}" if self.unboarded else ""),
            f"espn {self.espn_games[0]}/{self.espn_games[1]} games with odds ({self.espn_calls} calls)",
            f"props games {self.props_games}",
            f"{self.games} games, {self.markets} markets; written {self.written}, swept {self.swept}"
            + (f", books kept {self.kept_books}" if self.kept_books else "")
            + (f"; side misses {self.side_misses}" if self.side_misses else ""),
            f"book_lines +{self.lines_written}, pruned {self.lines_pruned}",
            f"{self.seconds:.0f} s",
            by,
        ] if p)


class Store:
    """The database side (core/ledger.py). Tests pass a fake."""

    def games(self, until: datetime, leagues: Sequence[str]) -> list[tuple[Any, ...]]:
        return ledger.consensus_games(until, leagues)

    def markets(self, games: Sequence[int], series: Sequence[str]) -> list[tuple[Any, ...]]:
        return ledger.consensus_markets(games, series)

    def team_names(self, leagues: Sequence[str]) -> dict[str, frozenset[str]]:
        return ledger.consensus_team_names(leagues)

    def aliases(self) -> Mapping[tuple[str, str], str]:
        return ledger.sports_team_aliases()

    def leagues(self) -> list[ledger.SportsLeagueRow]:
        return ledger.sports_leagues()

    def usage(self) -> dict[str, Any]:
        return ledger.odds_api_usage()

    def record_call(self, row: Mapping[str, Any]) -> None:
        ledger.odds_api_call_record(row)

    def stored(self, games: Sequence[str]) -> dict[str, str]:
        return ledger.leg_consensus_stored(games)

    def upsert(self, rows: Sequence[Mapping[str, Any]], at: datetime) -> int:
        return ledger.leg_consensus_upsert(rows, at)

    def sweep(self, games: Sequence[str], keep: Sequence[str]) -> int:
        return ledger.leg_consensus_sweep(games, keep)

    def lines(self, rows: Sequence[Mapping[str, Any]]) -> int:
        return ledger.book_lines_append(rows)

    def prune(self) -> int:
        return ledger.book_lines_prune()

    def tier_counts(self) -> list[tuple[str, str, int, int, int, int]]:
        return ledger.consensus_tier_counts()


def _games(rows: Sequence[tuple[Any, ...]]) -> tuple[list[Game], dict[int, list[str]]]:
    games, tickers = [], {}
    for sid, league, espn_id, starts, home, away, habbr, aabbr, gtickers in rows:
        games.append(Game(int(sid), league, str(espn_id), starts, home or "", away or "", habbr, aabbr))
        tickers[int(sid)] = list(gtickers or [])
    return games, tickers


def _chicago_day(t: datetime) -> date:
    return t.astimezone(OWNER_TZ).date()


def consensus_job(*, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc), store: Store | None = None,
                  odds_http: odds.Http | None = None, espn_http: espn.Http | None = None, key: str | None = None,
                  state: DayState = STATE) -> Run:
    t0 = time.monotonic()
    st = store or Store()
    at = now()
    today = _chicago_day(at)
    if state.day != today:
        state.day, state.prices, state.props = today, {}, set()
    run = Run(kind="afternoon" if at.astimezone(OWNER_TZ).hour >= AFTERNOON_HOUR else "morning")
    leagues = sorted(match.GAME_SERIES)
    games, tickers = _games(st.games(at + WINDOW, leagues))
    run.games = len(games)
    by_id = {g.sports_event_id: g for g in games}
    aliases = st.aliases()

    guard = CreditGuard.load(st.usage)
    closers: list[Callable[[], None]] = []
    if odds_http is None:
        odds_http, close = odds.httpx_get()
        closers.append(close)
    client = odds.OddsClient(odds_http, key if key is not None else odds.key_from_env(),
                             record=odds_credits.recorder(guard, st.record_call), guard=guard.check)
    fresh: set[int] = set()
    try:
        known = st.team_names(sorted(odds.SPORT_KEYS))
        _odds(client, run, games, at, today, state, fresh, known)
        run.misses = list(dict.fromkeys(run.misses))
        run.unboarded = list(dict.fromkeys(run.unboarded))
        espn_by_game, espn_at = _espn(run, games, st, espn_http, at)
        series = sorted({s for lg in leagues for s in match.GAME_SERIES[lg]} | set(match.PROP_SERIES))
        markets = [Market(str(t), str(e), str(s), int(sid), subj, None if line is None else float(line))
                   for t, e, s, sid, subj, line in st.markets(sorted(by_id), series)]
        run.markets = len(markets)
        rows: list[dict[str, Any]] = []
        for m in markets:
            g = by_id[m.sports_event_id]
            snap = state.prices.get(g.sports_event_id)
            views = match.book_views(m, g, snap[0], aliases) if snap else []
            ev = match.espn_view(m, g, espn_by_game.get(g.sports_event_id), aliases)
            if match.kind_of(m.series, g.league) in ("ml", "spread") and match.team_side(m, g, aliases) is None:
                run.side_misses += 1
            row = match.consensus_row(m, views, ev)
            if row is not None:
                src = snap[1] if row["tier"] == "books" and snap else espn_at
                row["source_at"] = src.isoformat()
                rows.append(row)
                run.rows[row["tier"]] = run.rows.get(row["tier"], 0) + 1
        run.written = st.upsert(rows, at)
        # The board refresh re-prices these events first (consensus/priority.py).
        by_ticker = {m.market_ticker: m.event_ticker for m in markets}
        priority.set_events({by_ticker[r["market_ticker"]] for r in rows})
        # The sweep: every game evaluated; a books row of a game with no Odds
        # API prices in this process today is kept, not swept.
        all_tickers = sorted({t for ts in tickers.values() for t in ts})
        keep = {r["market_ticker"] for r in rows}
        stale = [t for sid, ts in tickers.items() if sid not in state.prices for t in ts]
        if stale:
            kept = [k for k, tier in st.stored(stale).items() if tier == "books" and k not in keep]
            run.kept_books = len(kept)
            keep |= set(kept)
        run.swept = st.sweep(all_tickers, sorted(keep))
        lines: list[dict[str, Any]] = []
        for g in games:
            snap = state.prices.get(g.sports_event_id) if g.sports_event_id in fresh else None
            lines += match.book_lines(g, snap[0] if snap else None, espn_by_game.get(g.sports_event_id),
                                      snap[1] if snap else at, espn_at)
        run.lines_written = st.lines(lines)
        run.lines_pruned = st.prune()
        for sport, tier, n, claimed, pos, stale in st.tier_counts():
            run.tiers.setdefault(sport, {})[tier] = n
            run.claimed += claimed
            run.kalshi_positive += pos
            run.stale_positive += stale
            if stale:
                run.stale_by_tier[tier] = run.stale_by_tier.get(tier, 0) + stale
    finally:
        for c in closers:
            c()
        run.credits_run = sum(c.cost or 0 for c in client.calls)
        run.credits_month = guard.spent
        run.remaining = guard.remaining
        run.refused = guard.refused[0] if guard.refused else None
        run.seconds = time.monotonic() - t0
    log.info("consensus: %s", run.job_note)
    first_refusal = odds_credits.after_run(guard)
    why = []
    if run.misses:
        why.append(f"{len(run.misses)} Odds API events in the window matched no board game: "
                   + "; ".join(run.misses[:5]))
    if first_refusal:
        why.append(f"credits: {run.refused}")
    if why:
        raise ConsensusFailed("consensus: " + " | ".join(why) + f" (the rest was written: {run.job_note[:200]})")
    return run


def _odds(client: odds.OddsClient, run: Run, games: Sequence[Game], at: datetime, today: date,
          state: DayState, fresh: set[int], known: Mapping[str, frozenset[str]]) -> None:
    for lg in ("nba", "nfl"):
        lg_games = [g for g in games if g.league == lg]
        if lg == "nfl":
            # NFL game days only (owner, Nov 9): games whose Chicago date is today.
            lg_games = [g for g in lg_games if _chicago_day(g.starts_at) == today]
        if not lg_games:
            continue
        sport = odds.SPORT_KEYS[lg]
        uncovered = [g for g in lg_games if g.sports_event_id not in state.prices]
        events: Mapping[int, Mapping[str, Any]] = {}
        try:
            if uncovered and run.kind == "afternoon":
                # Free first: pay only when /events lists a board game not
                # priced today (tomorrow's NBA games are not listed yet).
                m = match.match_events(client.events(sport), lg_games, at, at + WINDOW, known.get(lg))
                run.misses += m.misses
                run.unboarded += m.unboarded
                events = m.by_game
                uncovered = [g for g in uncovered if g.sports_event_id in m.by_game]
            if uncovered:
                body = client.odds(sport)
                m = match.match_events(body, lg_games, at, at + WINDOW, known.get(lg))
                run.misses += m.misses
                run.unboarded += m.unboarded
                run.odds_events[lg] = (len(m.by_game), len(m.by_game) + len(m.misses))
                for sid, ev in m.by_game.items():
                    state.prices[sid] = (match.parse_event(ev), at)
                    fresh.add(sid)
                events = m.by_game
            if lg == "nfl" and any(g.sports_event_id not in state.props for g in lg_games):
                if not events:
                    m = match.match_events(client.events(sport), lg_games, at, at + WINDOW, known.get(lg))
                    run.misses += m.misses
                    run.unboarded += m.unboarded
                    events = m.by_game
                for g in lg_games:
                    ev = events.get(g.sports_event_id)
                    if g.sports_event_id in state.props or ev is None:
                        continue
                    body = client.event_odds(sport, str(ev["id"]), PROP_MARKETS)
                    body = dict(body) | {"_home": ev["_home"], "_away": ev["_away"]}
                    bp, _ = state.prices.get(g.sports_event_id, (BookPrices(), at))
                    state.prices[g.sports_event_id] = (match.parse_event(body, bp), at)
                    state.props.add(g.sports_event_id)
                    fresh.add(g.sports_event_id)
                    run.props_games += 1
        except CreditRefused as exc:
            log.warning("consensus: %s", exc)
            return


def _espn(run: Run, games: Sequence[Game], st: Store, http: espn.Http | None,
          at: datetime) -> tuple[dict[int, espn_odds.EspnOdds], datetime]:
    from sports.jobs import league_of

    want = [g for g in games if g.league in espn_odds.ODDS_LEAGUES]
    if not want:
        return {}, at
    leagues = {r.code: league_of(r) for r in st.leagues() if r.code in espn_odds.ODDS_LEAGUES}
    close = None
    if http is None:
        http, close = espn.httpx_get()
    client = espn.Espn(http)
    out: dict[int, espn_odds.EspnOdds] = {}
    try:
        days: dict[str, set[date]] = {}
        for g in want:
            days.setdefault(g.league, set()).add(eastern_day(g.starts_at))
        parsed: dict[tuple[str, str], espn_odds.EspnOdds] = {}
        for lg, ds in sorted(days.items()):
            if lg not in leagues:
                continue
            for d in sorted(ds):
                for eid, o in espn_odds.parse(client.scoreboard(leagues[lg], [d])).items():
                    parsed[(lg, eid)] = o
        for g in want:
            o = parsed.get((g.league, g.espn_id))
            if o is not None:
                out[g.sports_event_id] = o
    finally:
        run.espn_calls = client.calls
        if close:
            close()
    run.espn_games = (len(out), len(want))
    return out, at
