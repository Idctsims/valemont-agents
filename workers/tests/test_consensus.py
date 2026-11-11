"""The consensus engine (Chat 8 Phase 2; db/073; consensus/, core/odds_credits.py,
venues/odds/). No database, no network: a fake Store and fake HTTP.

The blend, the fees and the edges are SQL's (db/073 consensus_leg); their
tests are tests_live/test_consensus_sql.py. Here: the de-vig against its
oracle, the matching (exact lines only), the tiers, the credit guard, the
client, and the job's spending rules.
"""

from __future__ import annotations

import json
import pathlib
import unittest
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping
from unittest import mock

from consensus import devig, espn_odds, job, match
from consensus.match import BookPrices, Game, Market
from core import ledger, odds_credits
from core.odds_credits import CreditGuard, CreditRefused
from venues.odds import client as odds

UTC = timezone.utc
ORACLE = pathlib.Path(__file__).parent / "fixtures" / "devig_oracle.json"
#: Mon Nov 9, 2026, 16:15 UTC = 10:15 Chicago (the morning run).
MORNING = datetime(2026, 11, 9, 16, 15, tzinfo=UTC)
AFTERNOON = datetime(2026, 11, 9, 22, 15, tzinfo=UTC)


# ------------------------------------------------------------------ de-vig

class Devig(unittest.TestCase):
    def test_matches_the_penaltyblog_oracle_to_1e_9(self) -> None:
        data = json.loads(ORACLE.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["cases"]), 200)
        for case in data["cases"]:
            for method, fn in (("power", devig.power), ("multiplicative", devig.multiplicative)):
                got = fn(case["odds"])
                for g, w in zip(got, case[method], strict=True):
                    self.assertAlmostEqual(g, w, delta=1e-9, msg=f"{method} {case['odds']}")

    def test_the_oracle_itself_when_installed(self) -> None:
        try:
            import penaltyblog.implied as im
        except ImportError:
            self.skipTest("penaltyblog is dev-only (requirements-dev.txt); the stored oracle test covers it")
        for odds_ in ([1.625, 2.35], [1.5, 4.2, 6.5], [1.9091, 1.9091]):
            for method, fn in (("power", devig.power), ("multiplicative", devig.multiplicative)):
                ref = im.calculate_implied(odds_, method=method).probabilities
                for g, w in zip(fn(odds_), ref, strict=True):
                    self.assertAlmostEqual(g, w, delta=1e-9)

    def test_power_sums_to_one_and_shades_the_longshot_more(self) -> None:
        odds_ = [devig.american_to_decimal(-300), devig.american_to_decimal(240)]
        p, m = devig.power(odds_), devig.multiplicative(odds_)
        self.assertAlmostEqual(sum(p), 1.0, places=12)
        self.assertGreater(p[0], m[0])  # the favourite keeps more under power
        self.assertLess(p[1], m[1])

    def test_american_prices(self) -> None:
        self.assertEqual(devig.american_to_decimal(150), 2.5)
        self.assertEqual(devig.american_to_decimal(-160), 1.625)
        self.assertEqual(devig.american_to_decimal(100), 2.0)
        with self.assertRaises(devig.DevigError):
            devig.american_to_decimal(50)
        with self.assertRaises(devig.DevigError):
            devig.power([1.9])


# ------------------------------------------------------------------ matching

def game(**kw: Any) -> Game:
    base = dict(sports_event_id=183, league="nba", espn_id="401", starts_at=MORNING + timedelta(hours=8),
                home="Toronto Raptors", away="Memphis Grizzlies", home_abbr="TOR", away_abbr="MEM")
    return Game(**(base | kw))


def odds_event(home: str = "Toronto Raptors", away: str = "Memphis Grizzlies", *, start: datetime | None = None,
               books: Mapping[str, list[dict[str, Any]]] | None = None, eid: str = "ev1") -> dict[str, Any]:
    start = start or MORNING + timedelta(hours=8)
    default = {
        "draftkings": [
            {"key": "h2h", "outcomes": [{"name": away, "price": 114}, {"name": home, "price": -135}]},
            {"key": "spreads", "outcomes": [{"name": away, "price": -110, "point": 2.5},
                                            {"name": home, "price": -110, "point": -2.5}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": -110, "point": 221.5},
                                           {"name": "Under", "price": -110, "point": 221.5}]}],
        "fanduel": [
            {"key": "h2h", "outcomes": [{"name": away, "price": 120}, {"name": home, "price": -142}]},
            {"key": "spreads", "outcomes": [{"name": away, "price": -112, "point": 2.5},
                                            {"name": home, "price": -108, "point": -2.5}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": -105, "point": 222.5},
                                           {"name": "Under", "price": -115, "point": 222.5}]}],
    }
    return {"id": eid, "commence_time": start.isoformat().replace("+00:00", "Z"), "home_team": home,
            "away_team": away,
            "bookmakers": [{"key": k, "markets": v} for k, v in (books or default).items()]}


def mkt(ticker: str, series: str, line: float | None = None, subject: str | None = None, sid: int = 183) -> Market:
    return Market(ticker, ticker.rsplit("-", 1)[0], series, sid, subject, line)


class Events(unittest.TestCase):
    def test_matched_by_names_within_6_h(self) -> None:
        m = match.match_events([odds_event()], [game()], MORNING, MORNING + timedelta(hours=36))
        self.assertEqual(list(m.by_game), [183])
        self.assertEqual(m.misses, [])

    def test_the_odds_name_alias(self) -> None:
        g = game(home="LA Clippers", away="Phoenix Suns", home_abbr="LAC", away_abbr="PHX")
        m = match.match_events([odds_event("Los Angeles Clippers", "Phoenix Suns")], [g], MORNING,
                               MORNING + timedelta(hours=36))
        self.assertEqual(list(m.by_game), [183])
        self.assertEqual(match.espn_name("Los Angeles Clippers"), "LA Clippers")

    def test_an_unknown_name_is_a_miss_a_known_unboarded_game_is_not(self) -> None:
        known = frozenset({"toronto raptors", "memphis grizzlies", "boston celtics"})
        evs = [odds_event("Gotham Knights", "Memphis Grizzlies", eid="x"),
               odds_event("Boston Celtics", "Memphis Grizzlies", eid="y")]
        m = match.match_events(evs, [game()], MORNING, MORNING + timedelta(hours=36), known)
        self.assertEqual(len(m.misses), 1)
        self.assertIn("unknown name Gotham Knights", m.misses[0])
        self.assertEqual(len(m.unboarded), 1)

    def test_outside_the_window_and_too_far_in_time(self) -> None:
        far = odds_event(start=MORNING + timedelta(days=40))
        late = odds_event(start=MORNING + timedelta(hours=16))  # board game at +8 h: 8 h apart
        m = match.match_events([far, late], [game()], MORNING, MORNING + timedelta(hours=36),
                               frozenset({"toronto raptors", "memphis grizzlies"}))
        self.assertEqual(m.outside, 1)
        self.assertEqual(m.by_game, {})
        self.assertEqual(len(m.unboarded), 1)


class Markets(unittest.TestCase):
    def setUp(self) -> None:
        ev = odds_event() | {"_home": "Toronto Raptors", "_away": "Memphis Grizzlies"}
        self.bp = match.parse_event(ev)
        self.g = game()

    def views(self, m: Market) -> list[match.Priced]:
        return match.book_views(m, self.g, self.bp, {})

    def test_moneyline_both_sides(self) -> None:
        mem = self.views(mkt("KXNBAGAME-26NOV09MEMTOR-MEM", "KXNBAGAME"))
        tor = self.views(mkt("KXNBAGAME-26NOV09MEMTOR-TOR", "KXNBAGAME"))
        self.assertEqual({v.book for v in mem}, {"draftkings", "fanduel"})
        dk_mem = next(v for v in mem if v.book == "draftkings")
        dk_tor = next(v for v in tor if v.book == "draftkings")
        self.assertAlmostEqual(dk_mem.p_power + dk_tor.p_power, 1.0, places=12)
        self.assertLess(dk_mem.p_power, 0.5)  # the away dog at +114

    def test_spread_exact_both_sides_and_nothing_else(self) -> None:
        # "Toronto wins by over 2.5" = Toronto -2.5 (both books).
        tor = self.views(mkt("KXNBASPREAD-26NOV09MEMTOR-TOR3", "KXNBASPREAD", 2.5))
        self.assertEqual(len(tor), 2)
        # "Memphis wins by over 2.5" = Memphis -2.5: no book has it.
        self.assertEqual(self.views(mkt("KXNBASPREAD-26NOV09MEMTOR-MEM3", "KXNBASPREAD", 2.5)), [])
        # A rung with no exact line stays kalshi tier: no interpolation.
        self.assertEqual(self.views(mkt("KXNBASPREAD-26NOV09MEMTOR-TOR4", "KXNBASPREAD", 3.5)), [])

    def test_total_exact_line_only(self) -> None:
        v = self.views(mkt("KXNBATOTAL-26NOV09MEMTOR-221", "KXNBATOTAL", 221.5))
        self.assertEqual([x.book for x in v], ["draftkings"])  # fanduel lists 222.5
        self.assertAlmostEqual(v[0].p_power, 0.5, places=12)

    def test_team_side_by_ticker_code_and_alias(self) -> None:
        g = game(league="nba", home_abbr="GS", away_abbr="NO")
        aliases = {("nba", "GS"): "GSW", ("nba", "NO"): "NOP"}
        self.assertEqual(match.team_side(mkt("KXNBAGAME-26NOV09NOPGSW-GSW", "KXNBAGAME"), g, aliases), "home")
        self.assertEqual(match.team_side(mkt("KXNBASPREAD-26NOV09NOPGSW-NOP12", "KXNBASPREAD"), g, aliases),
                         "away")
        self.assertEqual(match.team_side(mkt("KXEPLGAME-26NOV21ARSCHE-TIE", "KXEPLGAME"), g, aliases), "draw")
        self.assertIsNone(match.team_side(mkt("KXNBAGAME-26NOV09NOPGSW-BOS", "KXNBAGAME"), g, aliases))

    def test_soccer_three_ways(self) -> None:
        g = game(league="epl", home="Arsenal", away="Chelsea", home_abbr="ARS", away_abbr="CHE")
        books = {b: [{"key": "h2h", "outcomes": [{"name": "Arsenal", "price": -120},
                                                 {"name": "Chelsea", "price": 320},
                                                 {"name": "Draw", "price": 270}]}] for b in ("a", "b")}
        bp = match.parse_event(odds_event("Arsenal", "Chelsea", books=books) | {"_home": "Arsenal", "_away": "Chelsea"})
        ps = [match.book_views(mkt(f"KXEPLGAME-26NOV21ARSCHE-{c}", "KXEPLGAME"), g, bp, {})[0].p_power
              for c in ("ARS", "CHE", "TIE")]
        self.assertAlmostEqual(sum(ps), 1.0, places=12)

    def test_props_exact_and_dst_keys(self) -> None:
        g = game(league="nfl", home="Minnesota Vikings", away="Buffalo Bills", home_abbr="MIN", away_abbr="BUF")
        books = {b: [{"key": "player_receptions", "outcomes": [
                    {"name": "Over", "description": "James Cook III", "price": -120, "point": 2.5},
                    {"name": "Under", "description": "James Cook III", "price": -105, "point": 2.5}]}]
                 for b in ("dk", "fd", "mgm")}
        bp = match.parse_event(odds_event("Minnesota Vikings", "Buffalo Bills", books=books)
                               | {"_home": "Minnesota Vikings", "_away": "Buffalo Bills"})
        # Kalshi "James Cook III: 3+" is line 2.5; the suffix does not split them.
        v = match.book_views(mkt("KXNFLREC-26NOV09BUFMIN-BUFJCOOK4-3", "KXNFLREC", 2.5, "James Cook III", 140),
                             g, bp, {})
        self.assertEqual(len(v), 3)
        self.assertEqual(match.book_views(mkt("KXNFLREC-26NOV09BUFMIN-BUFJCOOK4-4", "KXNFLREC", 3.5,
                                              "James Cook III", 140), g, bp, {}), [])
        self.assertEqual(match.player_key("BUF Bills D/ST"), match.player_key("Buffalo Bills D/ST"))
        self.assertEqual(match.player_key("Aaron Jones Sr."), "aaron jones")

    def test_anytime_td_is_stored_yes_only_never_priced(self) -> None:
        books = {"dk": [{"key": "player_anytime_td", "outcomes": [
            {"name": "Yes", "description": "Josh Allen", "price": 110}]}]}
        bp = match.parse_event(odds_event(books=books) | {"_home": "Toronto Raptors", "_away": "Memphis Grizzlies"})
        rows = match.book_lines(game(), bp, None, MORNING, None)
        td = [r for r in rows if r["market"] == "anytime_td"]
        self.assertEqual(len(td), 1)
        self.assertIsNone(td[0]["price_b"])
        self.assertIsNone(td[0]["p_a"])


class Tiers(unittest.TestCase):
    def p(self, book: str, x: float) -> match.Priced:
        return match.Priced(book, x, x + 0.001, (-110, -110))

    def test_two_books_is_the_books_tier_with_the_median(self) -> None:
        m = mkt("KXNBAGAME-X-TOR", "KXNBAGAME")
        r = match.consensus_row(m, [self.p("a", 0.50), self.p("b", 0.60), self.p("c", 0.52)], self.p("espn_dk", 0.55))
        self.assertEqual((r["tier"], r["n_books"], r["p_books"], r["p_espn"]), ("books", 3, 0.52, 0.55))
        self.assertEqual(r["p_books_mult"], 0.521)
        self.assertEqual(r["inputs"]["m"], "power")

    def test_one_book_falls_to_espn_or_to_kalshi(self) -> None:
        m = mkt("KXNBAGAME-X-TOR", "KXNBAGAME")
        r = match.consensus_row(m, [self.p("a", 0.50)], self.p("espn_dk", 0.55))
        self.assertEqual((r["tier"], r["n_books"], r["p_espn"], r["p_books"]), ("espn", 1, 0.55, None))
        self.assertIsNone(match.consensus_row(m, [self.p("a", 0.50)], None))
        self.assertIsNone(match.consensus_row(m, [], None))


class EspnOdds(unittest.TestCase):
    BODY = {"events": [{"id": "401873051", "competitions": [{"odds": [{
        "provider": {"name": "DraftKings"},
        "moneyline": {"home": {"close": {"odds": "+160"}}, "away": {"close": {"odds": "-192"}}},
        "pointSpread": {"home": {"close": {"line": "+3.5", "odds": "-115"}},
                        "away": {"close": {"line": "-3.5", "odds": "-105"}}},
        "total": {"over": {"close": {"line": "o45.5", "odds": "-105"}},
                  "under": {"close": {"line": "u45.5", "odds": "-115"}}}}]}]},
        {"id": "mls1", "competitions": [{"odds": [None]}]}]}

    def test_parse_the_close_line(self) -> None:
        o = espn_odds.parse(self.BODY)
        self.assertEqual(list(o), ["401873051"])
        e = o["401873051"]
        self.assertEqual((e.ml_home, e.ml_away, e.spread_home, e.total, e.over_price), (160, -192, 3.5, 45.5, -105))

    def test_espn_view_exact_line(self) -> None:
        e = espn_odds.parse(self.BODY)["401873051"]
        g = game(league="nfl", home="Minnesota Vikings", away="Buffalo Bills", home_abbr="MIN", away_abbr="BUF")
        buf = match.espn_view(mkt("KXNFLSPREAD-26NOV09BUFMIN-BUF4", "KXNFLSPREAD", 3.5), g, e, {})
        self.assertIsNotNone(buf)
        self.assertIsNone(match.espn_view(mkt("KXNFLSPREAD-26NOV09BUFMIN-BUF3", "KXNFLSPREAD", 2.5), g, e, {}))
        self.assertIsNotNone(match.espn_view(mkt("KXNFLTOTAL-26NOV09BUFMIN-46", "KXNFLTOTAL", 45.5), g, e, {}))
        self.assertEqual(espn_odds.price("EVEN"), 100)
        self.assertIsNone(espn_odds.price("OFF"))


# ------------------------------------------------------------------ credits

def call(cost: int, remaining: int | None, status: int = 200) -> odds.Call:
    return odds.Call("/sports/:sport/odds", "basketball_nba", None, "h2h", "us", status, cost, None, remaining, 10)


class Credits(unittest.TestCase):
    def test_refuses_under_50_remaining(self) -> None:
        g = CreditGuard(10, 49)
        with self.assertRaisesRegex(CreditRefused, "remaining 49 < 50"):
            g.check(3)
        CreditGuard(10, 50).check(3)

    def test_refuses_past_450_this_month(self) -> None:
        CreditGuard(447, 300).check(3)  # 450 exactly: allowed
        with self.assertRaisesRegex(CreditRefused, "would pass 450"):
            CreditGuard(448, 300).check(3)
        with self.assertRaisesRegex(CreditRefused, "would pass 450"):
            CreditGuard(451, 300).check(1)

    def test_the_run_s_own_calls_count(self) -> None:
        g = CreditGuard(440, 100)
        g.note(call(9, 91))
        with self.assertRaises(CreditRefused):
            g.check(3)
        g2 = CreditGuard(0, 60)
        g2.note(call(3, 49))
        with self.assertRaisesRegex(CreditRefused, "remaining 49"):
            g2.check(3)

    def test_the_spend_is_the_account_s_own_count(self) -> None:
        """db/074 (owner, Nov 9): the latest x-requests-used, not the log's
        sum (which misses Step 0's 11 credits)."""
        g = CreditGuard.load(lambda: {"used": 20, "spent": 9, "remaining": 480})
        self.assertEqual(g.spent, 20)
        g.note(call(3, 477))
        self.assertEqual(g.spent, 23)  # a call with no used header: start + cost
        g.note(odds.Call("/sports/:sport/odds", "x", None, "h2h", "us", 200, 3, 26, 474, 1))
        self.assertEqual(g.spent, 26)  # the header wins
        self.assertEqual(CreditGuard.load(lambda: {"spent": 9, "remaining": 480}).spent, 9)  # before db/074
        with self.assertRaisesRegex(CreditRefused, "spend 448"):
            CreditGuard.load(lambda: {"used": 448, "spent": 0, "remaining": 52}).check(3)

    def test_pushes_once_a_month_at_400_and_on_the_first_refusal(self) -> None:
        sent: list[str] = []
        with mock.patch.object(ledger, "notification_sent_this_month", side_effect=lambda k: k in sent), \
                mock.patch("core.push.send", side_effect=lambda m, **kw: sent.append(m.kind)):
            g = CreditGuard(399, 200)
            g.note(call(3, 197))
            self.assertFalse(odds_credits.after_run(g))
            self.assertEqual(sent, ["odds_credits_400"])
            self.assertFalse(odds_credits.after_run(g))
            self.assertEqual(sent, ["odds_credits_400"])
            g.refused.append("remaining 40 < 50")
            self.assertTrue(odds_credits.after_run(g))   # the month's first: the run fails loud
            self.assertFalse(odds_credits.after_run(g))  # later ones: noted, not failed
            self.assertEqual(sent, ["odds_credits_400", "odds_credits_refused"])


class Client(unittest.TestCase):
    def http(self, status: int = 200, body: Any = None, headers: Mapping[str, str] | None = None):
        seen: list[tuple[str, dict[str, str]]] = []

        def get(url: str, params: Mapping[str, str]) -> odds.Resp:
            seen.append((url, dict(params)))
            return odds.Resp(status, [] if body is None else body,
                             headers or {"x-requests-last": "3", "x-requests-used": "14", "x-requests-remaining": "486"},
                             100)
        return get, seen

    def test_every_call_recorded_paid_ones_guarded(self) -> None:
        get, seen = self.http()
        rec: list[odds.Call] = []
        guarded: list[int] = []
        c = odds.OddsClient(get, "SECRETKEY", record=rec.append, guard=guarded.append)
        c.events("basketball_nba")
        c.odds("basketball_nba")
        c.event_odds("americanfootball_nfl", "abc123", "player_receptions,player_reception_yds,player_rush_yds")
        self.assertEqual(guarded, [3, 3])
        self.assertEqual([r.endpoint for r in rec], ["/sports/:sport/events", "/sports/:sport/odds",
                                                    "/sports/:sport/events/:event/odds"])
        self.assertEqual((rec[1].cost, rec[1].used, rec[1].remaining), (3, 14, 486))
        self.assertEqual(seen[1][1]["markets"], "h2h,spreads,totals")
        self.assertEqual(seen[1][1]["regions"], "us")
        for r in rec:
            self.assertNotIn("SECRETKEY", json.dumps(r.row()))

    def test_a_refusal_happens_before_the_request(self) -> None:
        get, seen = self.http()
        def refuse(cost: int) -> None:
            raise CreditRefused("no")
        c = odds.OddsClient(get, "K", record=lambda _: None, guard=refuse)
        with self.assertRaises(CreditRefused):
            c.odds("basketball_nba")
        self.assertEqual(seen, [])

    def test_401_and_429_fail_loud_and_never_name_the_key(self) -> None:
        for status, word in ((401, "unauthorized"), (429, "limited"), (500, "failed")):
            get, _ = self.http(status=status)
            rec: list[odds.Call] = []
            c = odds.OddsClient(get, "SECRETKEY", record=rec.append, guard=lambda _: None)
            with self.assertRaises(odds.OddsApiError) as cm:
                c.odds("basketball_nba")
            self.assertIn(word, str(cm.exception))
            self.assertNotIn("SECRETKEY", str(cm.exception))
            self.assertEqual(rec[0].status, status)  # recorded even when it fails

    def test_no_key_refuses_to_start(self) -> None:
        with self.assertRaises(odds.OddsApiError):
            odds.OddsClient(lambda u, p: None, "", record=lambda _: None, guard=lambda _: None)


# ------------------------------------------------------------------ the job

class FakeStore(job.Store):
    def __init__(self, games: list[Game], markets: list[Market], *, usage: Mapping[str, Any] | None = None,
                 stored: Mapping[str, str] | None = None) -> None:
        self._games, self._markets = games, markets
        self._usage = dict(usage or {"spent": 0, "remaining": 480})
        self._stored = dict(stored or {})
        self.calls: list[dict[str, Any]] = []
        self.upserted: list[dict[str, Any]] = []
        self.swept: tuple[list[str], list[str]] | None = None
        self.lines_rows: list[dict[str, Any]] = []

    def games(self, until, leagues):
        return [(g.sports_event_id, g.league, g.espn_id, g.starts_at, g.home, g.away, g.home_abbr, g.away_abbr,
                 [f"G{g.sports_event_id}"]) for g in self._games]

    def markets(self, games, series):
        return [(m.market_ticker, m.event_ticker, m.series, m.sports_event_id, m.subject, m.line)
                for m in self._markets if m.sports_event_id in games]

    def aliases(self):
        return {}

    def team_names(self, leagues):
        from sports.kalshi_map import fold
        return {lg: frozenset(fold(n) for g in self._games if g.league == lg for n in (g.home, g.away))
                for lg in leagues}

    def leagues(self):
        return []

    def usage(self):
        return self._usage

    def record_call(self, row):
        self.calls.append(dict(row))

    def stored(self, games):
        return self._stored

    def upsert(self, rows, at):
        self.upserted = list(rows)
        return len(rows)

    def sweep(self, games, keep):
        self.swept = (list(games), list(keep))
        return 0

    def lines(self, rows):
        self.lines_rows = list(rows)
        return len(rows)

    def prune(self):
        return 0

    def tier_counts(self):
        return [("Basketball", "books", 2, 0, 0, 0)]


class FakeOdds:
    def __init__(self, bodies: Mapping[str, Any], cost: int = 3, remaining: int = 480) -> None:
        self.bodies, self.cost, self.remaining = bodies, cost, remaining
        self.paths: list[str] = []

    def __call__(self, url: str, params: Mapping[str, str]) -> odds.Resp:
        path = url.split("/v4", 1)[1]
        self.paths.append(path)
        paid = path.endswith("/odds")
        if paid:
            self.remaining -= self.cost
        return odds.Resp(200, self.bodies.get(path, []),
                         {"x-requests-last": str(self.cost if paid else 0), "x-requests-remaining": str(self.remaining)},
                         10)


class Job(unittest.TestCase):
    def setUp(self) -> None:
        p = mock.patch.object(job, "_espn", side_effect=lambda run, games, st, http, at: ({}, at))
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(odds_credits, "after_run", return_value=False)
        self.after = q.start()
        self.addCleanup(q.stop)

    def run_job(self, store: FakeStore, http: FakeOdds, now: datetime, state: job.DayState) -> job.Run:
        return job.consensus_job(now=lambda: now, store=store, odds_http=http, key="K", state=state)

    def nba(self) -> tuple[list[Game], list[Market]]:
        g = game()
        return [g], [mkt("KXNBAGAME-26NOV09MEMTOR-MEM", "KXNBAGAME"), mkt("KXNBAGAME-26NOV09MEMTOR-TOR", "KXNBAGAME"),
                     mkt("KXNBASPREAD-26NOV09MEMTOR-TOR3", "KXNBASPREAD", 2.5),
                     mkt("KXNBASPREAD-26NOV09MEMTOR-TOR6", "KXNBASPREAD", 5.5)]

    def test_morning_pays_once_and_writes_books_rows(self) -> None:
        games, markets = self.nba()
        st, http, state = FakeStore(games, markets), FakeOdds({"/sports/basketball_nba/odds": [odds_event()]}), job.DayState()
        run = self.run_job(st, http, MORNING, state)
        self.assertEqual(http.paths, ["/sports/basketball_nba/odds"])
        self.assertEqual(run.credits_run, 3)
        self.assertEqual({r["market_ticker"] for r in st.upserted},
                         {"KXNBAGAME-26NOV09MEMTOR-MEM", "KXNBAGAME-26NOV09MEMTOR-TOR", "KXNBASPREAD-26NOV09MEMTOR-TOR3"})
        self.assertTrue(all(r["tier"] == "books" for r in st.upserted))
        self.assertEqual(len(st.calls), 1)
        self.assertEqual(st.calls[0]["cost"], 3)
        self.assertTrue(any(r["market"] == "ml" for r in st.lines_rows))
        self.assertIn("credits 3 this run", run.job_note)

    def test_the_note_splits_stale_positives_by_tier(self) -> None:
        # Owner, Nov 10: books vs espn, summed across sports.
        games, markets = self.nba()
        st = FakeStore(games, markets)
        st.tier_counts = lambda: [("Basketball", "books", 18, 0, 0, 3), ("Basketball", "espn", 128, 0, 0, 20),
                                  ("Hockey", "espn", 28, 0, 0, 4), ("Basketball", "kalshi", 8663, 0, 27, 0)]
        run = self.run_job(st, FakeOdds({"/sports/basketball_nba/odds": [odds_event()]}), MORNING, job.DayState())
        self.assertEqual(run.stale_by_tier, {"books": 3, "espn": 24})
        self.assertIn("stale, not claimed 27 (books 3, espn 24); kalshi-tier positive 27", run.job_note)
        self.assertLess(run.job_note.index("(books 3, espn 24)"), 180)  # inside job_health's 500 characters

    def test_the_note_splits_claimed_by_tier(self) -> None:
        # Owner, Nov 11: claimed books vs espn, summed across sports.
        games, markets = self.nba()
        st = FakeStore(games, markets)
        st.tier_counts = lambda: [("Basketball", "books", 18, 2, 0, 0), ("Basketball", "espn", 128, 5, 0, 1),
                                  ("Hockey", "espn", 28, 1, 0, 0), ("Basketball", "kalshi", 8663, 0, 4, 0)]
        run = self.run_job(st, FakeOdds({"/sports/basketball_nba/odds": [odds_event()]}), MORNING, job.DayState())
        self.assertEqual(run.claimed, 8)
        self.assertEqual(run.claimed_by_tier, {"books": 2, "espn": 6})
        self.assertIn("(claimed 8 (books 2, espn 6); stale, not claimed 1 (books 0, espn 1)", run.job_note)
        self.assertLess(run.job_note.index("kalshi-tier positive"), 200)

    def test_afternoon_asks_free_first_and_pays_nothing_when_covered(self) -> None:
        games, markets = self.nba()
        state = job.DayState()
        self.run_job(FakeStore(games, markets), FakeOdds({"/sports/basketball_nba/odds": [odds_event()]}),
                     MORNING, state)
        http = FakeOdds({"/sports/basketball_nba/events": [odds_event()], "/sports/basketball_nba/odds": [odds_event()]})
        st = FakeStore(games, markets)
        run = self.run_job(st, http, AFTERNOON, state)
        self.assertEqual(http.paths, [])  # covered today: not even /events
        self.assertEqual(run.credits_run, 0)
        # The morning's prices still price the books rows (process memory).
        self.assertEqual(len([r for r in st.upserted if r["tier"] == "books"]), 3)

    def test_afternoon_pays_only_for_a_listed_uncovered_game(self) -> None:
        games, markets = self.nba()
        tomorrow = game(sports_event_id=188, home="New York Knicks", away="Milwaukee Bucks", home_abbr="NY",
                        away_abbr="MIL", starts_at=AFTERNOON + timedelta(hours=26))
        state = job.DayState()
        self.run_job(FakeStore(games, markets), FakeOdds({"/sports/basketball_nba/odds": [odds_event()]}),
                     MORNING, state)
        # Not listed yet: /events (free) only.
        http = FakeOdds({"/sports/basketball_nba/events": [odds_event()]})
        run = self.run_job(FakeStore(games + [tomorrow], markets), http, AFTERNOON, state)
        self.assertEqual(http.paths, ["/sports/basketball_nba/events"])
        self.assertEqual(run.credits_run, 0)
        # Listed: one paid call.
        knicks = odds_event("New York Knicks", "Milwaukee Bucks", start=tomorrow.starts_at, eid="k")
        http2 = FakeOdds({"/sports/basketball_nba/events": [odds_event(), knicks],
                          "/sports/basketball_nba/odds": [odds_event(), knicks]})
        run2 = self.run_job(FakeStore(games + [tomorrow], markets), http2, AFTERNOON, state)
        self.assertEqual(http2.paths, ["/sports/basketball_nba/events", "/sports/basketball_nba/odds"])
        self.assertEqual(run2.credits_run, 3)

    def test_nfl_mainlines_and_props_on_nfl_game_days_only(self) -> None:
        mnf = game(sports_event_id=140, league="nfl", home="Minnesota Vikings", away="Buffalo Bills", home_abbr="MIN",
                   away_abbr="BUF", starts_at=datetime(2026, 11, 10, 1, 15, tzinfo=UTC))
        sunday = game(sports_event_id=150, league="nfl", home="Detroit Lions", away="New England Patriots",
                      home_abbr="DET", away_abbr="NE", starts_at=datetime(2026, 11, 15, 18, 0, tzinfo=UTC))
        ev = odds_event("Minnesota Vikings", "Buffalo Bills", start=mnf.starts_at, eid="mnf1")
        bodies = {"/sports/americanfootball_nfl/odds": [ev],
                  "/sports/americanfootball_nfl/events/mnf1/odds": ev | {"bookmakers": []}}
        http = FakeOdds(bodies)
        run = self.run_job(FakeStore([mnf, sunday], []), http, MORNING, job.DayState())
        self.assertEqual(http.paths, ["/sports/americanfootball_nfl/odds", "/sports/americanfootball_nfl/events/mnf1/odds"])
        self.assertEqual(run.props_games, 1)
        self.assertEqual(run.credits_run, 6)
        # Tuesday (no NFL game that Chicago day): nothing NFL is paid for.
        http2 = FakeOdds(bodies)
        self.run_job(FakeStore([sunday], []), http2, datetime(2026, 11, 10, 16, 15, tzinfo=UTC), job.DayState())
        self.assertEqual(http2.paths, [])

    def test_an_unknown_odds_name_fails_after_the_writes(self) -> None:
        games, markets = self.nba()
        bad = odds_event("Gotham Knights", "Memphis Grizzlies", eid="z")
        st = FakeStore(games, markets)
        with self.assertRaisesRegex(job.ConsensusFailed, "unknown name Gotham Knights"):
            self.run_job(st, FakeOdds({"/sports/basketball_nba/odds": [odds_event(), bad]}), MORNING, job.DayState())
        self.assertTrue(st.upserted)  # written first

    def test_the_first_refusal_fails_loud_later_ones_do_not(self) -> None:
        games, markets = self.nba()
        self.after.return_value = True
        st = FakeStore(games, markets, usage={"spent": 449, "remaining": 51})
        http = FakeOdds({"/sports/basketball_nba/odds": [odds_event()]})
        with self.assertRaisesRegex(job.ConsensusFailed, "credits: this month's spend 449"):
            self.run_job(st, http, MORNING, job.DayState())
        self.assertEqual(http.paths, [])
        self.after.return_value = False
        run = self.run_job(FakeStore(games, markets, usage={"spent": 449, "remaining": 51}), FakeOdds({}), MORNING,
                           job.DayState())
        self.assertIn("REFUSED", run.job_note)

    def test_the_sweep_keeps_books_rows_it_could_not_reprice(self) -> None:
        games, markets = self.nba()
        st = FakeStore(games, markets, stored={"KXNBAGAME-26NOV09MEMTOR-MEM": "books", "KXOLD-X": "espn"})
        # A refusal: no Odds API prices in this process today.
        with mock.patch.object(CreditGuard, "check", side_effect=CreditRefused("refused")):
            run = self.run_job(st, FakeOdds({}), MORNING, job.DayState())
        self.assertEqual(st.swept[0], ["G183"])
        self.assertIn("KXNBAGAME-26NOV09MEMTOR-MEM", st.swept[1])
        self.assertNotIn("KXOLD-X", st.swept[1])
        self.assertEqual(run.kept_books, 1)


if __name__ == "__main__":
    unittest.main()
