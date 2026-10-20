"""GitHub repos in Tech Hub (Chat 4 Phase 2, db/049): the trending parser on
saved pages (and broken markup, which must fail loudly), the hard filters,
the stars_7d estimate and its label, selection (the cap of 8 and the
resurface rule), the token refusals, search pacing, the day-one run (8, not
a dump), the repo why-it-matters rules, and the fetch log line's GUIDs.

Fixtures (tests/fixtures/repos/) are github.com/trending?since=daily and
?since=weekly, fetched 2026-10-14 and trimmed to their first 4 cards; the
two broken ones are the daily page with the card class renamed (0 cards),
and with card 2's /stargazers link renamed (a card with no star count).

No database, no network: every ledger function the job calls is patched,
the real pool and urlopen refuse, and GitHub is a fake HTTP function.
"""

from __future__ import annotations

import json
import math
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping
from unittest import mock

from core import ledger
from core.jobs import JobSkipped
from feed import ingest, rank, repos, why
from feed.parse import RawItem
from tests.support import _refuse_database, _refuse_network

FIX = Path(__file__).with_name("fixtures") / "repos"
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 15)
TOKEN = "github_pat_" + "x" * 40


def fixture(name: str) -> bytes:
    return (FIX / f"{name}.html").read_bytes()


# ====================================================================== parser

class TrendingParser(unittest.TestCase):
    def test_the_daily_page(self) -> None:
        got = repos.parse_trending(fixture("trending_daily"), "daily")
        self.assertEqual([r.full_name for r in got],
                         ["morluto/rea", "debpalash/VoiceStudio", "mksglu/context-mode", "nvm-sh/nvm"])
        first = got[0]
        self.assertEqual((first.language, first.stars, first.stars_period, first.period),
                         ("TypeScript", 238274, 41187, "daily"))
        self.assertTrue(first.description.startswith("Reverse engineer anything with agents"))
        self.assertEqual(got[3].stars_period, 70)

    def test_the_weekly_page(self) -> None:
        got = repos.parse_trending(fixture("trending_weekly"), "weekly")
        self.assertEqual(len(got), 4)
        self.assertEqual((got[0].full_name, got[0].stars_period), ("morluto/rea", 183758))
        self.assertEqual(got[2].full_name, "mattpocock/skills")

    def test_markup_with_no_cards_fails_loudly_not_an_empty_day(self) -> None:
        with self.assertRaisesRegex(repos.TrendingParseFailed, "0 repos parsed"):
            repos.parse_trending(fixture("trending_broken_class"), "daily")
        with self.assertRaises(repos.TrendingParseFailed):
            repos.parse_trending(b"<html><body>nothing here</body></html>", "weekly")

    def test_a_card_missing_its_star_count_fails_loudly(self) -> None:
        with self.assertRaisesRegex(repos.TrendingParseFailed, "card 2"):
            repos.parse_trending(fixture("trending_broken_card"), "daily")

    def test_more_stars_in_the_period_than_in_total_is_impossible(self) -> None:
        page = ('<article class="Box-row"><h2><a href="/o/r">o / r</a></h2>'
                '<a href="/o/r/stargazers">1,000</a>'
                '<span class="d-inline-block float-sm-right">1,001 stars this week</span></article>')
        with self.assertRaisesRegex(repos.TrendingParseFailed, "1001 stars in the period but 1000 in total"):
            repos.parse_trending(page, "weekly")
        ok = page.replace("1,001 stars", "1,000 stars")
        self.assertEqual(repos.parse_trending(ok, "weekly")[0].stars_period, 1000)

    def test_the_fixture_reads_rea_as_github_states_it(self) -> None:
        # The page itself says "183,758 stars this week" next to 238,274
        # total (checked against the live page, 2026-10-14): not a parse error.
        rea = repos.parse_trending(fixture("trending_weekly"), "weekly")[0]
        self.assertEqual((rea.full_name, rea.stars, rea.stars_period), ("morluto/rea", 238274, 183758))

    def test_counts(self) -> None:
        self.assertEqual(repos.parse_count("238,274"), 238274)
        self.assertEqual(repos.parse_count("1.2k"), 1200)
        self.assertEqual(repos.parse_count("41,187 stars today"), 41187)
        self.assertIsNone(repos.parse_count("Star"))


# ====================================================================== filters

def cand(name: str = "o/r", **kw: Any) -> repos.Candidate:
    base = dict(pushed_at=NOW - timedelta(days=2), created_at=NOW - timedelta(days=20), stars=500,
                readme="# Title\nA real readme.", enriched=True, description="Does a thing.")
    base.update(kw)
    return repos.Candidate(name, f"https://github.com/{name}", **base)


class HardFilters(unittest.TestCase):
    def test_each_filter(self) -> None:
        self.assertIsNone(repos.hard_filter(cand(), NOW))
        self.assertEqual(repos.hard_filter(cand(archived=True), NOW), "archived")
        self.assertEqual(repos.hard_filter(cand(fork=True), NOW), "fork")
        self.assertEqual(repos.hard_filter(cand(readme=None), NOW), "no README")
        self.assertEqual(repos.hard_filter(cand(readme="   \n"), NOW), "no README")
        self.assertEqual(repos.hard_filter(cand(pushed_at=NOW - timedelta(days=91)), NOW), "not pushed in 90 days")
        self.assertEqual(repos.hard_filter(cand(pushed_at=None), NOW), "not pushed in 90 days")
        self.assertIsNone(repos.hard_filter(cand(pushed_at=NOW - timedelta(days=89)), NOW))

    def test_the_readme_is_asked_last(self) -> None:
        # Before the README is fetched only the metadata filters apply.
        self.assertIsNone(repos.hard_filter(cand(readme=None), NOW, readme_checked=False))
        self.assertEqual(repos.hard_filter(cand(readme=None, fork=True), NOW, readme_checked=False), "fork")


# ====================================================================== stars_7d

class Stars7d(unittest.TestCase):
    def test_measured_wins_and_is_the_only_measurement(self) -> None:
        c = cand(trending_weekly=900, trending_daily=200)
        self.assertEqual(repos.stars_7d(c, 340, NOW), (340, "measured"))
        self.assertEqual(repos.LABELS["measured"], "7d")
        self.assertFalse(repos.is_estimate("measured"))

    def test_until_7_days_of_snapshots_trending_is_the_labelled_estimate(self) -> None:
        self.assertEqual(repos.stars_7d(cand(trending_weekly=900, trending_daily=200), None, NOW),
                         (900, "trending_weekly"))
        self.assertEqual(repos.stars_7d(cand(trending_daily=200), None, NOW), (200, "trending_daily"))
        for source in ("trending_weekly", "trending_daily"):
            self.assertEqual(repos.LABELS[source], "trending est.")
            self.assertTrue(repos.is_estimate(source))

    def test_a_search_only_repo_is_labelled_too(self) -> None:
        young = cand(created_at=NOW - timedelta(days=3), stars=120)
        self.assertEqual(repos.stars_7d(young, None, NOW), (120, "since_created"))
        older = cand(created_at=NOW - timedelta(days=14), stars=1400)
        self.assertEqual(repos.stars_7d(older, None, NOW), (700, "age_average"))
        self.assertEqual(repos.LABELS["age_average"], "avg est.")
        # The two guesses read differently on the card (owner, 2026-10-14).
        self.assertNotEqual(repos.LABELS["age_average"], repos.LABELS["trending_weekly"])
        self.assertTrue(all(repos.is_estimate(s) for s in ("since_created", "age_average")))
        self.assertEqual(repos.stars_7d(cand(created_at=NOW - timedelta(days=400)), None, NOW), (None, None))

    def test_every_source_has_a_label_and_matches_db049(self) -> None:
        sql = (Path(__file__).resolve().parents[2] / "db" / "049_repos.sql").read_text(encoding="utf-8")
        for source in repos.LABELS:
            self.assertIn(f"'{source}'", sql)

    def test_db050_the_resurface_baseline_is_measured(self) -> None:
        """Owner, 2026-10-14: an estimate (or nothing) at the first surfacing
        is never the baseline; the first MEASURED stars_7d is. Pinned on the
        SQL text, which the trigger enforces (tests_live runs it)."""
        sql = (Path(__file__).resolve().parents[2] / "db" / "050_repo_measured_baseline.sql").read_text(encoding="utf-8")
        fn = sql[sql.index("CREATE OR REPLACE FUNCTION public.repo_may_surface"):sql.index("REVOKE ALL")]
        self.assertIn("WHEN f.stars_7d_source = 'measured' THEN f.stars_7d", fn)
        self.assertIn("w.snapped_on = t.snapped_on - 7", fn)
        self.assertIn("ORDER BY t.snapped_on", fn)
        self.assertIn("<= p_day - 14", fn)
        self.assertIn("p_stars_7d >= 2 * greatest((SELECT b.stars_7d FROM baseline b), 1)", fn)
        self.assertIn("RAISE NOTICE '050 self-test passed (rolled back)'", sql)

    def test_over_half_the_total_on_an_old_repo_is_flagged(self) -> None:
        rea = cand(stars=239045, trending_weekly=183758, created_at=NOW - timedelta(days=183))
        self.assertTrue(repos.trending_suspect(rea, NOW))
        # Half or less: not flagged. A repo 30 days old or younger: not flagged.
        self.assertFalse(repos.trending_suspect(cand(stars=1000, trending_weekly=500,
                                                     created_at=NOW - timedelta(days=90)), NOW))
        self.assertFalse(repos.trending_suspect(cand(stars=1000, trending_weekly=900,
                                                     created_at=NOW - timedelta(days=30)), NOW))
        self.assertTrue(repos.trending_suspect(cand(stars=1000, trending_daily=600,
                                                    created_at=NOW - timedelta(days=31)), NOW))
        self.assertFalse(repos.trending_suspect(cand(stars=1000, created_at=NOW - timedelta(days=400)), NOW))

    def test_a_negative_measurement_is_floored(self) -> None:
        self.assertEqual(repos.stars_7d(cand(), -5, NOW), (0, "measured"))


# ====================================================================== selection

def pick(name: str, score: float, s7: int | None = 10) -> repos.Pick:
    return repos.Pick(cand(name), (0.0,), score, s7, "measured" if s7 is not None else None)


class Selection(unittest.TestCase):
    def test_at_most_room_best_first(self) -> None:
        picks = [pick(f"o/r{i}", float(i)) for i in range(20)]
        got = repos.select(picks, lambda p: True, repos.DAILY_MAX)
        self.assertEqual(len(got), 8)
        self.assertEqual([p.candidate.full_name for p in got], [f"o/r{i}" for i in range(19, 11, -1)])
        self.assertEqual(repos.DAILY_MAX, 8)

    def test_room_left_today_is_honoured(self) -> None:
        picks = [pick(f"o/r{i}", float(i)) for i in range(20)]
        self.assertEqual(len(repos.select(picks, lambda p: True, 3)), 3)
        self.assertEqual(repos.select(picks, lambda p: True, 0), [])

    def test_the_resurface_rule_skips_and_the_next_one_fills(self) -> None:
        picks = [pick(f"o/r{i}", float(i)) for i in range(10)]
        refused = {"o/r9", "o/r7"}
        got = repos.select(picks, lambda p: p.candidate.full_name not in refused, 8)
        names = [p.candidate.full_name for p in got]
        self.assertEqual(len(names), 8)
        self.assertFalse(refused & set(names))

    def test_velocity_and_profile(self) -> None:
        self.assertEqual(repos.velocity(None), -repos.W_LOW_TRACTION)
        self.assertGreater(repos.velocity(1000), repos.velocity(100))
        # Under 50 stars in 7 days costs W_LOW_TRACTION; at 50 it is gone.
        self.assertAlmostEqual(repos.velocity(50) - repos.velocity(49),
                               repos.W_LOW_TRACTION + repos.W_VELOCITY * (math.log10(51) - math.log10(50)))
        # No profile yet: velocity, plus the interest bonus for a search hit.
        self.assertAlmostEqual(repos.rank_score(cand(), (1.0, 0.0), 99, None), repos.velocity(99))
        self.assertAlmostEqual(repos.rank_score(cand(interests=["AI agents"]), (1.0, 0.0), 99, None),
                               repos.velocity(99) + repos.W_INTEREST)
        prof = rank.Profile("tech", (0.0, 0.0), 1, (1.0, 0.0), (0.0, 1.0), {"agents": 0.3}, {}, {}, 1, 1, 0, 0, 0.5, 0.5)
        liked = repos.rank_score(cand(topics=("agents",)), (1.0, 0.0), 10, prof)
        disliked = repos.rank_score(cand(), (0.0, 1.0), 10, prof)
        self.assertGreater(liked, disliked)

    def test_topics_train_as_snake_case(self) -> None:
        self.assertEqual(repos.snake("ai-agents"), "ai_agents")
        self.assertEqual(repos.snake("Model Context Protocol"), "model_context_protocol")
        prof = rank.Profile("tech", (0.0, 0.0), 1, None, None, {"mcp_server": -0.4}, {}, {}, 0, 1, 0, 0, 0, 0)
        # A Not interested on that topic lowers every repo carrying it.
        self.assertLess(repos.rank_score(cand(topics=("mcp-server",)), (1.0, 0.0), 10, prof),
                        repos.rank_score(cand(topics=("other",)), (1.0, 0.0), 10, prof))

    def test_teaser_and_readme_text(self) -> None:
        c = cand(description="A tool.", readme="![badge](x.svg) # Hello\n[docs](http://x) <b>bold</b> ```code```" + " word" * 300)
        t = repos.teaser(c)
        self.assertLessEqual(len(t), repos.TEASER_MAX)
        self.assertTrue(t.startswith("A tool. — Hello docs bold"))
        self.assertNotIn("badge", t)


# ====================================================================== GitHub

class FakeHttp:
    """Routes GitHub URLs to canned answers; records every call."""

    def __init__(self, *, status: int = 200, rl_remaining: str = "4999", search_items: int = 10,
                 n_trending: int = 25) -> None:
        self.status, self.rl_remaining, self.search_items, self.n_trending = status, rl_remaining, search_items, n_trending
        self.calls: list[tuple[str, Mapping[str, Any] | None, Mapping[str, str]]] = []

    @staticmethod
    def repo_obj(name: str, i: int) -> dict[str, Any]:
        return {"full_name": name, "html_url": f"https://github.com/{name}", "description": f"Repo {name}",
                "language": "Python", "topics": ["ai-agents", f"t{i % 3}"], "license": {"spdx_id": "MIT"},
                "archived": False, "fork": False, "created_at": (NOW - timedelta(days=10)).isoformat(),
                "pushed_at": (NOW - timedelta(days=1)).isoformat(), "stargazers_count": 1000 + i}

    def trending_page(self, period: str) -> bytes:
        cards = "".join(
            f'<article class="Box-row"><h2 class="h3"><a href="/trend{period}/r{i}">x</a></h2>'
            f'<p class="col-9">Trending {i}</p><span itemprop="programmingLanguage">Go</span>'
            f'<a href="/trend{period}/r{i}/stargazers">{5000 + i}</a>'
            f'<span class="d-inline-block float-sm-right">{100 + i} stars today</span></article>'
            for i in range(self.n_trending))
        return f"<html><body>{cards}</body></html>".encode()

    def __call__(self, url: str, params: Mapping[str, Any] | None, headers: Mapping[str, str],
                 max_bytes: int | None) -> repos.HttpResponse:
        self.calls.append((url, params, headers))
        h = {"x-ratelimit-remaining": self.rl_remaining}
        if url.startswith("https://github.com/trending"):
            return repos.HttpResponse(200, self.trending_page(url.rsplit("=", 1)[1]), {})
        if self.status != 200:
            return repos.HttpResponse(self.status, b'{"message": "Bad credentials"}', h)
        path = url[len(repos.API):]
        if path == "/rate_limit":
            return repos.HttpResponse(200, json.dumps({"resources": {"core": {"remaining": 4999}}}).encode(), h)
        if path == "/search/repositories":
            q = params["q"].split(" created:")[0].replace(" ", "-")
            items = [self.repo_obj(f"search-{q}/r{i}", i) for i in range(self.search_items)]
            return repos.HttpResponse(200, json.dumps({"items": items}).encode(), h)
        if path.endswith("/readme"):
            return repos.HttpResponse(200, b"# Readme\nUseful words here.", h)
        if path.startswith("/repos/"):
            name = path[len("/repos/"):]
            return repos.HttpResponse(200, json.dumps(self.repo_obj(name, 7)).encode(), h)
        return repos.HttpResponse(404, b"", h)


class Token(unittest.TestCase):
    def test_missing_token_refuses_by_name_only(self) -> None:
        for env in ({}, {repos.TOKEN_ENV: "  "}, {"GITHUB_TOKEN": TOKEN}):
            with self.subTest(env=list(env)), self.assertRaises(repos.RepoTokenError) as cm:
                repos.read_token(env)
            self.assertIn(repos.TOKEN_ENV, str(cm.exception))
            self.assertNotIn(TOKEN, str(cm.exception))
        self.assertEqual(repos.read_token({repos.TOKEN_ENV: TOKEN}), TOKEN)

    def test_never_the_gh_cli_name(self) -> None:
        self.assertEqual(repos.TOKEN_ENV, "GITHUB_PUBLIC_READ_TOKEN")
        src = Path(repos.__file__).read_text(encoding="utf-8")
        self.assertNotIn('environ.get("GITHUB_TOKEN"', src)
        self.assertNotIn("env.get(\"GITHUB_TOKEN\"", src)

    def test_401_and_a_non_rate_limit_403_are_refusals_that_never_echo_it(self) -> None:
        for status in (401, 403):
            gh = repos.GitHub(TOKEN, FakeHttp(status=status))
            with self.subTest(status=status), self.assertRaises(repos.RepoTokenError) as cm:
                gh.check_token()
            self.assertIn(repos.TOKEN_ENV, str(cm.exception))
            self.assertNotIn(TOKEN, str(cm.exception))
            self.assertNotIn("x" * 10, str(cm.exception))

    def test_a_rate_limit_403_is_its_own_loud_error(self) -> None:
        gh = repos.GitHub(TOKEN, FakeHttp(status=403, rl_remaining="0"))
        with self.assertRaises(repos.RateLimited):
            gh.search("x", TODAY)

    def test_the_token_goes_only_to_the_api(self) -> None:
        http = FakeHttp()
        gh = repos.GitHub(TOKEN, http)
        gh.trending("daily")
        gh.check_token()
        (t_url, _, t_headers), (a_url, _, a_headers) = http.calls
        self.assertTrue(t_url.startswith("https://github.com/trending"))
        self.assertNotIn("Authorization", t_headers)
        self.assertEqual(a_headers["Authorization"], f"Bearer {TOKEN}")


class SearchPacing(unittest.TestCase):
    def test_searches_stay_under_30_a_minute(self) -> None:
        clock = [0.0]
        sleeps: list[float] = []

        def sleep(s: float) -> None:
            sleeps.append(s)
            clock[0] += s

        gh = repos.GitHub(TOKEN, FakeHttp(), sleep=sleep, clock=lambda: clock[0])
        for _label, q in repos.INTERESTS:
            gh.search(q, TODAY)
        self.assertEqual(gh.calls["search"], len(repos.INTERESTS))
        self.assertLess(len(repos.INTERESTS), 30)
        # Every gap is at least 60/24 s, so any 60 s window holds at most 24.
        self.assertTrue(all(s >= 60.0 / repos.SEARCH_PER_MIN - 1e-9 for s in sleeps))
        self.assertEqual(len(sleeps), len(repos.INTERESTS) - 1)
        self.assertLess(repos.SEARCH_PER_MIN, 30)

    def test_one_query_per_interest(self) -> None:
        self.assertEqual([label for label, _ in repos.INTERESTS],
                         ["AI agents", "MCP servers", "Claude/Anthropic tooling", "trading bots", "Next.js",
                          "Supabase", "Vercel", "automation", "marketing tools"])
        http = FakeHttp()
        repos.GitHub(TOKEN, http, sleep=lambda s: None).search("ai agent", TODAY)
        q = http.calls[0][1]["q"]
        self.assertIn(f"created:>={(TODAY - timedelta(days=0)).isoformat()}", q)
        self.assertEqual((http.calls[0][1]["sort"], http.calls[0][1]["order"]), ("stars", "desc"))


# ====================================================================== the job

class FakeRepoLedger:
    """Every ledger function repos_job calls, in memory."""

    def __init__(self, *, ok_today: bool = False, surfaced_today: int = 0, refuse: set[str] = frozenset()) -> None:
        self.ok_today, self.surfaced_today, self.refuse = ok_today, surfaced_today, set(refuse)
        self.ids: dict[str, int] = {}
        self.snapshots: list[tuple[int, int]] = []
        self.surfaced: list[ledger.RepoSurfaceIn] = []

    def job_ok_today(self, job: str, **_: Any) -> bool:
        return self.ok_today

    def owner_today(self, **_: Any) -> date:
        return TODAY

    def repos_upsert(self, rows, **_: Any) -> dict[str, int]:
        for r in rows:
            self.ids.setdefault(r.full_name.lower(), len(self.ids) + 1)
        return {r.full_name.lower(): self.ids[r.full_name.lower()] for r in rows}

    def repo_snapshot(self, pairs, **_: Any) -> int:
        self.snapshots += list(pairs)
        return len(pairs)

    def repos_to_track(self, days, limit, *, exclude=(), **_: Any):
        return []

    def repos_surfaced_today(self, **_: Any) -> int:
        return self.surfaced_today + len(self.surfaced)

    def repo_stars_7d_measured(self, ids, **_: Any) -> dict[int, int]:
        return {}

    def repo_may_surface(self, repo_id, stars_7d, **_: Any) -> bool:
        return True

    def feed_source(self, slug, **_: Any):
        return SimpleNamespace(id=99, slug=slug, is_test=False)

    def repo_surface(self, source, repo_id, s, **_: Any):
        if s.full_name in self.refuse:
            return None
        self.surfaced.append(s)
        return 1000 + len(self.surfaced)


class RepoJobCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeRepoLedger()
        self.patch_ledger(self.fake)
        for p in (mock.patch.object(ledger, "_pool", _refuse_database),
                  mock.patch("urllib.request.urlopen", _refuse_network),
                  mock.patch("feed.summarize.latest_profiles", lambda: {}),
                  mock.patch.object(repos.memory, "release", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

    def patch_ledger(self, fake: FakeRepoLedger) -> None:
        self.fake = fake
        for name in ("job_ok_today", "owner_today", "repos_upsert", "repo_snapshot", "repos_to_track",
                     "repos_surfaced_today", "repo_stars_7d_measured", "repo_may_surface", "feed_source",
                     "repo_surface"):
            p = mock.patch.object(ledger, name, getattr(fake, name))
            p.start()
            self.addCleanup(p.stop)

    def run_job(self, http: FakeHttp | None = None) -> repos.RepoRun:
        gh = repos.GitHub(TOKEN, http or FakeHttp(), sleep=lambda s: None)
        return repos.repos_job(gh=gh, embed=lambda texts: [[1.0, 0.0] for _ in texts], now=NOW)

    def test_day_one_surfaces_8_not_a_dump(self) -> None:
        # 2 × 25 trending + 9 × 10 search = 140 candidates, no history at all.
        run = self.run_job()
        self.assertGreater(run.candidates, 100)
        self.assertEqual(len(run.surfaced), 8)
        self.assertEqual(len(self.fake.surfaced), 8)
        self.assertEqual(run.snapshots, run.candidates)

    def test_each_surfacing_is_a_dated_repo_story_with_labelled_stars(self) -> None:
        self.run_job()
        for s in self.fake.surfaced:
            self.assertEqual(s.day, TODAY)
            self.assertIn("ai_agents", s.topics)
            self.assertIsNotNone(s.stars_7d)
            self.assertIn(s.stars_7d_source, repos.LABELS)
            self.assertNotEqual(s.stars_7d_source, "measured")  # no 7-day history on day one
            self.assertTrue(s.teaser)

    def test_the_call_count_is_in_the_note(self) -> None:
        run = self.run_job()
        self.assertEqual(run.calls["search"], len(repos.INTERESTS))
        self.assertEqual(run.calls["trending"], 2)
        self.assertIn(f"calls: search {len(repos.INTERESTS)}, core ", run.job_note)
        self.assertIn("surfaced 8", run.job_note)

    def test_room_already_used_today_is_respected(self) -> None:
        self.patch_ledger(FakeRepoLedger(surfaced_today=5))
        self.assertEqual(len(self.run_job().surfaced), 3)

    def test_a_database_refusal_is_reported_not_retried_blindly(self) -> None:
        http = FakeHttp()
        self.patch_ledger(FakeRepoLedger(refuse={"search-ai-agent/r9"}))
        run = self.run_job(http)
        self.assertLessEqual(len(run.surfaced) + len(run.refused), 8)
        if run.refused:
            self.assertIn("refused by the database", run.job_note)

    def test_already_ran_today_is_a_skip(self) -> None:
        self.patch_ledger(FakeRepoLedger(ok_today=True))
        with self.assertRaises(JobSkipped):
            self.run_job()

    def test_a_trending_page_with_0_repos_fails_the_job(self) -> None:
        with self.assertRaises(repos.TrendingParseFailed):
            self.run_job(FakeHttp(n_trending=0))

    def test_a_rejected_token_fails_the_job_before_anything_is_written(self) -> None:
        with self.assertRaises(repos.RepoTokenError):
            self.run_job(FakeHttp(status=401))
        self.assertEqual((self.fake.snapshots, self.fake.surfaced), ([], []))

    def test_without_an_injected_client_the_token_is_required(self) -> None:
        with self.assertRaises(repos.RepoTokenError):
            repos.repos_job(env={}, embed=lambda t: [], now=NOW)


# ====================================================================== why

class RepoWhy(unittest.TestCase):
    NAMES = ["Sail Beach Club", "Sims & Vale Capital"]

    def test_a_dashboard_use(self) -> None:
        line, why_not = why.check_repo({"target": "dashboard", "why": "Adds pgvector search patterns to the dashboard."},
                                       self.NAMES)
        self.assertEqual((line, why_not), ("Adds pgvector search patterns to the dashboard.", None))

    def test_a_venture_use(self) -> None:
        line, _ = why.check_repo({"target": "Sail Beach Club", "why": "Runs Sail Beach Club's booking reminders."},
                                 self.NAMES)
        self.assertEqual(line, "Runs Sail Beach Club's booking reminders.")

    def test_no_use_is_said_plainly(self) -> None:
        self.assertEqual(why.check_repo({"target": None, "why": None}, self.NAMES), (why.GENERAL, None))
        self.assertEqual(why.GENERAL, "General interest.")

    def test_refusals_fall_back_with_a_reason(self) -> None:
        for item in ({"target": "dashboard", "why": "A nice library for apps."},
                     {"target": "Nobody Inc", "why": "Helps Nobody Inc."},
                     {"target": "Sail Beach Club", "why": "This might help Sail Beach Club."},
                     {"target": "dashboard", "why": "The dashboard " + "word " * 25}):
            with self.subTest(item=item["why"][:30]):
                line, reason = why.check_repo(item, self.NAMES)
                self.assertEqual(line, why.GENERAL)
                self.assertTrue(reason)

    # -- owner, 2026-10-14, after day one scored 0/8 --------------------------

    def test_can_and_could_are_allowed_when_the_line_names_its_target(self) -> None:
        for item in ({"target": "dashboard", "why": "The dashboard could adopt these reduced-motion React components."},
                     {"target": "Sail Beach Club", "why": "Sail Beach Club can use it for booking reminders."},
                     {"target": "dev workflow", "why": "Adds an MCP server for binary analysis to the dev workflow."},
                     {"target": "client sites", "why": "Client sites could use these animated ASCII Next.js components."}):
            with self.subTest(why=item["why"][:30]):
                line, reason = why.check_repo(item, self.NAMES)
                self.assertEqual((line, reason), (item["why"], None))

    def test_may_and_might_stay_refused(self) -> None:
        for word in ("may", "might", "possibly", "perhaps", "potentially"):
            with self.subTest(word=word):
                line, reason = why.check_repo({"target": "dashboard", "why": f"The dashboard {word} use it."},
                                              self.NAMES)
                self.assertEqual(line, why.GENERAL)
                self.assertIn("hedges", reason)

    def test_news_why_keeps_its_rules(self) -> None:
        # "could" is still a hedge for news (why.check, not check_repo).
        line, reason = why.check({"venture": "Sail Beach Club", "why": "This could matter to Sail Beach Club."},
                                 self.NAMES)
        self.assertIsNone(line)
        self.assertIn("hedges", reason)

    def test_the_two_new_targets(self) -> None:
        self.assertIn("Your dev workflow", why.REPO_TARGETS[why.WORKFLOW][0])
        self.assertIn("Claude Code, Cursor and MCP", why.REPO_TARGETS[why.WORKFLOW][0])
        self.assertIn("Next.js, Supabase and Tailwind", why.REPO_TARGETS[why.CLIENTS][0])
        for t in (why.DASHBOARD, why.WORKFLOW, why.CLIENTS):
            self.assertIn(f'"{t}"', why.REPO_SYSTEM)
        msg = why.repo_user_message({}, [ledger.RepoWhyJob(1, "o/a", "S.", "GitHub", "D.", ("x",))])
        self.assertIn("OTHER TARGETS", msg)
        self.assertIn("freelance client sites".lower(), msg.lower())
        # Each target must be named in the line.
        for target, bad in ((why.WORKFLOW, "Speeds up builds."), (why.CLIENTS, "Good for websites.")):
            with self.subTest(target=target):
                line, reason = why.check_repo({"target": target, "why": bad}, self.NAMES)
                self.assertEqual(line, why.GENERAL)
                self.assertIn("does not name", reason)

    def test_the_models_second_person_targets_resolve(self) -> None:
        """Production, 2026-10-15 02:16 UTC: the second-person prompt made the
        model send "your dev workflow" / "your Claude Code" as the TARGET, and
        the bare-key lookup refused 7 of 8 good lines. These are its real
        answers; every one must be kept."""
        real = [
            ("your Claude Code", "Your Claude Code could use this MCP server to reverse engineer unknown binaries and app behavior inside your agent workflow."),
            ("your dev workflow", "Your dev workflow could adopt these Claude Code skills and fal MCP setup as a pattern for agent-driven tooling."),
            ("your dev workflow", "Lets your Claude Code generate a full award-style Astro website from one prompt and verify it in a real browser."),
            ("your dev workflow", "Lets you monitor and approve permissions from your Claude Code, Codex and Cursor sessions from your Mac notch or iPhone."),
            ("your client sites", "Your client sites could use these Next.js React components with reduced-motion support, installed via the shadcn CLI."),
        ]
        for target, line in real:
            with self.subTest(line=line[:40]):
                kept, reason = why.check_repo({"target": target, "why": line}, self.NAMES)
                self.assertIsNone(reason)
                self.assertEqual(kept, line)
        for raw, key in (("your dev workflow", why.WORKFLOW), ("Your Claude Code", why.WORKFLOW), ("the dashboard", why.DASHBOARD),
                         ("your freelance client sites", why.CLIENTS), ("Cursor", why.WORKFLOW), ("dev workflow", why.WORKFLOW),
                         ("Sail Beach Club", "sail beach club")):
            self.assertEqual(why.target_key(raw), key)
        # A named venture still resolves through its own rule.
        self.assertEqual(why.check_repo({"target": "Sail Beach Club", "why": "Sail Beach Club can use it for bookings."},
                                        self.NAMES)[1], None)

    def test_a_dev_workflow_line_names_what_it_lets_you_do(self) -> None:
        """Owner, 2026-10-20 (feed review 5): a tool name or "sharpen your
        workflow" is not a use. Day one's universal-modder line (Oct 14) and
        the two vague lines of Oct 15-19 are refused; the other 18 real lines
        that week mention Claude Code and pass."""
        wf = lambda line: why.check_repo({"target": "dev workflow", "why": line}, self.NAMES)  # noqa: E731
        refused = {
            # Day one (docs/tech_hub.md §7.2b), as written and in the second person.
            "Can give Claude Code skills and the fal MCP for agent-driven workflows in Tsims's Claude Code setup.":
                None,
            "Can give your Claude Code skills and the fal MCP for agent-driven workflows.": "for agent-driven workflows",
            "A read-only security scan skill can sharpen your Claude Code workflow across repos, Vercel and GitHub.":
                "sharpen",
            "Adds an MCP server and skills to your Claude Code.": "names the tool",
        }
        for line, needle in refused.items():
            with self.subTest(line=line[:40]):
                got, reason = wf(line)
                self.assertEqual(got, why.GENERAL)
                if needle:
                    self.assertIn(needle, reason)
        kept = [
            "Use this as your dev workflow's starting point for vetted Claude Code plugins, with trust checks before install.",
            "Let your Claude Code and MCP setup search and download 3D assets from 23 sites in one call.",
            "Route your Claude Code and Codex agents to different model providers from your menu bar, with quota rotation.",
            "Swap system prompts across your Claude Code, Cursor and Codex clients in one reversible step.",
            "Your dev workflow can use GitBot to run reusable Claude Code bots locally across your repositories.",
            "Your dev workflow can use this to have Claude Code ask about your approach and tradeoffs before writing code.",
            "Your Claude Code can use this skill to answer hard questions with a readable single HTML page.",
            "Your Claude Code and Cursor setup can pull vetted official MCP servers from this curated directory.",
            "Your Claude Code is the agentic terminal tool you build with, handling routine tasks and git workflows from natural language.",
            "Cuts your Claude Code context usage by roughly 98 percent through MCP sandboxing and persistent session memory.",
            "Your dev workflow can use its MCP server to give Claude Code local file, shell and computer-control tools.",
            "Gives your Claude Code an MCP server and skill that finds the cheapest API provider for any model.",
            "Your Claude Code setup can use these eleven free skills to reverse-engineer and test existing apps.",
            "Your dev workflow can use this Blender MCP server so Claude Code can drive scene building and 3D tasks directly.",
            "Your Claude Code or Cursor can query live Semrush keyword and backlink data through this MCP server.",
            "Lets your Claude Code, Cursor and other coding agents run as a managed fleet on your own subscriptions.",
            "This MCP audit CLI can check your Cursor and Claude Code MCP configs for poisoning and leaked secrets.",
            "Hycli's typed CLI and MCP actions can give your Claude Code and Cursor agents reliable website automation.",
            "clodfarm's multi-agent orchestration and usage pacing can help you run parallel Claude Code missions.",
            "Can add a reverse-engineering MCP server to your Claude Code.",
        ]
        for line in kept:
            with self.subTest(line=line[:40]):
                self.assertEqual(wf(line), (line, None))
        self.assertIn("lets you DO", why.REPO_SYSTEM)

    def test_second_person_only(self) -> None:
        """Owner, 2026-10-14: "your Claude Code", "your client sites", never
        "Tsims's"; the prompt the model copies from says "your" too."""
        ok = {"target": "dev workflow", "why": "Can add a reverse-engineering MCP server to your Claude Code."}
        self.assertEqual(why.check_repo(ok, self.NAMES), (ok["why"], None))
        for bad in ("Can monitor Tsims's Claude Code sessions from the notch.",
                    "Gives the reader's client sites animated ASCII components.",
                    "Can give the owner's dashboard a feed chart."):
            with self.subTest(why=bad[:30]):
                line, reason = why.check_repo({"target": "dev workflow" if "Claude" in bad else
                                               ("client sites" if "client" in bad else "dashboard"), "why": bad},
                                              self.NAMES)
                self.assertEqual(line, why.GENERAL)
                self.assertIn("names the reader", reason)
        text = why.REPO_SYSTEM + "".join(v[0] for v in why.REPO_TARGETS.values())
        self.assertNotIn("Tsims", text)
        self.assertIn('"your Claude Code"', why.REPO_SYSTEM)
        self.assertIn('"your client sites"', why.REPO_SYSTEM)
        # The rule is the repo why's alone; news why does not check it.
        self.assertNotIn("THIRD_PERSON", why.check.__code__.co_names)

    def _fake_complete(self, payloads: list[dict]):
        calls: list[str] = []

        def complete(**kw):
            calls.append(kw["messages"][0]["content"])
            kw["on_recorded"](700 + len(calls), kw["model"], 0.0)
            return SimpleNamespace(stop_reason=None, content=[SimpleNamespace(type="text",
                                                                              text=json.dumps(payloads.pop(0)))])
        return complete, calls

    def test_one_same_run_retry_with_the_reason(self) -> None:
        group = [ledger.RepoWhyJob(1, "o/a", "S.", "GitHub", None, ()),
                 ledger.RepoWhyJob(2, "o/b", "S.", "GitHub", None, ())]
        first = {"repos": [{"id": 1, "target": "dashboard", "why": "The dashboard might use it."},
                           {"id": 2, "target": None, "why": None}]}
        second = {"repos": [{"id": 1, "target": "dashboard", "why": "The dashboard can use it for feed charts."}]}
        complete, calls = self._fake_complete([first, second])
        saved: list[tuple[dict, int | None]] = []
        with mock.patch.object(why.ai, "complete", complete), \
                mock.patch.object(ledger, "feed_save_repo_why", lambda a, u, **_: saved.append((dict(a), u)) or len(a)):
            out = why.ask_repo_group(group, {}, self.NAMES)
        self.assertEqual(out, {1: "The dashboard can use it for feed charts.", 2: why.GENERAL})
        self.assertEqual(len(calls), 2)
        self.assertIn("your_last_answer_was_rejected_because", calls[1])
        self.assertIn("hedges ('might')", calls[1])
        self.assertNotIn('"id": 2', calls[1])  # the model's own null is not retried
        # Each answer is saved with the call that produced it.
        self.assertEqual(saved, [({2: why.GENERAL}, 701), ({1: "The dashboard can use it for feed charts."}, 702)])

    def test_no_retry_when_nothing_was_refused_or_no_room(self) -> None:
        group = [ledger.RepoWhyJob(1, "o/a", "S.", "GitHub", None, ())]
        refused = {"repos": [{"id": 1, "target": "dashboard", "why": "The dashboard might use it."}]}
        for payload, retry in (({"repos": [{"id": 1, "target": None, "why": None}]}, True), (refused, False)):
            complete, calls = self._fake_complete([payload])
            with self.subTest(retry=retry), mock.patch.object(why.ai, "complete", complete), \
                    mock.patch.object(ledger, "feed_save_repo_why", lambda a, u, **_: len(a)):
                self.assertEqual(why.ask_repo_group(group, {}, self.NAMES, retry=retry), {1: why.GENERAL})
                self.assertEqual(len(calls), 1)

    def test_the_job_never_passes_the_repo_cap(self) -> None:
        queue = [ledger.RepoWhyJob(i, f"o/{i}", "S.", "GitHub", None, ()) for i in range(1, 17)]
        used = [3]
        flags: list[bool] = []

        def ask(group, ctx, names, *, client=None, retry=True):
            flags.append(retry)
            used[0] += 1
            return {j.story_id: why.GENERAL for j in group}

        with mock.patch.object(ledger, "feed_repo_why_queue", lambda *a, **k: queue), \
                mock.patch.object(ledger, "ai_calls_today", lambda p, **_: used[0]), \
                mock.patch.object(ledger, "ctx_ventures", lambda **_: {}), \
                mock.patch.object(why, "ask_repo_group", ask):
            why.repo_why_job()
        # 3 of 4 used: one group, and no retry (it would be the 5th call).
        self.assertEqual(flags, [False])

    def test_every_repo_gets_a_line(self) -> None:
        jobs = [ledger.RepoWhyJob(1, "o/a", None, "GitHub", None, ()), ledger.RepoWhyJob(2, "o/b", None, "GitHub", None, ())]
        out = why.repo_answers_for(jobs, {"repos": [{"id": 1, "target": "dashboard", "why": "Speeds up the dashboard feed."}]},
                                   self.NAMES)
        self.assertEqual(out, {1: "Speeds up the dashboard feed.", 2: why.GENERAL})
        self.assertEqual(why.repo_answers_for(jobs, None, self.NAMES), {1: why.GENERAL, 2: why.GENERAL})

    def test_home_and_tech_why_cap_and_rank_order(self) -> None:
        """Owner, 2026-10-14: 100 calls a day (20 ran out at 14:55 Chicago),
        page one of each pillar, asked position by position across both;
        repo why keeps its own cap."""
        self.assertEqual((why.DAILY_CALLS, why.TOP, why.REPO_DAILY_CALLS), (100, 30, 4))
        self.assertEqual(why.TOP, rank.PAGE_SIZE)
        self.assertIn("ORDER BY x.pos, x.feed", ledger.FEED_WHY_QUEUE_SQL)
        self.assertNotEqual(why.PURPOSE, why.REPO_PURPOSE)

    def test_home_why_never_asks_a_repo_story(self) -> None:
        self.assertIn("st.category IS DISTINCT FROM 'repos'", ledger.FEED_WHY_QUEUE_SQL)
        self.assertIn("st.category = 'repos'", ledger.FEED_REPO_WHY_QUEUE_SQL)


class RepoStoriesInTheEngine(unittest.TestCase):
    def test_summaries_keep_a_repo_storys_topics(self) -> None:
        import inspect
        src = inspect.getsource(ledger.feed_save_summaries)
        self.assertIn("CASE WHEN category = 'repos' AND cardinality(topics) > 0 THEN topics", src)

    def test_the_prune_never_deletes_a_surfaced_story(self) -> None:
        for name in ("filtered_stories_deleted", "unreacted_stories_deleted"):
            self.assertIn("repo_surfacings", ledger.FEED_PRUNE_SQL[name])

    def test_ingest_never_fetches_the_github_source(self) -> None:
        import inspect
        self.assertIn("kind NOT IN ('github', 'tmdb')", inspect.getsource(ledger.feed_active_sources))

    def test_a_repo_story_never_pushes(self) -> None:
        from feed import tech
        # Not official, so the summarizer's category is community; the stored one stays repos.
        self.assertEqual(tech.decide("pricing_credits", False), "community")
        self.assertFalse(tech.wants_push(tech.decide("pricing_credits", False), True))


class BriefTechLine(unittest.TestCase):
    """The brief's one Tech line names a repo only when ctx_tech_brief carries
    top_repo, which db/049 includes only after a repo Love in 14 days."""

    def test_no_top_repo_no_repo_in_the_line(self) -> None:
        from core import brief
        line = brief._tech_lines({"official_24h": [{"title": "Claude 5.6", "source": "Anthropic"}]})
        self.assertEqual(line, ["Official: Claude 5.6 (Anthropic)."])
        self.assertEqual(brief._tech_lines({}), [])

    def test_the_top_repo_shares_the_one_line(self) -> None:
        from core import brief
        both = brief._tech_lines({"official_24h": [{"title": "A", "source": "B"}, {"title": "C", "source": "D"}],
                                  "top_repo": {"repo": "o/r", "summary": "x"}})
        self.assertEqual(both, ["Official: A (B). Plus 1 more. Top repo: o/r."])
        self.assertEqual(brief._tech_lines({"top_repo": {"repo": "o/r"}}), ["Top repo: o/r."])
        self.assertIn("tech_brief", brief.ONE_LINE_SECTIONS)

    def test_db049_gates_top_repo_on_a_repo_love_in_14_days(self) -> None:
        sql = (Path(__file__).resolve().parents[2] / "db" / "049_repos.sql").read_text(encoding="utf-8")
        brief_fn = sql[sql.index("CREATE OR REPLACE FUNCTION public.ctx_tech_brief()"):]
        self.assertIn("st.category = 'repos'", brief_fn)
        self.assertIn("lr.reacted_at > now() - interval '14 days'", brief_fn)
        self.assertIn("WHERE EXISTS (SELECT 1 FROM repo_love)", brief_fn)


# ====================================================================== fetch log line

def raw(ext: str, hours_ago: float | None) -> RawItem:
    return RawItem(external_id=ext, link=f"https://x.invalid/{ext}", title=ext, teaser=None,
                   published_at=None if hours_ago is None else NOW - timedelta(hours=hours_ago), categories=())


class FetchLogGuids(unittest.TestCase):
    def test_newest_three_cut_to_40(self) -> None:
        items = [raw("old", 30), raw("x" * 60, 1), raw("mid", 5), raw("undated", None), raw("newest", 0.5)]
        self.assertEqual(ingest.newest_guids(items), f"newest | {'x' * 40} | mid")

    def test_undated_last_and_none(self) -> None:
        self.assertEqual(ingest.newest_guids([raw("a", None), raw("b", None)]), "a | b")
        self.assertEqual(ingest.newest_guids([]), "none")
        self.assertEqual((ingest.LOG_GUIDS, ingest.LOG_GUID_CHARS), (3, 40))


if __name__ == "__main__":
    unittest.main()
