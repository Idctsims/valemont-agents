"""E1, "bet $X to win $Y" (consensus/payout.py; docs/payout_target.md): the
arithmetic, the search on a made-up board, the nearest answer, and the stored
ticket's shape. Pure: no database, no network."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from consensus import correlation as cr
from consensus import parlay_legs as pl
from consensus import payout

NOW = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)
D = Decimal


def market(i: int, bid: str, ask: str, *, yes_only: bool = False, eligible: bool = True) -> pl.Market:
    """One game's moneyline (one market per game: every pair is across games, independent)."""
    game = f"KXNBAGAME-26NOV25G{i:02d}"
    mid = (D(bid) + D(ask)) / 2
    return pl.Market(
        market_ticker=f"{game}-HOM", event_ticker=game, game_ticker=game, series_ticker="KXNBAGAME",
        sport="Basketball", league="nba", starts_at=NOW + timedelta(hours=3), market_kind="game_line",
        stat="winner", subject=f"Team {i}", team=f"T{i:02d}", line=None, tier="kalshi", p_yes=mid,
        yes_bid=D(bid), yes_ask=D(ask), payout="binary", combo_eligible=eligible, combo_yes_only=yes_only,
        combo_size_max=None, mutually_exclusive=False, label=f"Team {i}", game_title=f"Away {i} at Team {i}")


#: A spread of prices, so a ticket can land near many targets.
ASKS = ["0.30", "0.36", "0.42", "0.48", "0.55", "0.61", "0.67", "0.73", "0.80", "0.86", "0.90", "0.45",
        "0.52", "0.58", "0.64", "0.70"]
BOARD = [market(i, str(D(a) - D("0.02")), a) for i, a in enumerate(ASKS)]
FRESH = {m.market_ticker: NOW - timedelta(minutes=5) for m in BOARD}


class Arithmetic(unittest.TestCase):
    def test_fee_rounds_up_at_a_millionth(self) -> None:
        self.assertEqual(payout.fee(D("0.5")), D("0.0175"))
        self.assertEqual(payout.fee(D("0.1")), D("0.0063"))

    def test_the_target_price_pays_back_the_payout(self) -> None:
        for stake, back in ((D(10), D(100)), (D(25), D(60)), (D(5), D(400))):
            c = payout.target_price(stake, back)
            self.assertAlmostEqual(float(c + D("0.07") * c * (1 - c)), float(stake / back), places=6)
            self.assertIn(payout.contracts(stake, c), (int(back) - 1, int(back)))

    def test_ten_to_a_hundred_is_about_nine_point_four_cents(self) -> None:
        self.assertEqual(round(payout.target_price(D(10), D(100)), 4), D("0.0940"))


class Search(unittest.TestCase):
    t = cr.load_table()

    def test_ten_to_a_hundred_pays_at_least_a_hundred_and_at_most_ten_percent_more(self) -> None:
        legs = payout.legs_from(BOARD, FRESH, NOW)
        ans = payout.search(D(10), D(100), legs, self.t, draws=20_000, draws_max=20_000)
        self.assertFalse(ans.nearest)
        self.assertTrue(1 <= len(ans.picks) <= 3)
        lo, hi = ans.target / D("1.10"), ans.target
        rels = [p.priced.rel_cost for p in ans.picks]
        self.assertEqual(rels, sorted(rels))
        for p in ans.picks:
            self.assertTrue(lo <= p.priced.scored_price <= hi, p.priced.scored_price)
            self.assertGreaterEqual(payout.contracts(D(10), p.priced.scored_price), 99)
            self.assertLessEqual(len(p.legs), payout.MAX_LEGS)
            self.assertEqual(len({c.m.game_ticker for c in p.legs}), len(p.legs))
        keys = [tuple(sorted(c.key for c in p.legs)) for p in ans.picks]
        self.assertEqual(len(keys), len(set(keys)))

    def test_a_target_under_a_cent_answers_the_nearest(self) -> None:
        legs = payout.legs_from(BOARD, FRESH, NOW)
        ans = payout.search(D(1), D(1000), legs, self.t, draws=20_000, draws_max=20_000)
        self.assertTrue(ans.nearest)
        self.assertTrue(ans.picks)
        for p in ans.picks:
            self.assertGreaterEqual(p.priced.scored_price, D("0.01"))

    def test_a_single_bet_when_one_side_is_the_target(self) -> None:
        legs = payout.legs_from(BOARD, FRESH, NOW)
        ans = payout.search(D(10), D(15), legs, self.t, draws=20_000, draws_max=20_000)
        self.assertFalse(ans.nearest)
        self.assertTrue(any(len(p.legs) == 1 for p in ans.picks))

    def test_nothing_to_build_from_is_an_empty_nearest_answer(self) -> None:
        ans = payout.search(D(10), D(100), [], self.t)
        self.assertEqual(ans.picks, [])
        self.assertEqual(ans.candidates, 0)


class Legs(unittest.TestCase):
    def test_stale_legs_and_asks_outside_5_to_95c_are_out(self) -> None:
        stale = {**FRESH, BOARD[0].market_ticker: NOW - timedelta(minutes=21)}
        legs = payout.legs_from(BOARD, stale, NOW)
        self.assertNotIn(BOARD[0].market_ticker, {leg.market.market_ticker for leg in legs})
        for leg in legs:
            self.assertTrue(D("0.05") <= leg.ask <= D("0.95"))

    def test_a_yes_only_event_gives_yes_legs_only_and_ineligible_none(self) -> None:
        board = [market(1, "0.48", "0.50", yes_only=True), market(2, "0.48", "0.50", eligible=False)]
        at = {m.market_ticker: NOW for m in board}
        legs = payout.legs_from(board, at, NOW)
        self.assertEqual([(leg.market.market_ticker, leg.side) for leg in legs], [(board[0].market_ticker, "yes")])


class Stored(unittest.TestCase):
    def test_a_ticket_as_the_page_reads_it(self) -> None:
        t = cr.load_table()
        legs = payout.legs_from(BOARD, FRESH, NOW)
        ans = payout.search(D(10), D(100), legs, t, draws=20_000, draws_max=20_000)
        j = payout.ticket_json(ans.picks[0], D(10), {}, NOW)
        self.assertEqual(j["k"], len(j["legs"]))
        self.assertEqual(j["contracts"], payout.contracts(D(10), ans.picks[0].priced.scored_price))
        self.assertAlmostEqual(j["exp"], round((j["fair"] * j["contracts"] - 10) / 10, 4))
        leg = j["legs"][0]
        for key in ("m", "side", "k", "label", "who", "stat", "game", "sport", "start", "ask", "p", "tier", "age"):
            self.assertIn(key, leg)
        self.assertEqual(leg["age"], 300)


if __name__ == "__main__":
    unittest.main()
