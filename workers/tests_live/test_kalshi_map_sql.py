"""db/066 against the real database: the Kalshi mapping's reads, writes and
the no-relink guard.

Every test runs inside ONE transaction that is always rolled back and writes
only TEST rows (is_test, espn_id 'e2e-…'), dated 2099 so every read the job's
own SQL makes (ledger.KALSHI_MAP_ROWS_SQL, SPORTS_LINK_WRITE_SQL,
SPORTS_LINK_CONFLICT_SQL) returns this test's rows only. Skips when the
database is unreachable or db/066 is unpasted.
"""

from __future__ import annotations

import unittest
import uuid
from collections import Counter
from datetime import datetime, timezone

import psycopg

from core import ledger
from scripts.kalshi_map_dry_run import migration_aliases, migration_seed
from tests_live.test_feed_rank_sql import _rolled_back
from tests_live.test_sports_events_sql import SportsSqlCase, _row

FAR = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _t(tag: str) -> str:
    return f"KXE2ETEST-99JAN01{tag}{uuid.uuid4().hex[:6].upper()}"


class MapSqlCase(SportsSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.sports_team_aliases')").fetchone()[0] is None:
                raise unittest.SkipTest("db/066 not pasted")

    def add(self, conn: psycopg.Connection, n: int = 1, **kw) -> list[int]:
        """n test rows starting in 2099; their ids."""
        rows = [_row(starts_at=FAR.replace(hour=i).isoformat(), **kw) for i in range(n)]
        self.upsert(conn, *rows)
        return [conn.execute("SELECT id FROM sports_events WHERE league = %s AND espn_id = %s",
                             (r["league"], r["espn_id"])).fetchone()[0] for r in rows]

    def links(self, conn: psycopg.Connection, i: int) -> tuple:
        return conn.execute("SELECT link_state, link_reason, link_method, kalshi_event_ticker, home_away_swapped, "
                            "link_conflict, linked_at IS NOT NULL, link_state_at IS NOT NULL "
                            "FROM sports_events WHERE id = %s", (i,)).fetchone()

    @staticmethod
    def decision(i: int, state: str, reason: str | None = None, method: str | None = None,
                 ticker: str | None = None, swapped: bool | None = None) -> dict:
        return {"id": i, "link_state": state, "link_reason": reason, "link_method": method,
                "kalshi_event_ticker": ticker, "home_away_swapped": swapped}


class Objects(MapSqlCase):
    def test_aliases_registry_columns_triggers_index(self) -> None:
        reg, _ = migration_seed()
        aliases = migration_aliases()  # every migration's block: db/066's seed, db/067, ...
        with _rolled_back() as conn:
            row = conn.execute(
                "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                "has_table_privilege('authenticated', oid, 'SELECT'), "
                "has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                "FROM pg_class c WHERE oid = 'public.sports_team_aliases'::regclass").fetchone()
            self.assertEqual(row, (True, 1, False, True, False, True))
            stored = ledger.sports_team_aliases(conn=conn)
            self.assertEqual(stored, {(lg, e): k for lg, e, k, _ in aliases})
            self.assertEqual(Counter(lg for lg, _ in stored), Counter(a[0] for a in aliases))
            leagues = {lg.code: lg for lg in ledger.sports_leagues(conn=conn)}
            self.assertEqual({c: (lg.kalshi_series, lg.kalshi_map, lg.days_ahead) for c, lg in leagues.items()}, reg)
            self.assertEqual(leagues["epl"].sport, "soccer")
            self.assertEqual({r[0] for r in conn.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.sports_events'::regclass AND NOT tgisinternal "
                "AND tgenabled = 'O'")}, {"sports_events_link_guard", "sports_events_link_insert"})
            self.assertEqual(conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = "
                                          "'sports_events' AND (column_name LIKE 'link%%' OR column_name IN "
                                          "('linked_at', 'home_away_swapped', 'neutral_site'))").fetchone()[0], 9)
            self.assertEqual(conn.execute("SELECT has_function_privilege('authenticated', "
                                          "'public.sports_events_link_guard()', 'EXECUTE')").fetchone()[0], False)
            # The repair log: append-only, owner and readonly read it, nobody else writes it.
            row = conn.execute(
                "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                "has_table_privilege('authenticated', oid, 'SELECT'), "
                "has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                "FROM pg_class c WHERE oid = 'public.sports_event_relinks'::regclass").fetchone()
            self.assertEqual(row, (True, 1, False, True, False, True))
            self.assertEqual({r[0] for r in conn.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.sports_event_relinks'::regclass "
                "AND NOT tgisinternal AND tgenabled = 'O'")},
                {"sports_event_relinks_stamp", "sports_event_relinks_immutable", "sports_event_relinks_no_truncate"})
            fn = "'public.sports_event_relink(bigint, text, text, boolean)'"
            self.assertEqual(conn.execute(
                f"SELECT has_function_privilege('anon', {fn}, 'EXECUTE'), "
                f"has_function_privilege('authenticated', {fn}, 'EXECUTE'), "
                f"has_function_privilege('valemont_readonly', {fn}, 'EXECUTE')").fetchone(), (False, False, False))
            # One row per Kalshi event for games, matches and bouts only.
            self.assertIn("kind = ANY (ARRAY['game'::text, 'match'::text, 'bout'::text])", conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'sports_events_kalshi_ticker'").fetchone()[0])


class Reads(MapSqlCase):
    def test_the_jobs_read_narrow_columns_out_of_scope_never_again(self) -> None:
        with _rolled_back() as conn:
            g, oos = self.add(conn, 2, participants={"home": {"name": "H", "abbr": "JAX"},
                                                      "away": {"name": "A", "abbr": "WSH"}})
            m = self.add(conn, league="atp", kind="match", home=None, away=None,
                         participants=[{"name": "Ben Shelton"}, {"name": "Arthur Rinderknech"}])[0]
            ledger.sports_link_write([self.decision(oos, "out_of_scope", "test: out of scope")], is_test=True,
                                     conn=conn)
            rows = ledger.kalshi_map_rows(FAR, ["nfl", "atp"], is_test=True, conn=conn)
            self.assertEqual([r.id for r in rows], [m, g])  # league, then start: atp before nfl
            got = {r.id: r for r in rows}
            self.assertEqual((got[g].home_abbr, got[g].away_abbr, got[g].names, got[g].link_state), ("JAX", "WSH", (), None))
            self.assertEqual((got[m].home_abbr, got[m].names), (None, ("Ben Shelton", "Arthur Rinderknech")))
            self.assertEqual(ledger.kalshi_map_rows(FAR, ["nfl"], is_test=False, conn=conn), [])  # real rows only


class Writes(MapSqlCase):
    def test_written_once_then_never_without_change(self) -> None:
        with _rolled_back() as conn:
            a, b = self.add(conn, 2)
            t = _t("AB")
            first = [self.decision(a, "not_listed", "starts after KXNFLGAME's latest listed event"),
                     self.decision(b, "linked", None, "team_pair", t, False)]
            self.assertEqual(sorted(ledger.sports_link_write(first, is_test=True, conn=conn)),
                             sorted([(a, "not_listed"), (b, "linked")]))
            self.assertEqual(ledger.sports_link_write(first, is_test=True, conn=conn), [])
            self.assertEqual(self.links(conn, b), ("linked", None, "team_pair", t, False, None, True, True))
            self.assertEqual(self.links(conn, a)[:1] + self.links(conn, a)[6:], ("not_listed", False, True))
            # A real-row write never touches a test row.
            self.assertEqual(ledger.sports_link_write([self.decision(a, "not_linked", "x")], is_test=False,
                                                      conn=conn), [])

    def test_no_relink_a_linked_row_is_skipped_and_the_database_refuses(self) -> None:
        with _rolled_back() as conn:
            (a,) = self.add(conn)
            t = _t("NR")
            ledger.sports_link_write([self.decision(a, "linked", None, "team_pair", t, True)], is_test=True, conn=conn)
            # The job's write skips a linked row whatever it is sent.
            self.assertEqual(ledger.sports_link_write([self.decision(a, "not_linked", "x")], is_test=True, conn=conn), [])
            self.assertEqual(ledger.sports_link_write([self.decision(a, "linked", None, "team_pair", _t("XX"), True)],
                                                      is_test=True, conn=conn), [])
            self.assertEqual(self.links(conn, a)[3], t)
            # Any other writer: the guard refuses.
            for sql in ("UPDATE sports_events SET kalshi_event_ticker = %s WHERE id = %s",
                        "UPDATE sports_events SET kalshi_event_ticker = NULL, link_state = 'not_linked', "
                        "link_reason = %s, link_method = NULL, home_away_swapped = NULL WHERE id = %s"):
                with self.subTest(sql=sql[:50]):
                    with self.assertRaisesRegex(psycopg.errors.RaiseException, "never relinked or unlinked"):
                        with conn.transaction():
                            conn.execute(sql, (_t("ZZ"), a))
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "frozen"):
                with conn.transaction():
                    conn.execute("UPDATE sports_events SET home_away_swapped = false WHERE id = %s", (a,))

    def test_conflict_flag_once_and_never_the_link_itself(self) -> None:
        with _rolled_back() as conn:
            (a,) = self.add(conn)
            t, other = _t("CF"), _t("CG")
            ledger.sports_link_write([self.decision(a, "linked", None, "team_pair", t)], is_test=True, conn=conn)
            self.assertEqual(ledger.sports_link_conflicts([{"id": a, "link_conflict": other}], is_test=True,
                                                          conn=conn), [a])
            self.assertEqual(ledger.sports_link_conflicts([{"id": a, "link_conflict": other}], is_test=True,
                                                          conn=conn), [])
            self.assertEqual(ledger.sports_link_conflicts([{"id": a, "link_conflict": t}], is_test=True, conn=conn), [])
            self.assertEqual(self.links(conn, a)[3:6], (t, None, other))
            # An unlinked row is never flagged.
            (b,) = self.add(conn)
            self.assertEqual(ledger.sports_link_conflicts([{"id": b, "link_conflict": other}], is_test=True,
                                                          conn=conn), [])

    def test_one_game_per_event_golf_rounds_share(self) -> None:
        with _rolled_back() as conn:
            a, b = self.add(conn, 2)
            t = _t("UQ")
            ledger.sports_link_write([self.decision(a, "linked", None, "team_pair", t)], is_test=True, conn=conn)
            with self.assertRaises(psycopg.errors.UniqueViolation):
                with conn.transaction():
                    ledger.sports_link_write([self.decision(b, "linked", None, "team_pair", t)], is_test=True,
                                             conn=conn)
            g = self.add(conn, 2, league="pga", kind="golf_round", home=None, away=None, participants=None)
            tg = _t("PG")
            self.assertEqual(len(ledger.sports_link_write([self.decision(i, "linked", None, "by_date", tg) for i in g],
                                                          is_test=True, conn=conn)), 2)

    def test_espns_upsert_never_touches_the_link_and_writes_neutral_site(self) -> None:
        with _rolled_back() as conn:
            r = _row(starts_at=FAR.isoformat(), neutral_site=False)
            self.upsert(conn, r)
            a = conn.execute("SELECT id FROM sports_events WHERE league = 'nfl' AND espn_id = %s",
                             (r["espn_id"],)).fetchone()[0]
            t = _t("ES")
            ledger.sports_link_write([self.decision(a, "linked", None, "team_pair", t, False)], is_test=True, conn=conn)
            before = conn.execute("SELECT linked_at, link_state_at FROM sports_events WHERE id = %s", (a,)).fetchone()
            self.assertEqual(self.upsert(conn, dict(r, content_hash=uuid.uuid4().hex)), ledger.SportsUpsert(0, 0))
            self.assertEqual(self.upsert(conn, dict(r, neutral_site=True, status="live", espn_status="STATUS_IN_PROGRESS",
                                                   content_hash=uuid.uuid4().hex)), ledger.SportsUpsert(0, 1))
            self.assertEqual(conn.execute("SELECT neutral_site, status, kalshi_event_ticker, linked_at, link_state_at "
                                          "FROM sports_events WHERE id = %s", (a,)).fetchone(),
                             (True, "live", t) + tuple(before))


class Repairs(MapSqlCase):
    """db/066's hand repair: refused without its sports_event_relinks row,
    allowed with it; an unlink is for good."""

    def test_relink_and_unlink_only_with_the_log_row(self) -> None:
        with _rolled_back() as conn:
            a, b = self.add(conn, 2)
            t, t2 = _t("RA"), _t("RB")
            ledger.sports_link_write([self.decision(a, "linked", None, "team_pair", t, False)], is_test=True,
                                     conn=conn)
            # Without its log row: refused (a log row for another change does not count).
            conn.execute("INSERT INTO sports_event_relinks (event_id, old_ticker, new_ticker, reason) "
                         "VALUES (%s, %s, %s, 'test: another change')", (a, t, _t("RC")))
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "without its sports_event_relinks row"):
                with conn.transaction():
                    conn.execute("UPDATE sports_events SET kalshi_event_ticker = %s WHERE id = %s", (t2, a))
            # With it: the relink, method manual, the conflict cleared.
            log_id = ledger.sports_event_relink(a, t2, "test: wrong game", swapped=True, conn=conn)
            self.assertEqual(self.links(conn, a)[:6],
                             ("linked", "relinked by hand: test: wrong game", "manual", t2, True, None))
            self.assertEqual(conn.execute("SELECT event_id, old_ticker, new_ticker, reason, by_role = current_user, "
                                          "xact_id = pg_current_xact_id(), created_at = now() "
                                          "FROM sports_event_relinks WHERE id = %s", (log_id,)).fetchone(),
                             (a, t, t2, "test: wrong game", True, True, True))
            # The job's own write still skips it, and a second move needs a second log row.
            self.assertEqual(ledger.sports_link_write([self.decision(a, "linked", None, "team_pair", t, False)],
                                                      is_test=True, conn=conn), [])
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "never relinked or unlinked"):
                with conn.transaction():
                    conn.execute("UPDATE sports_events SET kalshi_event_ticker = %s WHERE id = %s", (t, a))
            # The unlink: out of scope for good; kalshi_map never reads it, and nothing links it again.
            ledger.sports_event_relink(a, None, "test: no Kalshi event", conn=conn)
            self.assertEqual(self.links(conn, a)[:6],
                             ("out_of_scope", "unlinked by hand: test: no Kalshi event", None, None, None, None))
            self.assertNotIn(a, [r.id for r in ledger.kalshi_map_rows(FAR, ["nfl"], is_test=True, conn=conn)])
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "unlinked by hand; it stays unlinked"):
                with conn.transaction():
                    ledger.sports_link_write([self.decision(a, "linked", None, "team_pair", t, False)],
                                             is_test=True, conn=conn)
            # The freed ticker can go to the right game.
            self.assertEqual(ledger.sports_link_write([self.decision(b, "linked", None, "team_pair", t2, False)],
                                                      is_test=True, conn=conn), [(b, "linked")])

    def test_the_functions_refusals_and_the_log_is_append_only(self) -> None:
        with _rolled_back() as conn:
            a, b = self.add(conn, 2)
            t = _t("RF")
            ledger.sports_link_write([self.decision(a, "linked", None, "team_pair", t)], is_test=True, conn=conn)
            for args, msg in (((b, _t("RG"), "test"), "is not linked"),
                              ((a, t, "test"), "is already linked to"),
                              ((a, _t("RH"), "  "), "a reason is required"),
                              ((a, None, "test"), None)):
                with self.subTest(msg=msg):
                    if msg is None:
                        with self.assertRaisesRegex(psycopg.errors.RaiseException, "takes no home/away flag"):
                            with conn.transaction():
                                ledger.sports_event_relink(*args, swapped=False, conn=conn)
                        continue
                    with self.assertRaisesRegex(psycopg.errors.RaiseException, msg):
                        with conn.transaction():
                            ledger.sports_event_relink(*args, conn=conn)
            log_id = ledger.sports_event_relink(a, _t("RI"), "test: kept", conn=conn)
            for sql in ("UPDATE sports_event_relinks SET reason = 'rewritten' WHERE id = %s",
                        "DELETE FROM sports_event_relinks WHERE id = %s"):
                with self.subTest(sql=sql[:30]):
                    with self.assertRaisesRegex(psycopg.errors.RaiseException, "append-only"):
                        with conn.transaction():
                            conn.execute(sql, (log_id,))
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "TRUNCATE is refused"):
                with conn.transaction():
                    conn.execute("TRUNCATE sports_event_relinks")

    def test_f1_sessions_share_an_event_matches_do_not(self) -> None:
        with _rolled_back() as conn:
            f = self.add(conn, 2, league="f1", kind="race_session", home=None, away=None, participants=None)
            tf = _t("F1")
            self.assertEqual(len(ledger.sports_link_write([self.decision(i, "linked", None, "by_date", tf) for i in f],
                                                          is_test=True, conn=conn)), 2)
            m = self.add(conn, 2, league="atp", kind="match", home=None, away=None,
                         participants=[{"name": "Ben Shelton"}, {"name": "Arthur Rinderknech"}])
            tm = _t("AT")
            with self.assertRaises(psycopg.errors.UniqueViolation):
                with conn.transaction():
                    ledger.sports_link_write([self.decision(i, "linked", None, "player_pair", tm) for i in m],
                                             is_test=True, conn=conn)


if __name__ == "__main__":
    unittest.main()
