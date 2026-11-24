"""Phase 4.2: P1's leg rules 1-4 (docs/preregistration_parlays.md §3), the funnel, and the re-quote.
Every boundary is tested on both sides, as written: the ask band is inclusive at 0.75 and 0.95,
the spread at 3c, freshness at 20 minutes."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from consensus import parlay_legs as pl
from venues.kalshi.events import KalshiShapeError

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)


def market(**kw):
    base = dict(market_ticker="KXNBAPTS-26NOV22AAABBB-P1-20", event_ticker="KXNBAPTS-26NOV22AAABBB",
                game_ticker="KXNBAGAME-26NOV22AAABBB", series_ticker="KXNBAPTS", sport="Basketball", league="nba",
                starts_at=NOW + timedelta(hours=3), market_kind="prop", stat="points", subject="P One", team="AAA",
                line=D("19.5"), tier="kalshi", p_yes=D("0.8"), yes_bid=D("0.79"), yes_ask=D("0.81"), payout="binary",
                combo_eligible=True, combo_yes_only=False, combo_size_max=None, mutually_exclusive=False)
    return pl.Market(**(base | kw))


def leg(side="yes", bid="0.79", ask="0.81", age=timedelta(minutes=1), **kw):
    q = None if bid is None and ask is None else pl.Quote(None if bid is None else D(bid), None if ask is None else D(ask), NOW - age)
    return pl.Leg(market(**kw), side, q)


class Universe(unittest.TestCase):
    def test_binary_not_started_eligible(self):
        self.assertTrue(pl.universe(leg(), NOW))
        self.assertFalse(pl.universe(leg(payout="scalar"), NOW))
        self.assertFalse(pl.universe(leg(starts_at=NOW), NOW))  # started at now: out
        self.assertFalse(pl.universe(leg(combo_eligible=False), NOW))

    def test_yes_only_events_give_yes_legs_only(self):
        self.assertTrue(pl.universe(leg("yes", combo_yes_only=True), NOW))
        self.assertFalse(pl.universe(leg("no", combo_yes_only=True), NOW))


class Fresh(unittest.TestCase):
    def test_twenty_minutes_inclusive_and_never_unread(self):
        self.assertTrue(pl.fresh(leg(age=timedelta(minutes=20)), NOW))
        self.assertFalse(pl.fresh(leg(age=timedelta(minutes=20, seconds=1)), NOW))
        self.assertFalse(pl.fresh(leg(bid=None, ask=None), NOW))  # not re-read
        self.assertFalse(pl.fresh(leg(age=timedelta(seconds=-5)), NOW))  # a quote from the future is not this slot's

    def test_the_side_needs_its_own_quote(self):
        self.assertFalse(pl.fresh(leg("yes", ask=None), NOW))
        self.assertTrue(pl.fresh(leg("no", ask=None), NOW))  # NO's ask is 1 - the YES bid
        self.assertFalse(pl.fresh(leg("no", bid=None), NOW))


class AskBand(unittest.TestCase):
    def test_inclusive_at_both_ends_on_the_sides_own_ask(self):
        for ask, ok in (("0.7499", False), ("0.75", True), ("0.95", True), ("0.9501", False)):
            with self.subTest(ask=ask):
                self.assertEqual(pl.ask_band(leg("yes", bid="0.70", ask=ask)), ok)
        # NO at a 0.20 YES bid asks 0.80; at 0.26 it asks 0.74
        self.assertTrue(pl.ask_band(leg("no", bid="0.20", ask="0.22")))
        self.assertFalse(pl.ask_band(leg("no", bid="0.26", ask="0.28")))


class Spread(unittest.TestCase):
    def test_three_cents_inclusive(self):
        self.assertTrue(pl.spread(leg(bid="0.78", ask="0.81")))
        self.assertFalse(pl.spread(leg(bid="0.7799", ask="0.81")))
        self.assertFalse(pl.spread(leg(bid=None, ask="0.81")))


class FunnelAndBindingRule(unittest.TestCase):
    def test_first_failed_is_in_rule_order(self):
        self.assertIsNone(pl.first_failed(leg(), NOW))
        self.assertEqual(pl.first_failed(leg(combo_eligible=False, bid="0.1", ask="0.5"), NOW), "universe")
        self.assertEqual(pl.first_failed(leg(age=timedelta(hours=1), bid="0.1", ask="0.5"), NOW), "fresh")
        self.assertEqual(pl.first_failed(leg(bid="0.5", ask="0.6"), NOW), "ask_band")
        self.assertEqual(pl.first_failed(leg(bid="0.70", ask="0.80"), NOW), "spread")

    def test_counts_per_sport_before_and_after_each_rule_with_the_readers_cut(self):
        legs = [leg(), leg("no"), leg(bid="0.70", ask="0.80"),
                leg(sport="Hockey", market_ticker="KXNHLGOAL-X-1"), leg(sport="Hockey", combo_eligible=False)]
        f = pl.funnel(legs, NOW, cut={"Hockey": 2})  # 2 cut markets = 4 legs
        self.assertEqual(f.table()["Basketball"], {"before": 3, "universe": 3, "fresh": 3, "ask_band": 2, "spread": 1})
        # NO of P1 asks 1 - 0.79 = 0.21: fails the band; the 0.70/0.80 YES fails the spread
        self.assertEqual(f.table()["Hockey"], {"before": 6, "universe": 1, "fresh": 1, "ask_band": 1, "spread": 1})
        self.assertEqual([(l.market.sport, l.side) for l in f.passed], [("Basketball", "yes"), ("Hockey", "yes")])

    def test_both_sides_of_every_market(self):
        ls = pl.legs_of([market()], {})
        self.assertEqual([l.side for l in ls], ["yes", "no"])
        self.assertTrue(all(l.quote is None for l in ls))


def page(markets, cursor=""):
    return json.dumps({"markets": markets, "cursor": cursor}).encode()


class Requote(unittest.TestCase):
    def fake_http(self, pages, statuses=None):
        calls = []

        def http(url):
            calls.append(url)
            st = (statuses or {}).get(len(calls), 200)
            if st != 200:
                return st, None
            key = "event_ticker=" if "event_ticker=" in url else "series_ticker="
            return 200, pages[url.split(key)[1].split("&")[0]].pop(0)
        return http, calls

    def test_one_event_a_call_every_page_and_the_quotes(self):
        e1, e2 = "KXNBAPTS-26NOV22AAABBB", "KXNBAGAME-26NOV22AAABBB"
        http, calls = self.fake_http({
            e1: [page([{"ticker": f"{e1}-A", "event_ticker": e1, "yes_bid_dollars": "0.7900", "yes_ask_dollars": "0.8100"}], "c2"),
                 page([{"ticker": f"{e1}-B", "event_ticker": e1, "yes_bid_dollars": None, "yes_ask_dollars": "0.0300"}])],
            e2: [page([{"ticker": f"{e2}-AAA", "event_ticker": e2, "yes_bid_dollars": "0.6000", "yes_ask_dollars": "0.6100"}])]})
        r = pl.requote([e1, e2], http=http, now=lambda: NOW, sleep=lambda s: None, pace=0)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all("status=open" in u for u in calls))
        self.assertEqual(r.quotes[f"{e1}-A"], pl.Quote(D("0.79"), D("0.81"), NOW))
        self.assertIsNone(r.quotes[f"{e1}-B"].yes_bid)
        self.assertEqual((r.events_asked, r.events_skipped, r.calls, r.limited), (2, 0, 3, 0))

    def test_a_429_is_retried_counted_and_slows_the_pace(self):
        e1 = "KXNBAPTS-26NOV22AAABBB"
        http, calls = self.fake_http({e1: [page([])]}, statuses={1: 429})
        r = pl.requote([e1], http=http, now=lambda: NOW, sleep=lambda s: None, pace=0.5)
        self.assertEqual((r.calls, r.limited), (2, 1))
        self.assertAlmostEqual(r.share_429, 0.5)
        self.assertGreater(r.pace, 0.5)

    def test_the_call_cap_stops_and_counts_the_rest(self):
        # Five series of one event each: five event reads, the cap stops the last two.
        es = [f"KXNBAS{i}-26NOV22AAABBB" for i in range(5)]
        http, _ = self.fake_http({e: [page([])] for e in es})
        r = pl.requote(es, http=http, now=lambda: NOW, sleep=lambda s: None, pace=0, max_calls=3)
        self.assertEqual((r.events_asked, r.events_skipped, r.cap), (3, 2, "calls"))

    def test_more_than_two_events_of_a_series_are_one_series_read_at_page_1000(self):
        # Owner, Nov 24: per series at page 1,000; per event for a series with <= 2 window events.
        es = [f"KXNBAPTS-26NOV22G{i}" for i in range(3)]          # 3 of KXNBAPTS: one series read
        game = ["KXNBAGAME-26NOV22G0", "KXNBAGAME-26NOV22G1"]     # 2 of KXNBAGAME: two event reads
        other = "KXNBAPTS-26NOV29LATER"                           # the series' event outside the slot
        http, calls = self.fake_http({
            "KXNBAPTS": [page([{"ticker": f"{e}-A", "event_ticker": e, "yes_bid_dollars": "0.80", "yes_ask_dollars": "0.82"}
                               for e in es] + [{"ticker": f"{other}-A", "event_ticker": other,
                                                "yes_bid_dollars": "0.10", "yes_ask_dollars": "0.12"}], "c2"),
                         page([{"ticker": f"{es[0]}-B", "event_ticker": es[0], "yes_bid_dollars": "0.50",
                                "yes_ask_dollars": "0.51"}])],
            **{g: [page([{"ticker": f"{g}-X", "event_ticker": g, "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.41"}])]
               for g in game}})
        r = pl.requote([*es, *game], http=http, now=lambda: NOW, sleep=lambda s: None, pace=0)
        self.assertEqual(len(calls), 4)  # two pages of the series, one per GAME event
        self.assertTrue(all("limit=1000" in u and "status=open" in u for u in calls))
        self.assertEqual(sum("series_ticker=KXNBAPTS" in u for u in calls), 2)
        self.assertEqual((r.series_reads, r.event_reads, r.events_asked, r.events_skipped), (1, 2, 5, 0))
        self.assertNotIn(f"{other}-A", r.quotes)  # another event of the series: dropped
        self.assertEqual(set(r.quotes), {f"{e}-A" for e in es} | {f"{es[0]}-B"} | {f"{g}-X" for g in game})
        self.assertEqual(r.markets_read, 7)  # the series' pages 4 + 1 (one of them dropped), 2 event reads
        self.assertEqual(len(r.answer_s), 4)
        self.assertEqual(r.answer_ms()["n"], 4)

    def test_the_plan_keeps_the_earliest_games_last(self):
        # `events` come latest start first; a read goes in the order of its earliest game.
        events = ["KXA-LATE", "KXB-1", "KXB-2", "KXB-3", "KXC-EARLY"]
        self.assertEqual(pl.requote_plan(events),
                         [("event", ("KXA-LATE",)), ("series", ("KXB-1", "KXB-2", "KXB-3")), ("event", ("KXC-EARLY",))])
        self.assertEqual(pl.requote_plan(["KXB-1", "KXB-2"]), [("event", ("KXB-1",)), ("event", ("KXB-2",))])

    def test_a_series_read_with_a_market_outside_the_series_fails_loudly(self):
        es = [f"KXNBAPTS-26NOV22G{i}" for i in range(3)]
        http, _ = self.fake_http({"KXNBAPTS": [page([{"ticker": "KXNHLX-1-A", "event_ticker": "KXNHLX-1",
                                                       "yes_bid_dollars": "0.1", "yes_ask_dollars": "0.2"}])]})
        with self.assertRaises(KalshiShapeError):
            pl.requote(es, http=http, now=lambda: NOW, sleep=lambda s: None, pace=0)

    def test_the_budget_counts_every_event_of_an_unread_series(self):
        es = ["KXA-1"] + [f"KXB-{i}" for i in range(3)]
        http, _ = self.fake_http({"KXA-1": [page([])], "KXB": [page([])]})
        r = pl.requote(es, http=http, now=lambda: NOW, sleep=lambda s: None, pace=0, max_calls=1)
        self.assertEqual((r.events_asked, r.events_skipped, r.cap), (1, 3, "calls"))

    def test_answer_times_summarized(self):
        rq = pl.Requote(answer_s=[0.1, 0.2, 0.3, 0.4, 1.0])
        self.assertEqual(rq.answer_ms(), {"n": 5, "p50": 300.0, "p90": 1000.0, "p99": 1000.0, "max": 1000.0,
                                          "total_s": 2.0})
        self.assertEqual(pl.Requote().answer_ms(), {"n": 0})

    def test_a_market_of_another_event_fails_loudly(self):
        e1 = "KXNBAPTS-26NOV22AAABBB"
        http, _ = self.fake_http({e1: [page([{"ticker": "X-1", "event_ticker": "X", "yes_bid_dollars": "0.1", "yes_ask_dollars": "0.2"}])]})
        with self.assertRaises(KalshiShapeError):
            pl.requote([e1], http=http, now=lambda: NOW, sleep=lambda s: None, pace=0)


def q(bid, ask):
    return pl.Quote(None if bid is None else D(bid), None if ask is None else D(ask), NOW)


class FreshP(unittest.TestCase):
    """The engine's blend and normalization on the slot's fresh quotes (owner, Nov 20)."""

    def test_kalshi_tier_is_the_fresh_mid_never_the_stored_p(self):
        m = market(p_yes=D("0.90"))
        self.assertEqual(pl.fresh_p([m], {m.market_ticker: q("0.40", "0.43")}), {m.market_ticker: D("0.415000")})
        self.assertEqual(pl.fresh_p([m], {}), {m.market_ticker: None})  # not re-read: no p at all
        self.assertEqual(pl.fresh_p([m], {m.market_ticker: q(None, "0.43")}), {m.market_ticker: None})

    def test_a_mutually_exclusive_event_is_normalized_inside_the_band_only(self):
        ev = [market(market_ticker=f"E-{c}", event_ticker="E", mutually_exclusive=True) for c in "ABC"]
        inside = {"E-A": q("0.29", "0.31"), "E-B": q("0.32", "0.34"), "E-C": q("0.39", "0.41")}  # sum 1.03
        out = pl.fresh_p(ev, inside)
        self.assertEqual(out["E-A"], (D("0.30") / D("1.03")).quantize(D("0.000001")))
        outside = {"E-A": q("0.34", "0.36"), "E-B": q("0.34", "0.36"), "E-C": q("0.39", "0.41")}  # sum 1.10
        self.assertEqual(pl.fresh_p(ev, outside)["E-A"], D("0.350000"))  # plain
        partial = {"E-A": q("0.29", "0.31"), "E-B": q("0.32", "0.34")}  # E-C not quoted
        self.assertEqual(pl.fresh_p(ev, partial), {"E-A": D("0.300000"), "E-B": D("0.330000"), "E-C": None})

    def test_normalized_events_names_exactly_the_events_the_rule_normalizes(self):
        ev = [market(market_ticker=f"E-{c}", event_ticker="E", mutually_exclusive=True) for c in "AB"]
        lad = [market(market_ticker=f"L-{c}", event_ticker="L", mutually_exclusive=False) for c in "AB"]
        quotes = {"E-A": q("0.59", "0.61"), "E-B": q("0.37", "0.39"),   # sum 0.98: normalized
                  "L-A": q("0.59", "0.61"), "L-B": q("0.37", "0.39")}   # not exclusive: plain
        self.assertEqual(pl.normalized_events([*ev, *lad], quotes), {"E"})

    def test_rungs_of_a_ladder_stay_plain(self):
        rungs = [market(market_ticker=f"L-{i}", event_ticker="L", mutually_exclusive=False) for i in range(3)]
        quotes = {"L-0": q("0.79", "0.81"), "L-1": q("0.49", "0.51"), "L-2": q("0.19", "0.21")}  # sum 1.50
        self.assertEqual(list(pl.fresh_p(rungs, quotes).values()), [D("0.800000"), D("0.500000"), D("0.200000")])
        single = [market(market_ticker="S", event_ticker="S", mutually_exclusive=True)]
        self.assertEqual(pl.fresh_p(single, {"S": q("0.60", "0.62")}), {"S": D("0.610000")})  # one market: plain

    def test_books_and_espn_blend_0_7_outside_0_3_fresh_mid_and_fall_back_as_the_engine_does(self):
        quotes = {"X": q("0.49", "0.51")}
        books = market(market_ticker="X", tier="books", p_books=D("0.60"))
        espn = market(market_ticker="X", tier="espn", p_espn=D("0.62"), p_books=D("0.10"))
        self.assertEqual(pl.fresh_p([books], quotes)["X"], D("0.570000"))  # 0.42 + 0.15
        self.assertEqual(pl.fresh_p([espn], quotes)["X"], D("0.584000"))   # 0.434 + 0.15
        no_line = market(market_ticker="X", tier="books", p_books=None)
        self.assertEqual(pl.fresh_p([no_line], quotes)["X"], D("0.500000"))  # consensus_leg: no outside -> mid
        kalshi = market(market_ticker="X", tier="kalshi", p_books=D("0.60"))
        self.assertEqual(pl.fresh_p([kalshi], quotes)["X"], D("0.500000"))  # the kalshi tier never blends
        stale = market(market_ticker="X", tier="books", p_books=D("0.60"), stale_outside=True)
        # The engine's 12 h flag blocks a claim, never the blend: kept exactly.
        self.assertEqual(pl.fresh_p([stale], quotes)["X"], D("0.570000"))

    def test_with_fresh_p_replaces_the_stored_p_and_nothing_else(self):
        m = market(p_yes=D("0.95"))
        (out,) = pl.with_fresh_p([m], {m.market_ticker: q("0.70", "0.72")})
        self.assertEqual(out.p_yes, D("0.710000"))
        self.assertEqual(out.__dict__ | {"p_yes": m.p_yes}, m.__dict__)


class OrderAndAge(unittest.TestCase):
    def test_latest_start_first_so_the_earliest_games_are_read_last(self):
        ms = [market(event_ticker="E-SOON", starts_at=NOW + timedelta(hours=1)),
              market(event_ticker="E-LATE", starts_at=NOW + timedelta(hours=9)),
              market(event_ticker="E-SOON", market_ticker="X2", starts_at=NOW + timedelta(hours=1)),
              market(event_ticker="E-MID-B", starts_at=NOW + timedelta(hours=4)),
              market(event_ticker="E-MID-A", starts_at=NOW + timedelta(hours=4))]
        self.assertEqual(pl.requote_order(ms), ["E-LATE", "E-MID-A", "E-MID-B", "E-SOON"])

    def test_oldest_age_is_the_oldest_quote_and_none_when_empty(self):
        q = {"A": pl.Quote(D("0.1"), D("0.2"), NOW - timedelta(seconds=30)),
             "B": pl.Quote(D("0.1"), D("0.2"), NOW)}
        self.assertEqual(pl.oldest_age(q, NOW), timedelta(seconds=30))
        self.assertEqual(pl.oldest_age({"B": q["B"]}, NOW), timedelta(0))
        self.assertIsNone(pl.oldest_age({}, NOW))

    def test_the_requote_floor_is_its_own(self):
        from venues.kalshi import board as kb
        self.assertEqual(pl.REQUOTE_FLOOR, 0.10)
        self.assertEqual(kb.PACE_FLOOR, 0.3)


class Rows(unittest.TestCase):
    def test_reader_rows_become_markets(self):
        from core import ledger
        row = dict(zip(ledger.PARLAY_CANDIDATE_COLUMNS, (
            "T-1", "T", "G", "S", "Basketball", "nba", NOW, "prop", "points", "P", "AAA", D("19.5"), "kalshi",
            D("0.800000"), D("0.7900"), D("0.8100"), "binary", True, False, None, False)))
        m = pl.markets_from_rows([row])[0]
        self.assertEqual((m.market_ticker, m.p_yes, m.yes_ask, m.combo_size_max), ("T-1", D("0.8"), D("0.81"), None))


if __name__ == "__main__":
    unittest.main()
