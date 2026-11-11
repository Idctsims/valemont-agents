"""The combo pricer (Chat 8 Phase 3, owner decisions Nov 11): the bivariate
CDF, Monte Carlo, the PSD shrink, the rho table, the refusals, nested rungs,
the NO-side sign flip, cross-game independence, the done-when pair and the
Kalshi combo status. Pure: no database, no network."""

from __future__ import annotations

import json
import math
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from consensus import combo, correlation as cr
from core import paths

FIX = Path(__file__).with_name("fixtures")
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
T = cr.load_table()


def leg(ticker: str, *, side: str = "yes", p: float = 0.5, kind: str = "prop", series: str = "KXNFLPASSYDS",
        game: str = "G1", league: str = "nfl", sport: str = "Football", subject: str | None = "Player A",
        stat: str | None = None, line: float | None = 9.5, team: str | None = "KC", event: str | None = None,
        exclusive: bool | None = False, eligible: bool = True, yes_only: bool = False, size_max: int | None = None,
        payout: str = "binary", ask: float | None = 0.5, starts: datetime | None = None) -> combo.LegIn:
    return combo.LegIn(
        market_ticker=ticker, side=side, event_ticker=event or f"{series}-{game}", game_ticker=game,
        series_ticker=series, sport=sport, league=league, market_kind=kind, payout=payout, subject=subject,
        stat=stat, line=line, label=ticker, team=team, tier="kalshi", p_yes=p, yes_ask=ask,
        no_ask=None if ask is None else 1 - ask + 0.02, fee_yes=0.0175, fee_no=0.0175, stale=False,
        mutually_exclusive=exclusive, combo_eligible=eligible, combo_yes_only=yes_only, combo_size_max=size_max,
        starts_at=starts or NOW + timedelta(hours=3))


def win(club: str, p: float, *, game: str = "G1", side: str = "yes", league: str = "nfl", series: str = "KXNFLGAME",
        exclusive: bool | None = True) -> combo.LegIn:
    return leg(f"{series}-{game}-{club}", side=side, p=p, kind="game_line", series=series, game=game, league=league,
               subject=club, stat="winner", line=None, team=club, exclusive=exclusive)


def priced(*legs: combo.LegIn, **kw: object) -> combo.Quote:
    rows = {x.market_ticker: x for x in legs}
    v = combo.validate([{"market_ticker": x.market_ticker, "side": x.side} for x in legs], rows, NOW)
    return combo.price(v, T, **kw)  # type: ignore[arg-type]


class Bivariate(unittest.TestCase):
    def test_against_the_reference_table_to_1e_7(self) -> None:
        pts = json.loads((FIX / "bvn_reference.json").read_text())["points"]
        self.assertGreater(len(pts), 500)
        for h, k, r, ref in pts:
            self.assertAlmostEqual(cr.bvn_cdf(h, k, r), ref, delta=1e-7, msg=(h, k, r))

    def test_the_origin_identity_and_the_edges(self) -> None:
        for r in (-0.99, -0.5, 0.0, 0.3, 0.8, 0.95):
            self.assertAlmostEqual(cr.bvn_cdf(0, 0, r), 0.25 + math.asin(r) / (2 * math.pi), places=12)
        self.assertEqual(cr.bvn_cdf(-math.inf, 1.0, 0.4), 0.0)
        self.assertAlmostEqual(cr.bvn_cdf(math.inf, 1.0, 0.4), cr.Phi(1.0), places=15)
        self.assertAlmostEqual(cr.rect2(-math.inf, 0.5, -math.inf, -0.2, 0.0), cr.Phi(0.5) * cr.Phi(-0.2), places=12)


class MonteCarlo(unittest.TestCase):
    SIG = [[1.0, 0.3, 0.2], [0.3, 1.0, 0.1], [0.2, 0.1, 1.0]]

    def test_reproducible_by_the_sorted_legs_with_its_standard_error(self) -> None:
        lo, hi = [-0.3, 0.1, -1.0], [math.inf] * 3
        a = cr.joint(lo, hi, self.SIG, ["b:yes", "a:no", "c:yes"], draws=20_000, draws_max=20_000)
        b = cr.joint(lo, hi, self.SIG, ["c:yes", "b:yes", "a:no"], draws=20_000, draws_max=20_000)
        c = cr.joint(lo, hi, self.SIG, ["c:yes", "b:yes", "a:yes"], draws=20_000, draws_max=20_000)
        self.assertEqual(a, b)
        self.assertNotEqual(a.p, c.p)
        self.assertEqual(a.method, "monte_carlo")
        self.assertAlmostEqual(a.se, math.sqrt(a.p * (1 - a.p) / 20_000), places=12)

    def test_independent_legs_match_the_product_within_4_se(self) -> None:
        ident = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
        z = [cr.Phi_inv(1 - p) for p in (0.6, 0.45, 0.7)]
        j = cr.joint(z, [math.inf] * 3, ident, ["x", "y", "z"], draws=200_000)
        self.assertLess(abs(j.p - 0.6 * 0.45 * 0.7), 4 * j.se)

    def test_a_small_estimate_escalates_to_the_larger_draw(self) -> None:
        z = [cr.Phi_inv(1 - 0.08)] * 3
        small = cr.joint(z, [math.inf] * 3, self.SIG, ["q"], draws=2_000, draws_max=50_000)
        self.assertEqual(small.draws, 50_000)  # 2,000 draws: SE well over 5% of ~0.001
        big = cr.joint([-1.0] * 3, [math.inf] * 3, self.SIG, ["q"], draws=2_000, draws_max=50_000)
        self.assertEqual(big.draws, 2_000)


class PsdShrink(unittest.TestCase):
    def test_a_psd_matrix_is_untouched(self) -> None:
        s, lam = cr.psd_shrink(MonteCarlo.SIG)
        self.assertEqual(lam, 1.0)
        self.assertEqual(s, MonteCarlo.SIG)

    def test_the_smallest_shrink_that_makes_it_psd(self) -> None:
        bad = [[1.0, 0.9, 0.9], [0.9, 1.0, -0.9], [0.9, -0.9, 1.0]]
        self.assertIsNone(cr.cholesky(bad))
        s, lam = cr.psd_shrink(bad)
        self.assertLess(lam, 1.0)
        self.assertIsNotNone(cr.cholesky(s))
        self.assertIsNone(cr.cholesky(cr.shrunk(bad, lam + 1e-6)))
        self.assertAlmostEqual(s[0][1], 0.9 * lam)


class Table(unittest.TestCase):
    def test_the_worker_copy_is_the_docs_file(self) -> None:
        docs = (paths.REPO_ROOT / "docs" / "correlation_rho.json").read_bytes()
        self.assertEqual(cr.table_sha256(docs), T.sha256)
        self.assertRegex(T.sha256, r"^[0-9a-f]{64}$")

    def test_every_row_is_sourced_and_a_correlation(self) -> None:
        for table, rows in T.data["rows"].items():
            for key, r in rows.items():
                self.assertIn(r["src"], T.data["sources"], (table, key))
                self.assertLessEqual(abs(r["rho"]), 1.0)
                self.assertRegex(key, r"^(self|mate|opp|team|oppteam|game)\|[a-z_0-9]+\|[a-z_0-9]+$")
        self.assertEqual(T.data["sources"]["nba_hub"]["commit"][:7], "3a3e298")
        self.assertEqual(T.data["sources"]["nba_hub"]["flag"], "external, unreproduced")
        self.assertEqual(T.row("nfl", "team|pass_yds|over"), (0.28, "measured_external"))

    def test_the_nba_values_adopted(self) -> None:
        self.assertEqual(T.row("nba", "team|pts|win"), (0.128, "measured_external"))
        self.assertEqual(T.row("nba", "self|pts|tpm"), (0.737, "measured_external"))
        self.assertEqual(T.row("nba", "mate|pts|pts"), (-0.024, "measured_external"))
        self.assertIsNone(T.row("nba", "self|pra|pts"))  # refused, never listed


def role(**kw: object) -> cr.Role:
    return cr.Role(**kw)  # type: ignore[arg-type]


class Relations(unittest.TestCase):
    def rho(self, league: str, a: cr.Role, b: cr.Role, sport: str = "") -> cr.PairRho:
        table, borrowed = T.table_for(league, sport)
        return cr.pair_rho(T, table, borrowed, T.no_draw(league), a, b)

    def test_the_opponents_moneyline_is_own_win_on_no_where_there_is_no_draw(self) -> None:
        own = self.rho("nba", role(kind="pts", club="BOS", player="BOS|x"), role(line="win", club="BOS"))
        opp = self.rho("nba", role(kind="pts", club="BOS", player="BOS|x"), role(line="win", club="TOR"))
        self.assertEqual(own.rho, 0.128)
        self.assertEqual(opp.rho, -0.128)
        for lg in ("nfl", "nhl", "cfb", "mcbb"):
            self.assertTrue(T.no_draw(lg))
        soccer = self.rho("epl", role(kind="goal", club="ARS", player="ARS|x"), role(line="win", club="CHE"),
                          sport="Soccer")
        self.assertEqual((soccer.rho, soccer.cls), (0.0, cr.UNMODELED))

    def test_one_qb_against_either_moneyline_is_consistent(self) -> None:
        # Owner, Nov 11: no opponent rows; the own-team row's sign flips.
        qb = role(kind="pass_yds", club="BUF", player="BUF|qb")
        own = self.rho("nfl", qb, role(line="win", club="BUF"))
        other = self.rho("nfl", qb, role(line="win", club="NYJ"))
        self.assertEqual((own.rho, own.cls), (0.15, "prior"))
        self.assertEqual((other.rho, other.cls), (-0.15, "prior"))
        self.assertFalse(any(k.startswith("oppteam|") for t in T.data["rows"].values() for k in t))
        # And priced: the QB over with NYJ YES equals the QB over with BUF NO.
        a = leg("PY", p=0.55, series="KXNFLPASSYDS", team="BUF")
        with_nyj = priced(a, win("NYJ", 0.40, exclusive=None)).fair_p
        with_buf_no = priced(a, win("BUF", 0.60, side="no", exclusive=None)).fair_p
        self.assertAlmostEqual(with_nyj, with_buf_no, places=12)

    def test_the_nhl_priors(self) -> None:
        g, p = role(kind="goal", club="TB", player="TB|k"), role(kind="pts", club="TB", player="TB|k")
        # Owner, Nov 11 (.3): goal and point are an implication, not a row.
        self.assertIsNone(T.row("nhl", "self|goal|pts"))
        self.assertEqual(self.rho("nhl", g, p).cls, cr.UNMODELED)  # rungs that do not imply: no row
        self.assertEqual(self.rho("nhl", g, role(line="win", club="TB")).rho, 0.25)
        mate = role(kind="goal", club="TB", player="TB|m")
        self.assertEqual(self.rho("nhl", g, mate).rho, 0.05)
        self.assertEqual(self.rho("nhl", role(kind="ast", club="TB", player="TB|m"), g).rho, 0.20)
        for a, b in (("goal", "goal"), ("goal", "pts"), ("ast", "goal"), ("ast", "pts"), ("first_goal", "goal"),
                     ("first_goal", "pts")):
            r = self.rho("nhl", role(kind=a, club="TB", player="TB|x"), role(kind=b, club="BUF", player="BUF|y"))
            self.assertEqual((r.rho, r.cls), (0.0, "prior"), (a, b))
        self.assertEqual(self.rho("nhl", g, role(line="win", club="BUF")).rho, -0.25)
        self.assertEqual(self.rho("nhl", p, role(kind="pts", club="TB", player="TB|m")).rho, 0.10)
        r = self.rho("nhl", p, role(kind="pts", club="BUF", player="BUF|o"))
        self.assertEqual((r.rho, r.cls), (0.0, "prior"))
        self.assertEqual(self.rho("nhl", g, role(line="over")).rho, 0.30)
        self.assertEqual(T.row("nhl", "self|goal|shots"), (0.35, "prior"))
        self.assertEqual(T.row("nhl", "team|shots|over"), (0.15, "prior"))
        self.assertEqual(T.kind_of("nhl", "nhl", "KXNHLANYGOAL"), "goal")

    def test_a_cover_uses_its_measured_row_else_the_moneyline_borrowed(self) -> None:
        nba = self.rho("nba", role(kind="pts", club="BOS", player="BOS|x"), role(line="cover", club="BOS"))
        self.assertEqual((nba.rho, nba.cls), (0.121, "measured_external"))
        nfl = self.rho("nfl", role(kind="rush_yds", club="KC", player="KC|rb"), role(line="cover", club="KC"))
        self.assertEqual((nfl.rho, nfl.cls), (0.30, "borrowed"))

    def test_cfb_and_mcbb_are_borrowed(self) -> None:
        r = self.rho("cfb", role(line="win", club="ALA"), role(line="cover", club="ALA"))
        self.assertEqual((r.rho, r.cls), (0.80, "borrowed"))
        r = self.rho("mcbb", role(line="win", club="DUKE"), role(line="cover", club="UNC"))
        self.assertEqual((r.rho, r.cls), (-0.939, "borrowed"))

    def test_a_stat_beside_a_sum_that_holds_it_is_refused_in_every_sport(self) -> None:
        cases = [("nba", "pts", "pra"), ("nba", "stl", "sb"), ("nfl", "rush_yds", "rr_yds"), ("nhl", "ast", "pts")]
        for lg, a, b in cases:
            with self.assertRaisesRegex(cr.Refused, "not jointly gaussian; pick one"):
                self.rho(lg, role(kind=a, club="X", player="X|p"), role(kind=b, club="X", player="X|p"))

    def test_anything_else_in_the_game_is_an_unmodeled_zero(self) -> None:
        r = self.rho("nfl", role(kind="pass_att", club="KC", player="KC|qb"), role(line="win", club="KC"))
        self.assertEqual((r.rho, r.cls), (0.0, cr.UNMODELED))


class Refusals(unittest.TestCase):
    def assert_refused(self, why: str, *legs: combo.LegIn) -> None:
        with self.assertRaisesRegex(cr.Refused, why):
            priced(*legs)

    def test_both_sides_and_the_same_leg_twice(self) -> None:
        a = leg("KXNFLPASSYDS-G1-A-250")
        rows = {a.market_ticker: a}
        for sides, why in ((("yes", "no"), "both sides of one market"), (("yes", "yes"), "the same leg twice")):
            with self.assertRaisesRegex(cr.Refused, why):
                combo.validate([{"market_ticker": a.market_ticker, "side": s} for s in sides], rows, NOW)

    def test_two_outcomes_of_one_result(self) -> None:
        self.assert_refused("two outcomes of one result", win("KC", 0.6), win("ATL", 0.4))
        # Not flagged by Kalshi: refused all the same.
        self.assert_refused("two outcomes of one result", win("KC", 0.6, exclusive=None), win("ATL", 0.4, exclusive=None))

    def test_rungs_that_cannot_both_hit(self) -> None:
        self.assert_refused("can't both hit", leg("P-25", line=24.5, p=0.3), leg("P-20", line=19.5, p=0.5, side="no"))

    def test_scalar_started_and_off_the_board(self) -> None:
        self.assert_refused("scalar markets are never legs", leg("L1", payout="scalar"), leg("L2", series="KXNFLRECYDS"))
        self.assert_refused("the game has started", leg("L1", starts=NOW - timedelta(minutes=1)),
                            leg("L2", series="KXNFLRECYDS"))
        with self.assertRaisesRegex(cr.Refused, "not on the board"):
            combo.validate([{"market_ticker": "GONE", "side": "yes"}, {"market_ticker": "L2", "side": "yes"}],
                           {"L2": leg("L2")}, NOW)

    def test_pts_and_pra_for_one_player(self) -> None:
        self.assert_refused("pick one", leg("A-PTS", series="KXNBAPTS", league="nba", sport="Basketball", team="BOS"),
                            leg("A-PRA", series="KXNBAPRA", league="nba", sport="Basketball", team="BOS"))


class NestedRungs(unittest.TestCase):
    def test_same_direction_is_the_harder_rung(self) -> None:
        q = priced(leg("P-10", line=9.5, p=0.7), leg("P-20", line=19.5, p=0.35), leg("P-20P", line=19.5, p=0.35,
                                                                                     series="KXNFLPASSYDSPROT"))
        self.assertAlmostEqual(q.fair_p, 0.35, places=12)
        self.assertEqual(q.blocks[0].latents, 1)
        self.assertEqual(len(q.blocks[0].nested), 1)

    def test_opposite_directions_price_exactly_p_low_minus_p_high(self) -> None:
        q = priced(leg("P-10", line=9.5, p=0.7), leg("P-20", line=19.5, p=0.35, side="no"))
        self.assertAlmostEqual(q.fair_p, 0.7 - 0.35, places=12)

    def test_spread_rungs_on_both_clubs_share_the_margin(self) -> None:
        a = leg("KXNFLSPREAD-G1-KC4", kind="game_line", series="KXNFLSPREAD", stat="spread", line=4.5, p=0.40,
                team="KC")
        b = leg("KXNFLSPREAD-G1-ATL6", kind="game_line", series="KXNFLSPREAD", stat="spread", line=6.5, p=0.25,
                team="ATL")
        with self.assertRaisesRegex(cr.Refused, "can't both hit"):
            priced(a, replace(b, line=2.5))
        # KC by over 4.5 and NOT (ATL by over 6.5): the KC rung alone binds.
        q = priced(a, replace(b, side="no"))
        self.assertAlmostEqual(q.fair_p, 0.40, places=12)
        # NOT (KC by over 4.5) and NOT (ATL by over 6.5): margin in (-6.5, 4.5].
        q = priced(replace(a, side="no"), replace(b, side="no"))
        self.assertAlmostEqual(q.fair_p, (1 - 0.25) - 0.40, places=12)


def nhl(ticker: str, series: str, p: float, *, line: float | None = 0.5, side: str = "yes", subject: str = "Nikita K",
        team: str = "TB", event: str | None = None) -> combo.LegIn:
    return leg(ticker, side=side, p=p, series=series, league="nhl", sport="Hockey", subject=subject, line=line,
               team=team, event=event)


class Implications(unittest.TestCase):
    """Owner, Nov 11 (.3): A always implies B for one player: joint = P(A)."""

    G, P, F = "KXNHLGOAL", "KXNHLPTS", "KXNHLFIRSTGOAL"

    def test_1_plus_goal_and_1_plus_point_price_at_the_goal(self) -> None:
        q = priced(nhl("G1", self.G, 0.30), nhl("P1", self.P, 0.55))
        self.assertEqual(q.fair_p, 0.30)
        self.assertEqual(q.blocks[0].latents, 1)
        self.assertEqual(q.blocks[0].nested, [["G1", "P1"]])

    def test_goal_yes_with_point_no_is_refused_the_other_way_prices(self) -> None:
        with self.assertRaisesRegex(cr.Refused, "implies .*can't both hit"):
            priced(nhl("G1", self.G, 0.30), nhl("P1", self.P, 0.55, side="no"))
        q = priced(nhl("G1", self.G, 0.30, side="no"), nhl("P1", self.P, 0.55))
        self.assertAlmostEqual(q.fair_p, 0.55 - 0.30, places=12)

    def test_a_first_goal_chain_prices_at_the_first_goal(self) -> None:
        q = priced(nhl("F", self.F, 0.09, line=None, event="KXNHLFIRSTGOAL-G1"), nhl("G1", self.G, 0.30),
                   nhl("P1", self.P, 0.55))
        self.assertEqual(q.fair_p, 0.09)
        self.assertEqual(q.blocks[0].latents, 1)

    def test_rungs_that_do_not_imply_stay_apart(self) -> None:
        # 1+ goal does not imply 2+ points: two latents, no row (unmodeled).
        q = priced(nhl("G1", self.G, 0.30), nhl("P2", self.P, 0.25, line=1.5))
        self.assertEqual(q.blocks[0].latents, 2)
        self.assertEqual(q.unmodeled_pairs, 1)
        # 2+ goals implies 1+ point.
        self.assertEqual(priced(nhl("G2", self.G, 0.06, line=1.5), nhl("P1", self.P, 0.55)).fair_p, 0.06)

    def test_prices_that_contradict_the_implication_are_refused(self) -> None:
        with self.assertRaisesRegex(cr.Refused, "the prices disagree"):
            priced(nhl("G1", self.G, 0.60), nhl("P1", self.P, 0.55))

    def test_with_the_team_win_the_merged_leg_takes_the_narrow_legs_rows(self) -> None:
        legs = [nhl("G1", self.G, 0.30), nhl("P1", self.P, 0.55),
                win("TB", 0.55, league="nhl", series="KXNHLGAME")]
        q = priced(*legs)
        self.assertEqual(q.blocks[0].latents, 2)
        self.assertEqual(q.blocks[0].pairs[0]["key"], "team|goal|win")
        self.assertAlmostEqual(q.fair_p, priced(nhl("G1", self.G, 0.30), legs[2]).fair_p, places=12)

    def test_nfl_first_td_implies_anytime_td(self) -> None:
        q = priced(leg("FTD", series="KXNFLFIRSTTD", p=0.11, line=None, subject="Bijan R", team="ATL"),
                   leg("TD1", series="KXNFLTD", p=0.48, line=0.5, subject="Bijan R", team="ATL"))
        self.assertEqual(q.fair_p, 0.11)

    def test_two_players_first_goals_are_refused(self) -> None:
        # Kalshi flags KXNHLFIRSTGOAL events not exclusive (read Nov 11): the table's rule refuses.
        ev = "KXNHLFIRSTGOAL-26NOV11TBBUF"
        with self.assertRaisesRegex(cr.Refused, "one scores first"):
            priced(nhl("F1", self.F, 0.09, line=None, event=ev),
                   nhl("F2", self.F, 0.08, line=None, event=ev, subject="Brayden P"))


class SignAndIndependence(unittest.TestCase):
    QB = dict(series="KXNFLPASSYDS", subject="Pat Q", team="KC")

    def test_a_no_leg_flips_the_sign_of_its_correlations(self) -> None:
        qb, w = leg("PY-250", p=0.55, **self.QB), win("KC", 0.62)  # type: ignore[arg-type]
        rho = T.row("nfl", "team|pass_yds|win")[0]  # type: ignore[index]
        q = priced(qb, replace(w, side="no"))
        za, zb = cr.Phi_inv(0.55), cr.Phi_inv(1 - 0.62)
        self.assertAlmostEqual(q.fair_p, cr.bvn_cdf(za, zb, -rho), places=12)
        q_yes = priced(qb, w)
        self.assertAlmostEqual(q_yes.fair_p, cr.bvn_cdf(za, cr.Phi_inv(0.62), rho), places=12)

    def test_different_games_are_independent_exactly(self) -> None:
        g1 = [leg("PY-250", p=0.55, **self.QB), win("KC", 0.62)]  # type: ignore[arg-type]
        g2 = [win("NYJ", 0.41, game="G2"), leg("RY-80", p=0.45, series="KXNFLRSHYDS", game="G2", team="NYJ")]
        q = priced(*g1, *g2)
        b1, b2 = priced(*g1).fair_p, priced(*g2).fair_p
        self.assertEqual(q.fair_p, b1 * b2)
        self.assertEqual(len(q.blocks), 2)

    def test_the_done_when_pair_prices_above_the_naive_product(self) -> None:
        q = priced(leg("PY-250", p=0.55, **self.QB), win("KC", 0.62))  # type: ignore[arg-type]
        self.assertAlmostEqual(q.naive_p, 0.55 * 0.62, places=12)
        self.assertGreater(q.fair_p, q.naive_p)
        self.assertEqual(q.blocks[0].method, "bivariate")
        self.assertIsNone(q.se)
        self.assertEqual(q.prior_pairs, 1)

    def test_three_legs_in_a_game_go_to_monte_carlo_with_an_se(self) -> None:
        legs = [leg("PY-250", p=0.55, **self.QB), win("KC", 0.62),  # type: ignore[arg-type]
                leg("RY-60", p=0.5, series="KXNFLRECYDS", subject="Wide R", team="KC")]
        q = priced(*legs, draws=20_000, draws_max=20_000)
        self.assertEqual(q.blocks[0].method, "monte_carlo")
        self.assertIsNotNone(q.se)
        self.assertEqual(priced(*legs, draws=20_000, draws_max=20_000).fair_p, q.fair_p)


class StalePrices(unittest.TestCase):
    def test_a_stale_leg_still_prices_and_flags_the_quote_with_each_legs_age(self) -> None:
        # Owner, Nov 11: any leg's Kalshi price over 20 min old flags the quote; it still prices.
        fresh = replace(leg("PY-250", p=0.55, series="KXNFLPASSYDS"), kalshi_at=NOW - timedelta(minutes=5))
        old = replace(win("KC", 0.62), stale_kalshi=True, kalshi_at=NOW - timedelta(minutes=41))
        q = priced(fresh, old)
        self.assertTrue(q.prices_stale)
        self.assertGreater(q.fair_p, 0)
        legs = {x["t"]: x for x in combo.block_json(q.blocks[0], NOW)["legs"]}
        self.assertEqual((legs["PY-250"]["age_s"], legs["PY-250"]["stale_k"]), (300, False))
        self.assertEqual((legs[old.market_ticker]["age_s"], legs[old.market_ticker]["stale_k"]), (2460, True))
        self.assertFalse(priced(fresh, replace(old, stale_kalshi=False)).prices_stale)
        self.assertIsNone(combo.block_json(q.blocks[0])["legs"][0]["age_s"])  # no time given


class ComboStatus(unittest.TestCase):
    def test_the_rules(self) -> None:
        a, b = leg("A1", event="E1"), leg("B1", event="E2", series="KXNFLRECYDS")
        self.assertEqual(combo.combo_status([a, b]), ("fits", None))
        s, why = combo.combo_status([replace(a, combo_eligible=False), b])
        self.assertEqual(s, "build_manually")
        self.assertIn("not combo-eligible", why or "")
        c = leg("A2", event="E1", line=19.5)
        self.assertEqual(combo.combo_status([replace(a, combo_size_max=1), replace(c, combo_size_max=1)])[0],
                         "build_manually")
        self.assertEqual(combo.combo_status([replace(a, combo_size_max=None), replace(c, combo_size_max=None)]),
                         ("fits", None))
        s, why = combo.combo_status([replace(a, combo_yes_only=True, side="no"), b])
        self.assertIn("YES only", why or "")

    def test_the_naive_cost_multiplies_ask_plus_fee(self) -> None:
        a, b = leg("A1", ask=0.40), leg("B1", ask=0.30, series="KXNFLRECYDS", side="no")
        q = priced(a, b)
        self.assertAlmostEqual(q.kalshi_naive_cost, (0.40 + 0.0175) * ((1 - 0.30 + 0.02) + 0.0175), places=12)
        self.assertIsNone(priced(replace(a, yes_ask=None), b).kalshi_naive_cost)


if __name__ == "__main__":
    unittest.main()
