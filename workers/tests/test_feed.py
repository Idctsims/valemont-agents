"""The feed (Chat 3 Phase 1): parsing, URL canonicalization, story assignment,
the drop rules, the summary validator, core/embed.py's retries and loud
failure, one failing source never failing the run, the one-time push after 6
failures, seed-and-skip, and the ingest -> summarize chain.

A stubbed feed ledger and injected fetch/decode/embed/push: no database, no
network, no Claude.
"""

from __future__ import annotations

import itertools
import json
import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest import mock

from core import ai, embed, jobs, ledger, push, system_jobs
from feed import cluster, ingest, summarize
from feed.parse import ParseFailed, canonical_url, keeps_category, parse_feed, strip_html
from feed.rules import Rule, Rules, SeedPhrase, best_seed
from feed.sources import SourcesInvalid, _spec, gnews_url, load_sources

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def rfc822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def rss(*items: str, title: str = "Feed") -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{title}</title>'
            + "".join(items) + "</channel></rss>").encode()


def rss_item(title: str, link: str, *, guid: str | None = None, when: datetime | None = NOW,
             desc: str = "", cats: tuple[str, ...] = ()) -> str:
    return ("<item>" + f"<title>{title}</title><link>{link}</link>"
            + (f"<guid>{guid}</guid>" if guid else "")
            + (f"<pubDate>{rfc822(when)}</pubDate>" if when else "")
            + (f"<description>{desc}</description>" if desc else "")
            + "".join(f"<category>{c}</category>" for c in cats) + "</item>")


def unit(i: int, n: int = 384) -> list[float]:
    """A unit vector along axis i."""
    v = [0.0] * n
    v[i % n] = 1.0
    return v


def mix(a: list[float], b: list[float], w: float) -> list[float]:
    return [x * (1 - w) + y * w for x, y in zip(a, b)]


def cosine_of(a, b) -> float:
    return embed.cosine(a, b)


# ===================================================================== parsing

class Parsing(unittest.TestCase):
    def test_rss_2_0(self) -> None:
        items = parse_feed(rss(rss_item("Texans trade for Harrison", "https://espn.com/a",
                                        guid="g1", desc="<p>The <b>Texans</b> made a deal.</p>", cats=("NFL",))))
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual((it.external_id, it.link, it.title), ("g1", "https://espn.com/a", "Texans trade for Harrison"))
        self.assertEqual(it.teaser, "The Texans made a deal.")
        self.assertEqual(it.published_at, NOW)
        self.assertEqual(it.categories, ("NFL",))

    def test_atom(self) -> None:
        atom = b"""<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"><title>PFT</title>
          <entry><title>Stroud on Harrison</title><id>tag:pft,1</id>
            <link href="https://www.nbcsports.com/x?utm_source=rss"/>
            <updated>2026-10-12T11:00:00Z</updated><summary>It is perfect for him.</summary></entry>
        </feed>"""
        it = parse_feed(atom)[0]
        self.assertEqual(it.external_id, "tag:pft,1")
        self.assertEqual(it.published_at, datetime(2026, 10, 12, 11, 0, tzinfo=timezone.utc))
        self.assertEqual(canonical_url(it.link), "https://www.nbcsports.com/x")

    def test_malformed_xml_with_recoverable_items_still_parses(self) -> None:
        broken = rss(rss_item("Good item", "https://a.example/1", guid="1")).replace(b"</channel></rss>", b"<item><title>cut")
        self.assertEqual([i.title for i in parse_feed(broken)][:1], ["Good item"])

    def test_html_or_garbage_is_not_a_feed(self) -> None:
        with self.assertRaises(ParseFailed):
            parse_feed(b"<html><body>Access denied</body></html>")
        with self.assertRaises(ParseFailed):
            parse_feed(b"\x00\x01 not xml at all")

    def test_an_empty_feed_is_empty_not_an_error(self) -> None:
        self.assertEqual(parse_feed(rss()), [])

    def test_non_utf8_feed_decodes(self) -> None:
        body = ('<?xml version="1.0" encoding="ISO-8859-1"?><rss version="2.0"><channel><title>F</title>'
                '<item><title>Pérez crashes in qualifying</title><link>https://a.example/p</link></item>'
                '</channel></rss>').encode("iso-8859-1")
        self.assertEqual(parse_feed(body)[0].title, "Pérez crashes in qualifying")

    def test_at_most_50_items_and_items_without_title_or_id_skipped(self) -> None:
        many = rss(*[rss_item(f"Item {i}", f"https://a.example/{i}") for i in range(70)],
                   "<item><description>no title</description></item>")
        self.assertEqual(len(parse_feed(many)), 50)

    def test_google_news_titles_lose_the_outlet_and_the_teaser(self) -> None:
        it = parse_feed(rss(rss_item("Kyrie sparks Dallas - NBA.com", "https://news.google.com/rss/articles/x",
                                     desc='<a href="x">Kyrie sparks Dallas</a> NBA.com')), gnews=True)[0]
        self.assertEqual(it.title, "Kyrie sparks Dallas")
        self.assertIsNone(it.teaser)

    def test_teaser_is_stripped_and_capped(self) -> None:
        self.assertEqual(len(strip_html("<p>" + "word " * 300 + "</p>", 500)), 500)
        self.assertIsNone(strip_html("<br/> ", 500))

    def test_categories(self) -> None:
        it = parse_feed(rss(rss_item("x", "https://a.example/x", cats=("Business",))))[0]
        self.assertTrue(keeps_category(it, ("business", "Media")))
        self.assertFalse(keeps_category(it, ("Politics",)))
        self.assertTrue(keeps_category(it, None))


class Canonicalization(unittest.TestCase):
    def test_tracking_parameters_and_fragment_go(self) -> None:
        self.assertEqual(
            canonical_url("https://www.espn.com/nfl/story?id=1&utm_source=rss&utm_medium=x&fbclid=abc#comments"),
            "https://www.espn.com/nfl/story?id=1")

    def test_host_and_scheme_lowercased_default_port_dropped(self) -> None:
        self.assertEqual(canonical_url("HTTPS://WWW.Variety.COM:443/2026/Film/X"), "https://www.variety.com/2026/Film/X")
        self.assertEqual(canonical_url("http://a.example:8080/p"), "http://a.example:8080/p")

    def test_kept_parameters_keep_their_order(self) -> None:
        self.assertEqual(canonical_url("https://a.example/p?b=2&gclid=z&a=1"), "https://a.example/p?b=2&a=1")

    def test_not_a_web_url(self) -> None:
        for bad in (None, "", "mailto:x@y.z", "javascript:alert(1)", "not a url"):
            self.assertIsNone(canonical_url(bad))


class SourcesFile(unittest.TestCase):
    def test_the_real_file_loads_with_46_active_of_50(self) -> None:
        specs = load_sources()
        self.assertEqual(len(specs), 50)
        self.assertEqual(sum(s.active for s in specs), 46)
        by = {s.slug: s for s in specs}
        self.assertEqual(by["semafor-business"].include_categories, ("Business", "Media"))
        self.assertIn("when%3A7d", by["the-real-deal-miami"].url)
        boat = by["boat-international"]
        self.assertEqual(boat.window_days, 7)
        for path in ("yachts%2Fnews", "yachts%2Freports", "yachts%2Feditorial-features"):
            self.assertIn(f"site%3Aboatinternational.com%2F{path}", boat.url)
        self.assertEqual(boat.url.count("+OR+"), 2)
        self.assertGreater(by["the-real-deal-miami"].priority, by["bisnow-south-florida"].priority)
        for slug in ("strictlyvc", "the-next-miami", "axios-pro-rata", "no-laying-up"):
            self.assertFalse(by[slug].active, slug)
            self.assertTrue(by[slug].notes, slug)

    def test_rules(self) -> None:
        with self.assertRaises(SourcesInvalid):
            _spec({"slug": "x", "name": "X", "pillars": ["news"], "kind": "rss", "url": "https://x"})
        with self.assertRaises(SourcesInvalid):
            _spec({"slug": "x", "name": "X", "pillars": ["home"], "kind": "gnews", "url": "https://x"})
        self.assertTrue(gnews_url("site:a.com", 2).startswith("https://news.google.com/rss/search?q=site%3Aa.com+when%3A2d"))


# ================================================================== clustering

def at_cos(c: float, base: int = 0, other: int = 1) -> list[float]:
    """A unit vector whose cosine to unit(base) is exactly c."""
    v = [0.0] * 384
    v[base], v[other] = c, (1 - c * c) ** 0.5
    return v


def story(sid: int, *members: tuple[str, list[float]], count: int | None = None) -> cluster.Candidate:
    return cluster.Candidate(sid, count if count is not None else len(members),
                             tuple(cluster.Member(t, tuple(v)) for t, v in members))


class Assignment(unittest.TestCase):
    def test_thresholds_are_the_owners(self) -> None:
        self.assertEqual((cluster.JOIN_COSINE, cluster.JOIN_FUZZ, cluster.FUZZ_MIN_COSINE, cluster.NEAR_MISS_FROM,
                          cluster.CANDIDATE_HOURS, cluster.MAX_ITEMS), (0.94, 90.0, 0.90, 0.88, 48, 25))

    def test_joins_on_cosine_at_094_not_below(self) -> None:
        s = story(7, ("Verstappen wins first Singapore GP from pole", unit(0)))
        self.assertEqual(cluster.assign(at_cos(0.95), "Different words entirely", [s]).joined_by, "cosine")
        self.assertIsNone(cluster.assign(at_cos(0.93), "Different words entirely", [s]).story_id)

    def test_fuzz_joins_only_with_cosine_090(self) -> None:
        s = story(7, ("Texans trade for Cardinals' Harrison, reunite WR with QB Stroud", unit(0)))
        same_words = "Texans trade for Cardinals Harrison reunite WR with QB Stroud"
        self.assertEqual(cluster.assign(at_cos(0.91), same_words, [s]).joined_by, "fuzz")
        # The FURYK case: a title that is a subset scores fuzz 100, but a low
        # cosine keeps it out.
        furyk = story(8, ("Constellation FURYK & FRIENDS", unit(0)))
        self.assertIsNone(cluster.assign(at_cos(0.87), "Vaughn Taylor makes birdie on No. 13 at Constellation FURYK & FRIENDS",
                                         [furyk]).story_id)

    def test_the_best_member_counts_not_the_first(self) -> None:
        s = story(7, ("old angle", unit(5)), ("Verstappen takes pole in Singapore", unit(0)))
        d = cluster.assign(at_cos(0.96), "Verstappen on pole", [s])
        self.assertEqual(d.story_id, 7)
        self.assertAlmostEqual(d.best.cosine, 0.96, places=6)

    def test_no_drift_an_item_like_the_centroid_but_no_member_does_not_join(self) -> None:
        # Members along two axes; their centroid sits between them. An item on
        # that centroid scores cosine 1.0 against it, but only ~0.71 against
        # either member: under the member rule it founds its own story.
        a, b = unit(0), unit(1)
        s = story(7, ("Story about A", a), ("Story about B", b))
        centroid = [(x + y) / (2 ** 0.5) for x, y in zip(a, b)]  # unit length
        self.assertAlmostEqual(cosine_of(centroid, cluster.running_mean(a, 1, b)), 1.0, places=6)
        d = cluster.assign(centroid, "A generic headline", [s])
        self.assertIsNone(d.story_id)

    def test_chaining_within_the_window_goes_through_a_member(self) -> None:
        # A ~ B (0.96), B ~ C (0.96), A unlike C (~0.84): C joins only because
        # B is a member. The 48-hour window that ends such chains is SQL
        # (FEED_CANDIDATES_SQL, on first_seen_at): tests_live pins it.
        a = unit(0)
        b = at_cos(0.96, 0, 1)
        c = [0.0] * 384
        # c: cos(b, c) = 0.96, built in the a-b plane plus a third axis.
        import math
        theta_ab = math.acos(0.96)
        c[0], c[1] = math.cos(2 * theta_ab), math.sin(2 * theta_ab)
        self.assertLess(cosine_of(a, c), cluster.JOIN_COSINE)
        self.assertAlmostEqual(cosine_of(b, c), 0.96, places=6)
        only_a = story(7, ("A headline", a))
        self.assertIsNone(cluster.assign(c, "C headline", [only_a]).story_id)
        with_b = story(7, ("A headline", a), ("B headline", b))
        self.assertEqual(cluster.assign(c, "C headline", [with_b]).story_id, 7)

    def test_a_full_story_overflows_into_a_new_one(self) -> None:
        full = story(7, ("Singapore GP result", unit(0)), count=cluster.MAX_ITEMS)
        d = cluster.assign(at_cos(0.99), "Singapore GP result again", [full])
        self.assertIsNone(d.story_id)
        self.assertEqual(d.overflow.story_id, 7)
        # With room in another eligible story, it joins that one instead.
        roomy = story(8, ("Singapore GP result, other outlet", unit(0)))
        d = cluster.assign(at_cos(0.97), "Singapore GP result again", [full, roomy])
        self.assertEqual((d.story_id, d.overflow), (8, None))

    def test_near_misses_are_088_to_094_not_joined(self) -> None:
        near = story(3, ("Something else", unit(0)))
        far = story(4, ("Unrelated", unit(9)))
        d = cluster.assign(at_cos(0.90), "A new headline", [near, far])
        self.assertIsNone(d.story_id)
        self.assertEqual([s.story_id for s in d.near_misses], [3])
        self.assertIsNone(cluster.assign(at_cos(0.87), "A new headline", [near]).near_misses or None)

    def test_the_best_eligible_story_wins(self) -> None:
        a = story(1, ("a", at_cos(0.95)))
        b = story(2, ("b", unit(0)))
        self.assertEqual(cluster.assign(unit(0), "x", [a, b]).story_id, 2)

    def test_lead_is_lowest_priority_then_earliest_then_id(self) -> None:
        t0 = NOW
        current = cluster.LeadKey(30, t0, 5)
        self.assertTrue(cluster.better_lead(cluster.LeadKey(10, t0 + timedelta(hours=1), 9), current))
        self.assertTrue(cluster.better_lead(cluster.LeadKey(30, t0 - timedelta(minutes=1), 9), current))
        self.assertFalse(cluster.better_lead(cluster.LeadKey(30, t0, 6), current))
        self.assertTrue(cluster.better_lead(cluster.LeadKey(99, t0, 1), None))

    def test_running_mean(self) -> None:
        self.assertEqual(cluster.running_mean([1.0, 0.0], 1, [0.0, 1.0]), [0.5, 0.5])
        self.assertEqual(cluster.running_mean([0.5, 0.5], 2, [1.0, 1.0]), [2 / 3, 2 / 3])


# ======================================================================= rules

RULES = [Rule(1, "title_regex", None, "box score"), Rule(2, "title_regex", "sports", "odds, picks"),
         Rule(3, "url_regex", None, r"^https?://(pro\.axios\.com|(www\.)?axios\.com/pro/)"),
         Rule(4, "seed_phrase_drop", None, "celebrity gossip"),
         Rule(5, "title_regex", None, "where to watch")]


class FilterRules(unittest.TestCase):
    def test_title_and_url_rules(self) -> None:
        r = Rules(RULES)
        self.assertEqual(r.filter_reason(title="Bears vs Packers BOX SCORE", url=None, pillars=["sports"]), "title_regex:1")
        self.assertEqual(r.filter_reason(title="Where to Watch the 49ers", url=None, pillars=["home"]), "title_regex:5")
        self.assertEqual(r.filter_reason(title="Deals", url="https://www.axios.com/pro/all-deals/x", pillars=["home"]),
                         "url_regex:3")
        self.assertIsNone(r.filter_reason(title="Axios Miami: a new tower", url="https://www.axios.com/local/miami/x",
                                          pillars=["home"]))

    def test_a_pillar_rule_applies_only_to_its_pillar(self) -> None:
        r = Rules(RULES)
        self.assertEqual(r.filter_reason(title="Ravens vs Falcons Predictions, Odds, Picks", url=None,
                                         pillars=["sports"]), "title_regex:2")
        self.assertIsNone(r.filter_reason(title="Ravens vs Falcons Predictions, Odds, Picks", url=None, pillars=["home"]))

    def test_seed_phrases_never_filter_and_record_only_from_070(self) -> None:
        r = Rules(RULES)
        self.assertEqual([x.id for x in r.seed_rules], [4])
        self.assertIsNone(r.filter_reason(title="celebrity gossip", url=None, pillars=["home"]))
        seeds = [SeedPhrase(4, "celebrity gossip", tuple(unit(0)))]
        self.assertEqual(best_seed(unit(0), seeds), (1.0, 4))
        self.assertIsNone(best_seed(mix(unit(0), unit(1), 0.6), seeds))  # cosine ~0.55

    def test_db034_rules_drop_promos_listings_and_clips_never_regulation(self) -> None:
        import re
        from core.paths import REPO_ROOT
        sql = (REPO_ROOT / "db" / "034_feed_reset.sql").read_text(encoding="utf-8")
        rows = re.findall(r"\('title_regex',\s+NULL,\s+'((?:[^']|'')*)'", sql)
        self.assertEqual(len(rows), 8)
        rx = [re.compile(p.replace("''", "'"), re.I) for p in rows]
        drop = ["Ravens vs. Falcons Betting Promos: Odds, Prediction, How to Watch Sunday Night Football",
                "NFL Betting Promos for Week 5: Claim Thousands in Bonus Value Today",
                "bet365 Bonus Code: Get $200 Bonus Bets for NFL Week 5 Sunday",
                "Ravens vs. Falcons Prediction Market Promos: Best Welcome Offers for SNF Week 5",
                "Kalshi Promo Code ACTION55: Claim $55 Bonus for Bears vs. Packers",
                "Kalshi referral code: get $25",
                "Sunshot yacht (Sunreef Yachts, 23.95m, 2025)",
                "Ad Astra yacht (Fountaine Pajot Yachts, 23.99m, 2024)",
                "Rod Pampling makes birdie on No. 10 at Constellation FURYK & FRIENDS"]
        keep = ["Ohio bans sportsbook promos aimed at college students",
                "Kalshi wins appeal over sports event contracts in Nevada",
                "CFTC weighs new rules for prediction markets",
                "DraftKings Sued By Seminole Tribe Over Sports Predictions In Florida",
                "Boat International: the superyacht market's record quarter",
                "Michael Brennan makes history with third PGA Tour win",
                # Yacht news survives: only the listing format
                # "Name yacht (Builder, NN.NNm, YYYY)" is a listing (owner, 2026-10-11).
                "Sunreef sells 40m catamaran to American owner",
                "Feadship delivers 87m superyacht Project 821",
                "Feadship delivers 87m yacht (Project 821) to its owner",
                "Lürssen launches 125m yacht Project Jag in Bremen",
                "First look inside first unit of ISA Yachts 40m Viper 130 Fast Naan",
                "Benetti sells 50m yacht (B.Now) ahead of Monaco show",
                "Why the 23.95m Sunreef 80 is selling out"]
        for t in drop:
            self.assertTrue(any(r.search(t) for r in rx), t)
        for t in keep:
            self.assertFalse(any(r.search(t) for r in rx), t)

    def test_db036_rules_drop_reviews_never_business_reviews(self) -> None:
        import re
        from core.paths import REPO_ROOT
        sql = (REPO_ROOT / "db" / "036_feed_review_rules.sql").read_text(encoding="utf-8")
        rows = re.findall(r"\('title_regex',\s+NULL,\s+'((?:[^']|'')*)'", sql)
        self.assertEqual(len(rows), 3)
        rx = [re.compile(p.replace("''", "'"), re.I) for p in rows]
        drop = ["Review: 'Hamnet' is a quiet triumph",
                "‘The Sea Speaks His Name’ Review: Absorbing Portrait of a Fisherman",
                "'Frankenstein' Review: Guillermo del Toro's Monster Is Tender",
                "Taylor Swift’s ‘The Life of a Showgirl’ Review",
                "Bad Bunny 'Debí Tirar Más Fotos' Review"]
        keep = ["Paramount Board Launches Strategic Review",
                "Deal Under Regulatory Review",
                "Warner Bros. Discovery Faces Antitrust Review",
                "Warner Bros.' Merger Faces FTC Review",
                "Variety's Year in Review: Box Office Up 12%",
                "Trump's tariff review: what it means for retailers",
                "Netflix Q3 earnings review"]
        for t in drop:
            self.assertTrue(any(r.search(t) for r in rx), t)
        for t in keep:
            self.assertFalse(any(r.search(t) for r in rx), t)

    def test_db037_junk_rules(self) -> None:
        import re
        from core.paths import REPO_ROOT
        sql = (REPO_ROOT / "db" / "037_feed_junk_rules.sql").read_text(encoding="utf-8")
        rules = re.findall(r"\('(title_regex|url_regex)',\s+NULL,\s+'((?:[^']|'')*)'", sql)
        self.assertEqual(len(rules), 4)
        url_rx = [re.compile(p.replace("''", "'"), re.I) for k, p in rules if k == "url_regex"]
        title_rx = [re.compile(p.replace("''", "'"), re.I) for k, p in rules if k == "title_regex"]
        # Complex: shop, store and tag pages go; articles stay.
        for u in ("https://www.complex.com/shop/products/uprs-la-rise-white-t-shirt",
                  "https://www.complex.com/shop/products/uprs-la-rise-black-t-shirt?Color=BLACK",
                  "https://www.complex.com/shop/artists/Sabrina%20Carpente",
                  "https://www.complex.com/tag/rivian", "https://complex.com/tags/presley-gerber",
                  "https://www.complex.com/store"):
            self.assertTrue(any(r.search(u) for r in url_rx), u)
        for u in ("https://www.complex.com/music/a/treyalston/megan-thee-stallion-miso-adopt",
                  "https://www.complex.com/pop-culture/a/markelibert/kelly-clarkson-art-play-brooklyn-pretty-woman",
                  "https://www.complex.com/sports/a/markelibert/tony-parker-chateau-joakim-noah-tour",
                  "https://www.complex.com/style/a/x/shopping-season-sneakers",
                  "https://www.notcomplex.com/tag/x"):
            self.assertFalse(any(r.search(u) for r in url_rx), u)
        drop = ["- NFL Communications", "NFL Communications", "Latest News", "Videos",
                "“Severance” Season 3 Review: The Office Strikes Back",
                "'Hamnet' Movie Review: Quiet Grief",
                "Netflix's 'Wednesday' Season 2 Part 1 Review: Darker",
                "‘The Sea Speaks His Name’ Review: Absorbing"]
        keep = ["Paramount Board Launches Strategic Review",
                "Deal Under Regulatory Review",
                "Warner Bros. Discovery Faces Antitrust Review",
                "Variety's Year in Review: Box Office Up 12%",
                "Year in Review: The Deals That Shaped Sports Business",
                "Disney's board and Iger's succession review: what comes next",
                "NFL Communications chief steps down after 12 years",
                "Sports business news: Fenway buys stake in PGA Tour Enterprises",
                "Kalshi's 'sports event contracts' face a new CFTC review"]
        for t in drop:
            self.assertTrue(any(r.search(t) for r in title_rx), t)
        for t in keep:
            self.assertFalse(any(r.search(t) for r in title_rx), t)

    def test_db038_drops_boat_international_directory_pages_never_news(self) -> None:
        import re
        from core.paths import REPO_ROOT
        sql = (REPO_ROOT / "db" / "038_feed_boat_rules.sql").read_text(encoding="utf-8")
        (pattern,) = re.findall(r"\('url_regex',\s+NULL,\s+'((?:[^']|'')*)'", sql)
        rx = re.compile(pattern.replace("''", "'"), re.I)
        rules = Rules([Rule(9, "url_regex", None, pattern.replace("''", "'"))])
        drop = [("Wajer 77", "https://www.boatinternational.com/yachts/the-superyacht-directory/wajer-77--16833"),
                ("Sunreef 80 Power", "https://www.boatinternational.com/yachts/the-superyacht-directory/sunreef-80-power--16812"),
                ("Azimut 78 Fly", "https://www.boatinternational.com/yachts/the-superyacht-directory/azimut-78-fly--16670"),
                ("Blessed yacht (Sunreef Yachts, 23.95m, 2023)",
                 "https://www.boatinternational.com/yachts/the-superyacht-directory/blessed--16818"),
                ("Fountaine Pajot Yachts Yachts For Sale and Charter",
                 "https://www.boatinternational.com/profiles/fountaine-pajot-yachts--17883")]
        keep = [("Feadship launches 87m Project 1012",
                 "https://www.boatinternational.com/yachts/news/feadship-launches-87m-project-1012"),
                ("Sunreef sells 40m catamaran to American owner",
                 "https://www.boatinternational.com/yachts/news/sunreef-sells-40m-catamaran-american-owner"),
                ("First look inside first unit of ISA Yachts 40m Viper 130 Fast Naan",
                 "https://www.boatinternational.com/yachts/news/isa-yachts-40m-viper-130-first-look-fast-naan"),
                ("Angiola Maiora review", "https://www.boatinternational.com/yachts/reports/angiola-maiora-36-exuma-yacht-review"),
                ("Superyacht sabbaticals",
                 "https://www.boatinternational.com/yachts/editorial-features/superyacht-sabbatical-long-term-yacht-tr")]
        for title, url in drop:
            self.assertTrue(rx.search(url), url)
            self.assertEqual(rules.filter_reason(title=title, url=url, pillars=["home"]), "url_regex:9")
        for title, url in keep:
            self.assertFalse(rx.search(url), url)
            self.assertIsNone(rules.filter_reason(title=title, url=url, pillars=["home"]), title)

    def test_every_seeded_db032_pattern_compiles_in_python(self) -> None:
        import re
        from core.paths import REPO_ROOT
        sql = (REPO_ROOT / "db" / "032_feed.sql").read_text(encoding="utf-8")
        rows = re.findall(r"\('(title_regex|url_regex)',\s+NULL,\s+'((?:[^']|'')*)'", sql)
        self.assertEqual(len(rows), 24)
        for _kind, pattern in rows:
            re.compile(pattern.replace("''", "'"), re.I)


# =================================================================== summaries

def job(sid: int, title: str = "Texans trade for Cardinals' Harrison", teaser: str | None = None) -> ledger.SummaryJob:
    return ledger.SummaryJob(sid, title, teaser, "ESPN NFL", "nfl", 0)


GOOD = {"summary": "Houston acquired the receiver from Arizona, pairing him again with his college quarterback.",
        "entities": ["Houston Texans", "Marvin Harrison Jr."], "topics": ["Trade", "nfl trade"], "sport": "nfl",
        "drop": "none"}


class SummaryValidator(unittest.TestCase):
    TEASER = "The Houston Texans traded for Arizona Cardinals receiver Marvin Harrison Jr. on Sunday, reuniting him with C.J. Stroud."

    def test_a_good_summary(self) -> None:
        v = summarize.validate(dict(GOOD, id=1), job(1, teaser=self.TEASER))
        self.assertEqual(v.topics, ("trade", "nfl_trade"))
        self.assertEqual(v.sport, "nfl")
        self.assertEqual(v.drop, "none")

    def test_too_long(self) -> None:
        self.assertEqual((summarize.MAX_WORDS, summarize.MAX_SENTENCES), (35, 2))
        summarize.validate(dict(GOOD, summary=" ".join(["word"] * 35) + "."), job(1))
        with self.assertRaisesRegex(summarize.SummaryInvalid, "36 words; the cap is 35"):
            summarize.validate(dict(GOOD, summary=" ".join(["word"] * 36) + "."), job(1))
        self.assertIn("at most 35 words", summarize.SYSTEM)
        with self.assertRaisesRegex(summarize.SummaryInvalid, "sentences"):
            summarize.validate(dict(GOOD, summary="One. Two. Three."), job(1))

    def test_abbreviations_are_not_sentence_ends(self) -> None:
        self.assertEqual(summarize.sentence_count("The U.S. team beat Mexico. C.J. Stroud starts Sunday."), 2)
        self.assertEqual(summarize.sentence_count("Mr. Ross and Inc. filings show a loss"), 1)

    PROSE = ("Officials said the plan would move forward only after the board finished its review "
             "of the costs and the timeline.")

    def test_a_lifted_8_word_run_of_ordinary_prose_still_fails(self) -> None:
        copied = "The plan would move forward only after the board finished, officials said."
        with self.assertRaisesRegex(summarize.SummaryInvalid, "copies 8 words from the teaser"):
            summarize.validate(dict(GOOD, summary=copied), job(1, teaser=self.PROSE))
        seven = "The plan would move forward only after a vote, officials said."
        summarize.validate(dict(GOOD, summary=seven), job(1, teaser=self.PROSE))

    def test_names_and_numbers_are_not_measured(self) -> None:
        # The owner's case: a run of names and records is facts, not copying.
        teaser = "The Denver Broncos (2-2) and Los Angeles Chargers (3-1) meet Sunday in a game with playoff stakes."
        summary = "The Denver Broncos (2-2) and Los Angeles Chargers (3-1) play Sunday, and the result shapes the division."
        summarize.validate(dict(GOOD, summary=summary), job(1, teaser=teaser))
        self.assertEqual(summarize.measured_words("The Denver Broncos (2-2) and Los Angeles Chargers (3-1)"),
                         ["the", "and"])
        # A capitalized function word at a sentence start is still measured.
        self.assertEqual(summarize.measured_words("After the Texans won 31-7"), ["after", "the", "won"])

    def test_copying_the_headline_is_caught_too(self) -> None:
        title = "Why the plan would move forward only after the board finished"
        with self.assertRaisesRegex(summarize.SummaryInvalid, "headline"):
            summarize.validate(dict(GOOD, summary="The plan would move forward only after the board finished."),
                               job(1, title=title))

    def test_unknown_duplicate_and_missing_ids(self) -> None:
        group = [job(1, teaser=self.TEASER), job(2)]
        payload = {"stories": [dict(GOOD, id=1), dict(GOOD, id=1), dict(GOOD, id=99)]}
        results, problems, _ = summarize.results_for(group, payload)
        self.assertEqual([(r.story_id, r.summary is not None) for r in results], [(1, True), (2, False)])
        self.assertTrue(any("unknown story id 99" in p for p in problems))
        self.assertTrue(any("returned twice" in p for p in problems))

    def test_garbage_counts_an_attempt_for_every_story(self) -> None:
        results, _, _ = summarize.results_for([job(1), job(2)], None)
        self.assertTrue(all(r.summary is None for r in results))

    def test_drop_is_shadow_by_default_and_filters_only_when_live(self) -> None:
        self.assertFalse(summarize.DROP_LIVE)
        gossip = dict(GOOD, id=1, drop="gossip")
        (r,), _, _ = summarize.results_for([job(1)], {"stories": [gossip]})
        self.assertEqual((r.drop, r.filtered_reason), ("gossip", None))
        with mock.patch.object(summarize, "DROP_LIVE", True):
            (r,), _, _ = summarize.results_for([job(1)], {"stories": [gossip]})
            self.assertEqual(r.filtered_reason, "drop:gossip")
            (r,), _, _ = summarize.results_for([job(1)], {"stories": [dict(GOOD, id=1)]})
            self.assertIsNone(r.filtered_reason)

    def test_the_prompt_carries_the_owners_rules(self) -> None:
        for phrase in ("Kalshi", "moves money", "Never copy 8", "restates the headline", "ALWAYS none"):
            self.assertIn(phrase, summarize.SYSTEM)
        self.assertEqual(summarize.schema()["properties"]["stories"]["items"]["properties"]["drop"]["enum"],
                         list(summarize.DROPS))


class FakeResponse(SimpleNamespace):
    pass


class SummaryJobRun(unittest.TestCase):
    def setUp(self) -> None:
        self.saved: list[tuple[list, Any]] = []
        self.queue = [job(i, teaser="A short teaser.") for i in range(1, 13)]
        for name, fn in {
            "feed_summarized_today": lambda **kw: 0,
            "feed_summary_queue": lambda limit, max_attempts, **kw: self.queue[:limit],
            "feed_save_summaries": lambda results, usage, **kw: self.saved.append((list(results), usage)),
            "_pool": lambda *a, **k: (_ for _ in ()).throw(AssertionError("database reached")),
        }.items():
            p = mock.patch.object(ledger, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def _complete(self, **kw: Any) -> Any:
        stories = json.loads(kw["messages"][0]["content"].split("\n", 1)[1])
        kw["on_recorded"](555, kw["model"], 0)
        body = {"stories": [dict(GOOD, id=s["id"]) for s in stories]}
        return FakeResponse(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(body))])

    def test_groups_of_10_with_haiku_non_critical_thinking_off(self) -> None:
        calls = []
        with mock.patch.object(ai, "complete", lambda **kw: calls.append(kw) or self._complete(**kw)):
            r = summarize.summarize_job(); self.assertEqual((r.accepted, r.rejected, r.retried), (12, 0, 0))
        self.assertEqual([len(json.loads(c["messages"][0]["content"].split("\n", 1)[1])) for c in calls], [10, 2])
        for c in calls:
            self.assertEqual((c["model"], c["critical"], c["thinking"]), ("claude-haiku-5-5", False, {"type": "disabled"}))
            self.assertEqual(c["system"][0]["cache_control"], {"type": "ephemeral"})
            self.assertEqual(c["purpose"], "feed_summarize")
        self.assertEqual([u for _, u in self.saved], [555, 555])

    def test_caps(self) -> None:
        self.assertEqual((summarize.RUN_CAP, summarize.DAILY_CAP), (60, 600))
        with mock.patch.object(ledger, "feed_summarized_today", lambda **kw: 595), \
                mock.patch.object(ai, "complete", self._complete):
            r = summarize.summarize_job(); self.assertEqual((r.accepted, r.rejected), (5, 0))
        with mock.patch.object(ledger, "feed_summarized_today", lambda **kw: 600), \
                mock.patch.object(ai, "complete", side_effect=AssertionError("no call past the cap")):
            r = summarize.summarize_job(); self.assertEqual((r.accepted, r.rejected), (0, 0))

    # -- the targeted retry (owner, 2026-10-11) ------------------------------

    TEASER = "Officials said the plan would move forward only after the board finished its review of the costs."
    COPIED = "The plan would move forward only after the board finished, officials said."
    REWRITTEN = "Officials tied the project's next step to the board completing its cost check."

    def _scripted(self, first: dict[int, str], retry: dict[int, str], calls: list) -> Any:
        """First call: per story the summary in `first` (default GOOD). A
        REWRITE call: per story the summary in `retry`."""
        def complete(**kw: Any) -> Any:
            calls.append(kw)
            content = kw["messages"][0]["content"]
            stories = json.loads(content.split("STORIES:\n", 1)[1])
            table = retry if content.startswith("REWRITE") else first
            kw["on_recorded"](556 if content.startswith("REWRITE") else 555, kw["model"], 0)
            body = {"stories": [dict(GOOD, id=s["id"], summary=table.get(s["id"], GOOD["summary"])) for s in stories]}
            return FakeResponse(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(body))])
        return complete

    def test_a_copy_failure_is_retried_once_naming_the_phrase_and_can_pass(self) -> None:
        self.queue = [job(1, teaser=self.TEASER), job(2, teaser=self.TEASER)]
        calls: list = []
        with mock.patch.object(ai, "complete", self._scripted({1: self.COPIED}, {1: self.REWRITTEN}, calls)):
            r = summarize.summarize_job()
        self.assertEqual((r.accepted, r.rejected, r.retried, r.retry_passed), (2, 0, 1, 1))
        self.assertEqual(len(calls), 2)
        retry = calls[1]["messages"][0]["content"]
        self.assertTrue(retry.startswith("REWRITE"))
        sent = json.loads(retry.split("STORIES:\n", 1)[1])
        self.assertEqual([s["id"] for s in sent], [1])
        self.assertEqual(sent[0]["copied_phrase"], "the plan would move forward only after the")
        self.assertEqual(sent[0]["rejected_summary"], self.COPIED)
        self.assertEqual(calls[1]["system"], calls[0]["system"])  # same cached prompt
        # Two saves: the first call's stories with its ai_usage row, the
        # retried story with the retry's.
        (first, usage1), (second, usage2) = self.saved
        self.assertEqual(([x.story_id for x in first], usage1), ([2], 555))
        self.assertEqual(([x.story_id for x in second], usage2), ([1], 556))
        self.assertEqual((second[0].summary, second[0].attempts), (self.REWRITTEN, 2))
        self.assertEqual(first[0].attempts, 1)

    def test_a_retry_that_copies_again_is_final_for_the_story(self) -> None:
        self.queue = [job(1, teaser=self.TEASER)]
        calls: list = []
        with mock.patch.object(ai, "complete", self._scripted({1: self.COPIED}, {1: self.COPIED}, calls)):
            r = summarize.summarize_job()
        self.assertEqual((r.accepted, r.rejected, r.retried, r.retry_passed), (0, 1, 1, 0))
        (out,), _ = self.saved[-1]
        self.assertEqual((out.summary, out.attempts), (None, 2))

    def test_no_retry_for_other_failures_or_a_last_attempt(self) -> None:
        too_long = "word " * 50
        last_try = ledger.SummaryJob(2, "t", self.TEASER, "Variety", None, summarize.MAX_ATTEMPTS - 1)
        self.queue = [job(1, teaser=self.TEASER), last_try]
        calls: list = []
        with mock.patch.object(ai, "complete", self._scripted({1: too_long, 2: self.COPIED}, {}, calls)):
            r = summarize.summarize_job()
        self.assertEqual(len(calls), 1)
        self.assertEqual((r.accepted, r.rejected, r.retried), (0, 2, 0))

    def test_a_spent_budget_is_a_skip_recorded_as_a_note_not_a_failure(self) -> None:
        with mock.patch.object(ai, "complete", side_effect=ai.BudgetExceeded("spent")):
            with self.assertRaises(jobs.JobSkipped):
                summarize.summarize_job()
        recorded = []
        with mock.patch.object(ledger, "job_started", lambda n: None), \
                mock.patch.object(ledger, "job_succeeded", lambda n, note=None: recorded.append(("ok", note))), \
                mock.patch.object(ledger, "job_failed", lambda n, e: recorded.append(("failed", e))), \
                mock.patch.object(ai, "complete", side_effect=ai.BudgetExceeded("spent")):
            jobs.run_tracked(system_jobs.FEED_SUMMARIZE)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0][0], "ok")
        self.assertTrue(recorded[0][1].startswith("skipped: AI budget spent"))


class Chain(unittest.TestCase):
    def test_summarize_score_and_why_run_after_ingest_even_when_ingest_fails(self) -> None:
        order = []
        with mock.patch.object(ingest, "ingest_job", side_effect=lambda: order.append("ingest") or (_ for _ in ()).throw(RuntimeError("db down"))), \
                mock.patch.object(system_jobs, "run_tracked", lambda j: order.append(j.name)):
            with self.assertRaises(RuntimeError):
                system_jobs.feed_ingest_job()
        self.assertEqual(order, ["ingest", "feed_summarize", "feed_score", "feed_why"])

    def test_a_chained_failure_does_not_fail_ingest_or_stop_the_chain(self) -> None:
        order = []

        def failing(job):
            order.append(job.name)
            raise RuntimeError("recorded")

        with mock.patch.object(ingest, "ingest_job", lambda: None), \
                mock.patch.object(system_jobs, "run_tracked", failing):
            system_jobs.feed_ingest_job()
        self.assertEqual(order, ["feed_summarize", "feed_score", "feed_why"])

    def test_why_runs_after_the_profile_job_even_when_it_is_skipped(self) -> None:
        order = []
        with mock.patch.object(system_jobs.feed_profile_mod, "profile_job",
                               side_effect=jobs.JobSkipped("built today")), \
                mock.patch.object(system_jobs, "run_tracked", lambda j: order.append(j.name)):
            with self.assertRaises(jobs.JobSkipped):
                system_jobs.feed_profile_job()
        self.assertEqual(order, ["feed_why"])


# ======================================================================= embed

class EmbedClient(unittest.TestCase):
    def setUp(self) -> None:
        p = mock.patch.dict(os.environ, {"EMBED_URL": "https://e.invalid/embed", "EMBED_TOKEN": "t" * 43})
        p.start()
        self.addCleanup(p.stop)
        self.sleeps: list[float] = []

    def post(self, script: list) -> Any:
        """Each call pops one entry: an int status (546/503/...), an exception,
        or 'ok'. Records batch sizes."""
        self.sizes: list[int] = []

        def _post(url, payload, headers, timeout):
            self.assertTrue(headers["Authorization"].startswith("Bearer "))
            n = len(payload["texts"])
            self.sizes.append(n)
            step = script.pop(0) if script else "ok"
            if isinstance(step, Exception):
                raise step
            if step == "ok":
                return 200, {"embeddings": [unit(i) for i in range(n)]}
            return step, {"error": "x"}
        return _post

    def test_batches_of_8(self) -> None:
        out = embed.embed([f"t{i}" for i in range(20)], post=self.post([]), sleep=self.sleeps.append)
        self.assertEqual(len(out), 20)
        self.assertEqual(self.sizes, [8, 8, 4])
        self.assertEqual(embed.BATCH, 8)

    def test_546_halves_the_batch_and_retries(self) -> None:
        out = embed.embed([f"t{i}" for i in range(8)], post=self.post([546]), sleep=self.sleeps.append)
        self.assertEqual(len(out), 8)
        self.assertEqual(self.sizes, [8, 4, 4])
        self.assertTrue(self.sleeps)

    def test_503_on_a_single_text_retries_then_fails_loudly(self) -> None:
        with self.assertRaisesRegex(embed.EmbedError, "after 4 tries"):
            embed.embed(["one"], post=self.post([503] * 10), sleep=self.sleeps.append)
        self.assertEqual(self.sizes, [1, 1, 1, 1])
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0])

    def test_network_errors_retry(self) -> None:
        out = embed.embed(["a", "b"], post=self.post([TimeoutError(), "ok"]), sleep=self.sleeps.append)
        self.assertEqual(len(out), 2)

    def test_401_fails_at_once_and_never_shows_the_token(self) -> None:
        with self.assertRaises(embed.EmbedError) as ctx:
            embed.embed(["a"], post=self.post([401]), sleep=self.sleeps.append)
        self.assertIn("EMBED_TOKEN", str(ctx.exception))
        self.assertNotIn("t" * 43, str(ctx.exception))
        self.assertEqual(self.sizes, [1])

    def test_wrong_shape_fails(self) -> None:
        with self.assertRaisesRegex(embed.EmbedError, "dimensions"):
            embed.embed(["a"], post=lambda *a: (200, {"embeddings": [[0.1] * 10]}), sleep=self.sleeps.append)
        with self.assertRaisesRegex(embed.EmbedError, "vectors for 2"):
            embed.embed(["a", "b"], post=lambda *a: (200, {"embeddings": [unit(0)]}), sleep=self.sleeps.append)

    def test_missing_config_fails_loudly(self) -> None:
        with mock.patch.dict(os.environ, {"EMBED_URL": ""}):
            with self.assertRaisesRegex(embed.EmbedError, "EMBED_URL"):
                embed.embed(["a"], post=self.post([]))


# ====================================================================== ingest

def source(sid: int, slug: str, *, kind: str = "rss", first: bool = False, priority: int = 20,
           failures: int = 0, alerted: bool = False, pillars: tuple[str, ...] = ("home",),
           cats: tuple[str, ...] | None = None) -> ledger.FeedSource:
    return ledger.FeedSource(sid, slug, slug.title(), pillars, None, kind, f"https://{slug}.invalid/feed",
                             priority, cats, None, None, None if first else NOW - timedelta(hours=1),
                             failures, alerted, False)


class FakeFeedLedger:
    def __init__(self) -> None:
        self.ids = itertools.count(100)
        self.items: dict[tuple[int, str], dict] = {}
        self.urls: set[str] = set()
        self.stories: dict[int, dict] = {}
        self.ok: list[tuple[int, str]] = []
        self.failed: list[tuple[int, str]] = []
        self.failures: dict[int, int] = {}
        self.alerted: list[int] = []
        self.seeded: list = []
        self.joins: list = []

    def install(self, case: unittest.TestCase) -> None:
        for name in ("feed_rules", "feed_candidates", "feed_known", "feed_insert_seeded", "feed_new_story",
                     "feed_join_story", "feed_source_ok", "feed_source_failed", "feed_source_alerted"):
            p = mock.patch.object(ledger, name, getattr(self, name))
            p.start()
            case.addCleanup(p.stop)
        p = mock.patch.object(ledger, "_pool", side_effect=AssertionError("database reached"))
        p.start()
        case.addCleanup(p.stop)

    def feed_rules(self, **kw):
        return [(r.id, r.kind, r.pillar, r.pattern) for r in RULES]

    def feed_candidates(self, hours, **kw):
        return [ledger.CandidateStory(sid, s["count"], tuple(s["members"])) for sid, s in self.stories.items()]

    def feed_known(self, source_id, external_ids, urls, **kw):
        return ({e for e in external_ids if (source_id, e) in self.items}, {u for u in urls if u in self.urls})

    def feed_insert_seeded(self, src, items, **kw):
        for it in items:
            self.items[(src.id, it.external_id)] = {"seeded": True}
        self.seeded.extend(items)
        return len(items)

    def _add(self, src, it, story):
        self.items[(src.id, it.external_id)] = {"story": story, "item": it}
        if it.url:
            self.urls.add(it.url)
        return next(self.ids)

    def feed_new_story(self, src, it, vec, title_vec, *, filtered_reason, seed, near_misses=(), **kw):
        sid = next(self.ids)
        self.stories[sid] = {"title": it.title, "members": [(it.title, tuple(title_vec))], "count": 1,
                             "filtered": filtered_reason, "seed": seed, "near": list(near_misses),
                             "text_vec": list(vec)}
        return sid, self._add(src, it, sid)

    def feed_join_story(self, src, it, vec, title_vec, story_id, *, seed, logged, **kw):
        self.stories[story_id]["count"] += 1
        self.stories[story_id]["members"].append((it.title, tuple(title_vec)))
        self.joins.append((story_id, it.title, logged))
        return self._add(src, it, story_id)

    def feed_source_ok(self, source_id, *, status, etag, last_modified, **kw):
        self.ok.append((source_id, status))

    def feed_source_failed(self, source_id, error, **kw):
        self.failures[source_id] = self.failures.get(source_id, 0) + 1
        self.failed.append((source_id, error))
        return self.failures[source_id], source_id in self.alerted

    def feed_source_alerted(self, source_id, **kw):
        self.alerted.append(source_id)


class IngestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.db = FakeFeedLedger()
        self.db.install(self)
        self.sent: list[push.Message] = []
        self.feeds: dict[str, Any] = {}
        self.decoded: dict[str, str | None] = {}
        self.sleeps: list[float] = []

    def get(self, url: str, headers: dict) -> ingest.Response:
        body = self.feeds[url]
        if isinstance(body, Exception):
            raise body
        if isinstance(body, int):
            return ingest.Response(body, b"", None, None)
        return ingest.Response(200, body, '"etag-1"', None)

    def embedder(self, texts):
        # Stories are told apart by a keyword in the title.
        axis = {"hotel": 0, "harrison": 1, "golf": 2, "verstappen": 3}
        out = []
        for t in texts:
            hit = next((i for k, i in axis.items() if k in t.lower()), 10 + len(t) % 300)
            out.append(unit(hit))
        return out

    def run_ingest(self) -> ingest.Ingest:
        run = ingest.Ingest(get=self.get, decode=lambda u: self.decoded.get(u), embed=self.embedder,
                            send=lambda m: self.sent.append(m) or [],
                            throttle=ingest.Throttle(2, sleep=self.sleeps.append, clock=lambda: 0.0),
                            now=lambda: NOW)
        run.prepare({})
        return run


class OneSourceNeverFailsTheRun(IngestCase):
    def test_a_failing_source_is_recorded_and_the_others_still_ingest(self) -> None:
        bad, good = source(1, "bad"), source(2, "good")
        self.feeds[bad.url] = ConnectionError("refused")
        self.feeds[good.url] = rss(rss_item("Ross and Soffer scrap Miami airport hotel", "https://a.invalid/1", guid="1"))
        run = self.run_ingest()
        stats = [run.run_source(s) for s in (bad, good)]
        self.assertEqual([s.status for s in stats], ["error", "ok"])
        self.assertEqual(self.db.failed[0][0], 1)
        self.assertIn("ConnectionError", self.db.failed[0][1])
        self.assertEqual(self.db.ok, [(2, "ok")])
        self.assertEqual(stats[1].new_stories, 1)

    def test_non_200_and_not_a_feed_are_source_failures(self) -> None:
        a, b = source(1, "a"), source(2, "b")
        self.feeds[a.url] = 404
        self.feeds[b.url] = b"<html>blocked</html>"
        run = self.run_ingest()
        self.assertEqual([run.run_source(s).status for s in (a, b)], ["error", "error"])
        self.assertIn("HTTP 404", self.db.failed[0][1])

    def test_an_embed_outage_fails_the_run_and_records_no_fetch(self) -> None:
        good = source(2, "good")
        self.feeds[good.url] = rss(rss_item("x", "https://a.invalid/1", guid="1"))
        run = self.run_ingest()
        run.embed = lambda texts: (_ for _ in ()).throw(embed.EmbedError("down"))
        with self.assertRaises(embed.EmbedError):
            run.run_source(good)
        self.assertEqual(self.db.ok, [])  # the ETag is not saved, so the items come back next run

    def test_304_is_a_good_fetch(self) -> None:
        s = source(1, "s")
        self.feeds[s.url] = 304
        self.assertEqual(self.run_ingest().run_source(s).status, "not_modified")
        self.assertEqual(self.db.ok, [(1, "not_modified")])


class FailurePushOnce(IngestCase):
    def test_the_sixth_failure_pushes_once_and_later_ones_do_not(self) -> None:
        s = source(1, "flaky")
        self.feeds[s.url] = 500
        run = self.run_ingest()
        for _ in range(9):
            run.run_source(s)
        self.assertEqual(len(self.sent), 1)
        msg = self.sent[0]
        self.assertEqual((msg.deep_link, msg.kind), ("/settings/sources", "feed_source_failing"))
        self.assertIn("6 fetches in a row", msg.body)
        self.assertEqual(self.db.alerted, [1])

    def test_no_device_leaves_it_unalerted_to_try_again(self) -> None:
        s = source(1, "flaky")
        self.feeds[s.url] = 500
        run = self.run_ingest()
        run.send = lambda m: (_ for _ in ()).throw(push.PushError("no devices"))
        for _ in range(6):
            run.run_source(s)
        self.assertEqual(self.db.alerted, [])


class StoryPlacement(IngestCase):
    def test_two_outlets_one_story_and_the_join_is_logged(self) -> None:
        a, b = source(1, "bisnow", priority=30), source(2, "trd", priority=35)
        self.feeds[a.url] = rss(rss_item("Stephen Ross, Jeffrey Soffer scrap Miami airport hotel", "https://a.invalid/1", guid="1"))
        self.feeds[b.url] = rss(rss_item("Ross and Soffer's airport hotel grounded by FAA", "https://b.invalid/1", guid="1"))
        run = self.run_ingest()
        run.run_source(a)
        run.run_source(b)
        self.assertEqual(len(self.db.stories), 1)
        story_id, _title, logged = self.db.joins[0]
        self.assertEqual(logged[0].kind, "join")
        self.assertEqual(logged[0].joined_by, "both" if logged[0].fuzz >= 90 else "cosine")

    def test_filter_rules_file_the_story_as_filtered(self) -> None:
        s = source(1, "s", pillars=("sports",))
        self.feeds[s.url] = rss(rss_item("Bears 24, Packers 17: box score", "https://a.invalid/1", guid="1"))
        stats = self.run_ingest().run_source(s)
        self.assertEqual(stats.filtered, 1)
        self.assertEqual(next(iter(self.db.stories.values()))["filtered"], "title_regex:1")

    def test_known_items_and_urls_are_skipped(self) -> None:
        s = source(1, "s")
        self.feeds[s.url] = rss(rss_item("Golf one", "https://a.invalid/1?utm_source=x", guid="1"),
                                rss_item("Golf one again", "https://a.invalid/1", guid="2"))
        run = self.run_ingest()
        self.assertEqual(run.run_source(s).new_stories, 1)
        again = run.run_source(s)
        self.assertEqual((again.new_stories, again.known), (0, 2))

    def test_categories_filter_semafor(self) -> None:
        s = source(1, "semafor", cats=("Business", "Media"))
        self.feeds[s.url] = rss(rss_item("Politics thing", "https://a.invalid/1", guid="1", cats=("Politics",)),
                                rss_item("Hotel deal", "https://a.invalid/2", guid="2", cats=("Business",)))
        self.assertEqual(self.run_ingest().run_source(s).fetched, 1)


class FirstFetch(IngestCase):
    def test_dated_items_older_than_72h_are_ignored_on_the_first_fetch_only(self) -> None:
        s = source(1, "s", first=True)
        self.feeds[s.url] = rss(rss_item("Golf fresh", "https://a.invalid/1", guid="1", when=NOW - timedelta(hours=5)),
                                rss_item("An old item", "https://a.invalid/2", guid="2", when=NOW - timedelta(hours=80)))
        self.assertEqual(self.run_ingest().run_source(s).new_stories, 1)
        later = source(2, "t")
        self.feeds[later.url] = rss(rss_item("An old item", "https://b.invalid/2", guid="2", when=NOW - timedelta(hours=80)))
        self.assertEqual(self.run_ingest().run_source(later).new_stories, 1)

    def test_an_undated_feed_is_seeded_and_skipped_on_its_first_fetch(self) -> None:
        s = source(1, "formula1", first=True)
        self.feeds[s.url] = rss(*[rss_item(f"Verstappen {i}", f"https://f1.invalid/{i}", guid=str(i), when=None)
                                  for i in range(10)])
        stats = self.run_ingest().run_source(s)
        self.assertEqual((stats.seeded, stats.new_stories), (10, 0))
        self.assertEqual(self.db.stories, {})
        self.assertEqual(self.db.ok, [(1, "ok")])
        # The next fetch: the ten are known; one new undated item is ingested,
        # dated by when it was first seen.
        later = source(1, "formula1")
        self.feeds[later.url] = rss(rss_item("Verstappen new", "https://f1.invalid/new", guid="new", when=None),
                                    *[rss_item(f"Verstappen {i}", f"https://f1.invalid/{i}", guid=str(i), when=None)
                                      for i in range(10)])
        stats = self.run_ingest().run_source(later)
        self.assertEqual((stats.new_stories + stats.joined, stats.known), (1, 10))
        item = self.db.items[(1, "new")]["item"]
        self.assertEqual(item.published_at, NOW)

    def test_future_dates_are_capped_at_the_fetch(self) -> None:
        s = source(1, "espn")
        self.feeds[s.url] = rss(rss_item("Golf ahead", "https://a.invalid/1", guid="1", when=NOW + timedelta(hours=1)))
        self.run_ingest().run_source(s)
        self.assertEqual(self.db.items[(1, "1")]["item"].published_at, NOW)


class GoogleNews(IngestCase):
    def test_decoded_links_are_canonical_and_failures_keep_the_google_link(self) -> None:
        s = source(1, "nba-com", kind="gnews")
        g1, g2 = "https://news.google.com/rss/articles/one", "https://news.google.com/rss/articles/two"
        self.feeds[s.url] = rss(rss_item("Kyrie sparks Dallas - NBA.com", g1, guid="a"),
                                rss_item("Warriors extend Podziemski - NBA.com", g2, guid="b"))
        self.decoded = {g1: "https://www.nba.com/news/kyrie?utm_source=gn"}
        stats = self.run_ingest().run_source(s)
        self.assertEqual(stats.decode_failed, 1)
        ok, failed = self.db.items[(1, "a")]["item"], self.db.items[(1, "b")]["item"]
        self.assertEqual((ok.url, ok.decode_failed, ok.title), ("https://www.nba.com/news/kyrie", False, "Kyrie sparks Dallas"))
        self.assertEqual((failed.url, failed.google_url, failed.decode_failed), (None, g2, True))

    def test_decodes_are_throttled_to_two_a_second(self) -> None:
        s = source(1, "g", kind="gnews")
        self.feeds[s.url] = rss(*[rss_item(f"T{i}", f"https://news.google.com/rss/articles/{i}", guid=str(i)) for i in range(3)])
        self.run_ingest().run_source(s)
        self.assertEqual(self.sleeps, [0.5, 0.5])

    def test_a_known_item_is_never_decoded_again(self) -> None:
        s = source(1, "g", kind="gnews")
        self.feeds[s.url] = rss(rss_item("T", "https://news.google.com/rss/articles/1", guid="1"))
        run = self.run_ingest()
        run.run_source(s)
        decodes = []
        run.decode = lambda u: decodes.append(u)
        run.run_source(s)
        self.assertEqual(decodes, [])

    def test_stale_items_are_dropped_before_decoding(self) -> None:
        """Owner, 2026-10-11: a Google News item published more than
        window_days + 2 days before the fetch is dropped at ingest, on every
        fetch, before its link is decoded. The case: an Axios Miami author
        page Google News dated 2024 (window 7 days)."""
        s = replace(source(1, "axios-miami", kind="gnews"), gnews_window_days=7)
        g = "https://news.google.com/rss/articles/"
        self.feeds[s.url] = rss(
            rss_item("Avery Lotz", g + "author", guid="old", when=datetime(2024, 6, 1, tzinfo=timezone.utc)),
            rss_item("Just past", g + "past", guid="past", when=NOW - timedelta(days=9, minutes=1)),
            rss_item("Just inside", g + "inside", guid="inside", when=NOW - timedelta(days=9)),
            rss_item("Today", g + "today", guid="today"))
        decodes: list[str] = []
        run = self.run_ingest()
        run.decode = lambda u: decodes.append(u) or f"https://www.axios.com/{u.rsplit('/', 1)[1]}"
        stats = run.run_source(s)
        self.assertEqual(stats.stale, 2)
        self.assertEqual(sorted(k[1] for k in self.db.items), ["inside", "today"])
        self.assertEqual(sorted(decodes), [g + "inside", g + "today"])
        self.assertIn("2 stale", run_line([stats]))

    def test_the_stale_rule_is_google_news_only(self) -> None:
        s = source(1, "espn")
        self.feeds[s.url] = rss(rss_item("Old feature", "https://espn.invalid/old", guid="old",
                                         when=NOW - timedelta(days=20)))
        stats = self.run_ingest().run_source(s)
        self.assertEqual((stats.stale, len(self.db.items)), (0, 1))


def run_line(sources: list[ingest.SourceStats]) -> str:
    return ingest.RunStats(sources).line()


if __name__ == "__main__":
    unittest.main()
