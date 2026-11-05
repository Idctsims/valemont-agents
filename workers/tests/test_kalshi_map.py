"""Kalshi mapping (Chat 7 Phase 2; db/066; sports/kalshi_map.py,
venues/kalshi/events.py). No database, no network: the ledger is stubbed, the
Kalshi transport is a fake serving trimmed real responses
(tests/fixtures/kalshi, recorded 2026-11-05 from the open listing: chosen
events whole with their milestones; golf and F1 market lists cut to 4).

The ESPN side is MapRow values with ESPN's real codes and starts for those
games, as sports_events holds them.
"""

from __future__ import annotations

import copy
import json
import logging
import pathlib
import re
import unittest
import urllib.parse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest import mock

from core import ledger
from scripts.kalshi_map_dry_run import migration_seed
from sports import espn, jobs, kalshi_map as km
from sports.espn import EspnShapeError, League
from venues.kalshi.client import KalshiClient
from venues.kalshi.events import (KalshiEvent, KalshiShapeError, Side, check_series, open_pages, parse_events_page,
                                  ticker_day)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "kalshi"
ESPN_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "sports"
UTC = timezone.utc
#: The fixtures' moment: Nov 5, 2026, 02:00 UTC (Nov 4, 9 PM ET).
NOW = datetime(2026, 11, 5, 2, 0, tzinfo=UTC)

SEED_REGISTRY, SEED_ALIASES = migration_seed()
ALIASES = {(lg, e): k for lg, e, k, _ in SEED_ALIASES}
SPORT = {"nfl": "football", "cfb": "football", "nba": "basketball", "mcbb": "basketball", "nhl": "hockey",
         "mlb": "baseball", "epl": "soccer", "mls": "soccer", "ucl": "soccer", "atp": "tennis", "wta": "tennis",
         "ufc": "mma", "f1": "racing", "pga": "golf"}
KIND = {"f1": "race_session", "pga": "golf_round", "atp": "match", "wta": "match", "ufc": "bout"}


def league(code: str) -> km.MapLeague:
    series, method, _ = SEED_REGISTRY[code]
    return km.MapLeague(code, KIND.get(code, "game"), SPORT[code], series, method)


def body(series: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"kalshi_{series}.json").read_bytes())


def events(series: str) -> list[KalshiEvent]:
    return parse_events_page(series, body(series)).events


def at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


_ids = iter(range(1, 10_000))


def game(lg: str, away: str | None, home: str | None, starts: str, *, state: str | None = None,
         ticker: str | None = None, conflict: str | None = None, rid: int | None = None) -> ledger.MapRow:
    return ledger.MapRow(rid or next(_ids), lg, "game", None, at(starts), home, away, (), state, ticker, conflict)


def pair(lg: str, kind: str, a: str, b: str, starts: str, **kw: Any) -> ledger.MapRow:
    return ledger.MapRow(kw.pop("rid", None) or next(_ids), lg, kind, None, at(starts), None, None, (a, b),
                         kw.get("state"), kw.get("ticker"), kw.get("conflict"))


def decide(code: str, rows: list[ledger.MapRow], evs: list[KalshiEvent] | None = None,
           aliases: dict | None = None, now: datetime = NOW) -> km.LeagueResult:
    lg = league(code)
    return km.decide_league(lg, rows, events(lg.series) if evs is None else evs,
                            ALIASES if aliases is None else aliases, now)


def by_id(res: km.LeagueResult) -> dict[int, km.Decision]:
    return {d.id: d for d in res.decisions}


# -------------------------------------------------------------- the shapes

class KalshiShapes(unittest.TestCase):
    def test_every_fixture_parses_and_every_event_has_a_milestone(self) -> None:
        for p in sorted(FIXTURES.glob("kalshi_KX*.json")):
            series = p.stem[len("kalshi_"):]
            with self.subTest(series=series):
                page = parse_events_page(series, json.loads(p.read_bytes()))
                self.assertTrue(page.events)
                self.assertEqual((page.no_milestone, page.no_sides), (0, 0))
                for e in page.events:
                    self.assertTrue(e.ticker.startswith(series + "-"))
                    self.assertIsNotNone(e.start)
                    self.assertNotIn("TIE", e.codes)

    def test_every_registered_series_passes_the_series_check(self) -> None:
        bodies = json.loads((FIXTURES / "kalshi_series.json").read_bytes())
        self.assertEqual(set(bodies), {s for s, _, _ in SEED_REGISTRY.values()})
        for s, b in bodies.items():
            with self.subTest(series=s):
                check_series(s, b)

    def test_series_check_is_loud(self) -> None:
        b = json.loads((FIXTURES / "kalshi_series.json").read_bytes())["KXNFLGAME"]
        with self.assertRaisesRegex(KalshiShapeError, r"KXNBAGAME: shape at \$\.series\.ticker"):
            check_series("KXNBAGAME", b)
        bad = copy.deepcopy(b)
        del bad["series"]["title"]
        with self.assertRaisesRegex(KalshiShapeError, r"\$\.series\.title: missing"):
            check_series("KXNFLGAME", bad)

    def test_a_changed_event_shape_is_loud(self) -> None:
        cases = {
            "events not a list": (lambda b: b.update(events={}), r"\$\.events: is dict"),
            "no milestones key": (lambda b: b.pop("milestones"), r"\$\.milestones: missing"),
            "event ticker gone": (lambda b: b["events"][0].pop("event_ticker"), r"events\[0\]\.event_ticker: missing"),
            "another series": (lambda b: b["events"][0].update(series_ticker="KXNBAGAME"), r"series_ticker"),
            "market off its event": (lambda b: b["events"][0]["markets"][0].update(ticker="KXNFLGAME-X-BAL"),
                                     r"markets\[0\]\.ticker"),
            "market name gone": (lambda b: b["events"][0]["markets"][1].pop("yes_sub_title"),
                                 r"markets\[1\]\.yes_sub_title: missing"),
            "milestone start gone": (lambda b: b["milestones"][0].pop("start_date"),
                                     r"milestones\[0\]\.start_date: missing"),
            "milestone start not a time": (lambda b: b["milestones"][0].update(start_date="soon"),
                                           r"milestones\[0\]\.start_date"),
        }
        for name, (mutate, pattern) in cases.items():
            with self.subTest(name):
                b = body("KXNFLGAME")
                mutate(b)
                with self.assertRaisesRegex(KalshiShapeError, pattern):
                    parse_events_page("KXNFLGAME", b)

    def test_codes_come_from_the_market_tickers_never_the_title(self) -> None:
        cle_no = {e.ticker: e for e in events("KXNFLGAME")}["KXNFLGAME-26NOV08CLENO"]
        self.assertEqual(cle_no.codes, {"CLE", "NO"})  # the milestone title says NOLA
        self.assertEqual(cle_no.order, ("CLE", "NO"))  # away, home
        jac = {e.ticker: e for e in events("KXNFLGAME")}["KXNFLGAME-26NOV05JACBAL"]
        self.assertEqual((jac.day, jac.start, jac.order), (date(2026, 11, 5), at("2026-11-06T01:15"), ("JAC", "BAL")))

    def test_ticker_dates(self) -> None:
        self.assertEqual(ticker_day("KXNFLGAME-26NOV05JACBAL"), date(2026, 11, 5))
        self.assertEqual(ticker_day("KXMLBGAME-26SEP221305TBNYYG1"), date(2026, 9, 22))
        self.assertIsNone(ticker_day("KXF1RACE-BRAZ26"))
        self.assertIsNone(ticker_day("KXPGATOUR-MAST27"))
        self.assertIsNone(ticker_day("KXNFLGAME-26FEB30AB"))  # not a date
        self.assertEqual([e.day for e in events("KXF1RACE")], [None])

    def test_soccer_draw_is_not_a_side(self) -> None:
        for e in events("KXEPLGAME"):
            self.assertEqual(len(e.sides), 2)

    def test_paging_follows_the_cursor_and_a_runaway_is_loud(self) -> None:
        nfl = body("KXNFLGAME")
        first = dict(nfl, events=nfl["events"][:2], cursor="next")
        second = dict(nfl, events=nfl["events"][2:], cursor="")
        fake = FakeKalshi({"KXNFLGAME": [first, second]})
        got = [e.ticker for p in open_pages(fake.client, "KXNFLGAME") for e in p.events]
        self.assertEqual(len(got), 5)
        self.assertEqual([q.get("cursor") for q in fake.queries("/events")], [None, "next"])
        loop = FakeKalshi({"KXNFLGAME": [dict(first)] * 25})
        with self.assertRaisesRegex(KalshiShapeError, "still paging"):
            list(open_pages(loop.client, "KXNFLGAME"))


# ------------------------------------------------------------ the matching

class TeamPair(unittest.TestCase):
    def test_every_nfl_game_links_through_the_alias_exceptions(self) -> None:
        rows = [game("nfl", "JAX", "BAL", "2026-11-06T01:15"), game("nfl", "CLE", "NO", "2026-11-08T18:00"),
                game("nfl", "LAR", "WSH", "2026-11-08T18:00"), game("nfl", "WSH", "NYG", "2026-11-13T01:15"),
                game("nfl", "JAX", "TEN", "2026-11-15T18:00")]
        res = decide("nfl", rows)
        self.assertEqual(Counter(d.state for d in res.decisions), {"linked": 5})
        self.assertEqual([d.ticker for d in res.decisions],
                         ["KXNFLGAME-26NOV05JACBAL", "KXNFLGAME-26NOV08CLENO", "KXNFLGAME-26NOV08LARWAS",
                          "KXNFLGAME-26NOV12WASNYG", "KXNFLGAME-26NOV15JACTEN"])
        self.assertEqual({d.swapped for d in res.decisions}, {False})
        self.assertEqual({d.method for d in res.decisions}, {"team_pair"})
        self.assertEqual(res.unmatched, [])

    def test_without_the_alias_the_game_is_not_linked_and_says_why(self) -> None:
        res = decide("nfl", [game("nfl", "JAX", "BAL", "2026-11-06T01:15")], aliases={})
        d = res.decisions[0]
        self.assertEqual((d.state, d.ticker), ("not_linked", None))
        self.assertEqual(d.reason, "no KXNFLGAME event for JAX/BAL within ±1 ET day")

    def test_nba_aliases(self) -> None:
        res = decide("nba", [game("nba", "UTAH", "NO", "2026-11-05T01:00"), game("nba", "MIA", "GS", "2026-11-06T03:00")])
        self.assertEqual([d.ticker for d in res.decisions], ["KXNBAGAME-26NOV04UTANOP", "KXNBAGAME-26NOV05MIAGSW"])

    def test_mutation_unordered_pair_a_swapped_game_still_links(self) -> None:
        # ESPN has Michigan home; Kalshi's ticker MICHCONN puts UConn home.
        espn_view = game("mcbb", "CONN", "MICH", "2026-11-07T01:00")
        d = decide("mcbb", [espn_view]).decisions[0]
        self.assertEqual((d.state, d.ticker, d.swapped), ("linked", "KXNCAAMBGAME-26NOV06MICHCONN", True))
        # The same game with ESPN's order matching Kalshi's: not swapped.
        d = decide("mcbb", [game("mcbb", "MICH", "CONN", "2026-11-07T01:00")]).decisions[0]
        self.assertEqual((d.state, d.swapped), ("linked", False))

    def test_mutation_plus_minus_one_day(self) -> None:
        # An early-dated ticker (NOV03 for a Nov 4 ET game) is inside ±1 day.
        d = decide("cfb", [game("cfb", "BALL", "MASS", "2026-11-05T00:00")]).decisions[0]
        self.assertEqual((d.state, d.ticker), ("linked", "KXNCAAFGAME-26NOV03BALLMASS"))
        # ESPN's time-TBD placeholder (midnight ET) matches by date, 19.5 h off.
        d = decide("cfb", [game("cfb", "MASS", "TOL", "2026-11-11T05:00")]).decisions[0]
        self.assertEqual((d.state, d.ticker), ("linked", "KXNCAAFGAME-26NOV11MASSTOL"))
        # Two days off: no candidate.
        d = decide("nfl", [game("nfl", "JAX", "BAL", "2026-11-08T01:15")]).decisions[0]
        self.assertEqual(d.state, "not_linked")
        # One day off either way: still the event.
        for starts in ("2026-11-07T01:15", "2026-11-05T01:15"):
            with self.subTest(starts=starts):
                d = decide("nfl", [game("nfl", "JAX", "BAL", starts)]).decisions[0]
                self.assertEqual(d.ticker, "KXNFLGAME-26NOV05JACBAL")

    def test_a_rematch_two_days_apart_links_each_game_to_its_own(self) -> None:
        rows = [game("nba", "ATL", "WSH", "2026-11-05T00:00"), game("nba", "WSH", "ATL", "2026-11-07T00:00")]
        res = decide("nba", rows)
        self.assertEqual([(d.state, d.ticker) for d in res.decisions],
                         [("linked", "KXNBAGAME-26NOV04ATLWAS"), ("linked", "KXNBAGAME-26NOV06WASATL")])

    def test_mutation_two_candidates_refuse(self) -> None:
        evs = events("KXNFLGAME")
        twin = KalshiEvent("KXNFLGAME-26NOV06JACBAL", "KXNFLGAME", date(2026, 11, 6), at("2026-11-06T18:00"),
                           (Side("BAL", "Baltimore"), Side("JAC", "Jacksonville")))
        d = decide("nfl", [game("nfl", "JAX", "BAL", "2026-11-06T01:15")], evs + [twin]).decisions[0]
        self.assertEqual((d.state, d.ticker), ("not_linked", None))
        self.assertEqual(d.reason, "2 Kalshi candidates: KXNFLGAME-26NOV05JACBAL, KXNFLGAME-26NOV06JACBAL")

    def test_two_espn_games_for_one_event_refuse_both(self) -> None:
        rows = [game("nfl", "JAX", "BAL", "2026-11-06T01:15"), game("nfl", "BAL", "JAX", "2026-11-06T01:15")]
        res = decide("nfl", rows)
        self.assertEqual({(d.state, d.reason) for d in res.decisions},
                         {("not_linked", "KXNFLGAME-26NOV05JACBAL matches 2 ESPN events")})

    def test_mutation_no_relink_a_linked_row_keeps_its_link_and_is_flagged_once(self) -> None:
        linked = game("nfl", "JAX", "BAL", "2026-11-06T01:15", state="linked", ticker="KXNFLGAME-26NOV04JACBAL")
        res = decide("nfl", [linked])
        self.assertEqual(res.decisions, [])  # never decided again
        self.assertEqual(res.conflicts, [(linked.id, "KXNFLGAME-26NOV05JACBAL")])
        self.assertEqual(res.counts["linked"], 1)
        flagged = ledger.MapRow(**{**linked.__dict__, "link_conflict": "KXNFLGAME-26NOV05JACBAL"})
        self.assertEqual(decide("nfl", [flagged]).conflicts, [])  # already flagged: not again
        same = ledger.MapRow(**{**linked.__dict__, "ticker": "KXNFLGAME-26NOV05JACBAL"})
        self.assertEqual((decide("nfl", [same]).decisions, decide("nfl", [same]).conflicts), ([], []))

    def test_an_event_already_linked_to_another_row_is_not_taken(self) -> None:
        holder = game("nfl", "JAX", "BAL", "2026-11-06T01:15", state="linked", ticker="KXNFLGAME-26NOV05JACBAL",
                      rid=900)
        d = by_id(decide("nfl", [holder, game("nfl", "BAL", "JAX", "2026-11-06T02:00", rid=901)]))[901]
        self.assertEqual((d.state, d.reason), ("not_linked", "KXNFLGAME-26NOV05JACBAL is already linked to sports_events 900"))

    def test_mutation_not_listed_beyond_the_latest_listed_start(self) -> None:
        # UTAMIN is the fixture's latest tip, 2026-11-09 00:00 UTC.
        rows = [game("nba", "BKN", "DAL", "2026-11-09T00:30", rid=1001),
                game("nba", "BKN", "DAL", "2026-11-08T23:30", rid=1002)]
        res = by_id(decide("nba", rows))
        self.assertEqual((res[1001].state, res[1001].reason), ("not_listed", "starts after KXNBAGAME's latest listed event"))
        self.assertEqual(res[1002].state, "not_linked")  # inside the window and no event
        self.assertEqual(res[1002].reason, "no KXNBAGAME event for BKN/DAL within ±1 ET day")
        empty = decide("nba", [game("nba", "BKN", "DAL", "2026-11-09T00:30")], [])
        self.assertEqual(empty.decisions[0].reason, "KXNBAGAME lists no open event")

    def test_a_started_row_with_no_open_event_keeps_its_state(self) -> None:
        before = game("nba", "BKN", "DAL", "2026-11-05T01:00", state="not_listed")
        res = decide("nba", [before])
        self.assertEqual((res.decisions, res.counts["not_listed"]), ([], 1))
        fresh = decide("nba", [game("nba", "BKN", "DAL", "2026-11-05T01:00")]).decisions[0]
        self.assertEqual((fresh.state, fresh.reason),
                         ("not_linked", "had started before its first check; no open KXNBAGAME event"))
        # A started game whose event is still open links as usual.
        live = decide("nba", [game("nba", "ATL", "WSH", "2026-11-05T00:00", state="not_listed")]).decisions[0]
        self.assertEqual(live.ticker, "KXNBAGAME-26NOV04ATLWAS")

    def test_missing_espn_code(self) -> None:
        d = decide("nfl", [game("nfl", None, "BAL", "2026-11-06T01:15")]).decisions[0]
        self.assertEqual((d.state, d.reason), ("not_linked", "ESPN gives no team code for one side"))

    def test_soccer_links_without_a_home_side(self) -> None:
        res = decide("epl", [game("epl", "COV", "EVE", "2026-11-06T20:00"), game("epl", "TOT", "LEE", "2026-11-07T12:30")])
        self.assertEqual([(d.state, d.swapped) for d in res.decisions], [("linked", None), ("linked", None)])

    def test_kalshi_events_no_espn_row_claims_are_listed(self) -> None:
        res = decide("mcbb", [game("mcbb", "CONN", "MICH", "2026-11-07T01:00")])
        self.assertIn("KXNCAAMBGAME-26NOV06PORTUTA", res.unmatched)
        self.assertNotIn("KXNCAAMBGAME-26NOV06MICHCONN", res.unmatched)
        self.assertNotIn("KXNCAAMBGAME-26NOV08BUTBSU", res.unmatched)  # after ESPN's last date here


class PlayersAndDates(unittest.TestCase):
    def test_tennis_by_names_accents_and_order_folded(self) -> None:
        rows = [pair("atp", "match", "Rinky Hijikata", "Tomás Martín Etcheverry", "2026-11-05T05:00"),
                pair("atp", "match", "Rinderknech Arthur", "Ben Shelton", "2026-11-05T05:00")]
        res = decide("atp", rows)
        self.assertEqual([(d.state, d.ticker, d.method, d.swapped) for d in res.decisions],
                         [("linked", "KXATPMATCH-26NOV05HIJETC", "player_pair", None),
                          ("linked", "KXATPMATCH-26NOV05SHERIN", "player_pair", None)])

    def test_tennis_surname_fallback(self) -> None:
        d = decide("atp", [pair("atp", "match", "Benjamin Shelton", "A. Rinderknech", "2026-11-05T05:00")]).decisions[0]
        self.assertEqual(d.ticker, "KXATPMATCH-26NOV05SHERIN")

    def test_ufc_dated_the_day_before(self) -> None:
        d = decide("ufc", [pair("ufc", "bout", "Lucas Brennan", "Austin Bashi", "2026-11-07T22:40")]).decisions[0]
        self.assertEqual((d.state, d.ticker), ("linked", "KXUFCFIGHT-26NOV06BASBRE"))

    def test_f1_race_links_by_date_and_qualifying_is_out_of_scope(self) -> None:
        race = ledger.MapRow(1, "f1", "race_session", "Race", at("2026-11-08T17:00"), None, None, (), None, None, None)
        qual = ledger.MapRow(2, "f1", "race_session", "Qual", at("2026-11-07T18:00"), None, None, (), None, None, None)
        res = by_id(decide("f1", [race, qual]))
        self.assertEqual((res[1].state, res[1].ticker, res[1].method), ("linked", "KXF1RACE-BRAZ26", "by_date"))
        self.assertEqual((res[2].state, res[2].reason),
                         ("out_of_scope", "out of scope: KXF1RACE lists the race winner only, no event for Qual"))

    def test_golf_rounds_share_their_tournament_and_futures_set_no_horizon(self) -> None:
        rounds = [ledger.MapRow(10 + n, "pga", "golf_round", f"R{n}", at(f"2026-11-0{4 + n}T05:00"), None, None, (),
                                None, None, None) for n in range(1, 5)]
        later = ledger.MapRow(20, "pga", "golf_round", "R1", at("2026-11-12T05:00"), None, None, (), None, None, None)
        res = by_id(decide("pga", rounds + [later]))
        self.assertEqual({res[i].ticker for i in range(11, 15)}, {"KXPGATOUR-WOWTC26"})
        # The Masters (April 2027) is a future, so it sets no horizon: the
        # next week's round is "not listed" against the WWTC's start.
        self.assertEqual(res[20].state, "not_listed")
        self.assertEqual(km.horizon(events("KXPGATOUR"), NOW), at("2026-11-05T13:30"))

    def test_an_off_league_is_out_of_scope(self) -> None:
        lg = km.MapLeague("mlb", "game", "baseball", "KXMLBGAME", "off")
        res = km.decide_league(lg, [game("mlb", "NYY", "BOS", "2026-11-06T00:00")], [], ALIASES, NOW)
        self.assertEqual((res.decisions[0].state, res.decisions[0].reason),
                         ("out_of_scope", "out of scope: mlb is not mapped (kalshi_map off)"))


# ------------------------------------------------------------------ the job

class FakeKalshi:
    """A KalshiClient whose transport serves fixture pages by series (a list
    of bodies is served in order: pages), /series/{t} from kalshi_series.json;
    `fail` maps a series to an HTTP status served instead; `limit_first`
    serves one 429 before the first request."""

    def __init__(self, pages: dict[str, list[dict]] | None = None, *, fail: dict[str, int] | None = None,
                 limit_first: bool = False) -> None:
        self.pages = {k: list(v) for k, v in (pages or {}).items()}
        self.fail = fail or {}
        self.limit_first = limit_first
        self.urls: list[str] = []
        self.series_bodies = json.loads((FIXTURES / "kalshi_series.json").read_bytes())
        self.client = KalshiClient(transport=self.transport, min_interval=0, sleep=lambda s: None)

    def transport(self, url: str) -> tuple[int, Any]:
        self.urls.append(url)
        if self.limit_first:
            self.limit_first = False
            return 429, None
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        if parts.path.endswith("/events"):
            s = q["series_ticker"]
            if s in self.fail:
                return self.fail[s], None
            if s not in self.pages:
                return 200, {"cursor": "", "events": [], "milestones": []}
            queue = self.pages[s]
            return 200, copy.deepcopy(queue.pop(0) if len(queue) > 1 else queue[0])
        s = parts.path.rsplit("/", 1)[1]
        return (200, copy.deepcopy(self.series_bodies[s])) if s in self.series_bodies else (404, None)

    def queries(self, path: str) -> list[dict[str, str]]:
        return [dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query)) for u in self.urls
                if urllib.parse.urlsplit(u).path.endswith(path)]


def registry_rows(*codes: str) -> list[ledger.SportsLeagueRow]:
    return [ledger.SportsLeagueRow(c, f"x/{c}", {}, "per_day", KIND.get(c, "game"), False, i, SPORT[c],
                                   SEED_REGISTRY[c][2], SEED_REGISTRY[c][0], SEED_REGISTRY[c][1])
            for i, c in enumerate(codes)]


def _refuse_database(*a: Any, **k: Any) -> Any:
    raise AssertionError("the test reached the database")


class JobCase(unittest.TestCase):
    def setUp(self) -> None:
        self.rows: list[ledger.MapRow] = []
        self.writes: list[list[dict]] = []
        self.conflict_writes: list[list[dict]] = []
        self.stored: dict[int, tuple] = {}
        self.leagues = registry_rows("nfl", "nba", "mlb")

        def write(rows, **kw):
            self.assertFalse(kw.get("is_test", False))
            self.writes.append(list(rows))
            out = []
            for r in rows:
                key = (r["link_state"], r["link_reason"], r["link_method"], r["kalshi_event_ticker"],
                       r["home_away_swapped"])
                if self.stored.get(r["id"]) != key:
                    self.stored[r["id"]] = key
                    out.append((r["id"], r["link_state"]))
            return out

        def conflicts(rows, **kw):
            self.conflict_writes.append(list(rows))
            return [r["id"] for r in rows]

        for p in (mock.patch.object(ledger, "_pool", _refuse_database),
                  mock.patch.object(ledger, "sports_leagues", lambda **k: self.leagues),
                  mock.patch.object(ledger, "sports_team_aliases", lambda **k: dict(ALIASES)),
                  mock.patch.object(ledger, "kalshi_map_rows", self.read_rows),
                  mock.patch.object(ledger, "sports_link_write", write),
                  mock.patch.object(ledger, "sports_link_conflicts", conflicts),
                  mock.patch.object(km.memory, "release", lambda: None)):
            p.start()
            self.addCleanup(p.stop)
        self.state = km.MapState()

    def read_rows(self, since, leagues, **kw):
        self.read_args = (since, list(leagues), kw)
        return [r for r in self.rows if r.league in leagues and r.starts_at >= since]

    def run_job(self, fake: FakeKalshi, at_: datetime = NOW) -> km.MapRun:
        return km.map_job(client=fake.client, now=lambda: at_, state=self.state)


class MapJob(JobCase):
    def fake(self, **kw: Any) -> FakeKalshi:
        return FakeKalshi({"KXNFLGAME": [body("KXNFLGAME")], "KXNBAGAME": [body("KXNBAGAME")]}, **kw)

    def test_a_run_links_writes_once_and_reports(self) -> None:
        self.rows = [game("nfl", "JAX", "BAL", "2026-11-06T01:15", rid=1),
                     game("nba", "MIA", "GS", "2026-11-06T03:00", rid=2),
                     game("nba", "BKN", "DAL", "2026-11-09T00:30", rid=3),
                     game("mlb", "NYY", "BOS", "2026-11-06T00:00", rid=4)]
        fake = self.fake()
        run = self.run_job(fake)
        self.assertEqual(self.read_args[0], NOW - km.EVAL_BACK)
        self.assertEqual({r["id"]: r["link_state"] for r in self.writes[0]},
                         {1: "linked", 2: "linked", 3: "not_listed", 4: "out_of_scope"})
        self.assertEqual((run.sent, run.written, run.conflicts), (4, 4, 0))
        # One open-events read per mapped league with rows; MLB (off) none.
        self.assertEqual(sorted(q["series_ticker"] for q in fake.queries("/events")), ["KXNBAGAME", "KXNFLGAME"])
        for q in fake.queries("/events"):
            self.assertEqual((q["status"], q["with_nested_markets"], q["with_milestones"], q["limit"]),
                             ("open", "true", "true", "100"))
        # The registry's series were each checked once (the daily contract).
        self.assertEqual(sum(1 for u in fake.urls if "/series/" in u), 3)
        self.assertEqual(run.contract, "contract: 3/3 series ok")
        self.assertEqual(run.calls, 5)
        self.assertIn("nfl linked 1; nba linked 1, not_listed 1; mlb out_of_scope 1", run.job_note)
        self.assertIn("sent 4, written 4, conflicts 0", run.job_note)
        self.assertIn("5 Kalshi calls, 0 429s", run.job_note)

    def test_zero_rows_written_without_change_and_one_contract_check_a_day(self) -> None:
        self.rows = [game("nfl", "JAX", "BAL", "2026-11-06T01:15", rid=1),
                     game("nba", "BKN", "DAL", "2026-11-09T00:30", rid=3)]
        self.run_job(self.fake())
        fake = self.fake()
        # NOW is 20:00 Chicago; 3 h later is still the same Chicago day.
        run = self.run_job(fake, NOW + timedelta(hours=3))
        self.assertEqual((run.sent, run.written), (0, 0))
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(sum(1 for u in fake.urls if "/series/" in u), 0)
        self.assertIsNone(run.contract)
        nxt = self.fake()
        self.run_job(nxt, NOW + timedelta(hours=6))  # 02:00 Chicago, the next day
        self.assertEqual(sum(1 for u in nxt.urls if "/series/" in u), 3)

    def test_a_restart_resends_but_the_database_writes_nothing(self) -> None:
        self.rows = [game("nfl", "JAX", "BAL", "2026-11-06T01:15", rid=1)]
        self.run_job(self.fake())
        self.state = km.MapState()  # a restart
        run = self.run_job(self.fake())
        self.assertEqual((run.sent, run.written), (1, 0))

    def test_linked_rows_are_never_sent_and_a_conflict_is_flagged(self) -> None:
        self.rows = [game("nfl", "JAX", "BAL", "2026-11-06T01:15", state="linked",
                          ticker="KXNFLGAME-26NOV04JACBAL", rid=7)]
        # tests/__init__.py silences logging; this test checks a log line.
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)
        with self.assertLogs("valemont.kalshi_map", "WARNING") as logs:
            run = self.run_job(self.fake())
        self.assertEqual(self.writes, [])
        self.assertEqual(self.conflict_writes, [[{"id": 7, "link_conflict": "KXNFLGAME-26NOV05JACBAL"}]])
        self.assertEqual(run.conflicts, 1)
        self.assertTrue(any("link is kept and flagged" in m for m in logs.output))

    def test_one_league_failing_fails_the_run_but_the_others_are_written(self) -> None:
        self.rows = [game("nfl", "JAX", "BAL", "2026-11-06T01:15", rid=1),
                     game("nba", "MIA", "GS", "2026-11-06T03:00", rid=2)]
        with self.assertRaises(km.KalshiMapFailed) as ctx:
            self.run_job(self.fake(fail={"KXNBAGAME": 500}))
        self.assertIn("nba: /events returned HTTP 500", str(ctx.exception))
        self.assertEqual([r["id"] for r in self.writes[0]], [1])

    def test_a_changed_shape_fails_loud(self) -> None:
        self.rows = [game("nfl", "JAX", "BAL", "2026-11-06T01:15", rid=1)]
        bad = body("KXNFLGAME")
        del bad["events"][0]["markets"][0]["yes_sub_title"]
        with self.assertRaises(km.KalshiMapFailed) as ctx:
            self.run_job(FakeKalshi({"KXNFLGAME": [bad]}))
        self.assertIn("KXNFLGAME: shape at $.events[0].markets[0].yes_sub_title: missing", str(ctx.exception))
        self.assertEqual(self.writes, [])

    def test_a_missing_series_fails_its_league_only(self) -> None:
        self.leagues = registry_rows("nfl", "nba")
        self.rows = [game("nfl", "JAX", "BAL", "2026-11-06T01:15", rid=1),
                     game("nba", "MIA", "GS", "2026-11-06T03:00", rid=2)]
        fake = self.fake()
        del fake.series_bodies["KXNBAGAME"]
        with self.assertRaises(km.KalshiMapFailed) as ctx:
            self.run_job(fake)
        self.assertIn("series KXNBAGAME", str(ctx.exception))
        self.assertEqual([r["id"] for r in self.writes[0]], [1])
        self.assertIsNone(self.state.contract_day)  # checked again next run

    def test_a_429_is_backed_off_and_counted(self) -> None:
        self.rows = [game("nfl", "JAX", "BAL", "2026-11-06T01:15", rid=1)]
        run = self.run_job(self.fake(limit_first=True))
        self.assertEqual(run.rate_limited, 1)
        self.assertEqual(self.writes[0][0]["link_state"], "linked")

    def test_no_rows_no_kalshi_events_calls(self) -> None:
        fake = self.fake()
        run = self.run_job(fake)
        self.assertEqual(fake.queries("/events"), [])
        self.assertIn("no rows in the window", run.job_note)


# ---------------------------------------------------- ESPN and the schedule

def espn_fixture(name: str) -> Any:
    return json.loads((ESPN_FIXTURES / f"espn_{name}.json").read_bytes())


class NeutralSite(unittest.TestCase):
    NFL = League("nfl", "football/nfl", {}, "per_day", "game")
    EPL = League("epl", "soccer/eng.1", {}, "per_day", "game")
    MLB = League("mlb", "baseball/mlb", {}, "per_day", "game")

    def test_stored_from_espn(self) -> None:
        rows = espn.parse(self.NFL, espn_fixture("nfl_scheduled"))
        self.assertEqual(sorted(r.neutral_site for r in rows), [False, True])
        self.assertIn("neutral_site", rows[0].row())

    def test_required_for_us_team_sports_none_for_soccer(self) -> None:
        data = espn_fixture("nfl_scheduled")
        del data["events"][0]["competitions"][0]["neutralSite"]
        with self.assertRaisesRegex(EspnShapeError, r"neutralSite: missing"):
            espn.parse(self.NFL, data)
        self.assertEqual({r.neutral_site for r in espn.parse(self.EPL, espn_fixture("epl_scheduled"))}, {None})
        mlb = espn_fixture("nfl_scheduled")
        del mlb["events"][0]["competitions"][0]["neutralSite"]
        self.assertIn(None, {r.neutral_site for r in espn.parse(self.MLB, mlb)})

    def test_the_flag_moves_the_content_hash(self) -> None:
        a = espn.parse(self.NFL, espn_fixture("nfl_scheduled"))[0]
        b = espn.SportsEvent(**{**a.__dict__, "neutral_site": not a.neutral_site})
        self.assertNotEqual(a.content_hash(), b.content_hash())


class ScheduleHorizon(unittest.TestCase):
    def test_nfl_and_cfb_pull_fourteen_days(self) -> None:
        self.assertEqual({c: d for c, (_, _, d) in SEED_REGISTRY.items() if d != 7}, {"nfl": 14, "cfb": 14})
        days = jobs.schedule_window(NOW, 14)
        self.assertEqual((days[0], days[-1], len(days)), (date(2026, 11, 3), date(2026, 11, 18), 16))
        self.assertEqual(len(jobs.schedule_window(NOW)), 9)
        row = registry_rows("nfl")[0]
        self.assertEqual(jobs.league_of(row).days_ahead, 14)


# ------------------------------------------------------------ the migration

class Migration066(unittest.TestCase):
    SQL = (pathlib.Path(__file__).resolve().parents[2] / "db" / "066_kalshi_mapping.sql").read_text(encoding="utf-8")

    def test_seed_tuples_match_their_column_lists(self) -> None:
        # migration_seed raises on a tuple of the wrong arity.
        self.assertEqual(len(SEED_REGISTRY), 14)
        self.assertEqual(Counter(a[0] for a in SEED_ALIASES),
                         {"mcbb": 61, "cfb": 14, "nba": 6, "epl": 5, "mls": 3, "nfl": 2})
        self.assertEqual(len({(a[0], a[1]) for a in SEED_ALIASES}), len(SEED_ALIASES))
        self.assertEqual(len({(a[0], a[2]) for a in SEED_ALIASES}), len(SEED_ALIASES))
        for lg, espn_code, kalshi, note in SEED_ALIASES:
            self.assertRegex(espn_code, r"^[A-Za-z0-9&'.-]{1,12}$")
            self.assertRegex(kalshi, r"^[A-Z0-9]{1,12}$")
            self.assertNotEqual(espn_code.upper(), kalshi)
            self.assertTrue(note)

    def test_the_self_test_counts_match_the_seed(self) -> None:
        want = ",".join(f"{lg} {n}" for lg, n in sorted(Counter(a[0] for a in SEED_ALIASES).items()))
        self.assertIn(f"<> '{want}'", self.SQL)

    def test_every_values_tuple_in_the_file_has_its_column_count(self) -> None:
        """Every INSERT ... VALUES / FROM (VALUES ...) AS v (cols) in the file:
        each tuple's top-level value count equals its column list's."""
        def split_top(s: str) -> list[str]:
            out, depth, cur, q = [], 0, "", False
            for ch in s:
                if ch == "'" :
                    q = not q
                if not q and ch == "(":
                    depth += 1
                if not q and ch == ")":
                    depth -= 1
                if not q and depth == 0 and ch == ",":
                    out.append(cur.strip())
                    cur = ""
                else:
                    cur += ch
            out.append(cur.strip())
            return out

        sql = re.sub(r"--[^\n]*", "", self.SQL)
        checked = 0
        for m in re.finditer(r"INSERT INTO [\w.]+ \(([^)]*)\)\s*VALUES\s*(.*?);", sql, re.S):
            cols = len(split_top(m.group(1)))
            body_ = re.split(r"\bRETURNING\b|\bON CONFLICT\b", m.group(2))[0]
            for t in re.finditer(r"\((.*?)\)(?=\s*(?:,\s*\(|$))", body_.strip(), re.S):
                self.assertEqual(len(split_top(t.group(1))), cols, t.group(0)[:80])
                checked += 1
        for m in re.finditer(r"FROM \(VALUES(.*?)\)\s*AS v \(([^)]*)\)", sql, re.S):
            cols = len(split_top(m.group(2)))
            for line in m.group(1).strip().splitlines():
                line = line.strip().rstrip(",")
                if line:
                    self.assertEqual(len(split_top(line[1:-1])), cols, line[:80])
                    checked += 1
        self.assertGreaterEqual(checked, 14 + 91 + 2 + 3)

    def test_the_worker_shares_an_event_exactly_where_the_index_allows(self) -> None:
        m = re.search(r"CREATE UNIQUE INDEX IF NOT EXISTS sports_events_kalshi_ticker .*? kind IN \(([^)]*)\);",
                      self.SQL, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(frozenset(re.findall(r"'(\w+)'", m.group(1))), km.UNIQUE_KINDS)
        self.assertEqual(km.UNIQUE_KINDS, frozenset({"game", "match", "bout"}))

    def test_phase_1s_upsert_is_in_the_self_test_as_deployed(self) -> None:
        """The self-test's Phase-1-shaped statements name every column the
        deployed db/065 upsert writes, and no db/066 column."""
        phase1 = ("league, kind, espn_id, parent_espn_id, event_name, session, starts_at, time_tbd, home, away, "
                  "participants, broadcast, national_tv, venue, status, espn_status, status_detail, period, clock, "
                  "score, content_hash, is_test")
        cols = [re.sub(r"\s+", " ", m.group(1)) for m in re.finditer(
            r"INSERT INTO public\.sports_events \(([^)]*)\)\s*VALUES \('nfl', 'game', '999066001'", self.SQL)]
        self.assertEqual(cols, [phase1, phase1])
        # And it is today's upsert with neutral_site taken out: no other column differs.
        today = re.search(r"INSERT INTO sports_events \(([^)]*)\)", ledger.SPORTS_EVENTS_UPSERT_SQL).group(1)
        self.assertEqual(re.sub(r"\s+", " ", today).replace(" neutral_site,", ""), phase1)

    def test_hand_repair_has_one_caller_the_script(self) -> None:
        """Never automatic: nothing in the worker but the hand script calls
        the repair (ledger.sports_event_relink / db/066's function)."""
        root = pathlib.Path(__file__).resolve().parents[1]
        callers = sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*.py")
                         if "sports_event_relink(" in p.read_text(encoding="utf-8")
                         and p.parts[len(root.parts)] not in ("tests", "tests_live"))
        self.assertEqual(callers, ["core/ledger.py", "scripts/sports_event_relink.py"])


class RelinkScript(unittest.TestCase):
    """scripts.sports_event_relink against a stubbed ledger."""

    def run_script(self, argv: list[str], link: ledger.EventLink | None) -> tuple[int, str, mock.MagicMock]:
        from scripts import sports_event_relink as script
        out: list[str] = []
        with mock.patch.object(script, "load_env"), mock.patch.object(ledger, "close_pool"), \
                mock.patch.object(ledger, "sports_event_link", return_value=link), \
                mock.patch.object(ledger, "sports_event_relink", return_value=7) as relink, \
                mock.patch("builtins.print", side_effect=lambda s="": out.append(str(s))):
            code = script.main(argv)
        return code, "\n".join(out), relink

    LINKED = ledger.EventLink(5, "nfl", "A at H", NOW, "linked", "team_pair", "KXNFLGAME-26NOV09AH", None)

    def test_relink_and_unlink_call_the_repair_once(self) -> None:
        code, out, relink = self.run_script(["--event", "5", "--ticker", "KXNFLGAME-26NOV10AH", "--swapped",
                                             "--reason", "wrong week"], self.LINKED)
        self.assertEqual(code, 0)
        relink.assert_called_once_with(5, "KXNFLGAME-26NOV10AH", "wrong week", swapped=True)
        self.assertIn("sports_event_relinks 7", out)
        code, _, relink = self.run_script(["--event", "5", "--unlink", "--reason", "no event"], self.LINKED)
        relink.assert_called_once_with(5, None, "no event", swapped=None)

    def test_refusals_write_nothing(self) -> None:
        for argv, link in ((["--event", "5", "--unlink", "--reason", "x", "--dry-run"], self.LINKED),
                           (["--event", "9", "--unlink", "--reason", "x"], None),
                           (["--event", "5", "--unlink", "--reason", "x"],
                            ledger.EventLink(5, "nfl", "A at H", NOW, "not_linked", None, None, None))):
            with self.subTest(argv=argv):
                code, _, relink = self.run_script(argv, link)
                relink.assert_not_called()
                self.assertEqual(code, 0 if "--dry-run" in argv else 1)
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            self.run_script(["--event", "5", "--unlink", "--swapped", "--reason", "x"], self.LINKED)


if __name__ == "__main__":
    unittest.main()
