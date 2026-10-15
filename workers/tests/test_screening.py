"""The Screening Room (Chat 4 Phase 3, db/051): the owner's services and the
Tonight split, the trailer filter on saved YouTube feeds, title cleaning and
TMDB matching on saved searches, the TMDB client's refusals and backoff, the
shaping of real TMDB detail responses, the whole-YouTube outage collapse,
screening ingest (trailers only, its own world, the capped backfill), the
match and refresh jobs, and the sources file.

Fixtures (tests/fixtures/screening/) are real responses fetched 2026-10-14:
six channel feeds (yt_*.xml), eight TMDB searches (tmdb_search_*.json), The
Gilded Age (tv 81723) and City of God (movie 598) with videos and providers.

No database, no network: the ledger is patched and TMDB is a fake HTTP
function.
"""

from __future__ import annotations

import html
import json
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from core import ledger, push
from core.jobs import JobSkipped
from feed import ingest
from feed.parse import parse_feed
from feed.sources import SourcesInvalid, _spec, load_sources
from screening import jobs, services, tmdb, trailers
from tests.support import _refuse_database
from tests.test_feed import NOW, IngestCase, source

FIX = Path(__file__).with_name("fixtures") / "screening"
WEB = Path(__file__).resolve().parents[2] / "apps" / "web" / "src" / "lib" / "tmdb" / "services.ts"
TOKEN = "eyJ" + "x" * 60


def fixture_json(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def feed_entries(name: str) -> list[tuple[str, str]]:
    text = (FIX / f"yt_{name}.xml").read_text(encoding="utf-8")
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", text, re.S):
        title = html.unescape(re.search(r"<title>(.*?)</title>", e, re.S).group(1))
        link = re.search(r'<link rel="alternate" href="([^"]+)"', e).group(1)
        out.append((title, link))
    return out


# ====================================================================== services

class Services(unittest.TestCase):
    def test_the_owners_services(self) -> None:
        self.assertEqual(set(services.MY_SERVICES.values()), {"Netflix", "HBO Max", "Paramount+", "Starz", "Prime Video"})
        # Ads tiers count; channels resold through Amazon/Roku/Apple and the rent store do not.
        for pid in (1796, 2616, 2100, 613):
            self.assertIn(pid, services.MY_SERVICES)
        for pid in (1825, 582, 633, 1853, 1794, 634, 1855, 10):
            self.assertNotIn(pid, services.MY_SERVICES)

    def test_the_web_twin_is_identical(self) -> None:
        ts = WEB.read_text(encoding="utf-8")
        block = re.search(r"MY_SERVICES: Readonly<Record<number, string>> = \{(.*?)\};", ts, re.S).group(1)
        web = {int(k): v for k, v in re.findall(r"(\d+): \"([^\"]+)\"", block)}
        self.assertEqual(web, services.MY_SERVICES)
        self.assertIn('STREAM_KINDS = ["flatrate", "ads", "free"]', ts)
        self.assertEqual(services.STREAM_KINDS, ("flatrate", "ads", "free"))

    def test_tonight_on_real_titles(self) -> None:
        gilded = tmdb.us_providers(fixture_json("tmdb_tv_81723.json"))
        s = services.split(gilded)
        self.assertEqual([t["service"] for t in s["tonight"]], ["HBO Max"])
        # The HBO Max Amazon Channel (1825) and Spectrum (486) are other streaming, not the owner's.
        self.assertEqual({r["id"] for r in s["other_stream"]}, {1825, 486})
        self.assertTrue(s["buy"])
        city = services.split(tmdb.us_providers(fixture_json("tmdb_movie_598.json")))
        self.assertEqual([(t["service"], t["id"]) for t in city["tonight"]], [("Paramount+", 2616)])
        self.assertTrue(city["rent"] and city["buy"])
        self.assertIn(191, {r["id"] for r in city["other_stream"]})  # a free service that is not the owner's

    def test_one_tonight_row_per_service_and_ads_free_count(self) -> None:
        p = {"flatrate": [{"id": 8, "name": "Netflix", "logo": None}],
             "ads": [{"id": 1796, "name": "Netflix with Ads", "logo": None}, {"id": 2100, "name": "Prime w Ads", "logo": None}],
             "free": [{"id": 613, "name": "Prime Free", "logo": None}]}
        s = services.split(p)
        self.assertEqual([(t["service"], t["kind"]) for t in s["tonight"]], [("Netflix", "flatrate"), ("Prime Video", "ads")])
        self.assertEqual(services.split(None), {"tonight": [], "other_stream": [], "rent": [], "buy": []})


# ====================================================================== trailers

class TrailerFilter(unittest.TestCase):
    def test_on_the_saved_feeds(self) -> None:
        kept = {name: [t for t, link in feed_entries(name) if trailers.is_trailer(t, link)]
                for name in ("netflix", "a24", "warnerbros", "marvel", "hbomax", "prime")}
        self.assertEqual(len(kept["netflix"]), 10)
        self.assertEqual(kept["warnerbros"], [])  # all Shorts and promos today
        self.assertEqual(kept["a24"], ["Misty Green | Official Trailer 2 HD | A24",
                                       "You Can See Everything | Official Trailer HD | A24"])
        self.assertIn("The Greatest - Official Trailer | Prime Video", kept["prime"])
        everything = [t for name in kept for t in kept[name]]
        for dropped in ("Love Is Blind S11: Boston | Sneak Peek | Netflix", "Misty Green | Official First Look | A24",
                        "The Gilded Age Seasons 1-3 Recap | The Gilded Age | HBO Max",
                        "Carrie S1E1 Full Episode \"First Period\" | Prime Video"):
            self.assertNotIn(dropped, everything)

    def test_shorts_clips_and_reactions_are_dropped(self) -> None:
        self.assertFalse(trailers.is_trailer("Dune | Official Trailer", "https://www.youtube.com/shorts/abc"))
        for t in ("Dune Trailer Reaction", "Dune Trailer Breakdown", "Dune | Official Clip", "Dune Featurette",
                  "Dune | Trailer Recap", "Dune Teaser Explained"):
            with self.subTest(t=t):
                self.assertFalse(trailers.is_trailer(t, "https://www.youtube.com/watch?v=abc"))
        for t in ("Dune | Official Trailer", "Dune: Part Three - Teaser", "Dune Trailer 2", "DUNE | Official Teaser Trailer"):
            with self.subTest(t=t):
                self.assertTrue(trailers.is_trailer(t, "https://www.youtube.com/watch?v=abc"))


class Cleaning(unittest.TestCase):
    def test_headlines_to_names(self) -> None:
        cases = {
            "Guarding Stars | Leighton Meester & Jared Padalecki | Official Trailer | Netflix": ("Guarding Stars", False),
            "The Gilded Age Season 4 | Official Trailer | HBO Max": ("The Gilded Age", True),
            "The Greatest - Official Trailer | Prime Video": ("The Greatest", False),
            "Kordell Stewart: Slash & Burn Trailer | Official Trailer | HBO Max": ("Kordell Stewart: Slash & Burn", False),
            "Misty Green | Official Trailer 2 HD | A24": ("Misty Green", False),
            "Love Is Blind S11: Boston | Official Trailer": ("Love Is Blind", True),
        }
        for headline, (name, tv) in cases.items():
            with self.subTest(h=headline[:30]):
                c = trailers.clean_title(headline)
                self.assertEqual((c.query, c.tv_hint), (name, tv))
        self.assertEqual(trailers.clean_title("Weapons (2025) | Official Trailer").year, 2025)

    def test_norm(self) -> None:
        self.assertEqual(trailers.norm("NARNIA: THE MAGICIAN’S NEPHEW"), trailers.norm("Narnia: The Magician's Nephew"))
        self.assertEqual(trailers.norm("Magician’s"), "magicians")
        self.assertEqual(trailers.norm("Slash & Burn"), "slash and burn")


class Matching(unittest.TestCase):
    SEARCHES = {"Guarding Stars": "guarding_stars", "The Gilded Age": "the_gilded_age",
                "NARNIA: THE MAGICIAN’S NEPHEW": "narnia__the_magician_s_nephew", "City of God": "city_of_god",
                "Misty Green": "misty_green", "Cyberpunk: Edgerunners 2": "cyberpunk__edgerunners_2",
                "Cyberpunk: Edgerunners": "cyberpunk__edgerunners_2", "The Greatest": "the_greatest",
                "Avengers #1": "avengers__1"}
    POSTED = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)

    def search(self, q: str) -> list[dict]:
        self.asked.append(q)
        return [r for r in fixture_json(f"tmdb_search_{self.SEARCHES[q]}.json")["results"]
                if r.get("media_type") in ("movie", "tv")]

    def setUp(self) -> None:
        self.asked: list[str] = []

    def m(self, headline: str):
        return trailers.match(self.search, headline, self.POSTED)[0]

    def test_exact_names_match(self) -> None:
        self.assertEqual(self.m("Guarding Stars | Official Trailer | Netflix"), trailers.Match(1551025, "movie", "Guarding Stars"))
        self.assertEqual(self.m("NARNIA: THE MAGICIAN’S NEPHEW | Official Trailer | Netflix").tmdb_id, 1147572)
        self.assertEqual(self.m("Misty Green | Official Trailer 2 HD | A24").tmdb_id, 1381221)

    def test_a_season_wants_tv(self) -> None:
        # The search also has a 2018 FILM "The Gilded Age"; the season makes it the series.
        self.assertEqual(self.m("The Gilded Age Season 4 | Official Trailer | HBO Max"),
                         trailers.Match(81723, "tv", "The Gilded Age"))

    def test_a_movie_needs_a_year_near_the_post(self) -> None:
        # HBO Max's 2026 "City of God" trailer is the series, not the 2003 film: unmatched, never wrong.
        self.assertIsNone(self.m("City of God | Official Trailer | HBO Max"))

    def test_a_sequel_number_falls_back_to_the_series(self) -> None:
        self.assertEqual(self.m("Cyberpunk: Edgerunners 2 | Official Trailer | Netflix"),
                         trailers.Match(105248, "tv", "Cyberpunk: Edgerunners"))
        self.assertEqual(self.asked, ["Cyberpunk: Edgerunners 2", "Cyberpunk: Edgerunners"])

    def test_a_new_series_with_the_exact_name(self) -> None:
        # Prime Video's "The Greatest" is the 2026 Muhammad Ali series (tv 258989), found
        # among "The Greatest Gift", "The Greatest Showman" and the rest by its exact name.
        self.assertEqual(self.m("The Greatest - Official Trailer | Prime Video"), trailers.Match(258989, "tv", "The Greatest"))

    def test_no_exact_name_is_unmatched(self) -> None:
        self.assertIsNone(self.m("Avengers #1 | Official Trailer | Marvel Comics"))


# ====================================================================== TMDB client

class FakeHttp:
    def __init__(self, answers: list[tmdb.Resp]) -> None:
        self.answers, self.calls = answers, []

    def __call__(self, url, params, headers):
        self.calls.append((url, dict(params), dict(headers)))
        return self.answers.pop(0)


class Client(unittest.TestCase):
    def test_missing_token_refuses_by_name(self) -> None:
        for env in ({}, {tmdb.TOKEN_ENV: " "}):
            with self.assertRaises(tmdb.TmdbTokenError) as cm:
                tmdb.read_token(env)
            self.assertIn("TMDB_READ_TOKEN", str(cm.exception))

    def test_401_is_loud_and_never_echoes_the_token(self) -> None:
        t = tmdb.Tmdb(TOKEN, FakeHttp([tmdb.Resp(401, {"status_message": "Invalid API key"}, {})]), sleep=lambda s: None)
        with self.assertRaises(tmdb.TmdbTokenError) as cm:
            t.search_multi("dune")
        self.assertIn("TMDB_READ_TOKEN", str(cm.exception))
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_bearer_region_and_language(self) -> None:
        http = FakeHttp([tmdb.Resp(200, {"results": [{"media_type": "person", "id": 1}, {"media_type": "tv", "id": 2}]}, {})])
        t = tmdb.Tmdb(TOKEN, http, sleep=lambda s: None)
        self.assertEqual(t.search_multi("x"), [{"media_type": "tv", "id": 2}])
        url, params, headers = http.calls[0]
        self.assertEqual(url, "https://api.themoviedb.org/3/search/multi")
        self.assertEqual((params["region"], params["language"]), ("US", "en-US"))
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")

    def test_429_waits_then_retries_and_gives_up_loudly(self) -> None:
        sleeps: list[float] = []
        ok = FakeHttp([tmdb.Resp(429, None, {"retry-after": "3"}), tmdb.Resp(200, {"results": []}, {})])
        tmdb.Tmdb(TOKEN, ok, sleep=sleeps.append, clock=lambda: 100.0).search_multi("x")
        self.assertIn(3.0, sleeps)
        never = FakeHttp([tmdb.Resp(429, None, {})] * (tmdb.MAX_429 + 1))
        with self.assertRaises(tmdb.TmdbError):
            tmdb.Tmdb(TOKEN, never, sleep=lambda s: None).search_multi("x")

    def test_at_most_20_a_second(self) -> None:
        clock = [0.0]
        sleeps: list[float] = []

        def sleep(s: float) -> None:
            sleeps.append(s)
            clock[0] += s

        t = tmdb.Tmdb(TOKEN, FakeHttp([tmdb.Resp(200, {"results": []}, {})] * 5), sleep=sleep, clock=lambda: clock[0])
        for _ in range(5):
            t.search_multi("x")
        self.assertEqual(len(sleeps), 4)
        self.assertTrue(all(abs(s - 1 / tmdb.RATE) < 1e-9 for s in sleeps))
        self.assertLessEqual(tmdb.RATE, 20)


class Shaping(unittest.TestCase):
    def test_a_movie(self) -> None:
        t = tmdb.title_from("movie", fixture_json("tmdb_movie_598.json"))
        self.assertEqual((t.tmdb_id, t.kind, t.name), (598, "movie", "City of God"))
        self.assertEqual(t.release_date, date(2004, 2, 13))  # the earliest US theatrical (type 3)
        self.assertIsNone(t.next_episode_date)
        self.assertRegex(t.trailer_key or "", r"^[A-Za-z0-9_-]{6,20}$")
        self.assertIn("flatrate", t.providers)

    def test_a_series(self) -> None:
        t = tmdb.title_from("tv", fixture_json("tmdb_tv_81723.json"))
        self.assertEqual((t.name, t.next_episode_date, t.status), ("The Gilded Age", date(2026, 11, 1), "Returning Series"))
        self.assertEqual(t.release_date, date(2022, 1, 24))


# ====================================================================== YouTube outage

def yt_source(sid: int, slug: str, first: bool = False) -> ledger.FeedSource:
    s = source(sid, slug, first=first, pillars=("screening",))
    return ledger.FeedSource(**{**s.__dict__, "url": f"https://www.youtube.com/feeds/videos.xml?channel_id=UC{sid:022d}"})


class YoutubeOutage(IngestCase):
    def setUp(self) -> None:
        super().setUp()
        ingest.YOUTUBE.runs, ingest.YOUTUBE.alerted = 0, False
        p = mock.patch.object(ledger, "notification_sent_since", lambda kind, hours, **kw: False)
        p.start()
        self.addCleanup(p.stop)

    def run_all(self, srcs: list[ledger.FeedSource]) -> bool:
        run = self.run_ingest()
        stats = [run.run_source(s) for s in srcs]
        return run.settle_youtube(stats, [s.slug for s in srcs if ingest.is_youtube(s)])

    def test_every_youtube_feed_failing_is_one_state_not_n_alerts(self) -> None:
        srcs = [yt_source(i, f"yt-{i}") for i in range(1, 15)]
        for s in srcs:
            self.feeds[s.url] = 404
        for _ in range(ingest.YT_ALERT_RUNS + 3):
            self.assertTrue(self.run_all(srcs))
        self.assertEqual(self.db.failed, [])  # no per-source failure counted
        alerts = [m for m in self.sent if m.kind == ingest.YT_OUTAGE_KIND]
        self.assertEqual(len(alerts), 1)  # one push, after YT_ALERT_RUNS runs
        self.assertEqual([m for m in self.sent if m.kind == "feed_source_failing"], [])
        # YouTube answers again: re-armed, and nothing more is pushed.
        for s in srcs:
            self.feeds[s.url] = (FIX / "yt_netflix.xml").read_bytes()
        self.assertFalse(self.run_all(srcs))
        self.assertEqual(ingest.YOUTUBE.runs, 0)

    def test_some_youtube_feeds_failing_is_counted_per_source(self) -> None:
        srcs = [yt_source(i, f"yt-{i}") for i in range(1, 5)]
        self.feeds[srcs[0].url] = 404
        for s in srcs[1:]:
            self.feeds[s.url] = (FIX / "yt_a24.xml").read_bytes()
        self.assertFalse(self.run_all(srcs))
        self.assertEqual([sid for sid, _ in self.db.failed], [srcs[0].id])

    def test_a_non_youtube_failure_is_never_held(self) -> None:
        home = source(90, "home-src")
        self.feeds[home.url] = 500
        srcs = [yt_source(i, f"yt-{i}") for i in range(1, 4)]
        for s in srcs:
            self.feeds[s.url] = 404
        run = self.run_ingest()
        stats = [run.run_source(s) for s in [home, *srcs]]
        self.assertEqual([sid for sid, _ in self.db.failed], [90])  # recorded at once
        self.assertTrue(run.settle_youtube(stats, [s.slug for s in srcs]))
        self.assertEqual([sid for sid, _ in self.db.failed], [90])


# ====================================================================== screening ingest

class ScreeningIngest(IngestCase):
    def test_only_trailers_are_stored_in_their_own_world(self) -> None:
        src = yt_source(7, "yt-netflix")
        body = (FIX / "yt_netflix.xml").read_bytes()
        self.feeds[src.url] = body
        stats = self.run_ingest().run_source(src)
        titles = [s["title"] for s in self.db.stories.values()]
        self.assertTrue(titles)
        self.assertTrue(all(trailers.is_trailer(t, None) for t in titles))
        self.assertGreater(stats.not_trailers, 0)
        self.assertEqual(stats.fetched, len(parse_feed(body)) - stats.not_trailers)

    def test_a_first_fetch_backfill_is_capped(self) -> None:
        src = yt_source(8, "yt-netflix", first=True)
        old = NOW + timedelta(days=30)  # the fixture's entries are all "old" relative to this clock
        self.feeds[src.url] = (FIX / "yt_netflix.xml").read_bytes()
        run = ingest.Ingest(get=self.get, decode=lambda u: None, embed=self.embedder, send=lambda m: [],
                            now=lambda: old)
        run.prepare({})
        run.run_source(src)
        kept = [v["item"] for v in self.db.items.values() if "item" in v]
        self.assertEqual(len(kept), ingest.BACKFILL)
        self.assertTrue(all(it.backfill for it in kept))

    def test_worlds(self) -> None:
        self.assertEqual(ingest.world_of(("screening",)), "screening")
        self.assertEqual(ingest.world_of(("tech",)), "tech")
        self.assertEqual(ingest.world_of(("home", "sports")), "main")
        c = ledger.CandidateStory(1, 1, (), "screening")
        self.assertFalse(c.tech)


# ====================================================================== jobs

class Jobs(unittest.TestCase):
    def setUp(self) -> None:
        self.recorded: list[tuple[int, int | None, str]] = []
        self.upserts: list[tmdb.TitleIn] = []
        patches = [
            mock.patch.object(ledger, "_pool", _refuse_database),
            mock.patch.object(ledger, "title_trailer_record", lambda i, t, q, **k: self.recorded.append((i, t, q)) or True),
            mock.patch.object(ledger, "titles_upsert", lambda t, **k: self.upserts.append(t) or 500 + len(self.upserts)),
            mock.patch.object(jobs.memory, "release", lambda: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def client(self) -> tmdb.Tmdb:
        def http(url, params, headers):
            if url.endswith("/search/multi"):
                q = params["query"]
                slug = Matching.SEARCHES.get(q)
                return tmdb.Resp(200, fixture_json(f"tmdb_search_{slug}.json") if slug else {"results": []}, {})
            if url.endswith("/tv/81723"):
                return tmdb.Resp(200, fixture_json("tmdb_tv_81723.json"), {})
            return tmdb.Resp(200, {"id": int(url.rsplit("/", 1)[1]), "title": "X", "name": "X"}, {})
        return tmdb.Tmdb(TOKEN, http, sleep=lambda s: None)

    def test_match_job_records_matched_and_unmatched(self) -> None:
        posted = datetime(2026, 10, 14, tzinfo=timezone.utc)
        queue = [ledger.TrailerItem(1, "The Gilded Age Season 4 | Official Trailer | HBO Max", posted),
                 ledger.TrailerItem(2, "Avengers #1 | Official Trailer | Marvel Comics", posted),
                 ledger.TrailerItem(3, "The Gilded Age | Official Teaser | HBO Max", posted)]
        with mock.patch.object(ledger, "screening_unmatched", lambda limit, **k: queue):
            run = jobs.match_job(client=self.client())
        self.assertEqual([(i, t) for i, t, _ in self.recorded], [(1, 501), (2, None), (3, 501)])
        self.assertEqual(len(self.upserts), 1)  # one title, fetched once for both trailers
        self.assertEqual(run.job_note.split(";")[0], "2 matched, 1 unmatched")

    def test_refresh_job_and_its_skip(self) -> None:
        shelf = [ledger.ShelfTitle(1, 81723, "tv", "watching")]
        with mock.patch.object(ledger, "job_ok_today", lambda j, **k: False), \
                mock.patch.object(ledger, "screening_shelf_titles", lambda **k: shelf):
            run = jobs.refresh_job(client=self.client())
        self.assertEqual(run.refreshed, 1)
        self.assertEqual(self.upserts[0].next_episode_date, date(2026, 11, 1))
        with mock.patch.object(ledger, "job_ok_today", lambda j, **k: True), self.assertRaises(JobSkipped):
            jobs.refresh_job(client=self.client())

    def test_without_a_token_the_jobs_refuse(self) -> None:
        with mock.patch.object(ledger, "screening_unmatched", lambda limit, **k: [ledger.TrailerItem(1, "X Trailer", NOW)]), \
                self.assertRaises(tmdb.TmdbTokenError):
            jobs.match_job(env={})


# ====================================================================== sources

class SourcesFile(unittest.TestCase):
    def test_the_15_trailer_channels(self) -> None:
        s = {x.slug: x for x in load_sources() if "screening" in x.pillars and x.kind == "rss"}
        self.assertEqual(len(s), 15)
        self.assertTrue(all(x.slug.startswith("yt-") and x.active and not x.official and x.pillars == ("screening",)
                            for x in s.values()))
        self.assertEqual(s["yt-hbomax"].url, "https://www.youtube.com/feeds/videos.xml?channel_id=UCx-KWLTKlB83hDI6UKECtJQ")
        self.assertEqual(len({x.url for x in s.values()}), 15)

    def test_the_tmdb_videos_source(self) -> None:
        # Trailer source #2 (db/053): screening only, never official, the one non-YouTube screening source.
        s = [x for x in load_sources() if x.kind == "tmdb"]
        self.assertEqual([(x.slug, x.pillars, x.official, x.url) for x in s],
                         [("tmdb-videos", ("screening",), False, "https://api.themoviedb.org/3")])
        bad = {"slug": "tmdb-x", "name": "X", "pillars": ["home"], "kind": "tmdb", "url": "https://api.themoviedb.org/3"}
        with self.assertRaises(SourcesInvalid):
            _spec(bad)
        with self.assertRaises(SourcesInvalid):
            _spec({**bad, "pillars": ["screening"], "url": "https://example.com/3"})

    def test_rules(self) -> None:
        good = {"slug": "yt-x", "name": "X", "pillars": ["screening"], "kind": "rss",
                "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC" + "a" * 22}
        _spec(good)
        for bad in ({**good, "pillars": ["screening", "home"]}, {**good, "slug": "x"},
                    {**good, "url": "https://example.invalid/feed"}, {**good, "kind": "page"}):
            with self.subTest(bad=bad), self.assertRaises(SourcesInvalid):
                _spec(bad)


if __name__ == "__main__":
    unittest.main()
