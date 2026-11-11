"""db/071 against the real database: the Kalshi board.

Every test runs inside ONE transaction that is always rolled back and writes
only TEST rows (series tickers starting E2E, is_test) through the worker's own
statements (ledger.KALSHI_BOARD_*_SQL via the ledger functions), reading back
only those rows by ticker. Skips when the database is unreachable or db/071 is
unpasted.
"""

from __future__ import annotations

import copy
import json
import pathlib
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg

from core import ledger
from sports import kalshi_board as job
from tests_live.test_feed_rank_sql import RankSqlCase, _act_as, _rolled_back
from venues.kalshi import board as kb
from venues.kalshi.fees import FeeRegime

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "kalshi_board"
#: Reset from the DATABASE clock before every test (BoardSqlCase.setUp): the
#: SQL compares against now(), and in a 20-minute gate a clock read at import
#: is stale by the time a test runs (the db/071 gate's first run, Nov 6:
#: touch refused to move checked_at back to an import-time "later").
NOW = datetime.now(timezone.utc)


def tag() -> str:
    return uuid.uuid4().hex[:8].upper()


class BoardSqlCase(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.kalshi_board')").fetchone()[0] is None:
                raise unittest.SkipTest("db/071 not pasted")

    def setUp(self) -> None:
        global NOW
        with _rolled_back() as conn:
            NOW = conn.execute("SELECT now()").fetchone()[0]

    def game(self, conn: psycopg.Connection, *, start: datetime | None = None, n_markets: int = 2,
             kind: str = "prop") -> tuple[str, str, str, list[str]]:
        """One test series, event and markets. Returns (series, event, game, markets)."""
        series = f"E2ETL{tag()}"
        event = f"{series}-26NOV07AAABBB"
        game = f"{series}GAME-26NOV07AAABBB"
        ledger.kalshi_board_series_upsert([{
            "series_ticker": series, "title": "TL series", "sport": "TL Sport", "league": None,
            "category": "Sports", "tags": ["TL Sport"], "fee_type": "quadratic", "fee_multiplier": "1",
            "fee_regime": {"fee_type": "quadratic"}, "combo_eligible": True, "collection_tickers": ["TLCOLL-R"]}],
            is_test=True, conn=conn)
        ledger.kalshi_board_events_upsert([{
            "event_ticker": event, "series_ticker": series, "game_ticker": game, "game_title": "TL Away vs TL Home",
            "milestone_start": (start or NOW + timedelta(hours=3)).isoformat(), "close_time": None,
            "expected_expiration_time": None, "combo_eligible": True, "combo_yes_only": False,
            "combo_collections": ["TLCOLL-R"]}], NOW, is_test=True, conn=conn)
        markets = [f"{event}-M{i}" for i in range(n_markets)]
        ledger.kalshi_board_markets_upsert([self.market(event, m, kind, i) for i, m in enumerate(markets)], NOW,
                                           is_test=True, conn=conn)
        return series, event, game, markets

    @staticmethod
    def market(event: str, ticker: str, kind: str = "prop", i: int = 0, **over) -> dict:
        row = {"market_ticker": ticker, "event_ticker": event, "market_kind": kind, "variant": None,
               "subject": "TL Player", "stat": "points", "line": str(9.5 + i), "label": f"TL Player: {10 + i}+",
               "yes_bid": "0.4000", "yes_ask": "0.4200", "volume": str(100 * (i + 1)), "open_interest": "50",
               "taker_fee_at_ask": "0.017052"}
        row.update(over)
        return row


class Objects(BoardSqlCase):
    def test_rls_grants_fillfactor_provider(self) -> None:
        with _rolled_back() as conn:
            for table in ("kalshi_series_board", "kalshi_board_events", "kalshi_board"):
                with self.subTest(table=table):
                    row = conn.execute(
                        "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                        "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('authenticated', oid, 'SELECT'), "
                        "has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                        "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                        "FROM pg_class c WHERE oid = %s::regclass", (f"public.{table}",)).fetchone()
                    self.assertEqual(row, (True, 1, False, True, False, True))
            view = conn.execute(
                "SELECT has_table_privilege('anon', 'public.v_kalshi_board', 'SELECT'), "
                "has_table_privilege('authenticated', 'public.v_kalshi_board', 'SELECT'), "
                "has_table_privilege('authenticated', 'public.v_kalshi_board', 'INSERT,UPDATE,DELETE'), "
                "has_table_privilege('valemont_readonly', 'public.v_kalshi_board', 'SELECT'), "
                "(SELECT reloptions FROM pg_class WHERE oid = 'public.v_kalshi_board'::regclass)").fetchone()
            self.assertEqual(view[:4], (False, True, False, True))
            self.assertIn("security_invoker=true", view[4])
            self.assertIn("fillfactor=80", conn.execute(
                "SELECT reloptions FROM pg_class WHERE oid = 'public.kalshi_board'::regclass").fetchone()[0])
            for fn in ("kalshi_board_series_upsert(jsonb, boolean)",
                       "kalshi_board_events_upsert(jsonb, timestamptz, boolean)",
                       "kalshi_board_markets_upsert(jsonb, timestamptz, boolean)",
                       "kalshi_board_touch(text[], text[], timestamptz, boolean)",
                       "kalshi_board_sweep(text[], text[], boolean)", "kalshi_board_link(boolean)",
                       "kalshi_board_prune(boolean)"):
                with self.subTest(fn=fn):
                    self.assertEqual(conn.execute(
                        "SELECT has_function_privilege('anon', %(f)s, 'EXECUTE'), "
                        "has_function_privilege('authenticated', %(f)s, 'EXECUTE'), "
                        "has_function_privilege('valemont_readonly', %(f)s, 'EXECUTE')",
                        {"f": f"public.{fn}"}).fetchone(), (False, False, False))
            for fn in ("kalshi_board_sports(boolean)", "kalshi_board_games(text, text, boolean, boolean)",
                       "kalshi_board_game(text, text, boolean, boolean, boolean)",  # db/077: p_tickers
                       "ctx_kalshi_board()"):
                with self.subTest(fn=fn):
                    self.assertEqual(conn.execute(
                        "SELECT has_function_privilege('anon', %(f)s, 'EXECUTE'), "
                        "has_function_privilege('authenticated', %(f)s, 'EXECUTE'), "
                        "has_function_privilege('valemont_readonly', %(f)s, 'EXECUTE')",
                        {"f": f"public.{fn}"}).fetchone(), (False, True, True))
            self.assertEqual(conn.execute("SELECT brief_section, enabled, sort FROM context_providers "
                                          "WHERE key = 'kalshi_board'").fetchone(), (False, True, 48))


class Writes(BoardSqlCase):
    def test_changed_only_and_last_changed_at(self) -> None:
        with _rolled_back() as conn:
            series, event, _, markets = self.game(conn)
            same = [self.market(event, m, "prop", i) for i, m in enumerate(markets)]
            later = NOW + timedelta(minutes=1)
            self.assertEqual(ledger.kalshi_board_markets_upsert(same, later, is_test=True, conn=conn), 0)
            moved = copy.deepcopy(same)
            moved[0]["yes_ask"] = "0.4500"
            self.assertEqual(ledger.kalshi_board_markets_upsert(moved, later, is_test=True, conn=conn), 1)
            vol = copy.deepcopy(moved)
            vol[0]["volume"] = "999"
            latest = NOW + timedelta(minutes=2)
            self.assertEqual(ledger.kalshi_board_markets_upsert(vol, latest, is_test=True, conn=conn), 1)
            got = conn.execute("SELECT yes_ask, fetched_at, last_changed_at FROM kalshi_board "
                               "WHERE market_ticker = %s", (markets[0],)).fetchone()
            self.assertEqual(got, (Decimal("0.4500"), latest, later))
            # The real writer never touches a test row.
            self.assertEqual(ledger.kalshi_board_markets_upsert(vol, latest, conn=conn), 0)

    def test_a_test_market_never_hangs_under_a_real_event_and_kinds_are_checked(self) -> None:
        with _rolled_back() as conn:
            _, event, _, _ = self.game(conn)
            for bad, err in (({"market_kind": "parlay"}, psycopg.errors.CheckViolation),
                             ({"yes_ask": "1.5"}, (psycopg.errors.CheckViolation,
                                                   psycopg.errors.NumericValueOutOfRange))):
                with self.subTest(bad=bad), self.assertRaises(err):
                    with conn.transaction():
                        ledger.kalshi_board_markets_upsert([self.market(event, f"{event}-BAD", **bad)], NOW,
                                                           is_test=True, conn=conn)
            with self.assertRaises(psycopg.errors.CheckViolation), conn.transaction():
                ledger.kalshi_board_series_upsert([{"series_ticker": "KXREALTL", "title": "x", "sport": "x",
                                                    "fee_type": "quadratic", "fee_multiplier": "1",
                                                    "fee_regime": {}}], is_test=True, conn=conn)

    def test_touch_and_sweep(self) -> None:
        with _rolled_back() as conn:
            series, event, _, markets = self.game(conn, n_markets=3)
            later = NOW + timedelta(minutes=5)
            ledger.kalshi_board_touch([event], [series], later, is_test=True, conn=conn)
            self.assertEqual(conn.execute("SELECT fetched_at FROM kalshi_board_events WHERE event_ticker = %s",
                                          (event,)).fetchone()[0], later)
            self.assertEqual(conn.execute("SELECT checked_at FROM kalshi_series_board WHERE series_ticker = %s",
                                          (series,)).fetchone()[0], later)
            self.assertEqual(ledger.kalshi_board_sweep([event], markets[:1], is_test=True, conn=conn), 2)
            self.assertEqual([r[0] for r in conn.execute(
                "SELECT market_ticker FROM kalshi_board WHERE event_ticker = %s", (event,))], markets[:1])
            self.assertEqual(ledger.kalshi_board_sweep([event], [], is_test=True, conn=conn), 1)

    def test_link_takes_the_sports_events_start(self) -> None:
        with _rolled_back() as conn:
            _, event, game, _ = self.game(conn)
            espn_start = NOW + timedelta(hours=3, minutes=7)
            sid = conn.execute(
                "INSERT INTO sports_events (league, kind, espn_id, event_name, starts_at, home, away, status, "
                "espn_status, content_hash, is_test, kalshi_event_ticker, link_state, link_method) "
                "VALUES ('nba', 'game', %s, 'TL', %s, 'H', 'A', 'scheduled', 'STATUS_SCHEDULED', %s, true, %s, "
                "'linked', 'manual') RETURNING id",
                (f"e2e-tl-{uuid.uuid4().hex[:12]}", espn_start, uuid.uuid4().hex, game)).fetchone()[0]
            self.assertGreaterEqual(ledger.kalshi_board_link(is_test=True, conn=conn), 1)
            self.assertEqual(conn.execute("SELECT sports_event_id, starts_at FROM kalshi_board_events "
                                          "WHERE event_ticker = %s", (event,)).fetchone(), (sid, espn_start))
            # The real link never touches a test event.
            conn.execute("UPDATE kalshi_board_events SET sports_event_id = NULL WHERE event_ticker = %s", (event,))
            ledger.kalshi_board_link(conn=conn)
            self.assertIsNone(conn.execute("SELECT sports_event_id FROM kalshi_board_events WHERE event_ticker = %s",
                                           (event,)).fetchone()[0])

    def test_prune_by_start_plus_6_h_test_rows_apart(self) -> None:
        with _rolled_back() as conn:
            _, old, _, _ = self.game(conn, start=NOW - timedelta(hours=6, minutes=5))
            _, fresh, _, _ = self.game(conn, start=NOW - timedelta(hours=5, minutes=50))
            ledger.kalshi_board_prune(conn=conn)  # real rows only
            alive = lambda e: conn.execute("SELECT 1 FROM kalshi_board_events WHERE event_ticker = %s",
                                           (e,)).fetchone() is not None
            self.assertTrue(alive(old))
            got = ledger.kalshi_board_prune(is_test=True, conn=conn)
            self.assertGreaterEqual(got["events"], 1)
            self.assertGreaterEqual(got["markets"], 2)
            self.assertFalse(alive(old))
            self.assertTrue(alive(fresh))
            self.assertIsNone(conn.execute("SELECT 1 FROM kalshi_board WHERE event_ticker = %s", (old,)).fetchone())

    def test_the_workers_own_rows_fit(self) -> None:
        """Rows exactly as venues/kalshi/board.py builds them from the recorded
        responses, re-tickered as test rows, go in and read back."""
        with _rolled_back() as conn:
            series = f"E2ETL{tag()}"
            regime = FeeRegime(series=series, fee_type="quadratic", multiplier=Decimal(1), effective_from=None,
                               fetched_at=NOW)
            s = kb.Series(series, "TL", "Basketball", ("Basketball",), "Sports", regime)
            ledger.kalshi_board_series_upsert([job.series_row(s, "nba", ["TLCOLL-R"])], is_test=True, conn=conn)
            raws = json.loads((FIXTURES / "markets_KXNBAREB.json").read_bytes())["markets"]
            event = f"{series}-26NOV06BOSCHA"
            rows = []
            for raw in raws:
                raw = dict(raw, event_ticker=event, ticker=raw["ticker"].replace(raw["event_ticker"], event))
                rows.append(kb.parse_market(raw, s, "$")[0])
            g = kb.Game(f"{series}GAME-1", "BOS vs CHA", NOW + timedelta(hours=2), "NBA", "basketball_game", (event,))
            capped = f"{series}-26NOV06WASATL"
            ledger.kalshi_board_events_upsert([
                job.event_row(event, g, (NOW + timedelta(days=2), None), kb.Combo(("TLCOLL-R",), False, None, 0)),
                job.event_row(capped, g, (None, None), kb.Combo(("TLCOLL-R",), True, 1, None))],
                NOW, is_test=True, conn=conn)
            self.assertEqual(conn.execute(
                "SELECT event_ticker, combo_size_max, combo_quoters, combo_yes_only FROM kalshi_board_events "
                "WHERE event_ticker = ANY(%s) ORDER BY event_ticker", ([event, capped],)).fetchall(),
                [(event, None, 0, False), (capped, 1, None, True)])  # absent stays NULL, never 0 or 1
            self.assertEqual(ledger.kalshi_board_markets_upsert(rows, NOW, is_test=True, conn=conn), len(rows))
            got = conn.execute("SELECT subject, stat, line, yes_ask, taker_fee_at_ask, no_bid FROM v_kalshi_board "
                               "WHERE market_ticker = %s", (rows[0]["market_ticker"],)).fetchone()
            self.assertEqual(got[:2], ("Ryan Kalkbrenner", "rebounds"))
            self.assertEqual(got[2], Decimal(rows[0]["line"]))
            self.assertEqual(got[5], 1 - got[3])


class Readers(BoardSqlCase):
    def test_games_game_sports_and_rls(self) -> None:
        with _rolled_back() as conn:
            _, event, game, _ = self.game(conn, n_markets=2, kind="prop")
            ledger.kalshi_board_markets_upsert([self.market(event, f"{event}-ML", "game_line", 0, label="TL Home")],
                                               NOW, is_test=True, conn=conn)
            with conn.cursor() as cur:
                _act_as(cur, "authenticated", self.owner)
                sports = [r[0] for r in cur.execute("SELECT kalshi_board_sports(true)")]
                self.assertIn("TL Sport", {s["sport"] for s in sports})
                self.assertNotIn("TL Sport", {r[0]["sport"] for r in cur.execute("SELECT kalshi_board_sports()")})
                games = [r[0] for r in cur.execute("SELECT kalshi_board_games('TL Sport', NULL, false, true)")]
                self.assertEqual([(g["g"], g["l"], g["p"], g["c"], g.get("test")) for g in games],
                                 [(game, 1, 2, 3, True)])
                self.assertEqual(cur.execute("SELECT count(*) FROM kalshi_board_games('TL Sport')").fetchone()[0], 0)
                ev = [r[0] for r in cur.execute("SELECT kalshi_board_game(%s, NULL, false, true)", (game,))]
                self.assertEqual([m["k"] for m in ev[0]["m"]], ["game_line", "prop", "prop"])
                props = [r[0] for r in cur.execute("SELECT kalshi_board_game(%s, 'prop', false, true)", (game,))]
                self.assertEqual(len(props[0]["m"]), 2)
                self.assertGreaterEqual(cur.execute("SELECT count(*) FROM kalshi_board WHERE event_ticker = %s",
                                                    (event,)).fetchone()[0], 3)
                cur.execute("RESET ROLE")
                _act_as(cur, "authenticated", uuid.uuid4())
                self.assertEqual(cur.execute("SELECT count(*) FROM kalshi_board WHERE event_ticker = %s",
                                             (event,)).fetchone()[0], 0)
                cur.execute("RESET ROLE")
                _act_as(cur, "anon")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute("SELECT count(*) FROM kalshi_board")

    def test_ctx_never_shows_test_rows(self) -> None:
        with _rolled_back() as conn:
            self.game(conn, start=NOW + timedelta(hours=1))
            ctx = conn.execute("SELECT ctx_kalshi_board()").fetchone()[0]
            # db/072 adds "scalar" (scalar markets counted apart by sport).
            self.assertEqual(set(ctx), {"about", "as_of", "counts", "scalar", "combo_eligible", "top_tonight"})
            self.assertNotIn("TL Sport", ctx["counts"])
            self.assertNotIn("TL Sport", ctx["scalar"])
            self.assertFalse(any("TL Player" in (t.get("market") or "") for t in ctx["top_tonight"]))


class Payout072(BoardSqlCase):
    """db/072: the worker's own parse of a ladder writes payout 'scalar', no
    line, variant 'ladder'; the readers count it apart and keep it out of
    every combo figure. Kalshi's market_type says 'binary' on it (Nov 7)."""

    LADDER = {"Payout Per Unit": "$0.05", "football_player": "x", "football_team": "y", "scalar_cap": "20",
              "scalar_floor": "0", "scalar_payout_type": "capped_cent", "statistic": "receptions"}

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'kalshi_board' "
                            "AND column_name = 'payout'").fetchone() is None:
                raise unittest.SkipTest("db/072 not pasted")

    def test_a_parsed_ladder_round_trips_and_is_counted_apart(self) -> None:
        with _rolled_back() as conn:
            series, event, game, markets = self.game(conn, n_markets=2)
            raw = copy.deepcopy(json.loads((FIXTURES / "markets_KXNBAREB.json").read_bytes())["markets"][0])
            raw.update(ticker=f"{event}-LAD", event_ticker=event, title="TL Player Receptions Ladder",
                       yes_sub_title="TL Player", custom_strike=dict(self.LADDER), floor_strike=None,
                       strike_type="custom", primary_participant_key="football_player", market_type="binary")
            regime = FeeRegime(series=series, fee_type="quadratic", multiplier=Decimal(1), effective_from=None,
                               fetched_at=NOW)
            row, ex = kb.parse_market(raw, kb.Series(series, "TL", "TL Sport", ("TL Sport",), "Sports", regime),
                                      "$.markets[0]")
            self.assertFalse(ex["miss"])
            self.assertEqual(ledger.kalshi_board_markets_upsert([row], NOW, is_test=True, conn=conn), 1)
            self.assertEqual(conn.execute(
                "SELECT payout, variant, subject, stat, line, label FROM kalshi_board WHERE market_ticker = %s",
                (f"{event}-LAD",)).fetchone(),
                ("scalar", "ladder", "TL Player", "receptions", None, "TL Player: receptions, $1 at 20"))
            # The two binary props the fixture wrote say binary.
            self.assertEqual({r[0] for r in conn.execute(
                "SELECT payout FROM kalshi_board WHERE market_ticker = ANY(%s)", (markets,))}, {"binary"})
            g = conn.execute("SELECT x FROM kalshi_board_games('TL Sport', NULL, false, true) x "
                             "WHERE x->>'g' = %s", (game,)).fetchone()[0]
            self.assertEqual((g.get("p"), g.get("s"), g.get("c")), (2, 1, 2))
            ev = conn.execute("SELECT kalshi_board_game(%s, NULL, false, true)", (game,)).fetchone()[0]
            self.assertEqual(sum(1 for m in ev["m"] if m.get("s")), 1)
            combo = conn.execute("SELECT x FROM kalshi_board_games('TL Sport', NULL, true, true) x "
                                 "WHERE x->>'g' = %s", (game,)).fetchone()[0]
            self.assertNotIn("s", combo)
            # Unchanged, it is not rewritten.
            self.assertEqual(ledger.kalshi_board_markets_upsert([row], NOW, is_test=True, conn=conn), 0)

    def test_the_checks_refuse_a_scalar_with_a_line_and_a_bad_payout(self) -> None:
        for over, name in (({"payout": "scalar", "variant": "ladder"}, "kalshi_board_scalar_shape"),
                           ({"payout": "range"}, "kalshi_board_payout_check"),
                           ({"variant": "spiral"}, "kalshi_board_variant_check")):
            with self.subTest(name), _rolled_back() as conn:
                _, event, _, _ = self.game(conn, n_markets=0)
                with self.assertRaises(psycopg.errors.CheckViolation) as cm:
                    ledger.kalshi_board_markets_upsert([self.market(event, f"{event}-X", **over)], NOW,
                                                       is_test=True, conn=conn)
                self.assertEqual(cm.exception.diag.constraint_name, name)


if __name__ == "__main__":
    unittest.main()
