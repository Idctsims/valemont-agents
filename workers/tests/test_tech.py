"""Tech Hub (Chat 4 Phase 1, db/045): the page watcher on saved pages, Hacker
News thresholds, the push decision and its cap path, the categorizer mapping,
the no-cross-in rules on the Python side, and ingest of tech sources.

Fixtures (tests/fixtures/tech/) are the real pages and feeds, fetched
2026-10-13 and trimmed to the region holding their first entries; nothing
inside a kept region was rewritten.
"""

from __future__ import annotations

import json
import logging
import math
import re
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import ledger, push
from core.paths import REPO_ROOT
from feed import ingest, rank, tech, watch
from feed.parse import MAX_ITEMS, ParseFailed, canonical_url, head_items, parse_feed
from feed.rules import Rule, Rules
from feed.sources import SourcesInvalid, _spec, load_sources
from feed.summarize import MAX_ATTEMPTS
from tests.test_feed import NOW, FakeFeedLedger, IngestCase, rss, rss_item, source

FIX = Path(__file__).with_name("fixtures") / "tech"
TODAY = date(2026, 10, 13)


def page(name: str, url: str, **cfg) -> list:
    return watch.parse_page((FIX / f"{name}.html").read_bytes(), url, watch.page_config(cfg), TODAY)


# ====================================================================== watcher

class PageWatcher(unittest.TestCase):
    def test_anthropic_news_links_with_dates_in_the_card(self) -> None:
        items = page("anthropic_news", "https://www.anthropic.com/news", mode="links", href=r"^/news/[a-z0-9-]+$")
        self.assertGreaterEqual(len(items), 5)
        by = {i.link: i for i in items}
        usage = by["https://www.anthropic.com/news/2026-usage-policy-update"]
        self.assertEqual(usage.title, "2026 Usage Policy update")
        self.assertEqual(usage.published_at.date(), date(2026, 10, 8))
        self.assertEqual(usage.precision, "date")
        self.assertEqual(usage.external_id, "page:https://www.anthropic.com/news/2026-usage-policy-update")
        # A featured card: its headline, not its description, is the title.
        cyber = by["https://www.anthropic.com/news/cyber-verification-program"]
        self.assertEqual(cyber.title, "Expanding the Cyber Verification Program")
        self.assertTrue(cyber.teaser and cyber.teaser.startswith("We"))
        self.assertEqual(len(by), len(items))  # once per href

    def test_claude_blog_skips_the_label(self) -> None:
        items = page("claude_blog", "https://claude.com/blog", mode="links",
                     href=r"^/(blog|resources/articles)/[a-z0-9-]+$", skip=["article"])
        titles = [i.title for i in items]
        self.assertIn("Claude now works with Google Docs, Sheets, and Slides", titles)
        self.assertNotIn("Article", titles)
        self.assertTrue(all(i.link.startswith("https://claude.com/resources/articles/") for i in items))

    def test_supabase_dates_follow_the_link(self) -> None:
        items = page("supabase_changelog", "https://supabase.com/changelog", mode="links",
                     href=r"^/changelog/[a-z0-9-]+$", date_after=3)
        self.assertEqual(items[0].title, "OpenTelemetry traces and metrics in the Supabase .NET SDK")
        self.assertEqual(items[0].published_at.date(), date(2026, 10, 14))
        # Without date_after the same links carry no date and are skipped.
        self.assertEqual(page("supabase_changelog", "https://supabase.com/changelog", mode="links",
                              href=r"^/changelog/[a-z0-9-]+$"), [])

    def test_claude_api_release_notes_one_item_per_dated_heading(self) -> None:
        items = page("claude_api_notes", "https://platform.claude.com/docs/en/release-notes/overview",
                     mode="sections", heading=["h3"])
        self.assertGreaterEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first.link, "https://platform.claude.com/docs/en/release-notes/overview#october-9-2026")
        self.assertEqual(first.published_at.date(), date(2026, 10, 9))
        self.assertTrue(first.title.startswith("You can now let a Claude Managed Agents agent"))
        # Inline code and links never split the lead sentence.
        cache = next(i for i in items if i.published_at.date() == date(2026, 10, 7))
        self.assertIn("prompt cache reads on Claude Sonnet 5.5", cache.title)
        self.assertEqual(len({i.link for i in items}), len(items))

    def test_claude_apps_release_notes_take_the_bold_lead(self) -> None:
        items = page("claude_apps_notes", "https://support.claude.com/en/articles/12138966-release-notes",
                     mode="sections", heading=["h3"])
        self.assertEqual(items[0].title, "Claude Haiku 5.5 launch")
        self.assertEqual(items[0].published_at.date(), date(2026, 10, 7))
        self.assertTrue(items[0].link.endswith("#h_90213b2c7a"))
        self.assertTrue(items[0].teaser.startswith("We just launched Claude Haiku 5.5"))  # not the title again
        # Month headings ("October 2026") are not entries.
        self.assertFalse(any(i.title.startswith("October") for i in items))

    def test_openai_changelog_blocks_with_year_less_dates(self) -> None:
        items = page("openai_api_changelog", "https://developers.openai.com/api/docs/changelog", mode="blocks",
                     block_class="grid-cols-[3rem_1fr]", content_class="_ChangelogMarkdown")
        self.assertGreaterEqual(len(items), 4)
        self.assertEqual(items[0].title, "Added Ultrafast mode for GPT-6.1 Sol in the Responses API.")
        self.assertEqual(items[0].published_at.date(), date(2026, 10, 8))
        decisions = next(i for i in items if "Decisions API" in i.title)
        self.assertEqual(decisions.title, "Released the Decisions API in beta with gpt-6-luna.")  # no " ."
        # Two entries on one day get two links and two ids.
        oct6 = [i for i in items if i.published_at.date() == date(2026, 10, 6)]
        self.assertEqual(len(oct6), 2)
        self.assertEqual(len({i.link for i in oct6} | {i.external_id for i in oct6}), 4)

    def test_a_missing_container_marker_is_a_parse_failure(self) -> None:
        with self.assertRaises(ParseFailed):
            page("anthropic_news", "https://www.anthropic.com/news", mode="links", href="^/news/",
                 start="<main id=never-there>")

    def test_config_rules(self) -> None:
        for bad in ({"mode": "rss"}, {"mode": "links"}, {"mode": "links", "href": "("},
                    {"mode": "blocks", "block_class": "x"}, {"mode": "sections", "heading": ["p"]},
                    {"mode": "links", "href": "x", "max_bytes": 10}, {"mode": "links", "href": "x", "colour": 1}):
            with self.subTest(cfg=bad), self.assertRaises(watch.ConfigInvalid):
                watch.page_config(bad)


class Dates(unittest.TestCase):
    def test_formats(self) -> None:
        for text, want in (("Oct 8, 2026", date(2026, 10, 8)), ("October 7, 2026", date(2026, 10, 7)),
                           ("2026-10-08", date(2026, 10, 8)), ("8 October 2026", date(2026, 10, 8)),
                           ("Sept 3, 2026", date(2026, 9, 3)), ("Oct 8", date(2026, 10, 8))):
            with self.subTest(text=text):
                self.assertEqual(watch.parse_date(text, TODAY), want)
        for text in ("Announcements", "October 2026", "Oct 32, 2026", "", "Feature"):
            with self.subTest(text=text):
                self.assertIsNone(watch.parse_date(text, TODAY))

    def test_a_year_less_date_is_never_in_the_future(self) -> None:
        self.assertEqual(watch.parse_date("Oct 14", TODAY), date(2026, 10, 14))  # a day ahead (US vs UTC)
        self.assertEqual(watch.parse_date("Dec 20", TODAY), date(2025, 12, 20))
        self.assertEqual(watch.parse_date("Dec 31", date(2027, 1, 2)), date(2026, 12, 31))


class Feeds(unittest.TestCase):
    def test_cursor_rss_states_days_not_times(self) -> None:
        items = parse_feed((FIX / "cursor_changelog.xml").read_bytes())
        self.assertEqual(len(items), 5)
        self.assertTrue(all(i.precision == "date" for i in items))  # every entry stamped 00:00:00 GMT
        self.assertTrue(all(i.link.startswith("https://cursor.com/") for i in items))

    def test_a_timed_feed_is_time_precision(self) -> None:
        items = parse_feed((FIX / "claude_code_releases.atom").read_bytes())
        self.assertEqual(len(items), 5)
        self.assertTrue(all(i.precision == "time" for i in items))

    def test_head_items_cuts_a_big_feed_and_it_still_parses(self) -> None:
        atom = (FIX / "claude_code_releases.atom").read_bytes()
        cut = head_items(atom, 2)
        self.assertIsNotNone(cut)
        self.assertEqual(len(parse_feed(cut)), 2)
        self.assertTrue(cut.rstrip().endswith(b"</feed>"))
        rss_cut = head_items((FIX / "cursor_changelog.xml").read_bytes(), 3)
        self.assertEqual(len(parse_feed(rss_cut)), 3)
        self.assertIsNone(head_items(atom, 50))  # fewer entries than asked: read whole

    def test_page_urls_keep_their_fragment_only_when_asked(self) -> None:
        u = "https://Support.Claude.com/en/articles/1-release-notes?utm_source=x#h_9"
        self.assertEqual(canonical_url(u), "https://support.claude.com/en/articles/1-release-notes")
        self.assertEqual(canonical_url(u, keep_fragment=True),
                         "https://support.claude.com/en/articles/1-release-notes#h_9")


class HackerNews(unittest.TestCase):
    def hits(self, *rows: dict) -> bytes:
        return json.dumps({"hits": list(rows)}).encode()

    def test_thresholds_are_checked_on_every_hit(self) -> None:
        body = self.hits(
            {"objectID": "1", "title": "Big", "url": "https://a.invalid/1", "points": 100, "num_comments": 5,
             "created_at_i": 1_760_000_000},
            {"objectID": "2", "title": "Small", "url": "https://a.invalid/2", "points": 99, "created_at_i": 1},
            {"objectID": "3", "title": "No points", "url": "https://a.invalid/3"},
        )
        items = watch.parse_hn(body, watch.hn_config({"min_points": 100}))
        self.assertEqual([i.external_id for i in items], ["hn:1"])
        self.assertEqual(items[0].teaser, "100 points and 5 comments on Hacker News.")
        self.assertEqual([i.external_id for i in watch.parse_hn(body, watch.hn_config({"min_points": 50}))],
                         ["hn:1", "hn:2"])

    def test_a_text_post_links_to_its_hn_page(self) -> None:
        body = self.hits({"objectID": "77", "title": "Show HN: a thing", "url": None, "points": 60,
                          "created_at_i": 1_760_000_000})
        self.assertEqual(watch.parse_hn(body, watch.hn_config({"min_points": 50}))[0].link,
                         "https://news.ycombinator.com/item?id=77")

    def test_the_real_responses_respect_the_owners_thresholds(self) -> None:
        front = watch.parse_hn((FIX / "hn_front.json").read_bytes(), watch.hn_config({"min_points": 100}))
        show = watch.parse_hn((FIX / "hn_show.json").read_bytes(), watch.hn_config({"min_points": 50}))
        self.assertTrue(front and show)
        raw_front = json.loads((FIX / "hn_front.json").read_bytes())["hits"]
        points = {f"hn:{h['objectID']}": h["points"] for h in raw_front}
        self.assertTrue(all(points[i.external_id] >= 100 for i in front))

    def test_not_json_is_a_parse_failure_and_config_is_checked(self) -> None:
        with self.assertRaises(ParseFailed):
            watch.parse_hn(b"<html>", watch.hn_config({"min_points": 1}))
        for bad in ({}, {"min_points": 0}, {"min_points": "100"}, {"min_points": 5, "x": 1}):
            with self.subTest(cfg=bad), self.assertRaises(watch.ConfigInvalid):
                watch.hn_config(bad)


# ====================================================================== sources

class SourcesFile(unittest.TestCase):
    def test_the_tech_sources(self) -> None:
        t = {s.slug: s for s in load_sources() if "tech" in s.pillars}
        self.assertEqual(len(t), 21)
        self.assertEqual(sum(s.active for s in t.values()), 20)
        self.assertEqual(sum(s.official for s in t.values()), 19)
        # The owner's replacement for the blocked ChatGPT notes, below openai.com/news.
        self.assertGreater(t["openai-community-announcements"].priority, t["openai-news"].priority)
        self.assertTrue(all(t[s].official and t[s].active for s in
                            ("openai-community-announcements", "openai-python", "anthropic-sdk-python")))
        self.assertTrue(all(s.pillars == ("tech",) for s in t.values()))
        chatgpt = t["chatgpt-release-notes"]
        self.assertFalse(chatgpt.active)
        self.assertIn("Cloudflare", chatgpt.notes)
        # The fallback mirror ranks below the watcher it backs up.
        self.assertGreater(t["anthropic-news-feed"].priority, t["anthropic-news"].priority)
        self.assertEqual({t["hn-front"].config["min_points"], t["hn-show"].config["min_points"]}, {100, 50})
        self.assertFalse(t["hn-front"].official or t["hn-show"].official)

    def test_rules(self) -> None:
        base = {"slug": "x", "name": "X", "pillars": ["tech"], "kind": "rss", "url": "https://x.invalid/feed"}
        for bad in ({**base, "pillars": ["tech", "home"]},
                    {**base, "kind": "page"},
                    {**base, "kind": "page", "config": {"mode": "links"}},
                    {**base, "kind": "hn", "config": {"min_points": 10}},
                    {**base, "config": {"mode": "links", "href": "x"}},
                    {**base, "pillars": ["home"], "official": True},
                    {**base, "kind": "ftp"}):
            with self.subTest(raw=bad), self.assertRaises(SourcesInvalid):
                _spec(bad)
        ok = _spec({**base, "kind": "hn", "url": "https://hn.algolia.com/api/v1/search?tags=front_page",
                    "config": {"min_points": 100}})
        self.assertEqual(ok.config, {"min_points": 100})


# ====================================================================== rules

class TechRules(unittest.TestCase):
    def test_home_rules_never_touch_a_tech_story(self) -> None:
        rules = Rules([Rule(1, "title_regex", None, "promo code"), Rule(2, "title_regex", "tech", "^ask hn")])
        self.assertEqual(rules.filter_reason(title="Promo code inside", url=None, pillars=("home",)), "title_regex:1")
        self.assertIsNone(rules.filter_reason(title="Promo code: $50 in free credits", url=None, pillars=("tech",)))
        self.assertEqual(rules.filter_reason(title="Ask HN: anything", url=None, pillars=("tech",)), "title_regex:2")
        self.assertIsNone(rules.filter_reason(title="Ask HN: anything", url=None, pillars=("home",)))


class NoCrossIn(unittest.TestCase):
    def story(self, sid: int, pillars: tuple[str, ...], title: str, sport: str | None = None) -> rank.StoryIn:
        return rank.StoryIn(sid, pillars, sport, title, (), (), None, (1.0, 0.0))

    def test_tech_is_scored_in_tech_only_and_nothing_crosses_into_it(self) -> None:
        follows = [rank.Follow("Alabama Crimson Tide", 1, None, True, ("alabama",), "cfb"),
                   rank.Follow("Kalshi", 1, "home", False, ("kalshi",), None)]
        # A tech story naming a tier-1 follow is still Tech's alone.
        self.assertEqual(rank.placements(self.story(1, ("tech",), "Kalshi ships an MCP server"), follows),
                         [("tech", False)])
        # A sports story still crosses to Home, never to Tech.
        self.assertEqual(rank.placements(self.story(2, ("sports",), "Alabama wins", "cfb"), follows),
                         [("sports", False), ("home", True)])
        # A follow with no pillar is Home's and Sports', never Tech's.
        self.assertIsNone(rank.match_follows("tech", follows, "Alabama", (), "cfb").tier)

    def test_official_tech_items_decay_last(self) -> None:
        # db/046 (owner): a 48 h official item outranks a 20 h HN item of the
        # same score on Tech; the same pair on Home keeps 18 h for both.
        official = rank.Candidate(1, 5.0, 48.0, official=True)
        community = rank.Candidate(2, 5.0, 20.0)
        self.assertEqual([p.id for p in rank.order_for_you([community, official], "tech")], [1, 2])
        self.assertEqual([p.id for p in rank.order_for_you([community, official], "home")], [2, 1])
        self.assertAlmostEqual(rank.rank_of(5.0, 48.0, 0.0, rank.half_life("tech", True)),
                               5.0 - math.log(2) * 48 / 72)
        sql = (REPO_ROOT / "db" / "046_tech_official_decay.sql").read_text(encoding="utf-8")
        self.assertIn(f"{rank.TECH_OFFICIAL_HALF_LIFE_H}::float8 AS tech_official_half_life_h, "
                      "-- rank.TECH_OFFICIAL_HALF_LIFE_H", sql)

    def test_the_read_path_constants_match_db_045(self) -> None:
        sql = (REPO_ROOT / "db" / "045_tech_hub.sql").read_text(encoding="utf-8")
        self.assertIn(f"{rank.TECH_WINDOW_H}          AS tech_window_h, -- rank.TECH_WINDOW_H", sql)
        self.assertIn(f"{rank.WINDOW_H}           AS window_h,      -- rank.WINDOW_H", sql)
        self.assertIn(f"{rank.CROSS_CAP}            AS cross_cap,     -- rank.CROSS_CAP", sql)
        # The SQL categories are the worker's.
        cats = re.search(r"CHECK \(category IN \((.*?)\)\);", sql, re.S).group(1)
        for c in tech.CATEGORIES:
            self.assertIn(f"'{c}'", cats)


# ====================================================================== categories and the push

def job(sid: int, *, official: bool = True, title: str = "A headline", teaser: str | None = None,
        attempts: int = 0, backfill: bool = False, age: timedelta = timedelta(hours=1)) -> ledger.TechSummaryJob:
    """A fresh item by default: first seen NOW, published `age` before."""
    return ledger.TechSummaryJob(sid, title, teaser, "OpenAI" if official else "Hacker News", official, attempts,
                                 backfill, NOW - age, NOW)


class Categorizer(unittest.TestCase):
    def test_mapping(self) -> None:
        self.assertEqual(tech.decide("pricing_credits", True), "pricing_credits")
        self.assertEqual(tech.decide("tooling", True), "tooling")
        self.assertEqual(tech.decide("pricing_credits", False), "community")  # HN is the community's chip
        self.assertEqual(tech.decide("other", True), "other")
        self.assertIsNone(tech.decide("deals", True))   # a Home category is never a tech answer
        self.assertIsNone(tech.decide("repos", True))   # Phase 2's, not the model's
        self.assertIsNone(tech.decide(None, True))

    def test_results_keep_the_answer_and_count_failures(self) -> None:
        group = [job(1), job(2, official=False), job(3)]
        payload = {"stories": [
            {"id": 1, "summary": "OpenAI gives new API accounts free credits.", "entities": ["OpenAI"],
             "topics": ["Free Credits"], "category": "pricing_credits", "claimable": True},
            {"id": 2, "summary": "A developer shows a tool.", "entities": [], "topics": [],
             "category": "pricing_credits", "claimable": True},
            {"id": 3, "summary": "One. Two. Three.", "entities": [], "topics": [], "category": "agents",
             "claimable": False},
            {"id": 99, "summary": "Stranger.", "entities": [], "topics": [], "category": "agents", "claimable": False},
        ]}
        out, reasons = tech.results_for(group, payload)
        by = {o.story_id: o for o in out}
        self.assertEqual((by[1].category, by[1].claimable, by[1].topics), ("pricing_credits", True, ("free_credits",)))
        self.assertEqual(by[2].category, "community")
        self.assertIsNone(by[3].summary)
        self.assertIn("3 sentences", reasons[3])
        self.assertNotIn(99, by)

    def test_the_prompt_carries_the_owners_question_and_every_category(self) -> None:
        self.assertIn("Does this offer free credits, a free tier increase, or a promo the reader can claim?",
                      tech.SYSTEM)
        for c in tech.CATEGORIES:
            self.assertIn(c, tech.SYSTEM)
        self.assertEqual(tech.schema()["properties"]["stories"]["items"]["properties"]["category"]["enum"],
                         list(tech.CATEGORIES))


class PushDecision(unittest.TestCase):
    def test_the_rule(self) -> None:
        self.assertTrue(tech.wants_push("pricing_credits", True))
        for cat, yes in (("pricing_credits", False), ("pricing_credits", None), ("models_features", True),
                         ("community", True), (None, True)):
            with self.subTest(cat=cat, yes=yes):
                self.assertFalse(tech.wants_push(cat, yes))

    def test_the_message(self) -> None:
        m = tech.push_message(42, "OpenAI doubles free API credits", "New accounts get $10.")
        self.assertEqual((m.kind, m.title, m.deep_link, m.tag),
                         ("tech_free_credits", "Free credits: OpenAI doubles free API credits",
                          "/tech/story/42", "tech-credits-42"))
        self.assertTrue(tech.push_message(42, "x", None, test=True).title.startswith("Free credits (test): "))


class PushEligibility(unittest.TestCase):
    """Owner, 2026-10-14: a backfill never pushes. Eligible only if first seen
    after the source's first fetch (not backfill) AND published within 24 h
    of first seen. db/046 tech_push_eligible enforces it in SQL too."""

    def test_both_conditions(self) -> None:
        seen = NOW
        self.assertTrue(tech.push_eligible(False, seen - timedelta(hours=1), seen))
        self.assertTrue(tech.push_eligible(False, seen - timedelta(hours=24), seen))      # exactly 24 h
        self.assertFalse(tech.push_eligible(True, seen - timedelta(hours=1), seen))       # a backfill
        self.assertFalse(tech.push_eligible(False, seen - timedelta(hours=24, seconds=1), seen))  # old news
        self.assertFalse(tech.push_eligible(False, None, seen))

    def test_an_ineligible_story_is_never_sent(self) -> None:
        sends: list[push.Message] = []
        asked: list[int] = []
        with mock.patch.object(ledger, "tech_credit_push", lambda sid, send: asked.append(sid) or send() or 1):
            run = tech.TechRun()
            out = ledger.SummaryOut(9, "Summary.", category="pricing_credits", claimable=True)
            tech.maybe_push(out, job(9, backfill=True), run, send=lambda m: sends.append(m) or [])
            tech.maybe_push(out, job(10, age=timedelta(hours=30)), run, send=lambda m: sends.append(m) or [])
            tech.maybe_push(out, job(11), run, send=lambda m: sends.append(m) or [])
        self.assertEqual((asked, run.ineligible, len(sends)), ([11], [9, 10], 1))  # the database is never asked
        self.assertIn("2 not push-eligible", run.job_note)

    def test_the_sql_is_the_twin(self) -> None:
        sql = (REPO_ROOT / "db" / "046_tech_official_decay.sql").read_text(encoding="utf-8")
        self.assertIn("NOT i.backfill AND i.fetched_at - i.published_at <= interval '24 hours'", sql)
        self.assertEqual(tech.PUSH_FRESH, timedelta(hours=24))
        self.assertIn("i.backfill, i.published_at, i.fetched_at", ledger.FEED_TECH_SUMMARY_QUEUE_SQL)


class CapPath(unittest.TestCase):
    """The cap and the never-twice rule are the database's (tests_live
    test_tech_sql pins them in SQL); here: the worker sends only when the
    ledger says yes, and records what happened."""

    def setUp(self) -> None:
        self.answers: list[int | None] = []
        self.sends: list[push.Message] = []

        def fake_push(story_id, send):
            ans = self.answers.pop(0)
            if ans is None:
                return None
            send()
            return ans

        p = mock.patch.object(ledger, "tech_credit_push", fake_push)
        p.start()
        self.addCleanup(p.stop)

    def send(self, msg: push.Message):
        self.sends.append(msg)
        return [SimpleNamespace(notification_id=900 + len(self.sends))]

    def out(self, sid: int, cat: str = "pricing_credits", yes: bool = True) -> ledger.SummaryOut:
        return ledger.SummaryOut(sid, "Summary.", category=cat, claimable=yes)

    def test_sent_capped_and_skipped(self) -> None:
        run = tech.TechRun()
        self.answers = [1, None]
        tech.maybe_push(self.out(1), job(1), run, send=self.send)
        tech.maybe_push(self.out(2), job(2), run, send=self.send)   # the database said no: never sent
        tech.maybe_push(self.out(3, yes=False), job(3), run, send=self.send)  # not asked at all
        self.assertEqual((run.pushes, run.capped, len(self.sends)), (1, [2], 1))
        self.assertIn("1 free-credits push", run.job_note)

    def test_a_failed_push_is_recorded_and_loud(self) -> None:
        # tests/__init__.py silences logging suite-wide; this test reads logs.
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)
        run = tech.TechRun()
        self.answers = [5]

        def broken(msg):
            raise push.PushError("no active push subscriptions")

        with self.assertLogs("valemont.feed.tech", "ERROR"):
            tech.maybe_push(self.out(5), job(5), run, send=broken)
        self.assertEqual(run.pushes, 0)
        self.assertIn("push failed", run.job_note)


class TechJob(unittest.TestCase):
    def setUp(self) -> None:
        self.saved: list[tuple[list[ledger.SummaryOut], int | None]] = []
        self.queue = [job(1, teaser="OpenAI is giving new API accounts free credits this month."),
                      job(2, official=False)]
        for name, fn in {
            "feed_tech_summary_queue": lambda limit, max_attempts: list(self.queue),
            "feed_save_summaries": lambda results, uid: self.saved.append((list(results), uid)),
            "tech_credit_push": lambda sid, send: send() or 7,
        }.items():
            p = mock.patch.object(ledger, name, fn)
            p.start()
            self.addCleanup(p.stop)
        self.answers: list[dict] = []

        def complete(**kw):
            kw["on_recorded"](55, "claude-haiku-5-5", 0.0001)
            text = json.dumps(self.answers.pop(0))
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

        p = mock.patch("core.ai.complete", complete)
        p.start()
        self.addCleanup(p.stop)

    def test_official_first_one_retry_then_the_push(self) -> None:
        bad = {"id": 1, "summary": "One. Two. Three.", "entities": [], "topics": [], "category": "pricing_credits",
               "claimable": True}
        good = {**bad, "summary": "New OpenAI API accounts receive free credits for a month."}
        hn = {"id": 2, "summary": "A user shares a project.", "entities": [], "topics": [], "category": "agents",
              "claimable": False}
        self.answers = [{"stories": [bad, hn]}, {"stories": [good]}]
        sent: list[push.Message] = []
        run = tech.tech_summarize_job(send=lambda m: sent.append(m) or [SimpleNamespace(notification_id=3)])
        self.assertEqual((run.accepted, run.rejected, run.retried, run.pushes), (2, 0, 1, 1))
        self.assertEqual([m.deep_link for m in sent], ["/tech/story/1"])
        retried = self.saved[1][0]
        self.assertEqual((retried[0].story_id, retried[0].attempts, retried[0].category), (1, 2, "pricing_credits"))
        self.assertEqual(self.saved[0][0][0].category, "community")  # HN

    def test_the_queue_puts_official_first(self) -> None:
        sql = ledger.FEED_TECH_SUMMARY_QUEUE_SQL
        self.assertRegex(sql, r"ORDER BY s\.official DESC, i\.published_at DESC")
        self.assertIn("'tech' = ANY (st.pillars)", sql)
        self.assertIn("NOT ('tech' = ANY (st.pillars))", ledger.FEED_SUMMARY_QUEUE_SQL)


# ====================================================================== the brief

class TechBriefSection(unittest.TestCase):
    """Owner: one Tech line, only when an official item landed in 24 h;
    otherwise the section is silent (db/045 ctx_tech_brief returns NULL)."""

    SECTIONS = [ledger.BriefSection("goals", "Goals", 10), ledger.BriefSection("tech_brief", "Tech", 50)]
    PAYLOAD = {"local_date": "2026-10-14", "weekday": "Wednesday", "iso_week": 42,
               "goals": {"week": {"held": 2, "done": 1, "goals": [{"title": "Ship tech", "state": "open"}]}}}

    def setUp(self) -> None:
        self.stored: list[dict] = []
        self.prompts: list[str] = []
        for name, fn in {
            "brief_sections": lambda **kw: list(self.SECTIONS),
            "insert_brief": lambda **kw: self.stored.append(kw) or 1,
        }.items():
            p = mock.patch.object(ledger, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def build(self, payload: dict, answer: dict) -> dict:
        from core import brief

        def complete(**kw):
            self.prompts.append(kw["messages"][0]["content"])
            kw["on_recorded"](1, "claude-sonnet-5-5", 0.0)
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(answer))])

        with mock.patch.object(ledger, "context_snapshot", lambda **kw: payload), \
                mock.patch("core.ai.complete", complete):
            brief.build_today(allow_fallback=False)
        return self.stored[-1]["body"]

    def test_silent_when_the_provider_returns_null(self) -> None:
        body = self.build({**self.PAYLOAD, "tech_brief": None},
                          {"headline": "A day", "one_move": "Do it.",
                           "sections": [{"key": "goals", "title": "x", "lines": ["Goals: 1 of 2 done."]}]})
        self.assertNotIn("tech_brief", self.prompts[0])  # never offered to the model
        self.assertEqual([s["key"] for s in body["sections"]], ["goals"])

    def test_one_line_when_an_official_item_landed(self) -> None:
        landed = {"official_24h": [{"title": "Claude Haiku 5.5 launch", "source": "Claude apps release notes"},
                                   {"title": "Ultrafast mode", "source": "OpenAI API changelog"}]}
        body = self.build({**self.PAYLOAD, "tech_brief": landed},
                          {"headline": "A day", "one_move": "Do it.",
                           "sections": [{"key": "goals", "title": "x", "lines": ["Goals: 1 of 2 done."]},
                                        {"key": "tech_brief", "title": "x", "lines": ["One.", "Two."]}]})
        tech_section = next(s for s in body["sections"] if s["key"] == "tech_brief")
        self.assertEqual((tech_section["title"], tech_section["lines"]), ("Tech", ["One."]))

    def test_the_fallback_writes_one_line(self) -> None:
        from core import brief
        landed = {"official_24h": [{"title": "Claude Haiku 5.5 launch", "source": "Claude apps release notes"},
                                   {"title": "Ultrafast mode", "source": "OpenAI API changelog"}]}
        made = brief.fallback_brief({**self.PAYLOAD, "tech_brief": landed}, self.SECTIONS)
        lines = next(s for s in made.body["sections"] if s["key"] == "tech_brief")["lines"]
        self.assertEqual(lines, ["Official: Claude Haiku 5.5 launch (Claude apps release notes). Plus 1 more."])


# ====================================================================== ingest

def tech_source(sid: int, slug: str, *, kind: str = "rss", first: bool = False, official: bool = True,
                config: dict | None = None, url: str | None = None) -> ledger.FeedSource:
    s = source(sid, slug, kind=kind, first=first, pillars=("tech",))
    return ledger.FeedSource(s.id, s.slug, s.name, s.pillars, None, kind, url or s.url, s.priority, None, None,
                             None, s.last_ok_at, 0, False, False, 2, official, config)


class TechFeedLedger(FakeFeedLedger):
    """Knows which stories are tech, as feed_candidates does (db/045)."""

    def feed_candidates(self, hours, **kw):
        return [ledger.CandidateStory(sid, s["count"], tuple(s["members"]), s.get("tech", False))
                for sid, s in self.stories.items()]

    def feed_new_story(self, src, it, vec, title_vec, **kw):
        made = super().feed_new_story(src, it, vec, title_vec, **kw)
        self.stories[made[0]]["tech"] = "tech" in src.pillars
        return made


class TechIngest(IngestCase):
    def setUp(self) -> None:
        super().setUp()
        self.db = TechFeedLedger()
        self.db.install(self)
        self.kwargs: dict[str, dict] = {}

    def get(self, url: str, headers: dict, **kw) -> ingest.Response:
        self.kwargs[url] = kw
        return super().get(url, headers)

    def test_a_page_source_is_streamed_capped_and_parsed(self) -> None:
        cfg = {"mode": "sections", "heading": ["h3"], "max_bytes": 300000}
        s = tech_source(1, "claude-apps-notes", kind="page", config=cfg,
                        url="https://support.claude.com/en/articles/12138966-release-notes")
        self.feeds[s.url] = (FIX / "claude_apps_notes.html").read_bytes()
        stats = self.run_ingest().run_source(s)
        self.assertEqual(self.kwargs[s.url], {"max_bytes": 300000})
        self.assertGreater(stats.new_stories, 0)
        items = [v["item"] for v in self.db.items.values() if "item" in v]
        self.assertTrue(all(i.precision == "date" and "#h_" in i.url for i in items))
        self.assertFalse(any(i.backfill for i in items))  # not a first fetch

    def test_a_tech_feed_is_read_head_first(self) -> None:
        s = tech_source(2, "vercel")
        self.feeds[s.url] = rss(rss_item("Golf", "https://v.invalid/1", guid="1"))
        self.run_ingest().run_source(s)
        self.assertEqual(self.kwargs[s.url], {"max_bytes": ingest.TECH_FEED_MAX_BYTES, "head": MAX_ITEMS})
        home = source(3, "bloomberg")
        self.feeds[home.url] = rss(rss_item("Golf", "https://b.invalid/1", guid="1"))
        self.run_ingest().run_source(home)
        self.assertEqual(self.kwargs[home.url], {})  # Home's fetch is unchanged

    def test_the_first_fetch_backfills_the_newest_five_and_marks_them(self) -> None:
        s = tech_source(4, "mcp-blog", first=True)
        self.feeds[s.url] = rss(*[rss_item(f"Post {i}", f"https://m.invalid/{i}", guid=str(i),
                                           when=NOW - timedelta(days=10 + i)) for i in range(8)])
        stats = self.run_ingest().run_source(s)
        self.assertEqual(stats.new_stories + stats.joined, ingest.BACKFILL)
        items = [v["item"] for v in self.db.items.values() if "item" in v]
        self.assertEqual(sorted(i.external_id for i in items), ["0", "1", "2", "3", "4"])
        self.assertTrue(all(i.backfill for i in items))

    def test_a_tech_item_never_joins_a_home_story(self) -> None:
        home, t = source(5, "bloomberg"), tech_source(6, "openai-news")
        title = "OpenAI hotel deal announced in Miami"  # one embedding axis ("hotel"), the same title
        self.feeds[home.url] = rss(rss_item(title, "https://b.invalid/x", guid="x"))
        self.feeds[t.url] = rss(rss_item(title, "https://o.invalid/x", guid="x"))
        run = self.run_ingest()
        run.run_source(home)
        run.run_source(t)
        self.assertEqual(len(self.db.stories), 2)
        self.assertEqual(self.db.joins, [])
        self.assertEqual(sorted(s["tech"] for s in self.db.stories.values()), [False, True])


class Fetcher(unittest.TestCase):
    """The streamed read stops early: it never holds more than it keeps."""

    def stream_client(self, chunks: list[bytes], status: int = 200):
        class R:
            status_code = status
            headers = {"etag": '"e"'}

            def iter_bytes(self, n):
                yield from chunks

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        client = SimpleNamespace(stream=lambda method, url, headers: R(), get=None, close=lambda: None)
        f = ingest.Fetcher.__new__(ingest.Fetcher)
        f.client = client
        return f

    def test_head_cut(self) -> None:
        atom = (FIX / "claude_code_releases.atom").read_bytes()
        chunks = [atom[i:i + 4096] for i in range(0, len(atom), 4096)]
        r = self.stream_client(chunks)("https://x.invalid", {}, head=2)
        self.assertEqual(len(parse_feed(r.content)), 2)
        self.assertLess(len(r.content), len(atom))

    def test_byte_cap(self) -> None:
        r = self.stream_client([b"a" * 70_000] * 10)("https://x.invalid", {}, max_bytes=100_000)
        self.assertEqual(len(r.content), 100_000)
        self.assertEqual(r.etag, '"e"')


if __name__ == "__main__":
    unittest.main()
