"""Phase 4.3: the ticket search, prices and leg reasons (docs/preregistration_parlays.md §3-§4).

Each ticket rule 5-11 on its boundary, the order and tie-breaks, the tick
floor, the fee rounding, determinism (same ticket, same reason bytes), the
cross-sport replacement step and its "extrapolated fill" label. The real rho
table is used, so rule 7 and 9 come from the shipped pricer."""

import unittest
from collections import Counter
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
    def test_ask_over_p_lowest_first_then_ticker_then_side(self):
        a = cand("B", ask="0.80", p="0.90").leg   # 0.889
        b = cand("A", ask="0.90", p="0.90").leg   # 1.0
        c = cand("C", ask="0.81", p="0.90").leg   # 0.9
        d = cand("A0", ask="0.90", p="0.90").leg  # 1.0, ticker after "A"
        out, _ = pt.candidates([b, d, c, a])
        self.assertEqual([x.m.market_ticker for x in out], ["B", "C", "A", "A0"])

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


def board(n, sport="Basketball", prefix="L", ask="0.92", p="0.95"):
    """n candidates on n games of one sport, ordered by their tickers."""
    return [cand(f"{prefix}{i:02d}", ask=ask, p=p, sport=sport) for i in range(n)]


class Search(unittest.TestCase):
    def test_one_ticket_per_count_priced_and_the_lowest_relative_cost_wins(self):
        # Identical independent legs: the relative cost is (ask / p)^n - 1, so
        # below 1 the most legs win and above 1 the fewest do.
        under = pt.search("safer", sorted(board(6, ask="0.92", p="0.95"), key=lambda c: c.order), T,
                          sport="Basketball", **DRAWS)
        self.assertEqual((sorted(under.tried), len(under.ticket.legs)), ([3, 4, 5], 5))
        r = pt.search("safer", sorted(board(6, ask="0.95", p="0.94"), key=lambda c: c.order), T,
                      sport="Basketball", **DRAWS)
        self.assertIsNotNone(r.ticket)
        self.assertEqual(len(r.ticket.legs), 3)
        self.assertEqual(r.ticket.agent, "parlay_safer")
        self.assertEqual((r.ticket.scope, r.ticket.fill_note, r.ticket.rule_version), ("sport", None, "P1"))
        self.assertIsNone(r.ticket.expected_quote)
        self.assertEqual(r.ticket.payload()["expected_quote_note"], "pending (M1)")
        self.assertEqual(r.ticket.rho_sha256, T.sha256)

    def test_the_same_candidates_give_the_same_ticket_and_the_same_reason_bytes(self):
        cands = sorted([*board(8), cand("X", ask="0.95", p="0.96", game="GX", subject="Same Guy"),
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

    def test_an_empty_slot_names_the_rule_that_bound(self):
        cands = sorted(board(5, ask="0.20", p="0.25"), key=lambda c: c.order)  # 0.2^3 < 1c
        r = pt.search("safer", cands, T, sport="Basketball", **DRAWS)
        self.assertIsNone(r.ticket)
        self.assertEqual(r.binding, "tick floor")

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
        top = cand("A", ask="0.80", p="0.90")                                  # 0.889, taken
        dup = cand("B", ask="0.81", p="0.90", subject="Player A")               # 0.900, same player: rule 8
        low = cand("C", ask="0.85", p="0.90")                                   # 0.944, taken
        cands = sorted([top, dup, low], key=lambda c: c.order)
        b = pt.greedy(cands, 2, T)
        self.assertEqual([c.m.market_ticker for c in b.legs], ["A", "C"])
        lines = pt.leg_reasons(b.legs, cands, b.why)
        self.assertEqual(lines[0], "YES A: kalshi consensus p 0.900, ask 80¢, ask÷p 0.889, "
                                   "ranked 1 of 3; 0 ranked higher were not taken")
        self.assertEqual(lines[1], "YES C: kalshi consensus p 0.900, ask 85¢, ask÷p 0.944, "
                                   "ranked 3 of 3; 1 ranked higher were not taken (one prop per player: 1)")

    def test_a_fractional_ask_keeps_its_cents(self):
        c = cand("A", ask="0.805", p="0.90")
        self.assertIn("ask 80.5¢", pt.leg_reasons([c], [c], {})[0])


class CrossSport(unittest.TestCase):
    def test_all_one_sport_then_the_last_leg_is_replaced_from_another_sport(self):
        bball = board(12, "Basketball", "B", ask="0.92", p="0.96")
        hockey = board(2, "Hockey", "H", ask="0.93", p="0.95")               # ranked after every B
        cands = sorted([*bball, *hockey], key=lambda c: c.order)
        b = pt.greedy(cands, 11, T)
        self.assertEqual({c.m.sport for c in b.legs}, {"Basketball"})
        out = pt.replace_for_second_sport(b, cands, 11, T)
        self.assertEqual([c.m.market_ticker for c in out.legs][-1], "H00")
        self.assertEqual(out.removed.m.market_ticker, "B10")
        self.assertEqual(out.why[("B10", "yes")], pt.REPLACED)

    def test_none_to_replace_with_gives_no_ticket(self):
        cands = sorted(board(12, "Basketball", "B"), key=lambda c: c.order)
        self.assertIsNone(pt.replace_for_second_sport(pt.greedy(cands, 11, T), cands, 11, T))
        r = pt.search("lotto", cands, T, sport=None, cross_sport=True, **DRAWS)
        self.assertIsNone(r.ticket)
        self.assertEqual(r.binding, "no second sport")

    def test_a_cross_sport_ticket_always_carries_extrapolated_fill(self):
        # Ratios above 1 (fewest legs win), every Basketball leg ranked above Hockey:
        # 11 legs are 10 B + H00 after the replacement step.
        cands = sorted([*board(12, "Basketball", "B", ask="0.95", p="0.94"),
                        *board(3, "Hockey", "H", ask="0.97", p="0.95")], key=lambda c: c.order)
        r = pt.search("lotto", cands, T, sport=None, cross_sport=True, **DRAWS)
        self.assertEqual(len(r.ticket.legs), 11)
        self.assertIsNotNone(r.ticket)
        self.assertEqual((r.ticket.scope, r.ticket.sport, r.ticket.fill_note), ("cross_sport", None, "extrapolated fill"))
        self.assertEqual(r.ticket.payload()["fill_note"], "extrapolated fill")
        self.assertGreater(len({c.m.sport for c in r.ticket.legs}), 1)
        self.assertTrue(any("replaced for a second sport: 1" in s for s in r.ticket.leg_reasons))

    def test_a_ticket_already_spanning_two_sports_is_kept_as_built(self):
        cands = sorted([*board(6, "Basketball", "B", ask="0.95", p="0.97"),
                        *board(6, "Hockey", "H", ask="0.95", p="0.97")], key=lambda c: c.order)
        b = pt.greedy(cands, 11, T)
        self.assertIs(pt.replace_for_second_sport(b, cands, 11, T), b)


class Slot(unittest.TestCase):
    def test_every_tier_per_sport_and_one_cross_sport_lotto(self):
        legs = [c.leg for c in [*board(12, "Basketball", "B", ask="0.95", p="0.97"),
                                *board(4, "Hockey", "H", ask="0.95", p="0.97")]]
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
