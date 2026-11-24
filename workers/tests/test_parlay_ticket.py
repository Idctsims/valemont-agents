"""Phase 4.3: the ticket search, prices and leg reasons (docs/preregistration_parlays.md §3-§4).

Each ticket rule 5-11 on its boundary, the order and tie-breaks, the tick
floor, the fee rounding, determinism (same ticket, same reason bytes), the
cross-sport replacement step and its "extrapolated fill" label. The real rho
table is used, so rule 7 and 9 come from the shipped pricer."""

import gzip
import itertools
import json
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from consensus import correlation as cr
from consensus import parlay_legs as pl
from consensus import parlay_ticket as pt

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
T = cr.load_table()
DRAWS = dict(draws=4000, draws_max=4000)


def cand(ticker, *, side="yes", ask="0.90", bid=None, p="0.90", game=None, event=None, sport="Basketball",
         league="nba", series="KXNBAPTS", kind="prop", subject=None, team="AAA", cap=None, excl=False, tier="kalshi"):
    """A candidate leg. Each defaults to its own game and event (no same-game pairs)."""
    ask_d = D(ask)
    if bid is None:  # a 2c-wide quote on the side's ask
        bid_yes = ask_d - D("0.02") if side == "yes" else D(1) - ask_d
        ask_yes = ask_d if side == "yes" else D(1) - ask_d + D("0.02")
    else:
        bid_yes, ask_yes = D(bid), ask_d
    game = game or f"G-{ticker}"
    m = pl.Market(market_ticker=ticker, event_ticker=event or f"{game}-E", game_ticker=game, series_ticker=series,
                  sport=sport, league=league, starts_at=NOW + timedelta(hours=3), market_kind=kind, stat="points",
                  subject=subject if subject is not None else f"Player {ticker}", team=team, line=D("9.5"), tier=tier,
                  p_yes=None if p is None else (D(p) if side == "yes" else D(1) - D(p)), yes_bid=bid_yes,
                  yes_ask=ask_yes, payout="binary", combo_eligible=True, combo_yes_only=False, combo_size_max=cap,
                  mutually_exclusive=excl)
    leg = pl.Leg(m, side, pl.Quote(bid_yes, ask_yes, NOW - timedelta(minutes=1)))
    c, _ = pt.candidates([leg])
    return c[0] if c else leg


class Order(unittest.TestCase):
    def test_ask_over_the_raw_fresh_mid_lowest_first_then_ticker_then_side(self):
        # v2 §3: the consensus p plays no part in the order (here it would rank them the other way).
        a = cand("B", ask="0.80", bid="0.80", p="0.70").leg   # ask/mid 1.000
        b = cand("A", ask="0.90", bid="0.88", p="0.99").leg   # 1.011
        c = cand("C", ask="0.81", bid="0.80", p="0.75").leg   # 1.006
        d = cand("A0", ask="0.90", bid="0.88", p="0.99").leg  # 1.011, ticker after "A"
        out, _ = pt.candidates([b, d, c, a])
        self.assertEqual([x.m.market_ticker for x in out], ["B", "C", "A", "A0"])

    def test_a_no_legs_mid_is_one_minus_the_yes_mid(self):
        m = pl.Market(**(cand("N").leg.market.__dict__ | {"p_yes": D("0.15")}))
        (no,), _ = pt.candidates([pl.Leg(m, "no", pl.Quote(D("0.18"), D("0.20"), NOW))])
        # NO ask = 1 - YES bid = 0.82; NO mid = 1 - 0.19 = 0.81
        self.assertEqual((no.ask, no.mid, no.ratio.quantize(D("0.0001"))), (D("0.82"), D("0.81"), D("1.0123")))

    def test_a_leg_without_consensus_p_is_not_a_candidate_and_is_counted(self):
        out, no_p = pt.candidates([cand("A", p=None), cand("B").leg])
        self.assertEqual(([x.m.market_ticker for x in out], no_p), (["B"], 1))


class Rules(unittest.TestCase):
    def test_5_leg_count(self):
        self.assertEqual(pt.first_broken([cand("A"), cand("B")], cand("C"), 2, T), 5)
        self.assertIsNone(pt.first_broken([cand("A"), cand("B")], cand("C"), 3, T))

    def test_6_event_cap_one_means_one_and_absent_means_none(self):
        a = cand("A", game="G1", event="E1", cap=1)
        b = cand("B", game="G1", event="E1", cap=1, team="BBB")
        self.assertEqual(pt.first_broken([a], b, 5, T), 6)
        a2 = cand("A", game="G1", event="E1", cap=2, subject="P1")
        b2 = cand("B", game="G1", event="E1", cap=2, subject="P2")
        self.assertIsNone(pt.first_broken([a2], b2, 5, T))  # the cap holds 2: allowed
        a3 = cand("A", game="G1", event="E1", subject="P1")
        b3 = cand("B", game="G1", event="E1", subject="P2")
        self.assertIsNone(pt.first_broken([a3], b3, 5, T))  # no cap of its own

    def test_7_refused_pairs(self):
        yes = cand("M", side="yes", game="G1")
        no = cand("M", side="no", game="G1")
        self.assertEqual(pt.first_broken([yes], no, 5, T), 7)  # both sides of one market
        x = cand("X", game="G1", event="E1", excl=True, kind="game_line", series="KXNBAGAME", subject="")
        y = cand("Y", game="G1", event="E1", excl=True, kind="game_line", series="KXNBAGAME", subject="")
        self.assertEqual(pt.first_broken([x], y, 5, T), 7)  # two outcomes of one exclusive event
        pts = cand("P1", game="G1", event="E-PTS", series="KXNBAPTS", subject="Same Guy")
        pra = cand("P2", game="G1", event="E-PRA", series="KXNBAPRA", subject="Same Guy")
        self.assertEqual(pt.first_broken([pts], pra, 5, T), 7)  # a stat beside a sum that holds it

    def test_8_one_prop_per_player(self):
        pts = cand("P1", game="G1", event="E-PTS", series="KXNBAPTS", subject="Same Guy")
        reb = cand("P2", game="G1", event="E-REB", series="KXNBAREB", subject="Same Guy")
        self.assertEqual(pt.first_broken([pts], reb, 5, T), 8)
        other_game = cand("P3", game="G2", event="E-REB2", series="KXNBAREB", subject="Same Guy")
        self.assertEqual(pt.first_broken([pts], other_game, 5, T), 8)  # a player is a player in any game

    def test_9_an_unmodeled_same_game_pair_is_never_priced_at_zero(self):
        a = cand("A", game="G1", series="KXNBAPTS", subject="P1")
        b = cand("B", game="G1", series="KXNBAUNKNOWN", subject="P2")
        self.assertEqual(pt.first_broken([a], b, 5, T), 9)
        c = cand("C", game="G1", series="KXNBAPTS", subject="P3", team="BBB")
        self.assertIsNone(pt.first_broken([a], c, 5, T))  # opp|pts|pts is a row

    def test_10_at_most_three_legs_from_one_game(self):
        legs = [cand(f"L{i}", game="G1", series="KXNBAPTS", subject=f"P{i}", team="AAA" if i % 2 else "BBB")
                for i in range(4)]
        for i in range(1, 3):
            self.assertIsNone(pt.first_broken(legs[:i], legs[i], 5, T))
        self.assertEqual(pt.first_broken(legs[:3], legs[3], 5, T), 10)

    def test_11_the_tick_floor_is_inclusive_at_one_cent(self):
        a, b = cand("A", ask="0.10", p="0.20"), cand("B", ask="0.10", p="0.20")
        self.assertIsNone(pt.first_broken([a], b, 5, T))  # exactly 1c
        c = cand("C", ask="0.09", p="0.20")
        self.assertEqual(pt.first_broken([a], c, 5, T), 11)

    def test_the_first_broken_rule_is_reported_in_rule_order(self):
        a = cand("A", game="G1", event="E1", cap=1, subject="Same Guy")
        b = cand("B", game="G1", event="E1", cap=1, series="KXNBAREB", subject="Same Guy")
        self.assertEqual(pt.first_broken([a], b, 5, T), 6)  # 6 before 8


class Prices(unittest.TestCase):
    def test_fee_charged_once_on_the_combo_rounded_up_to_the_cent_at_100(self):
        self.assertEqual(pt.taker_fee_order(D("0.5")), D("1.75"))
        self.assertEqual(pt.taker_fee_order(D("0.123")), D("0.76"))  # 0.755097 rounds up
        self.assertEqual(pt.taker_fee_order(D("0.01")), D("0.07"))   # 0.0693 rounds up

    def test_the_prices_on_a_ticket(self):
        a, b, c = cand("A", ask="0.80", p="0.85"), cand("B", ask="0.90", p="0.92"), cand("C", ask="0.85", p="0.88")
        pr = pt.price_ticket([a, b, c], T, **DRAWS)
        self.assertEqual(pr.scored_price, D("0.80") * D("0.90") * D("0.85"))
        self.assertEqual(pr.taker_fee_order, pt.taker_fee_order(pr.scored_price))
        self.assertEqual(pr.taker_fee, pr.taker_fee_order / 100)
        self.assertEqual(pr.maker_fee_assumed, pr.taker_fee / 2)
        self.assertEqual(pr.naive_p, D("0.79") * D("0.89") * D("0.84"))  # side mids
        self.assertAlmostEqual(pr.fair_p, 0.85 * 0.92 * 0.88, places=9)  # three games: independent
        self.assertAlmostEqual(pr.rel_cost, float(pr.scored_price) / pr.fair_p - 1, places=12)
        self.assertEqual((pr.pairs, pr.measured_share), (0, 1.0))

    def test_edge_leg_needs_books_tier_and_two_cents_under_the_least_favourable(self):
        span = pt.Span(p_lo=D("0.85"), p_hi=D("0.88"), fee_yes=D("0.01"), fee_no=D("0.01"))
        books = cand("A", ask="0.82", p="0.86", tier="books")
        self.assertTrue(pt.is_edge_leg(books, span))           # 0.85 - 0.83 = 0.02, inclusive
        self.assertFalse(pt.is_edge_leg(cand("A", ask="0.83", p="0.86", tier="books"), span))
        self.assertFalse(pt.is_edge_leg(cand("A", ask="0.82", p="0.86", tier="espn"), span))
        self.assertFalse(pt.is_edge_leg(books, None))
        no = cand("A", side="no", ask="0.09", p="0.14", tier="books")  # NO: 1 - p_hi = 0.12
        self.assertTrue(pt.is_edge_leg(no, span))              # 0.12 - 0.10 = 0.02


def board(n, sport="Basketball", prefix="L", ask="0.92", p="0.95", bid=None):
    """n candidates on n games of one sport, ordered by their tickers. A bid
    equal to the ask makes ask/mid exactly 1, below any 2c-wide leg."""
    return [cand(f"{prefix}{i:02d}", ask=ask, p=p, sport=sport, bid=bid) for i in range(n)]


class Search(unittest.TestCase):
    def test_one_ticket_per_count_inside_the_window_and_the_lowest_relative_cost_wins(self):
        # 75c legs: 3 legs price 0.4219, above Safer's 0.40, so only 4 and 5 can fill.
        # Identical independent legs: the relative cost is (ask / p)^n - 1, so below 1
        # the most legs win and above 1 the fewest that fit do.
        under = pt.search("safer", sorted(board(6, ask="0.75", p="0.78"), key=lambda c: c.order), T,
                          sport="Basketball", **DRAWS)
        self.assertEqual(under.tried[3], "window: unreachable at this leg count")
        self.assertEqual(len(under.ticket.legs), 5)
        r = pt.search("safer", sorted(board(6, ask="0.75", p="0.74"), key=lambda c: c.order), T,
                      sport="Basketball", **DRAWS)
        self.assertIsNotNone(r.ticket)
        self.assertEqual(len(r.ticket.legs), 4)
        self.assertTrue(D("0.15") <= r.ticket.priced.scored_price <= D("0.40"))
        self.assertEqual(r.ticket.agent, "parlay_safer")
        self.assertEqual((r.ticket.scope, r.ticket.fill_note, r.ticket.rule_version, r.ticket.search), ("sport", None, "P2.1", "greedy"))
        self.assertIsNone(r.ticket.expected_quote)
        self.assertEqual(r.ticket.payload()["expected_quote_note"], "pending (M1)")
        self.assertEqual(r.ticket.rho_sha256, T.sha256)

    def test_the_same_candidates_give_the_same_ticket_and_the_same_reason_bytes(self):
        cands = sorted([*board(9, ask="0.75", p="0.78"), cand("X", ask="0.95", p="0.96", game="GX", subject="Same Guy"),
                        cand("Y", ask="0.80", p="0.96", game="GY", subject="Same Guy")], key=lambda c: c.order)
        one = pt.search("mid", cands, T, sport="Basketball", **DRAWS)
        two = pt.search("mid", list(cands), T, sport="Basketball", **DRAWS)
        self.assertEqual(one.ticket.payload(), two.ticket.payload())
        self.assertEqual([s.encode("utf-8") for s in one.ticket.leg_reasons],
                         [s.encode("utf-8") for s in two.ticket.leg_reasons])

    def test_too_few_candidates_is_an_empty_slot_naming_rules_1_to_4(self):
        r = pt.search("safer", board(2), T, sport="Basketball", **DRAWS)
        self.assertIsNone(r.ticket)
        self.assertEqual(r.binding, "rules 1-4: 2 candidates < 3")

    def test_a_tier_that_cannot_reach_its_window_is_empty_with_window_never_a_wider_band(self):
        cands = sorted(board(5, ask="0.95", p="0.96"), key=lambda c: c.order)  # 0.95^5 = 0.774 > 0.40
        r = pt.search("safer", cands, T, sport="Basketball", **DRAWS)
        self.assertIsNone(r.ticket)
        self.assertEqual(r.binding, "window: no leg count from 3 to 5 reaches it on asks 95-95¢")
        self.assertEqual(set(r.tried.values()), {"window: unreachable at this leg count"})

    def test_too_few_candidates_for_the_fewest_legs_that_reach_the_window_binds_window(self):
        # The Nov 20 Tennis case: 3 candidates passed rules 1-4. At 80c asks, 4 legs price
        # 0.4096 (above Safer's 0.40), so 5 is the fewest that reach it: `window`, counts recorded.
        cands = sorted(board(3, sport="Tennis", ask="0.80", p="0.82"), key=lambda c: c.order)
        r = pt.search("safer", cands, T, sport="Tennis", **DRAWS)
        self.assertIsNone(r.ticket)
        self.assertEqual(r.binding, "window: 3 candidates < 5, the fewest legs that reach the window")
        self.assertEqual(r.tried, {3: "window: unreachable at this leg count",
                                   4: "window: unreachable at this leg count", 5: "short: 3 candidates"})

    def test_no_candidates_only_when_nothing_passed_rules_1_to_4(self):
        r = pt.search("safer", [], T, sport="Tennis", **DRAWS)
        self.assertEqual((r.ticket, r.binding, r.candidates), (None, "no candidates", 0))
        r2 = pt.search("safer", board(2, sport="Tennis"), T, sport="Tennis", **DRAWS)
        self.assertEqual(r2.binding, "rules 1-4: 2 candidates < 3")

    def test_an_empty_slot_names_the_rule_that_bound(self):
        # Reachable counts exist (75c legs), but one event caps them to one leg each: event cap binds.
        cands = sorted([cand(f"E{i}", ask="0.75", p="0.78", game=f"G{i}", event="E1", cap=1, subject=f"P{i}")
                        for i in range(6)], key=lambda c: c.order)
        r = pt.search("safer", cands, T, sport="Basketball", **DRAWS)
        self.assertIsNone(r.ticket)
        self.assertEqual(r.binding, "event cap")


class Window(unittest.TestCase):
    """v2's per-tier scored-price windows (owner, Nov 20)."""

    def test_the_windows_and_their_inclusive_bounds(self):
        self.assertEqual(pt.WINDOWS, {"safer": (D("0.15"), D("0.40")), "mid": (D("0.03"), D("0.15")),
                                      "lotto": (D("0.01"), D("0.03"))})
        for tier, (lo, hi) in pt.WINDOWS.items():
            self.assertTrue(pt.in_window(lo, (lo, hi)) and pt.in_window(hi, (lo, hi)))
            self.assertFalse(pt.in_window(lo - D("0.0001"), (lo, hi)) or pt.in_window(hi + D("0.0001"), (lo, hi)))

    def test_which_leg_counts_can_ever_fill_on_the_75_to_95_band(self):
        band = (D("0.75"), D("0.95"))
        can = {tier: [n for n in range(pt.TIERS[tier][1], pt.TIERS[tier][2] + 1)
                      if pt.reachable(D(1), n, pt.WINDOWS[tier], *band)] for tier in pt.TIERS}
        self.assertEqual(can, {"safer": [4, 5], "mid": [7, 8, 9, 10], "lotto": list(range(13, 21))})

    def test_the_search_explores_down_the_band_toward_the_window(self):
        # 95c legs with no spread rank first (ask/mid 1.000); 75c legs (1.014) after.
        # The order alone would buy five 95c legs (0.774). The reach check takes two
        # 95c legs, then turns the third away (`window`) and fills from the 75c legs.
        cands = sorted([*board(5, prefix="H", ask="0.95", p="0.96", bid="0.95"),
                        *board(5, prefix="L", ask="0.75", p="0.78")], key=lambda c: c.order)
        b = pt.greedy(cands, 5, T, pt.WINDOWS["safer"])
        self.assertEqual([str(c.ask) for c in b.legs], ["0.95", "0.95", "0.75", "0.75", "0.75"])
        self.assertEqual(b.why[("H02", "yes")], "window")
        self.assertTrue(pt.in_window(pt._product(b.legs), pt.WINDOWS["safer"]))
        r = pt.search("safer", cands, T, sport="Basketball", **DRAWS)
        self.assertTrue(D("0.15") <= r.ticket.priced.scored_price <= D("0.40"))
        self.assertTrue(any("window: " in s for s in r.ticket.leg_reasons))

    def test_tie_breaks_measured_share_then_fewer_legs_then_tickers(self):
        def p(rel, measured, pairs):
            return pt.Priced(D(1), D(0), D(0), D(0), D(1), 1.0, None, rel, pairs, measured, pairs - measured, "fits", None)

        def b(*tickers):
            return pt.Build([cand(x) for x in tickers])

        self.assertEqual(pt._choose([(b("A", "B", "C"), p(0.1, 0, 1)), (b("D", "E", "F"), p(0.1, 1, 1))])[0].legs[0]
                         .m.market_ticker, "D")                                 # more measured
        self.assertEqual(len(pt._choose([(b("A", "B", "C", "D"), p(0.1, 0, 0)),
                                         (b("E", "F", "G"), p(0.1, 0, 0))])[0].legs), 3)  # fewer legs
        self.assertEqual(pt._choose([(b("B", "C", "D"), p(0.1, 0, 0)),
                                     (b("A", "C", "D"), p(0.1, 0, 0))])[0].legs[0].m.market_ticker, "A")
        self.assertEqual(pt._choose([(b("A", "B", "C"), p(0.2, 1, 1)),
                                     (b("D", "E", "F"), p(0.1, 0, 1))])[0].legs[0].m.market_ticker, "D")  # rel first


class Reasons(unittest.TestCase):
    def test_the_template_exactly(self):
        top = cand("A", ask="0.80", bid="0.80", p="0.90")                      # ask/mid 1.000, taken
        dup = cand("B", ask="0.81", bid="0.80", p="0.90", subject="Player A")   # 1.006, same player: rule 8
        low = cand("C", ask="0.85", bid="0.83", p="0.90")                      # 1.012, taken
        cands = sorted([top, dup, low], key=lambda c: c.order)
        b = pt.greedy(cands, 2, T)
        self.assertEqual([c.m.market_ticker for c in b.legs], ["A", "C"])
        lines = pt.leg_reasons(b.legs, cands, b.why)
        # These test markets carry no Kalshi title, so the leg is named by its ticker alone.
        self.assertEqual(lines[0], "YES A: ask 80¢, mid 0.800, ask÷mid 1.000, ranked 1 of 3; "
                                   "kalshi consensus p 0.900; 0 ranked higher were not taken")
        self.assertEqual(lines[1], "YES C: ask 85¢, mid 0.840, ask÷mid 1.012, ranked 3 of 3; "
                                   "kalshi consensus p 0.900; 1 ranked higher were not taken (one prop per player: 1)")

    def test_the_leg_is_named_by_its_kalshi_title_and_the_ticker_stays_the_key(self):
        leg = cand("KXNBAPTS-26NOV22AAABBB-P1-20").leg
        titled = pt.Cand(pl.Leg(pl.Market(**{**leg.market.__dict__, "label": "  P One: 20+ "}), "no", leg.quote),
                         D("0.10"), D("0.21"))
        line = pt.leg_reasons([titled], [titled], {})[0]
        self.assertTrue(line.startswith("NO P One: 20+ pts: ask 21¢, mid 0.110, ask÷mid 1.909"), line)
        self.assertIn("; kalshi consensus p 0.100; ", line)
        self.assertEqual(titled.key, ("KXNBAPTS-26NOV22AAABBB-P1-20", "no"))
        self.assertEqual(pt.label_of(leg.market), "KXNBAPTS-26NOV22AAABBB-P1-20")  # no title: the ticker
        self.assertEqual(pt.leg_in(titled).label, "  P One: 20+ ")  # the pricer's refusals name it too

    def test_the_leg_in_words_has_its_stat_and_its_game(self):
        base = cand("A").leg.market.__dict__

        def label(**kw):
            return pt.label_of(pl.Market(**(base | kw)))

        self.assertEqual(label(label="AJ Dybantsa", stat="double double", game_title="WAS Wizards at CHI Bulls"),
                         "AJ Dybantsa double-double (WAS Wizards at CHI Bulls)")
        self.assertEqual(label(label="Over 183.5 points", market_kind="game_line", stat="total",
                               game_title="EIU at Illinois"), "Over 183.5 points (EIU at Illinois)")
        self.assertEqual(label(label="Aarhus", market_kind="game_line", stat="winner", game_title="Aarhus vs Lyngby"),
                         "Aarhus wins (Aarhus vs Lyngby)")
        self.assertEqual(label(label="10+ corners", market_kind="other", stat=None, game_title=None), "10+ corners")
        self.assertEqual(label(label="X: 2+", stat="some new stat", game_title="G"), "X: 2+ some new stat (G)")
        self.assertEqual(label(label=None), "A")  # no title: the ticker

    def test_a_stale_stored_p_never_reaches_ordering(self):
        # Stored p would rank A first (ask/p 0.80/0.99); the fresh mids rank B first.
        a = pl.Market(**(cand("A").leg.market.__dict__ | {"p_yes": D("0.99")}))
        b = pl.Market(**(cand("B").leg.market.__dict__ | {"p_yes": D("0.50")}))
        quotes = {"A": pl.Quote(D("0.79"), D("0.81"), NOW), "B": pl.Quote(D("0.89"), D("0.91"), NOW)}
        fresh = pl.with_fresh_p([a, b], quotes)
        order, _ = pt.candidates([pl.Leg(m, "yes", quotes[m.market_ticker]) for m in fresh])
        self.assertEqual([c.m.market_ticker for c in order], ["B", "A"])
        self.assertEqual([c.p for c in order], [D("0.900000"), D("0.800000")])
        gone, no_p = pt.candidates([pl.Leg(m, "yes", quotes["A"]) for m in pl.with_fresh_p([a], {})])
        self.assertEqual((gone, no_p), ([], 1))  # not re-read: its stored p is not used

    def test_a_fractional_ask_keeps_its_cents(self):
        c = cand("A", ask="0.805", p="0.90")
        self.assertIn("ask 80.5¢", pt.leg_reasons([c], [c], {})[0])


class CrossSport(unittest.TestCase):
    LOTTO = None  # set in setUp

    def setUp(self):
        self.LOTTO = pt.WINDOWS["lotto"]

    def test_all_one_sport_then_the_last_leg_is_replaced_from_another_sport(self):
        bball = board(14, "Basketball", "B", ask="0.75", p="0.78", bid="0.75")  # ask/mid 1.000
        hockey = board(2, "Hockey", "H", ask="0.76", p="0.78")                  # 1.013: ranked after every B
        cands = sorted([*bball, *hockey], key=lambda c: c.order)
        b = pt.greedy(cands, 13, T, self.LOTTO)
        self.assertEqual({c.m.sport for c in b.legs}, {"Basketball"})
        out = pt.replace_for_second_sport(b, cands, 13, T, self.LOTTO)
        self.assertEqual([c.m.market_ticker for c in out.legs][-1], "H00")
        self.assertEqual(out.removed.m.market_ticker, "B12")
        self.assertEqual(out.why[("B12", "yes")], pt.REPLACED)
        self.assertTrue(pt.in_window(pt._product(out.legs), self.LOTTO))

    def test_a_replacement_must_keep_the_ticket_inside_the_window(self):
        # 13 B legs at 0.75^13 = 0.0238; swapping the last for a 0.40 leg would price 0.0127,
        # still inside; for a 0.20 leg 0.0063, outside: the 0.20 leg is turned away (`window`).
        bball = board(13, "Basketball", "B", ask="0.75", p="0.78", bid="0.75")
        low = [cand("H00", ask="0.20", p="0.21", sport="Hockey")]
        cands = sorted([*bball, *low], key=lambda c: c.order)
        b = pt.greedy(cands, 13, T, self.LOTTO)
        self.assertIsNone(pt.replace_for_second_sport(b, cands, 13, T, self.LOTTO))

    def test_none_to_replace_with_gives_no_ticket(self):
        cands = sorted(board(14, "Basketball", "B", ask="0.75", p="0.78"), key=lambda c: c.order)
        self.assertIsNone(pt.replace_for_second_sport(pt.greedy(cands, 13, T, self.LOTTO), cands, 13, T, self.LOTTO))
        r = pt.search("lotto", cands, T, sport=None, cross_sport=True, **DRAWS)
        self.assertIsNone(r.ticket)
        self.assertEqual(r.binding, "no second sport")

    def test_a_cross_sport_ticket_always_carries_extrapolated_fill(self):
        # Ratios above 1 (fewest legs that fit win), every Basketball leg ranked above Hockey:
        # 13 legs (the fewest that reach 0.03) are 12 B + H00 after the replacement step.
        cands = sorted([*board(14, "Basketball", "B", ask="0.75", p="0.74", bid="0.75"),
                        *board(3, "Hockey", "H", ask="0.76", p="0.75")], key=lambda c: c.order)
        r = pt.search("lotto", cands, T, sport=None, cross_sport=True, **DRAWS)
        self.assertEqual(len(r.ticket.legs), 13)
        self.assertIsNotNone(r.ticket)
        self.assertEqual((r.ticket.scope, r.ticket.sport, r.ticket.fill_note), ("cross_sport", None, "extrapolated fill"))
        self.assertEqual(r.ticket.payload()["fill_note"], "extrapolated fill")
        self.assertGreater(len({c.m.sport for c in r.ticket.legs}), 1)
        self.assertTrue(any("replaced for a second sport: 1" in s for s in r.ticket.leg_reasons))

    def test_a_ticket_already_spanning_two_sports_is_kept_as_built(self):
        cands = sorted([*board(7, "Basketball", "B", ask="0.75", p="0.78"),
                        *board(7, "Hockey", "H", ask="0.75", p="0.78")], key=lambda c: c.order)
        b = pt.greedy(cands, 13, T, self.LOTTO)
        self.assertIs(pt.replace_for_second_sport(b, cands, 13, T, self.LOTTO), b)


FIXTURE = Path(__file__).parent / "fixtures" / "parlay_p21_football.json.gz"
#: The four thin Football slots of Nov 22-24 (M5 snapshots 28-31: Sun 16:45, Mon 10:45,
#: Mon 16:45, Tue 10:45), each slot's Football markets with a side that passes rules 3-4
#: at the snapshot's prices (exported read-only, Nov 24). The owner's simulation of P2.1
#: on them (Nov 24, draws 20,000): Safer (legs, scored price, rel cost over fair).
P21_SAFER = {28: (4, D("0.3966"), -0.061), 29: (4, D("0.3976"), 0.024),
             30: (5, D("0.3960"), 0.034), 31: (4, D("0.3966"), -0.026)}
P21_DRAWS = dict(draws=20000, draws_max=20000)


def football_slot(snapshot):
    """(the snapshot's Football candidates in §3's order, its time) from the fixture."""
    data = next(x for x in json.loads(gzip.decompress(FIXTURE.read_bytes())) if x["snapshot"] == snapshot)
    at = datetime.fromisoformat(data["taken_at"])
    rows = []
    for r in data["markets"]:
        r = dict(r)
        r["starts_at"] = datetime.fromisoformat(r["starts_at"])
        rows.append(r)
    markets = pl.markets_from_rows(rows)
    quotes = {m.market_ticker: pl.Quote(m.yes_bid, m.yes_ask, at) for m in markets}
    f = pl.funnel(pl.legs_of(markets, quotes), at)
    cands, _ = pt.candidates(f.passed)
    return [c for c in cands if c.m.sport == "Football"], at


class P21(unittest.TestCase):
    """docs/preregistration_parlays.md v2.1 §3 (owner, Nov 24): the depth-first rebuild only
    when the greedy draws nothing, its deterministic budget, and `3 per game`."""

    def test_the_greedy_ticket_is_p2s_and_the_rebuild_never_runs_when_the_greedy_draws(self):
        cands = sorted(board(6, ask="0.75", p="0.74"), key=lambda c: c.order)
        with mock.patch.object(pt, "depth_first", side_effect=AssertionError("rebuild ran")):
            r = pt.search("safer", cands, T, sport="Basketball", **DRAWS)
        self.assertEqual(r.ticket.search, "greedy")
        # Byte-identical to the greedy pass of the same count: legs, prices and reasons.
        b = pt.greedy(cands, len(r.ticket.legs), T, pt.WINDOWS["safer"])
        self.assertEqual([c.key for c in b.legs], [c.key for c in r.ticket.legs])
        self.assertEqual(pt.leg_reasons(b.legs, cands, b.why), r.ticket.leg_reasons)

    def test_the_four_football_slots_reproduce_the_simulation(self):
        for snapshot, (legs, price, rel) in P21_SAFER.items():
            with self.subTest(snapshot=snapshot):
                cands, _ = football_slot(snapshot)
                r = pt.search("safer", cands, T, sport="Football", **P21_DRAWS)
                self.assertIsNotNone(r.ticket, r.tried)
                self.assertEqual((r.ticket.search, len(r.ticket.legs), r.ticket.priced.scored_price.quantize(D("0.0001"))),
                                 ("depth_first", legs, price))
                self.assertAlmostEqual(r.ticket.priced.rel_cost, rel, places=3)
                self.assertTrue(any(v.startswith("rebuilt: ") for v in r.tried.values()), r.tried)
                # P2's greedy pass drew nothing at any count on these slots.
                self.assertTrue(all(len(pt.greedy(cands, n, T, pt.WINDOWS["safer"]).legs) < n for n in (4, 5)))

    def test_lotto_on_the_two_game_slot_is_3_per_game_and_mid_too(self):
        cands, _ = football_slot(28)  # Sun 16:45: 2 games, at most 6 legs under rule 10
        self.assertEqual(len({c.m.game_ticker for c in cands}), 2)
        for tier in ("mid", "lotto"):
            with self.subTest(tier=tier):
                r = pt.search(tier, cands, T, sport="Football", **P21_DRAWS)
                self.assertIsNone(r.ticket)
                self.assertEqual(r.binding, "3 per game")
                self.assertTrue(any(v.startswith("3 per game: ") for v in r.tried.values()), r.tried)

    def test_a_count_above_3_per_game_is_never_tried_and_binds_when_none_is_left(self):
        cands = sorted([cand(f"S{i}", ask="0.75", p="0.78", game="G1", event=f"E{i}", subject=f"P{i}")
                        for i in range(6)], key=lambda c: c.order)
        r = pt.search("safer", cands, T, sport="Basketball", **DRAWS)
        self.assertEqual(r.binding, "3 per game")
        self.assertEqual(r.tried[4], "3 per game: 4 legs > 3 x 1 games")

    def test_the_budget_counts_checks_never_time_and_is_deterministic(self):
        cands, _ = football_slot(28)
        window = pt.WINDOWS["safer"]
        one, how = pt.depth_first(cands, 4, T, window)
        two, how2 = pt.depth_first(cands, 4, T, window)
        self.assertEqual((how, how2), ("found", "found"))
        self.assertEqual([c.key for c in one.legs], [c.key for c in two.legs])
        self.assertEqual(pt.depth_first(cands, 4, T, window, budget=1), (None, "budget spent"))
        # The clock plays no part: a frozen or a racing clock gives the same ticket.
        with mock.patch("time.perf_counter", side_effect=itertools.count(0, 1000)), \
                mock.patch("time.monotonic", side_effect=itertools.count(0, 1000)):
            three, _ = pt.depth_first(cands, 4, T, window)
        self.assertEqual([c.key for c in three.legs], [c.key for c in one.legs])
        self.assertEqual(pt.REBUILD_BUDGET, 20000)

    def test_a_rebuilt_ticket_is_the_first_in_order_that_keeps_every_rule(self):
        cands, _ = football_slot(31)
        b, how = pt.depth_first(cands, 4, T, pt.WINDOWS["safer"])
        self.assertEqual(how, "found")
        idx = [cands.index(c) for c in b.legs]
        self.assertEqual(idx, sorted(idx))  # taken in §3's order
        legs = []
        for c in b.legs:  # each keeps rules 5-11 against the legs before it
            self.assertIsNone(pt.first_broken(legs, c, 4, T))
            legs.append(c)
        self.assertTrue(pt.in_window(pt._product(b.legs), pt.WINDOWS["safer"]))
        # Every candidate ranked above a taken leg and not held has a reason.
        last = max(idx)
        self.assertEqual({c.key for c in cands[:last] if c not in b.legs} - set(b.why), set())


class Slot(unittest.TestCase):
    def test_every_tier_per_sport_and_one_cross_sport_lotto(self):
        legs = [c.leg for c in [*board(12, "Basketball", "B", ask="0.75", p="0.78"),
                                *board(4, "Hockey", "H", ask="0.75", p="0.78")]]
        s = pt.slot(legs, T, **DRAWS)
        keys = [(r.tier, r.scope, r.sport) for r in s.results]
        self.assertEqual(keys, [("safer", "sport", "Basketball"), ("safer", "sport", "Hockey"),
                                ("mid", "sport", "Basketball"), ("mid", "sport", "Hockey"),
                                ("lotto", "sport", "Basketball"), ("lotto", "sport", "Hockey"),
                                ("lotto", "cross_sport", None)])
        by = {k: r for k, r in zip(keys, s.results)}
        self.assertEqual(by[("mid", "sport", "Hockey")].binding, "rules 1-4: 4 candidates < 6")
        for r in s.results:
            if r.ticket and r.scope == "sport":
                self.assertEqual({c.m.sport for c in r.ticket.legs}, {r.sport})
        self.assertEqual(Counter(r.ticket is None for r in s.results)[False] >= 4, True)


if __name__ == "__main__":
    unittest.main()
