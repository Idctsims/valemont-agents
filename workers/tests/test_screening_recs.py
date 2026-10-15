"""Screening Room recommendations (Chat 4 Phase 4, db/053): the math
(screening/recs.py) and the jobs around it (screening/rec_jobs.py).

    exclusions      seen, shelved, reacted (Not interested above all) and
                    below the quality floor never reach For you
    ranking         the Tonight boost, the freshness term, the kind balance
    because         the nearest loved title, deterministic, ties to the
                    lower id, never the title itself
    day one         from the seeds alone (no reaction yet), every pick names
                    a seed as its because and none is a seed
    signals         the record's weights (finished and loved strongest)
    release alerts  only unalerted events; one message per title; the
                    08:30-20:00 window; a refused push is a skip
    TMDB videos     official YouTube Trailers and Teasers only (real
                    fixtures: City of God has none, The Gilded Age two)
    two sources     a TMDB video is keyed exactly as the RSS path keys the
                    same video, and a key either path already has is skipped
    pins            the seeds and the floor match db/053's text; the Python
                    youtube_key matches the generated column's regex

No database, no network: the ledger is patched; vectors are small stubs.
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

from core import ledger, push
from core.jobs import JobSkipped
from screening import jobs, rec_jobs, recs, seeds, services, tmdb
from tests.support import _refuse_database

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "053_screening_recs.sql"
FIX = Path(__file__).with_name("fixtures") / "screening"
TODAY = date(2026, 10, 15)


def fixture_json(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def v(*xs: float) -> tuple[float, ...]:
    return tuple(float(x) for x in xs)


# Four directions in a 4-d space: "crime", "money", "romance", "space".
CRIME, MONEY, ROMANCE, SPACE = v(1, 0, 0, 0), v(0, 1, 0, 0), v(0, 0, 1, 0), v(0, 0, 0, 1)
ZERO = v(0, 0, 0, 0)


def blend_of(*parts: tuple[float, tuple[float, ...]]) -> tuple[float, ...]:
    return tuple(sum(w * p[i] for w, p in parts) for i in range(4))


def record(tid: int, **kw: Any) -> recs.Record:
    base = dict(reaction=None, seen=False, seed=False, finished=False, on_shelf=False)
    base.update(kw)
    return recs.Record(tid, **base)


# ====================================================================== exclusions

class Exclusions(unittest.TestCase):
    def test_every_exclusion(self) -> None:
        self.assertFalse(recs.excluded(record(1)))
        for kw in ({"seen": True}, {"seed": True, "reaction": "love", "seen": True}, {"finished": True},
                   {"on_shelf": True}, {"reaction": "not_interested"}, {"reaction": "love"},
                   {"reaction": "neutral"}):
            self.assertTrue(recs.excluded(record(1, **kw)), kw)
        # A cleared reaction ("none" reads as None) is not an exclusion.
        self.assertFalse(recs.excluded(record(1, reaction=None)))
        # A trailer reaction alone never hides the title (it only shapes the profile).
        self.assertFalse(recs.excluded(record(1, trailer_reaction="not_interested")))

    def test_the_quality_floor(self) -> None:
        self.assertEqual((recs.QUALITY_MIN_AVG, recs.QUALITY_MIN_COUNT), (6.8, 200))
        self.assertTrue(recs.passes_floor(6.8, 200))
        self.assertFalse(recs.passes_floor(6.79, 5000))
        self.assertFalse(recs.passes_floor(9.0, 199))
        self.assertFalse(recs.passes_floor(None, 500))
        self.assertFalse(recs.passes_floor(8.0, None))

    def test_the_pool_applies_floor_and_exclusions(self) -> None:
        pool = recs.Pool()
        rows = [{"id": 1, "media_type": "movie", "vote_average": 7.5, "vote_count": 900},   # kept
                {"id": 2, "media_type": "movie", "vote_average": 6.5, "vote_count": 9000},  # under the average
                {"id": 3, "media_type": "tv", "vote_average": 8.5, "vote_count": 150},      # too few votes
                {"id": 4, "media_type": "tv", "vote_average": 8.0, "vote_count": 400},      # excluded (seen)
                {"id": 5, "media_type": "person", "vote_average": 9, "vote_count": 999},    # not a title
                {"id": 1, "media_type": "tv", "vote_average": 7.0, "vote_count": 300}]      # same id, other kind
        for r in rows:
            pool.add(r, "recommendations", via=(949, "movie"))
        pool.add(rows[0], "trending")
        self.assertEqual(pool.keys_passing(excluded=[(4, "tv")]), [(1, "movie"), (1, "tv")])
        self.assertEqual(pool.lanes[(1, "movie")], {"recommendations", "trending"})
        self.assertEqual(pool.via[(1, "movie")], {(949, "movie")})
        self.assertNotIn((5, "person"), pool.results)


# ====================================================================== signals

class Signals(unittest.TestCase):
    def test_weights(self) -> None:
        cases = [
            (record(1, reaction="love", finished=True), ("love", 1.5)),       # the strongest
            (record(1, reaction="love", seed=True, seen=True), ("love", 1.0)),
            (record(1, reaction="love", seen=True, seen_pass="seen_loved"), ("love", 1.0)),
            (record(1, reaction="love"), ("love", 1.0)),
            (record(1, finished=True), ("love", 0.5)),
            (record(1, trailer_reaction="love"), ("love", 0.5)),
            (record(1, reaction="not_interested"), ("ni", 1.0)),
            (record(1, reaction="not_interested", seen=True, seen_pass="seen_not_for_me"), ("ni", 1.0)),
            (record(1, trailer_reaction="not_interested"), ("ni", 0.5)),
            (record(1, reaction="neutral"), ("neutral", 0.0)),
        ]
        for r, want in cases:
            s = recs.signal_of(r)
            self.assertEqual((s.kind, s.weight), want, r)
        self.assertIsNone(recs.signal_of(record(1)))
        self.assertIsNone(recs.signal_of(record(1, on_shelf=True)))
        self.assertEqual(max(w for _, (_, w) in cases), recs.W_FINISHED_LOVED)
        # The title's own reaction beats its trailer's.
        self.assertEqual(recs.signal_of(record(1, reaction="not_interested", trailer_reaction="love")).kind, "ni")


# ====================================================================== ranking

def profile(loved: dict[int, tuple[float, ...]], ni: dict[int, tuple[float, ...]] | None = None,
            notes: list[tuple[float, ...]] | None = None) -> recs.Profile:
    vecs = {**loved, **(ni or {})}
    signals = [recs.Signal(t, "love", 1.0) for t in loved] + [recs.Signal(t, "ni", 1.0) for t in (ni or {})]
    return recs.build_profile(ZERO, signals, vecs, notes or [], seeds=list(loved))


class Ranking(unittest.TestCase):
    def test_similarity_orders_titles(self) -> None:
        p = profile({100: CRIME, 101: MONEY})
        ranked = recs.rank([recs.TitleIn(1, SPACE), recs.TitleIn(2, blend_of((1, CRIME), (1, MONEY))),
                            recs.TitleIn(3, CRIME)], p, TODAY)
        self.assertEqual([s.title_id for s in ranked][-1], 1)
        self.assertGreater(ranked[0].score, ranked[-1].score)

    def test_the_tonight_boost(self) -> None:
        p = profile({100: CRIME})
        a = recs.TitleIn(1, blend_of((1, CRIME), (0.2, SPACE)))
        b = recs.TitleIn(2, blend_of((1, CRIME), (0.2, SPACE)), tonight=("Netflix",))
        sa, sb = recs.score(a, p, TODAY), recs.score(b, p, TODAY)
        self.assertAlmostEqual(sb.score - sa.score, recs.TONIGHT, places=3)
        self.assertEqual((sa.components["tonight"], sb.components["tonight"]), (0.0, recs.TONIGHT))
        # The boost moves a title on the owner's services above an equal one that is not.
        self.assertEqual([s.title_id for s in recs.rank([a, b], p, TODAY)], [2, 1])
        # It never outweighs a large gap in taste.
        far = recs.TitleIn(3, SPACE, tonight=("Netflix",))
        self.assertEqual(recs.rank([a, far], p, TODAY)[0].title_id, 1)

    def test_freshness(self) -> None:
        self.assertEqual(recs.freshness(TODAY - timedelta(days=30), TODAY, False), 1.0)
        self.assertEqual(recs.freshness(TODAY - timedelta(days=2 * recs.FRESH_DAYS + 1), TODAY, False), 0.0)
        self.assertEqual(recs.freshness(None, TODAY, True), recs.TRENDING_FRESH)
        self.assertEqual(recs.freshness(TODAY + timedelta(days=9), TODAY, False), 0.0)  # not out yet
        self.assertLessEqual(recs.freshness(TODAY, TODAY, True), 1.0)

    def test_not_interested_pushes_down(self) -> None:
        loved_only = profile({100: CRIME, 101: ROMANCE})
        with_ni = profile({100: CRIME, 101: ROMANCE}, ni={200: ROMANCE})
        t = recs.TitleIn(1, ROMANCE)
        self.assertLess(recs.score(t, with_ni, TODAY).score, recs.score(t, loved_only, TODAY).score)

    def test_three_loves_shift_the_ranking(self) -> None:
        # The done-when, in miniature: 3 Not interested on space titles and 3
        # Loves on money titles move a money title up and a space title down.
        before = profile({100: CRIME})
        after = profile({100: CRIME, 1: MONEY, 2: MONEY, 3: MONEY}, ni={4: SPACE, 5: SPACE, 6: SPACE})
        money, space = recs.TitleIn(10, blend_of((1, MONEY), (0.3, CRIME))), recs.TitleIn(11, blend_of((1, SPACE), (0.3, CRIME)))
        self.assertGreater(recs.score(money, after, TODAY).score, recs.score(money, before, TODAY).score)
        self.assertLess(recs.score(space, after, TODAY).score, recs.score(space, before, TODAY).score)

    def test_balance_keeps_both_kinds(self) -> None:
        ranked = [recs.Scored(i, 50.0 - i, None, {}) for i in range(1, 18)]
        kinds = {i: ("movie" if i <= 12 else "tv") for i in range(1, 18)}
        top = recs.balance(ranked, kinds)
        self.assertEqual(len(top), 12)
        self.assertEqual(sum(1 for s in top if kinds[s.title_id] == "tv"), recs.MIN_EACH_KIND)
        # By score alone it would be 12 movies: the four lowest movies (9..12) give way.
        self.assertEqual([s.title_id for s in top], [1, 2, 3, 4, 5, 6, 7, 8, 13, 14, 15, 16])
        # Enough of both already: nothing changes.
        mixed = {i: ("movie" if i % 2 else "tv") for i in range(1, 18)}
        self.assertEqual([s.title_id for s in recs.balance(ranked, mixed)], list(range(1, 13)))
        # Fewer than four of a kind in the pool: all of them, the rest by score.
        few = {i: ("tv" if i == 17 else "movie") for i in range(1, 18)}
        self.assertEqual([s.title_id for s in recs.balance(ranked, few)], list(range(1, 12)) + [17])
        # A short list (Wags' top 5): the best of the guaranteed, by score.
        self.assertEqual([s.title_id for s in recs.balance(ranked, kinds, n=5)], [1, 2, 3, 4, 13])


# ====================================================================== because

class Because(unittest.TestCase):
    def test_the_nearest_loved_title(self) -> None:
        p = profile({100: CRIME, 101: MONEY, 102: ROMANCE})
        self.assertEqual(recs.score(recs.TitleIn(1, blend_of((1, MONEY), (0.2, CRIME))), p, TODAY).because, 101)
        self.assertEqual(recs.score(recs.TitleIn(2, ROMANCE), p, TODAY).because, 102)

    def test_deterministic_and_ties_go_to_the_lower_id(self) -> None:
        p = profile({102: CRIME, 100: CRIME, 101: MONEY})
        t = recs.TitleIn(1, CRIME)
        firsts = {recs.score(t, p, TODAY).because for _ in range(5)}
        self.assertEqual(firsts, {100})
        # Built in any order, the same answer.
        q = recs.build_profile(ZERO, [recs.Signal(i, "love", 1.0) for i in (101, 102, 100)],
                               {100: CRIME, 101: MONEY, 102: CRIME}, [])
        self.assertEqual(recs.score(t, q, TODAY).because, 100)

    def test_no_loved_title_no_because(self) -> None:
        p = recs.build_profile(ZERO, [], {}, [CRIME])
        self.assertIsNone(recs.score(recs.TitleIn(1, CRIME), p, TODAY).because)

    def test_never_its_own_because_in_the_record(self) -> None:
        captured: list[Any] = []

        class Conn:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def transaction(self): return self
            def cursor(self): return self
            def executemany(self, sql, rows): captured.extend(rows)

        with mock.patch.object(ledger, "_brief_conn", lambda conn: Conn()):
            ledger.title_scores_upsert([recs.Scored(7, 1.0, 7, {}), recs.Scored(8, 1.0, 9, {})], 1)
        self.assertEqual([(r[0], r[2]) for r in captured], [(7, None), (8, 9)])


# ====================================================================== day one

class DayOne(unittest.TestCase):
    def test_seeds_alone(self) -> None:
        # No reaction yet: the seeds are the only loves, every pick names one,
        # and no seed is ever a pick (they are seen).
        seed_ids = {100: CRIME, 101: MONEY, 102: ROMANCE}
        p = recs.build_profile(ZERO, [recs.Signal(t, "love", recs.W_SEED) for t in seed_ids], seed_ids,
                               [blend_of((1, CRIME), (1, MONEY))], seeds=list(seed_ids))
        self.assertEqual((p.love_count, p.seed_count, p.ni_count), (3, 3, 0))
        self.assertIsNone(p.ni)
        self.assertAlmostEqual(p.notes_weight, recs.K_NOTES / (recs.K_NOTES + 3), places=4)
        records = {t: record(t, seed=True, seen=True, reaction="love") for t in seed_ids}
        pool = [recs.TitleIn(t, vec) for t, vec in ((1, CRIME), (2, MONEY), (3, ROMANCE), (4, SPACE))]
        pool += [recs.TitleIn(t, seed_ids[t]) for t in seed_ids]
        eligible = [t for t in pool if not (t.title_id in records and recs.excluded(records[t.title_id]))]
        ranked = recs.rank(eligible, p, TODAY)
        self.assertEqual({s.title_id for s in ranked}, {1, 2, 3, 4})
        self.assertTrue(all(s.because in seed_ids for s in ranked))
        self.assertEqual({s.title_id: s.because for s in ranked if s.title_id != 4}, {1: 100, 2: 101, 3: 102})

    def test_the_seeds_match_db053(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        titles = set(re.findall(r"\((\d+), '(tv|movie)', '([^']+)'\)", sql))
        self.assertEqual({(int(i), k, n.replace("''", "'")) for i, k, n in titles},
                         {(i, k, n) for i, k, n, _ in seeds.SEEDS})
        groups = set(re.findall(r"\((\d+), '(?:tv|movie)', '(tv|film|romcom)', (\d+)\)", sql))
        self.assertEqual({(int(i), g) for i, g, _ in groups}, {(i, g) for i, _, _, g in seeds.SEEDS})
        for note in seeds.NOTES:
            self.assertIn(f"'{note}'", sql)
        self.assertEqual(len(seeds.SEEDS), 15)
        self.assertEqual([g for *_, g in seeds.SEEDS].count("romcom"), 3)

    def test_the_floor_and_balance_match_db053(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn(f"t.vote_average >= {recs.QUALITY_MIN_AVG} AND t.vote_count >= {recs.QUALITY_MIN_COUNT}", sql)
        self.assertIn(f"ORDER BY e.score DESC, e.id) <= {recs.MIN_EACH_KIND}", sql)
        self.assertIn("c.last_seen_at > now() - interval '3 days'", ledger.SCREENING_POOL_SQL)
        self.assertIn("c.last_seen_at > now() - interval '3 days'", MIGRATION.read_text(encoding="utf-8"))


# ====================================================================== the seen-it pass

class SeenPass(unittest.TestCase):
    def test_groups_and_order(self) -> None:
        seeds_by = {100: "tv", 101: "film", 102: "romcom"}
        vecs = {100: CRIME, 101: MONEY, 102: ROMANCE}
        cands = [
            recs.PassIn(1, blend_of((1, CRIME), (0.1, SPACE)), 500, "tv"),           # tv, close
            recs.PassIn(2, blend_of((1, MONEY), (0.1, SPACE)), 5000, "movie", ("Drama",)),
            recs.PassIn(3, blend_of((1, ROMANCE), (0.1, SPACE)), 4000, "movie", ("Comedy", "Romance")),
            recs.PassIn(4, blend_of((1, MONEY), (0.9, SPACE)), 90000, "movie", ("Drama",)),  # famous, less alike
            recs.PassIn(5, CRIME, 200, "tv"),                                         # too little known
            recs.PassIn(6, ROMANCE, 999, "movie", ("Romance",)),                      # under 1000
            recs.PassIn(101, MONEY, 99999, "movie"),                                   # a seed itself
        ]
        for c in cands:
            vecs.setdefault(c.title_id, c.vec)
        out = recs.seen_pass(cands, seeds_by, vecs, ZERO, size=6)
        self.assertEqual(out, [(2, "film"), (3, "romcom"), (1, "tv"), (4, "film")])
        self.assertEqual(recs.pass_group(recs.PassIn(9, CRIME, 300, "tv")), "tv")
        self.assertIsNone(recs.pass_group(recs.PassIn(9, CRIME, 299, "tv")))


# ====================================================================== discover

class Discover(unittest.TestCase):
    def test_filters_from_tastes(self) -> None:
        tastes = [rec_jobs.Taste((1, "movie"), 1.0, (18, 80), (10, 20)),
                  rec_jobs.Taste((2, "movie"), 1.0, (18, 53), (10, 30)),
                  rec_jobs.Taste((3, "movie"), -1.0, (53,), (30,)),
                  rec_jobs.Taste((4, "tv"), 1.0, (18,), (99,))]
        f = rec_jobs.discover_filters(tastes, "movie")
        self.assertEqual(f[0]["with_genres"], "18,80")         # drama twice; thriller cancelled by the NI
        self.assertEqual(f[1]["with_keywords"], "10")          # only a keyword two loves share
        self.assertEqual((f[0]["vote_average.gte"], f[0]["vote_count.gte"], f[0]["sort_by"]),
                         (6.8, 200, "vote_count.desc"))
        self.assertEqual(rec_jobs.discover_filters([rec_jobs.Taste((3, "movie"), -1.0, (53,), ())], "movie"), [])
        self.assertEqual(len(rec_jobs.discover_filters(tastes, "tv")), 1)  # tv: genres only


# ====================================================================== TMDB videos

class TmdbVideos(unittest.TestCase):
    def test_city_of_god_has_no_official_trailer(self) -> None:
        # Real response: its only Trailer is unofficial; the rest are Clips.
        self.assertEqual(tmdb.official_videos(fixture_json("tmdb_movie_598.json")), [])

    def test_the_gilded_age(self) -> None:
        vids = tmdb.official_videos(fixture_json("tmdb_tv_81723.json"))
        self.assertEqual([(x.type, x.key) for x in vids], [("Trailer", "wKj1cMz3yfI"), ("Teaser", "ZYBuU32B8PI")])
        self.assertEqual(vids[0].published_at, datetime(2021, 12, 13, 16, 0, 6, tzinfo=timezone.utc))

    def test_the_filter(self) -> None:
        def video(**kw: Any) -> dict:
            return {"site": "YouTube", "type": "Trailer", "official": True, "key": "abcDEF12345",
                    "name": "Official Trailer", "published_at": "2026-10-01T10:00:00.000Z", **kw}
        details = {"videos": {"results": [
            video(), video(key="t2", type="Teaser"), video(type="Clip", key="clip1234"),
            video(type="Featurette", key="feat1234"), video(site="Vimeo", key="vimeo123"),
            video(official=False, key="unoff123"), video(official="true", key="strtrue1"),
            video(key="bad key!"), video(key="teaser123", type="Teaser", published_at="2026-10-05T00:00:00Z")]}}
        got = [x.key for x in tmdb.official_videos(details)]
        self.assertEqual(got, ["teaser123", "abcDEF12345"])  # newest first; "t2" is too short a key

    def test_keywords_and_votes_shaped(self) -> None:
        movie = {"id": 1, "title": "X", "keywords": {"keywords": [{"id": 5, "name": "Heist"}, {"id": 6, "name": "heist"}]},
                 "genres": [{"id": 80, "name": "Crime"}], "vote_average": 7.1, "vote_count": 950}
        t = tmdb.title_from("movie", movie)
        self.assertEqual((t.keywords, t.vote_average, t.vote_count), (("heist",), 7.1, 950))
        self.assertEqual((tmdb.keyword_ids(movie), tmdb.genre_ids(movie)), ((5, 6), (80,)))
        tv = fixture_json("tmdb_tv_81723.json")
        g = tmdb.title_from("tv", tv)
        self.assertEqual((g.last_episode_date, g.tonight), (date(2025, 8, 10), ("HBO Max",)))
        self.assertEqual(tmdb.keywords({"keywords": {"results": [{"id": 1, "name": "Gilded"}]}}), ("gilded",))

    def test_the_embedding_text(self) -> None:
        text = recs.embed_text(("Crime", "Drama"), ("heist", "los angeles"), "A detective hunts a thief.")
        self.assertEqual(text, "Genres: Crime, Drama. Keywords: heist, los angeles. A detective hunts a thief.")
        self.assertNotIn("Heat", recs.embed_text(("Crime",), (), None))
        self.assertEqual(recs.embed_text((), (), None), "")


# ====================================================================== two trailer sources

class TwoSources(unittest.TestCase):
    def test_one_key_either_path(self) -> None:
        now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
        title = tmdb.title_from("tv", fixture_json("tmdb_tv_81723.json"))
        vid = tmdb.official_videos(fixture_json("tmdb_tv_81723.json"))[0]
        it = rec_jobs.trailer_item(title, vid, now)
        # The RSS path stores a YouTube entry as yt:video:<key> with the watch URL (canonical form).
        rss_url = "https://www.youtube.com/watch?v=wKj1cMz3yfI"
        self.assertEqual((it.external_id, it.url), ("yt:video:wKj1cMz3yfI", rss_url))
        self.assertEqual(rec_jobs.youtube_key(it.url), rec_jobs.youtube_key(rss_url), "wKj1cMz3yfI")
        self.assertEqual(rec_jobs.youtube_key("https://youtube.com/watch?feature=share&v=wKj1cMz3yfI"), "wKj1cMz3yfI")
        self.assertIsNone(rec_jobs.youtube_key("https://www.youtube.com/shorts/wKj1cMz3yfI"))
        self.assertIsNone(rec_jobs.youtube_key("https://example.com/watch?v=wKj1cMz3yfI"))
        self.assertTrue(it.title.startswith("The Gilded Age | "))

    def test_the_python_key_is_the_columns(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        col = re.search(r"substring\(url FROM '([^']+)'\)", sql).group(1)
        self.assertEqual(col, rec_jobs.YT_KEY.pattern)

    def test_known_keys_are_skipped_and_new_ones_added(self) -> None:
        stories: list[tuple[str, int]] = []
        recorded: list[tuple[int, int, str]] = []
        tv = fixture_json("tmdb_tv_81723.json")

        class T:
            calls = 0
            def movie_list(self, which): return []
            def tv_on_the_air(self): return []

        detail = rec_jobs.Detail(tmdb.title_from("tv", tv), (), (), tuple(tmdb.official_videos(tv)))
        rec = ledger.TitleRecord(7, 81723, "tv", "The Gilded Age", None, None, False, None, False, None, "watching",
                                 False, None, True)
        prof = profile({100: CRIME})
        ranked = rec_jobs.Ranked(prof, {}, [])
        run = rec_jobs.RecRun("full")

        def fetch(t, keys, embed, run, *, keep=None):
            if keys:
                keep(keys[0], 7, detail)
            return {}

        with mock.patch.object(ledger, "_pool", _refuse_database), \
                mock.patch.object(ledger, "feed_source", lambda slug, **k: f"source:{slug}"), \
                mock.patch.object(ledger, "screening_title_vectors", lambda ids, **k: {}), \
                mock.patch.object(ledger, "owner_today", lambda **k: TODAY), \
                mock.patch.object(ledger, "screening_known_youtube_keys", lambda keys, **k: {"wKj1cMz3yfI"}), \
                mock.patch.object(ledger, "feed_new_story",
                                  lambda src, it, vec, tv_, **k: stories.append((it.external_id, src)) or (50, 60)), \
                mock.patch.object(ledger, "title_trailer_record", lambda i, t, q, **k: recorded.append((i, t, q))), \
                mock.patch.object(rec_jobs, "_fetch", fetch):
            n = rec_jobs.tmdb_trailers(T(), lambda texts: [[0.1] * 4 for _ in texts], ranked, [rec], run)
        # The trailer the RSS path already has is skipped; the teaser is new.
        self.assertEqual(n, 1)
        self.assertEqual(stories, [("yt:video:ZYBuU32B8PI", "source:tmdb-videos")])
        self.assertEqual(recorded, [(60, 7, "tmdb:yt:video:ZYBuU32B8PI")])

    def test_a_race_lost_is_not_an_error(self) -> None:
        # feed_new_story returns None when the other path inserted the key first.
        self.assertIn("if made is None", Path(rec_jobs.__file__).read_text(encoding="utf-8"))

    def test_list_titles_need_recent_videos_and_the_threshold(self) -> None:
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        old = tmdb.Video("oldkey12", "Trailer", "Trailer", now - timedelta(days=200))
        new = tmdb.Video("newkey12", "Trailer", "Trailer", now - timedelta(days=3))
        d = rec_jobs.Detail(tmdb.title_from("movie", {"id": 1, "title": "X"}), (), (), (new, old))
        self.assertEqual([x.key for x in rec_jobs.pick_videos(d, True, now)], ["newkey12"])
        self.assertEqual([x.key for x in rec_jobs.pick_videos(d, False, now)], ["newkey12", "oldkey12"])
        p = profile({100: CRIME})
        scored = {1: recs.Scored(1, 3.0, 100, {"similarity": 0.5}), 2: recs.Scored(2, 9.0, 100, {"similarity": 0.05})}
        ranked = rec_jobs.Ranked(p, scored, [])
        targets = rec_jobs.trailer_targets(ranked, {1: d, 2: d}, {9: d})
        self.assertEqual([(tid, from_list) for tid, _, from_list in targets], [(9, False), (1, True)])


# ====================================================================== release alerts

def event(tid: int, name: str, what: str = "episode", service: str | None = "HBO Max", alerted: bool = False,
          key: str | None = None) -> ledger.ReleaseEvent:
    return ledger.ReleaseEvent(tid, 1000 + tid, "tv" if what == "episode" else "movie", name, what,
                               key or f"{what}:2026-10-15", service, alerted)


class ReleaseAlerts(unittest.TestCase):
    def setUp(self) -> None:
        p = mock.patch.object(ledger, "_pool", _refuse_database)
        p.start()
        self.addCleanup(p.stop)
        self.at = datetime(2026, 10, 15, 8, 30, tzinfo=ZoneInfo("America/Chicago"))

    def test_the_message(self) -> None:
        one = rec_jobs.release_message([event(1, "The Gilded Age")])
        self.assertEqual((one.title, one.body, one.deep_link, one.kind),
                         ("Tonight: The Gilded Age on HBO Max", "New episode today.", "/screening/title/tv/1001",
                          "screening_release"))
        many = rec_jobs.release_message([
            event(1, "The Gilded Age"), event(1, "The Gilded Age", "arrival", "Netflix", key="arrival:Netflix"),
            event(2, "Some Film", "release", None), event(3, "Another", "arrival", "Starz", key="arrival:Starz")])
        self.assertEqual(many.title, "Tonight: The Gilded Age on HBO Max (+2 more)")
        self.assertEqual(many.body, "Also: Some Film, out today; Another, now on Starz")
        test = rec_jobs.release_message([event(1, "X")], test=True)
        self.assertEqual((test.title, test.kind), ("Tonight (test): X on HBO Max", "screening_release_test"))
        theatrical = rec_jobs.release_message([event(2, "Some Film", "release", None)])
        self.assertEqual(theatrical.title, "Tonight: Some Film, out today")

    def test_only_unalerted_events_are_pushed(self) -> None:
        sent: list[push.Message] = []
        handed: list[list[ledger.ReleaseEvent]] = []

        def release_push(events, send, **kw):
            handed.append(list(events))
            send(events)
            return 9

        events = [event(1, "A"), event(2, "B", alerted=True)]
        with mock.patch.object(ledger, "screening_events", lambda **k: events), \
                mock.patch.object(ledger, "release_push", release_push):
            note = rec_jobs.release_push_job(send=lambda m: sent.append(m) or [], now_local=self.at)
        self.assertEqual([[e.title_id for e in h] for h in handed], [[1]])
        self.assertEqual([m.title for m in sent], ["Tonight: A on HBO Max"])
        self.assertIn("release push 9", note)

    def test_nothing_new_is_a_skip_not_a_push(self) -> None:
        with mock.patch.object(ledger, "screening_events", lambda **k: [event(1, "A", alerted=True)]), \
                mock.patch.object(ledger, "release_push", lambda *a, **k: self.fail("pushed")):
            with self.assertRaises(JobSkipped):
                rec_jobs.release_push_job(send=lambda m: self.fail("sent"), now_local=self.at)

    def test_already_pushed_today_is_a_skip(self) -> None:
        with mock.patch.object(ledger, "screening_events", lambda **k: [event(1, "A")]), \
                mock.patch.object(ledger, "release_push", lambda *a, **k: None):
            with self.assertRaises(JobSkipped):
                rec_jobs.release_push_job(send=lambda m: [], now_local=self.at)

    def test_the_window(self) -> None:
        for hm in ((8, 29), (20, 0), (23, 0), (6, 0)):
            with mock.patch.object(ledger, "screening_events", lambda **k: self.fail("read")):
                with self.assertRaises(JobSkipped):
                    rec_jobs.release_push_job(send=lambda m: [], now_local=self.at.replace(hour=hm[0], minute=hm[1]))

    def test_the_test_push_is_marked_test(self) -> None:
        calls: list[dict] = []

        def release_push(events, send, **kw):
            calls.append(kw)
            send(events)
            return 3

        sent: list[push.Message] = []
        with mock.patch.object(ledger, "screening_test_push_title", lambda tid=None, **k: event(5, "X")), \
                mock.patch.object(ledger, "release_push", release_push):
            self.assertEqual(rec_jobs.test_release_push({}, send=lambda m: sent.append(m) or []), 3)
        self.assertEqual(calls, [{"test": True}])
        self.assertEqual(sent[0].kind, "screening_release_test")

    def test_arrivals_from_the_refresh(self) -> None:
        nf = {"flatrate": [{"id": 8, "name": "Netflix"}]}
        shelf = ledger.ShelfTitle(1, 10, "tv", "want", nf)
        self.assertEqual(jobs.arrivals(shelf, ("HBO Max", "Netflix")), ["HBO Max"])
        self.assertEqual(jobs.arrivals(shelf, ("Netflix",)), [])
        # No baseline (never read from TMDB) or off the Want/Watching shelves: none.
        self.assertEqual(jobs.arrivals(ledger.ShelfTitle(1, 10, "tv", "want", None), ("Netflix",)), [])
        self.assertEqual(jobs.arrivals(ledger.ShelfTitle(1, 10, "tv", "finished", {}), ("Netflix",)), [])
        # An ads tier is the same service: not a new arrival.
        ads = ledger.ShelfTitle(1, 10, "tv", "watching", {"ads": [{"id": 1796, "name": "Netflix with Ads"}]})
        self.assertEqual(jobs.arrivals(ads, ("Netflix",)), [])

    def test_the_test_push_script_arms_0830(self) -> None:
        from scripts.screening_test_push import next_0830
        chi = ZoneInfo("America/Chicago")
        self.assertEqual(next_0830(datetime(2026, 10, 15, 7, 0, tzinfo=chi)), datetime(2026, 10, 15, 8, 30, tzinfo=chi))
        self.assertEqual(next_0830(datetime(2026, 10, 15, 9, 0, tzinfo=chi)), datetime(2026, 10, 16, 8, 30, tzinfo=chi))


# ====================================================================== the brief

class BriefLine(unittest.TestCase):
    def test_one_screening_line(self) -> None:
        from core import brief
        line = brief._screening_lines({
            "releases_today": [{"title": "The Gilded Age", "what": "episode", "on": "HBO Max"},
                               {"title": "Some Film", "what": "release"}],
            "top_pick": {"title": "Too Big to Fail", "because_you_loved": "Margin Call", "tonight": ["HBO Max"]}})
        self.assertEqual(line, ["Today: The Gilded Age (new episode) on HBO Max; Some Film. "
                                "Pick: Too Big to Fail (on HBO Max), because you loved Margin Call."])
        self.assertEqual(brief._screening_lines({}), [])
        self.assertIn("screening_brief", brief.ONE_LINE_SECTIONS)
        self.assertIn("screening_brief", brief.FALLBACK_SECTIONS)


if __name__ == "__main__":
    unittest.main()
