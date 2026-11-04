"""The sports data layer (Chat 7 Phase 1; docs/sports_data.md).

No database, no network: the ledger is patched (its pool refuses), ESPN is a
fake HTTP function or a fake provider, and the fixtures are trimmed real ESPN
responses (tests/fixtures/sports, recorded 2026-11-04).
"""

from __future__ import annotations

import copy
import json
import pathlib
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest import mock

from core import ledger
from sports import espn, jobs, provider
from sports.espn import EspnFetchError, EspnShapeError, League, SportsEvent

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "sports"

LEAGUES = {
    "nfl": League("nfl", "football/nfl", {}, "per_day", "game", True, 20),
    "cfb": League("cfb", "football/college-football", {"groups": "80", "limit": "500"}, "per_day", "game", True, 10),
    "nba": League("nba", "basketball/nba", {}, "per_day", "game", True, 30),
    "f1": League("f1", "racing/f1", {}, "range", "race_session", True, 40),
    "pga": League("pga", "golf/pga", {}, "range", "golf_round", True, 50),
    "mcbb": League("mcbb", "basketball/mens-college-basketball", {"groups": "50", "limit": "500"}, "per_day",
                   "game", False, 60),
    "nhl": League("nhl", "hockey/nhl", {}, "per_day", "game", False, 70),
    "mlb": League("mlb", "baseball/mlb", {}, "per_day", "game", False, 80),
    "epl": League("epl", "soccer/eng.1", {}, "per_day", "game", False, 90),
    "mls": League("mls", "soccer/usa.1", {}, "per_day", "game", False, 100),
    "ucl": League("ucl", "soccer/uefa.champions", {}, "per_day", "game", False, 110),
    "atp": League("atp", "tennis/atp", {}, "range", "match", False, 120),
    "wta": League("wta", "tennis/wta", {}, "range", "match", False, 130),
    "ufc": League("ufc", "mma/ufc", {}, "range", "bout", False, 140),
}


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"espn_{name}.json").read_bytes())


def parse(name: str) -> list[SportsEvent]:
    return espn.parse(LEAGUES[name.split("_")[0]], fixture(name))


def by_id(events: list[SportsEvent]) -> dict[str, SportsEvent]:
    return {e.espn_id: e for e in events}


def _refuse_database(*a, **k):
    raise AssertionError("the test reached the database")


# ------------------------------------------------------------------ parsing

class Parsing(unittest.TestCase):
    def test_every_fixture_parses_into_valid_rows(self) -> None:
        names = sorted(p.stem[len("espn_"):] for p in FIXTURES.glob("espn_*.json"))
        self.assertEqual(len(names), 18)
        for name in names:
            with self.subTest(fixture=name):
                lg = LEAGUES[name.split("_")[0]]
                for e in espn.parse(lg, fixture(name)):
                    self.assertEqual((e.league, e.kind), (lg.code, lg.kind))
                    self.assertRegex(e.espn_id, r"^[A-Za-z0-9-]{1,48}$")
                    self.assertFalse(e.espn_id.startswith("e2e-"))
                    self.assertIn(e.status, espn.STATUSES)
                    self.assertRegex(e.espn_status, r"^[A-Z0-9_]{1,60}$")
                    self.assertTrue(1 <= len(e.event_name) <= 200)
                    self.assertIsNotNone(e.starts_at.tzinfo)
                    if e.kind == "game":
                        self.assertTrue(e.home and e.away)
                    self.assertRegex(e.content_hash(), r"^[0-9a-f]{64}$")
                    json.dumps(e.row())  # what ledger.sports_events_upsert sends

    def test_empty_days_parse_to_nothing(self) -> None:
        self.assertEqual(parse("mlb_empty"), [])
        self.assertEqual(parse("mls_empty"), [])

    def test_nfl_final_score_and_broadcast(self) -> None:
        g = by_id(parse("nfl_final"))["401873026"]
        self.assertEqual((g.away, g.home), ("Baltimore Ravens", "Buffalo Bills"))
        self.assertEqual(g.status, "final")
        self.assertEqual(g.score, {"home": 36, "away": 10})
        self.assertEqual(g.broadcast, ("CBS",))
        self.assertTrue(g.national_tv)
        self.assertEqual(g.starts_at, datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc))

    def test_scheduled_game_has_no_score(self) -> None:
        for g in parse("nfl_scheduled") + parse("cfb_scheduled"):
            self.assertEqual(g.status, "scheduled")
            self.assertIsNone(g.score)

    def test_cfb_rank_and_conference_for_game_day(self) -> None:
        games = by_id(parse("cfb_scheduled"))
        utah = games["401856849"].participants
        self.assertEqual((utah["home"]["rank"], utah["away"]["rank"]), (3, 9))
        self.assertEqual(utah["home"]["conference_id"], "4")
        sec = games["401856743"].participants
        self.assertEqual((sec["home"]["conference_id"], sec["away"]["conference_id"]), ("8", "8"))  # the SEC
        # Unranked is absent, never 99.
        self.assertNotIn("rank", games["401858512"].participants["home"])

    def test_a_game_with_no_broadcasts_is_stored_without_one(self) -> None:
        games = parse("nba_scheduled")
        bare = [g for g in games if not g.broadcast]
        self.assertEqual(len(bare), 1)
        self.assertFalse(bare[0].national_tv)
        self.assertEqual(len(games), 3)

    def test_live_soccer(self) -> None:
        live = [g for g in parse("ucl_live") if g.status == "live"]
        self.assertEqual(len(live), 1)
        g = live[0]
        self.assertEqual((g.espn_status, g.period, g.clock), ("STATUS_SECOND_HALF", 2, "63'"))
        self.assertEqual(set(g.score), {"home", "away"})
        self.assertTrue(all(isinstance(v, int) for v in g.score.values()))

    def test_f1_keeps_qualifying_and_race_skips_practice(self) -> None:
        for name in ("f1_final", "f1_scheduled"):
            self.assertEqual({e.session for e in parse(name)}, {"Qual", "Race"})
        race = [e for e in parse("f1_final") if e.session == "Race"][0]
        self.assertEqual(race.kind, "race_session")
        self.assertEqual(race.parent_espn_id, "600057446")
        self.assertEqual(race.score["results"][0], {"pos": 1, "name": "Kimi Antonelli"})
        self.assertLessEqual(len(race.score["results"]), espn.F1_RESULTS)
        self.assertEqual([r["pos"] for r in race.score["results"]], sorted(r["pos"] for r in race.score["results"]))
        self.assertTrue(all(e.score is None for e in parse("f1_scheduled")))

    def test_golf_rounds_are_derived_one_per_day(self) -> None:
        rounds = parse("pga_scheduled")
        self.assertEqual([r.espn_id for r in rounds], [f"401850979-r{n}" for n in (1, 2, 3, 4)])
        self.assertEqual([r.session for r in rounds], ["R1", "R2", "R3", "R4"])
        self.assertTrue(all(r.time_tbd and r.status == "scheduled" and r.score is None for r in rounds))
        self.assertEqual([r.starts_at - rounds[0].starts_at for r in rounds], [timedelta(days=d) for d in range(4)])

    def test_golf_final_standings_on_the_last_round(self) -> None:
        rounds = parse("pga_final")
        self.assertEqual([r.status for r in rounds], ["final"] * 4)
        self.assertEqual([r.score is None for r in rounds], [True, True, True, False])
        s = rounds[3].score
        self.assertEqual(s["round"], 4)
        self.assertEqual(len(s["leaders"]), espn.GOLF_LEADERS)
        self.assertEqual({k: s["leaders"][0][k] for k in ("name", "pos", "to_par", "thru")},
                         {"name": "Andrew Novak", "pos": "1", "to_par": "-25", "thru": 18})
        self.assertEqual([(x["name"], x["pos"]) for x in s["leaders"][1:3]],
                         [("Kristoffer Ventura", "T2"), ("Steven Fisk", "T2")])
        self.assertEqual(s["followed"], [])  # neither followed golfer is in the trimmed fixture

    def test_golf_followed_players(self) -> None:
        with mock.patch.object(espn, "FOLLOWED_GOLFERS", ("Steven Fisk",)):
            s = parse("pga_final")[3].score
        self.assertEqual(len(s["followed"]), 1)
        self.assertEqual({k: s["followed"][0][k] for k in ("name", "pos", "to_par", "thru")},
                         {"name": "Steven Fisk", "pos": "T2", "to_par": "-20", "thru": 18})

    def _golf_in(self, name: str, period: int) -> list[SportsEvent]:
        data = fixture("pga_final")
        st = data["events"][0]["competitions"][0]["status"]
        st["period"] = period
        st["type"].update(name=name, state="in", completed=False)
        return espn.parse(LEAGUES["pga"], data)

    def test_golf_round_in_play(self) -> None:
        rounds = self._golf_in("STATUS_IN_PROGRESS", 2)
        self.assertEqual([r.status for r in rounds], ["final", "live", "scheduled", "scheduled"])
        self.assertEqual([r.score is not None for r in rounds], [False, True, False, False])
        self.assertEqual(rounds[1].score["round"], 2)

    def test_golf_round_play_complete_is_final(self) -> None:
        rounds = self._golf_in("STATUS_PLAY_COMPLETE", 2)
        self.assertEqual([r.status for r in rounds], ["final", "final", "scheduled", "scheduled"])

    def test_ufc_bouts_and_winners(self) -> None:
        final = parse("ufc_final")
        self.assertTrue(final)
        for b in final:
            self.assertEqual(len(b.participants), 2)
            if b.score:
                self.assertIn(b.score["winner"], [p["name"] for p in b.participants if p.get("winner")])
        first = by_id(final)["401914105"]
        self.assertEqual((first.session, first.score), ("W Flyweight", {"winner": "Lucia Szabová"}))
        for b in parse("ufc_scheduled"):
            self.assertEqual(b.status, "scheduled")
            self.assertTrue(all("winner" not in p for p in b.participants))

    def test_tennis_singles_only_known_players_only(self) -> None:
        matches = parse("atp_masters")
        self.assertEqual(len(matches), 4)  # the fixture's 2 TBD matches per grouping are not stored
        for m in matches:
            self.assertEqual(m.parent_espn_id, "13-2026")
            self.assertNotIn(espn.TBD_NAME, [p["name"] for p in m.participants])
        first = by_id(matches)["187334"]
        self.assertEqual(first.score, {"sets": [[6, 1], [7, 6]]})
        self.assertEqual(first.session, "Qualifying 1st Round")

    def test_tennis_scope_majors_masters_finals(self) -> None:
        data = fixture("wta_finals")
        self.assertEqual({e["name"] for e in data["events"]}, {"FAGE WTA Finals Indian Wells", "American Tennis Open Miami"})
        matches = espn.parse(LEAGUES["wta"], data)
        self.assertTrue(all(m.parent_espn_id == "382-2026" for m in matches))  # Miami is out of scope
        # An in-scope event with no status and no matches (ESPN's Miami shape) stores nothing, loudly or not at all.
        miami = copy.deepcopy([e for e in data["events"] if e["id"] == "1083-2026"][0])
        miami["major"] = True
        self.assertEqual(espn.parse(LEAGUES["wta"], {"events": [miami], "leagues": []}), [])


# ------------------------------------------------------------------ status

class StatusLifecycle(unittest.TestCase):
    def test_mapping_table(self) -> None:
        cases = {
            ("STATUS_SCHEDULED", "pre"): "scheduled",
            ("STATUS_POSTPONED", "pre"): "postponed",
            ("STATUS_CANCELED", "pre"): "canceled",
            ("STATUS_DELAYED", "pre"): "delayed",
            ("STATUS_IN_PROGRESS", "in"): "live",
            ("STATUS_HALFTIME", "in"): "live",
            ("STATUS_END_PERIOD", "in"): "live",
            ("STATUS_RAIN_DELAY", "in"): "delayed",
            ("STATUS_SUSPENDED", "in"): "delayed",
            ("STATUS_FINAL", "post"): "final",
            ("STATUS_FINAL_OT", "post"): "final",
            ("STATUS_FULL_TIME", "post"): "final",
            ("STATUS_RETIRED", "post"): "final",
            ("STATUS_WALKOVER", "post"): "final",
            ("STATUS_POSTPONED", "post"): "postponed",
            ("STATUS_CANCELED", "post"): "canceled",
            ("STATUS_ABANDONED", "post"): "canceled",
            ("STATUS_SUSPENDED", "post"): "suspended",
            ("STATUS_SOMETHING_NEW", "in"): "live",  # unfamiliar name: by its state
        }
        for (name, state), want in cases.items():
            with self.subTest(name=name, state=state):
                self.assertEqual(espn.map_status(name, state), want)

    def test_unfamiliar_name_is_kept_verbatim(self) -> None:
        data = fixture("nfl_final")
        data["events"][0]["competitions"][0]["status"]["type"]["name"] = "STATUS_SOMETHING_NEW"
        g = espn.parse(LEAGUES["nfl"], data)[0]
        self.assertEqual((g.status, g.espn_status), ("final", "STATUS_SOMETHING_NEW"))

    def test_unknown_state_is_a_shape_failure(self) -> None:
        data = fixture("nfl_final")
        data["events"][0]["competitions"][0]["status"]["type"]["state"] = "paused"
        with self.assertRaises(EspnShapeError) as ctx:
            espn.parse(LEAGUES["nfl"], data)
        self.assertIn("state", ctx.exception.path)

    def test_transitions_change_the_hash(self) -> None:
        data = fixture("ucl_live")
        before = {e.espn_id: e.content_hash() for e in espn.parse(LEAGUES["ucl"], data)}
        st = data["events"][0]["competitions"][0]["status"]
        st["type"].update(name="STATUS_FULL_TIME", state="post", completed=True, shortDetail="FT")
        after = {e.espn_id: e for e in espn.parse(LEAGUES["ucl"], data)}
        changed = data["events"][0]["id"]
        self.assertEqual(after[changed].status, "final")
        self.assertNotEqual(after[changed].content_hash(), before[changed])
        other = [k for k in before if k != changed][0]
        self.assertEqual(after[other].content_hash(), before[other])


# ----------------------------------------------------------- loud failures

class SchemaFailuresAreLoud(unittest.TestCase):
    def assertShape(self, league: str, data: Any, path_part: str) -> None:
        with self.assertRaises(EspnShapeError) as ctx:
            espn.parse(LEAGUES[league], data)
        self.assertIn(path_part, ctx.exception.path)
        self.assertEqual(ctx.exception.league, league)

    def test_missing_events(self) -> None:
        self.assertShape("nfl", {"leagues": []}, "events")

    def test_missing_leagues(self) -> None:
        self.assertShape("mlb", {"events": []}, "leagues")

    def test_renamed_team_name(self) -> None:
        data = fixture("nfl_final")
        team = data["events"][0]["competitions"][0]["competitors"][0]["team"]
        team["fullName"] = team.pop("displayName")
        self.assertShape("nfl", data, "displayName")

    def test_wrong_type(self) -> None:
        data = fixture("nba_scheduled")
        data["events"][0]["competitions"][0]["date"] = 1762300800
        self.assertShape("nba", data, "date")

    def test_bad_timestamp(self) -> None:
        data = fixture("nba_scheduled")
        data["events"][0]["competitions"][0]["date"] = "tonight"
        self.assertShape("nba", data, "date")

    def test_missing_status_state(self) -> None:
        data = fixture("f1_final")
        del data["events"][0]["competitions"][-1]["status"]["type"]["state"]
        self.assertShape("f1", data, "state")

    def test_three_teams(self) -> None:
        data = fixture("nhl_scheduled")
        comps = data["events"][0]["competitions"][0]["competitors"]
        comps.append(copy.deepcopy(comps[0]))
        self.assertShape("nhl", data, "competitors")

    def test_present_optional_field_of_wrong_type(self) -> None:
        data = fixture("nfl_final")
        data["events"][0]["competitions"][0]["broadcasts"] = [{"market": "national", "names": [7]}]
        self.assertShape("nfl", data, "names")

    def test_golf_player_without_a_name(self) -> None:
        data = fixture("pga_final")
        del data["events"][0]["competitions"][0]["competitors"][0]["athlete"]["displayName"]
        self.assertShape("pga", data, "displayName")

    def test_not_an_object(self) -> None:
        self.assertShape("ufc", ["events"], "$")


# -------------------------------------------------------------------- HTTP

class FakeHttp:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, params: Any) -> espn.Resp:
        self.calls.append((url, dict(params)))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


EMPTY = espn.Resp(200, {"events": [], "leagues": []}, 600)


class Client(unittest.TestCase):
    def test_per_day_league_one_call_per_date(self) -> None:
        http = FakeHttp(EMPTY)
        e = espn.Espn(http, sleep=lambda s: None)
        e.events(LEAGUES["cfb"], [date(2026, 11, 7), date(2026, 11, 5), date(2026, 11, 6)])
        self.assertEqual([c[1]["dates"] for c in http.calls], ["20261105", "20261106", "20261107"])
        self.assertTrue(all(c[1]["groups"] == "80" and c[1]["limit"] == "500" for c in http.calls))
        self.assertTrue(http.calls[0][0].endswith("/football/college-football/scoreboard"))
        self.assertEqual((e.calls, e.wire_bytes), (3, 1800))

    def test_range_league_one_call(self) -> None:
        http = FakeHttp(EMPTY)
        espn.Espn(http).events(LEAGUES["pga"], [date(2026, 11, 3), date(2026, 11, 11)])
        self.assertEqual([c[1]["dates"] for c in http.calls], ["20261103-20261111"])

    def test_one_retry_on_5xx(self) -> None:
        slept: list[float] = []
        http = FakeHttp(espn.Resp(503, None, 0), espn.Resp(200, fixture("nfl_final"), 11000))
        e = espn.Espn(http, sleep=slept.append)
        self.assertEqual(len(e.events(LEAGUES["nfl"], [date(2026, 11, 1)])), 2)
        self.assertEqual((len(http.calls), slept), (2, [espn.RETRY_AFTER_S]))

    def test_400_fails_without_retry(self) -> None:
        http = FakeHttp(espn.Resp(400, {"code": 400}, 55))
        with self.assertRaises(EspnFetchError) as ctx:
            espn.Espn(http, sleep=lambda s: None).events(LEAGUES["nba"], [date(2026, 11, 4)])
        self.assertEqual(len(http.calls), 1)
        self.assertIn("HTTP 400", str(ctx.exception))

    def test_transport_errors_twice_fail(self) -> None:
        http = FakeHttp(TimeoutError("read timed out"))
        with self.assertRaises(EspnFetchError) as ctx:
            espn.Espn(http, sleep=lambda s: None).events(LEAGUES["nba"], [date(2026, 11, 4)])
        self.assertEqual(len(http.calls), 2)
        self.assertIn("TimeoutError", str(ctx.exception))

    def test_body_not_json(self) -> None:
        with self.assertRaises(EspnFetchError):
            espn.Espn(FakeHttp(espn.Resp(200, None, 10))).events(LEAGUES["nba"], [date(2026, 11, 4)])


class ProviderSwitch(unittest.TestCase):
    def test_espn_is_the_default(self) -> None:
        p = provider.provider_from_env({}, http=FakeHttp(EMPTY))
        self.assertEqual(p.name, "espn")

    def test_api_sports_refuses_loudly(self) -> None:
        for env in ({"SPORTS_PROVIDER": "api_sports"}, {"SPORTS_PROVIDER": "api_sports", "API_SPORTS_KEY": "k"}):
            with self.subTest(key="API_SPORTS_KEY" in env), self.assertRaises(provider.ProviderUnavailable):
                provider.provider_from_env(env)

    def test_unknown_provider_refuses(self) -> None:
        with self.assertRaises(provider.ProviderUnavailable):
            provider.provider_from_env({"SPORTS_PROVIDER": "scraper"})


# ------------------------------------------------------------------- tiers

NOW = datetime(2026, 11, 4, 18, 50, tzinfo=timezone.utc)


def cached(status: str, starts: datetime, *, kind: str = "game", tbd: bool = False, league: str = "nba") -> jobs.Cached:
    return jobs.Cached(league, kind, starts, tbd, status, "h")


class Tiers(unittest.TestCase):
    def test_finished_events_are_cold(self) -> None:
        for st in ("final", "postponed", "canceled", "suspended"):
            self.assertEqual(jobs.tier(cached(st, NOW), NOW), "cold")

    def test_scheduled_with_a_time(self) -> None:
        for offset, want in ((timedelta(minutes=16), "cold"), (timedelta(minutes=15), "hot"),
                             (timedelta(0), "hot"), (-timedelta(hours=3), "hot"),
                             (-timedelta(hours=3, minutes=1), "warm"), (-timedelta(hours=24), "cold")):
            with self.subTest(starts_in=offset):
                self.assertEqual(jobs.tier(cached("scheduled", NOW + offset), NOW), want)

    def test_time_tbd_is_warm_from_its_day_start(self) -> None:
        self.assertEqual(jobs.tier(cached("scheduled", NOW + timedelta(minutes=5), tbd=True), NOW), "cold")
        self.assertEqual(jobs.tier(cached("scheduled", NOW - timedelta(hours=6), tbd=True), NOW), "warm")
        self.assertEqual(jobs.tier(cached("scheduled", NOW - timedelta(hours=25), tbd=True), NOW), "cold")

    def test_live_is_hot_to_its_ceiling_then_warm_then_cold(self) -> None:
        self.assertEqual(jobs.tier(cached("live", NOW - timedelta(hours=2)), NOW), "hot")
        self.assertEqual(jobs.tier(cached("delayed", NOW - timedelta(hours=2)), NOW), "hot")
        self.assertEqual(jobs.tier(cached("live", NOW - timedelta(hours=9)), NOW), "warm")
        self.assertEqual(jobs.tier(cached("live", NOW - timedelta(hours=25)), NOW), "cold")
        self.assertEqual(jobs.tier(cached("live", NOW - timedelta(hours=19), kind="golf_round", tbd=True), NOW), "hot")

    def test_plan(self) -> None:
        c = jobs.EventCache()
        self.assertEqual(c.plan(NOW), {})
        c.events[("nba", "1")] = cached("live", NOW - timedelta(hours=1))
        c.events[("nba", "2")] = cached("scheduled", NOW + timedelta(hours=5))  # cold: not fetched for
        c.events[("pga", "x-r1")] = cached("scheduled", NOW - timedelta(hours=10), kind="golf_round",
                                           tbd=True, league="pga")
        c.events[("nfl", "3")] = cached("final", NOW - timedelta(hours=1), league="nfl")
        plan = c.plan(NOW)
        self.assertEqual(plan, {"nba": ("hot", [date(2026, 11, 4)]), "pga": ("warm", [date(2026, 11, 4)])})
        c.last_poll["pga"] = NOW - timedelta(minutes=9)
        self.assertNotIn("pga", c.plan(NOW))
        c.last_poll["pga"] = NOW - timedelta(minutes=10)
        self.assertIn("pga", c.plan(NOW))

    def test_dates_are_eastern(self) -> None:
        # 00:30 UTC Nov 5 is 7:30 PM Eastern Nov 4: ESPN files it under Nov 4.
        c = jobs.EventCache()
        c.events[("nba", "1")] = cached("live", datetime(2026, 11, 5, 0, 30, tzinfo=timezone.utc))
        self.assertEqual(c.plan(datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc))["nba"][1], [date(2026, 11, 4)])


# -------------------------------------------------------------------- jobs

class FakeProvider:
    name = "fake"

    def __init__(self, data: dict[str, Any], fail: dict[str, Exception] | None = None) -> None:
        self.data = data
        self.fail = fail or {}
        self.requests: list[tuple[str, list[date]]] = []
        self.calls = 0
        self.wire_bytes = 0
        self.closed = False

    def events(self, lg: League, days: list[date]) -> list[SportsEvent]:
        self.requests.append((lg.code, list(days)))
        self.calls += 1
        self.wire_bytes += 1024
        if lg.code in self.fail:
            raise self.fail[lg.code]
        return espn.parse(lg, copy.deepcopy(self.data.get(lg.code, {"events": [], "leagues": []})))

    def close(self) -> None:
        self.closed = True


def league_rows(*codes: str) -> list[ledger.SportsLeagueRow]:
    return [ledger.SportsLeagueRow(LEAGUES[c].code, LEAGUES[c].path, dict(LEAGUES[c].params), LEAGUES[c].fetch_mode,
                                   LEAGUES[c].kind, LEAGUES[c].owner_five, LEAGUES[c].sort) for c in codes]


class JobsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.upserts: list[list[dict[str, Any]]] = []
        self.stored: dict[tuple[str, str], str] = {}

        def upsert(rows, **kw):
            self.assertFalse(kw.get("is_test", False))
            self.upserts.append(list(rows))
            ins = upd = 0
            for r in rows:
                k = (r["league"], r["espn_id"])
                if k not in self.stored:
                    ins += 1
                elif self.stored[k] != r["content_hash"]:
                    upd += 1
                self.stored[k] = r["content_hash"]
            return ledger.SportsUpsert(ins, upd)

        for p in (mock.patch.object(ledger, "_pool", _refuse_database),
                  mock.patch.object(ledger, "sports_events_upsert", upsert),
                  mock.patch.object(jobs.memory, "release", lambda: None)):
            p.start()
            self.addCleanup(p.stop)
        self.cache = jobs.EventCache()
        self.registry: dict[str, League] = {}

    def schedule(self, prov: FakeProvider, *codes: str) -> jobs.SportsRun:
        with mock.patch.object(ledger, "sports_leagues", lambda **k: league_rows(*codes)):
            return jobs.schedule_job(provider=prov, now=lambda: NOW, cache=self.cache, registry=self.registry)

    def live(self, prov: FakeProvider, at: datetime = NOW) -> jobs.SportsRun:
        made: list[FakeProvider] = []

        def factory() -> FakeProvider:
            made.append(prov)
            return prov
        run = jobs.live_job(provider_factory=factory, now=lambda: at, cache=self.cache, leagues=self.registry)
        self.made = made
        return run


class ScheduleJob(JobsCase):
    def test_every_league_over_yesterday_to_a_week_ahead(self) -> None:
        prov = FakeProvider({"nfl": fixture("nfl_scheduled"), "pga": fixture("pga_scheduled")})
        run = self.schedule(prov, "nfl", "pga", "mlb")
        want_days = [date(2026, 11, 3) + timedelta(days=d) for d in range(9)]
        self.assertEqual(prov.requests, [("nfl", want_days), ("pga", want_days), ("mlb", want_days)])
        self.assertEqual(run.leagues, {"nfl": 2, "pga": 4, "mlb": 0})
        self.assertEqual((run.sent, run.inserted, run.updated), (6, 6, 0))
        self.assertEqual(len(self.cache.events), 6)
        self.assertEqual(set(self.registry), {"nfl", "pga", "mlb"})
        self.assertTrue(prov.closed)
        self.assertIn("nfl 2, pga 4, mlb 0", run.job_note)

    def test_a_second_run_sends_nothing_unchanged(self) -> None:
        prov = FakeProvider({"nfl": fixture("nfl_scheduled")})
        self.schedule(prov, "nfl")
        run = self.schedule(prov, "nfl")
        self.assertEqual((run.sent, run.inserted, run.updated), (0, 0, 0))
        self.assertEqual(len(self.upserts), 1)

    def test_one_league_failing_does_not_stop_the_others_but_fails_the_run(self) -> None:
        prov = FakeProvider({"nba": fixture("nba_scheduled")},
                            fail={"nfl": EspnShapeError("nfl", "$.events[0].id", "missing")})
        with self.assertRaises(jobs.SportsFetchFailed) as ctx:
            self.schedule(prov, "nfl", "nba")
        self.assertIn("nfl: shape at $.events[0].id: missing", str(ctx.exception))
        self.assertEqual({r["league"] for batch in self.upserts for r in batch}, {"nba"})
        self.assertTrue(prov.closed)

    def test_a_failed_league_keeps_its_cached_events(self) -> None:
        self.schedule(FakeProvider({"nfl": fixture("nfl_scheduled")}), "nfl")
        with self.assertRaises(jobs.SportsFetchFailed):
            self.schedule(FakeProvider({}, fail={"nfl": EspnFetchError("nfl", "HTTP 503")}), "nfl")
        self.assertEqual(len([k for k in self.cache.events if k[0] == "nfl"]), 2)

    def test_no_active_league_is_loud(self) -> None:
        with self.assertRaises(RuntimeError):
            self.schedule(FakeProvider({}))


class LiveJob(JobsCase):
    def test_idle_when_nothing_is_in_play_zero_calls(self) -> None:
        run = self.live(FakeProvider({}))
        self.assertTrue(run.idle)
        self.assertEqual(self.made, [])
        self.assertIn("0 ESPN calls", run.job_note)
        # Everything finished or far off: still idle.
        self.schedule(FakeProvider({"nfl": fixture("nfl_final"), "ufc": fixture("ufc_scheduled")}), "nfl", "ufc")
        self.assertTrue(self.live(FakeProvider({})).idle)
        self.assertEqual(self.made, [])

    def test_polls_only_the_league_in_play_and_writes_only_changes(self) -> None:
        data = {"ucl": fixture("ucl_live"), "nba": fixture("nba_scheduled")}
        self.schedule(FakeProvider(data), "ucl", "nba")
        writes_after_schedule = len(self.upserts)
        prov = FakeProvider(data)
        run = self.live(prov)
        self.assertEqual(prov.requests, [("ucl", [date(2026, 11, 4)])])  # NBA tips at 00:00 UTC: not yet
        self.assertEqual((run.sent, len(self.upserts)), (0, writes_after_schedule))  # nothing changed
        # The goal: one row changes, one row is sent.
        moved = copy.deepcopy(data["ucl"])
        live = [e for e in moved["events"] if e["status"]["type"]["state"] == "in"][0]
        live["competitions"][0]["status"]["displayClock"] = "64'"
        run = self.live(FakeProvider({"ucl": moved, "nba": data["nba"]}))
        self.assertEqual((run.sent, run.updated), (1, 1))
        self.assertEqual(self.upserts[-1][0]["clock"], "64'")

    def test_tip_off_window_turns_a_league_hot(self) -> None:
        self.schedule(FakeProvider({"nba": fixture("nba_scheduled")}), "nba")
        prov = FakeProvider({"nba": fixture("nba_scheduled")})
        self.live(prov, at=datetime(2026, 11, 4, 23, 46, tzinfo=timezone.utc))  # 14 min before 00:00 UTC
        self.assertEqual(prov.requests, [("nba", [date(2026, 11, 4)])])

    def test_a_failed_league_is_loud_and_the_others_still_write(self) -> None:
        data = {"ucl": fixture("ucl_live"), "nba": fixture("nba_scheduled")}
        self.schedule(FakeProvider(data), "ucl", "nba")
        at = datetime(2026, 11, 4, 23, 50, tzinfo=timezone.utc)  # both in play
        moved = copy.deepcopy(data["ucl"])
        [e for e in moved["events"] if e["status"]["type"]["state"] == "in"][0]["competitions"][0]["status"][
            "displayClock"] = "90'"
        prov = FakeProvider({"ucl": moved}, fail={"nba": EspnFetchError("nba", "dates=20261104: HTTP 503")})
        with self.assertRaises(jobs.SportsFetchFailed) as ctx:
            self.live(prov, at=at)
        self.assertIn("nba", str(ctx.exception))
        self.assertEqual(self.upserts[-1][0]["league"], "ucl")

    def test_an_in_play_event_missing_from_espn_is_counted_not_deleted(self) -> None:
        data = {"ucl": fixture("ucl_live")}
        self.schedule(FakeProvider(data), "ucl")
        gone = copy.deepcopy(data["ucl"])
        gone["events"] = [e for e in gone["events"] if e["status"]["type"]["state"] != "in"]
        run = self.live(FakeProvider({"ucl": gone}))
        self.assertEqual(run.missing, 1)
        self.assertEqual(len([k for k in self.cache.events if k[0] == "ucl"]), 2)

    def test_warm_league_waits_ten_minutes(self) -> None:
        self.schedule(FakeProvider({"pga": fixture("pga_scheduled")}), "pga")
        at = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)  # round 1's day, no tee time known
        first = FakeProvider({"pga": fixture("pga_scheduled")})
        self.live(first, at=at)
        self.assertEqual(first.requests, [("pga", [date(2026, 11, 5)])])
        self.assertTrue(self.live(FakeProvider({}), at=at + timedelta(minutes=5)).idle)
        again = FakeProvider({"pga": fixture("pga_scheduled")})
        self.live(again, at=at + timedelta(minutes=10))
        self.assertEqual(len(again.requests), 1)


class HashRule(unittest.TestCase):
    def test_hash_covers_every_stored_field(self) -> None:
        g = parse("nfl_final")[0]
        self.assertEqual(g.content_hash(), parse("nfl_final")[0].content_hash())
        for change in ({"score": {"home": 37, "away": 10}}, {"broadcast": ("CBS", "Paramount+")},
                       {"starts_at": g.starts_at + timedelta(minutes=5)}, {"status": "postponed"}):
            with self.subTest(change=list(change)):
                self.assertNotEqual(g.content_hash(), SportsEvent(**{**g.__dict__, **change}).content_hash())

    def test_row_has_no_kalshi_or_test_fields(self) -> None:
        row = parse("nfl_final")[0].row()
        self.assertFalse({"kalshi_event_ticker", "is_test", "id"} & set(row))
        self.assertTrue(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", row["starts_at"]))


class Registered(unittest.TestCase):
    def test_both_jobs_are_system_jobs(self) -> None:
        from core import system_jobs
        built = {j.name: j for j in system_jobs.build_system_jobs({})}
        self.assertEqual(built["sports_live"].expected_interval_s, 60)
        self.assertEqual(built["sports_schedule"].expected_interval_s, 24 * 60 * 60)
        self.assertTrue(built["sports_schedule"].run_at_boot)


if __name__ == "__main__":
    unittest.main()
