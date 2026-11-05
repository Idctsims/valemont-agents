"""db/065 against the real database: the sports data layer.

Every test runs inside ONE transaction that is always rolled back, writes only
TEST rows (is_test, espn_id 'e2e-…') through the worker's own upsert text
(ledger.SPORTS_EVENTS_UPSERT_SQL), and reads only those rows by id. Skips when
the database is unreachable or db/065 is unpasted.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

import psycopg

from core import ledger
from tests_live.test_feed_rank_sql import RankSqlCase, _act_as, _rolled_back


def _row(**kw) -> dict:
    eid = kw.pop("espn_id", f"e2e-tl-{uuid.uuid4().hex[:12]}")
    row = {"league": "nfl", "kind": "game", "espn_id": eid, "parent_espn_id": None,
           "event_name": "TL Away at TL Home", "session": None,
           "starts_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), "time_tbd": False,
           "home": "TL Home", "away": "TL Away", "participants": {"home": {"name": "TL Home"}},
           "broadcast": ["CBS", "Paramount+"], "national_tv": True, "venue": "TL Stadium",
           "status": "scheduled", "espn_status": "STATUS_SCHEDULED", "status_detail": "1:00 PM",
           "period": 0, "clock": "0:00", "score": None, "content_hash": uuid.uuid4().hex}
    row.update(kw)
    return row


class SportsSqlCase(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.sports_events')").fetchone()[0] is None:
                raise unittest.SkipTest("db/065 not pasted")
            if conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'sports_events' "
                            "AND column_name = 'neutral_site'").fetchone() is None:
                raise unittest.SkipTest("the upsert writes neutral_site: db/066 not pasted")

    def upsert(self, conn: psycopg.Connection, *rows: dict) -> ledger.SportsUpsert:
        return ledger.sports_events_upsert(list(rows), is_test=True, conn=conn)

    def read(self, conn: psycopg.Connection, espn_id: str, cols: str = "status, score, kalshi_event_ticker"):
        return conn.execute(f"SELECT {cols} FROM sports_events WHERE league = 'nfl' AND espn_id = %s",
                            (espn_id,)).fetchone()


class Objects(SportsSqlCase):
    def test_rls_grants_provider_indexes_registry(self) -> None:
        with _rolled_back() as conn:
            for table in ("sports_leagues", "sports_events"):
                with self.subTest(table=table):
                    row = conn.execute(
                        "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                        "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('authenticated', oid, 'SELECT'), "
                        "has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                        "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                        "FROM pg_class c WHERE oid = %s::regclass", (f"public.{table}",)).fetchone()
                    self.assertEqual(row, (True, 1, False, True, False, True))
            self.assertEqual(conn.execute("SELECT brief_section, enabled FROM context_providers "
                                          "WHERE key = 'sports_today'").fetchone(), (False, True))
            self.assertEqual({r[0] for r in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'sports_events'")},
                {"sports_events_pkey", "sports_events_league_espn_id", "sports_events_league_starts",
                 "sports_events_live", "sports_events_kalshi_ticker"})  # the last from db/066
            self.assertEqual(conn.execute("SELECT string_agg(code, ',' ORDER BY sort) FROM sports_leagues "
                                          "WHERE owner_five").fetchone()[0], "cfb,nfl,nba,f1,pga")
            leagues = ledger.sports_leagues(conn=conn)
            self.assertEqual(len(leagues), 14)
            self.assertEqual({lg.code: lg.fetch_mode for lg in leagues if lg.fetch_mode == "range"},
                             {"f1": "range", "pga": "range", "atp": "range", "wta": "range", "ufc": "range"})
            cfb = [lg for lg in leagues if lg.code == "cfb"][0]
            self.assertEqual(cfb.espn_params, {"groups": "80", "limit": "500"})
            for fn, ro in (("public.ctx_sports_today()", True),
                           ("public.add_test_sports_event(text, text, text, text, timestamptz, text)", False)):
                self.assertEqual(conn.execute("SELECT has_function_privilege('valemont_readonly', %s, 'EXECUTE'), "
                                              "has_function_privilege('anon', %s, 'EXECUTE')", (fn, fn)).fetchone(),
                                 (ro, False))


class Upsert(SportsSqlCase):
    def test_writes_only_what_changed(self) -> None:
        with _rolled_back() as conn:
            a, b = _row(), _row()
            self.assertEqual(self.upsert(conn, a, b), ledger.SportsUpsert(2, 0))
            self.assertEqual(self.upsert(conn, a, b), ledger.SportsUpsert(0, 0))  # same hashes: no write
            a2 = dict(a, status="live", espn_status="STATUS_IN_PROGRESS", score={"home": 7, "away": 0},
                      content_hash=uuid.uuid4().hex)
            self.assertEqual(self.upsert(conn, a2, b), ledger.SportsUpsert(0, 1))
            self.assertEqual(self.read(conn, a["espn_id"]), ("live", {"home": 7, "away": 0}, None))
            stored = self.read(conn, a["espn_id"], "broadcast, is_test, starts_at")
            self.assertEqual(stored[:2], (["CBS", "Paramount+"], True))

    def test_no_row_is_written_without_a_change(self) -> None:
        with _rolled_back() as conn:
            a = _row(status="final", espn_status="STATUS_FINAL", score={"home": 24, "away": 17})
            self.upsert(conn, a)
            # A different hash over the same content: nothing written.
            self.assertEqual(self.upsert(conn, dict(a, content_hash=uuid.uuid4().hex)), ledger.SportsUpsert(0, 0))
            # A golf round past play is sent with no score; the stored one is kept, so nothing changes.
            self.assertEqual(self.upsert(conn, dict(a, score=None, content_hash=uuid.uuid4().hex)),
                             ledger.SportsUpsert(0, 0))
            self.assertEqual(self.read(conn, a["espn_id"])[1], {"home": 24, "away": 17})
            # A published time is a change: one write, the TBD flag cleared.
            t = _row(time_tbd=True, starts_at="2026-11-14T05:00:00+00:00")
            self.upsert(conn, t)
            self.assertEqual(self.upsert(conn, dict(t, time_tbd=False, starts_at="2026-11-14T20:30:00+00:00",
                                                    content_hash=uuid.uuid4().hex)), ledger.SportsUpsert(0, 1))
            got = self.read(conn, t["espn_id"], "time_tbd, starts_at")
            self.assertEqual(got, (False, datetime(2026, 11, 14, 20, 30, tzinfo=timezone.utc)))

    def test_a_stored_score_is_never_cleared_and_the_ticker_never_touched(self) -> None:
        with _rolled_back() as conn:
            a = _row(status="live", score={"home": 3, "away": 0})
            self.upsert(conn, a)
            conn.execute("UPDATE sports_events SET kalshi_event_ticker = 'KXNFLGAME-TL' "
                         "WHERE league = 'nfl' AND espn_id = %s", (a["espn_id"],))
            self.upsert(conn, dict(a, status="final", espn_status="STATUS_FINAL", score=None,
                                   content_hash=uuid.uuid4().hex))
            self.assertEqual(self.read(conn, a["espn_id"]), ("final", {"home": 3, "away": 0}, "KXNFLGAME-TL"))

    def test_the_real_writer_never_touches_a_test_row(self) -> None:
        with _rolled_back() as conn:
            a = _row()
            self.upsert(conn, a)
            # Same key, new content, as the worker would send it (is_test false).
            # The conflict path's WHERE leaves the test row as it was; whether
            # Postgres first refuses the e2e- id by CHECK is beside the point.
            try:
                with conn.transaction():
                    res = ledger.sports_events_upsert([dict(a, status="final", content_hash=uuid.uuid4().hex)],
                                                      is_test=False, conn=conn)
                self.assertEqual(res.written, 0)
            except psycopg.errors.CheckViolation:
                pass
            self.assertEqual(self.read(conn, a["espn_id"], "status, is_test"), ("scheduled", True))

    def test_uniqueness_kind_and_test_id_rules(self) -> None:
        with _rolled_back() as conn:
            a = _row()
            self.upsert(conn, a)
            cols = ("league, kind, espn_id, event_name, starts_at, home, away, status, espn_status, "
                    "content_hash, is_test")
            dup = (f"INSERT INTO sports_events ({cols}) VALUES (%s, %s, %s, 'x', now(), 'a', 'b', 'scheduled', "
                   "'STATUS_SCHEDULED', md5('x'), %s)")
            for args, err in ((("nfl", "game", a["espn_id"], True), psycopg.errors.UniqueViolation),
                              (("nfl", "bout", f"e2e-tl-{uuid.uuid4().hex[:8]}", True),
                               psycopg.errors.ForeignKeyViolation),
                              (("nfl", "game", f"tl-{uuid.uuid4().hex[:8]}", True), psycopg.errors.CheckViolation),
                              (("nfl", "game", f"e2e-tl-{uuid.uuid4().hex[:8]}", False),
                               psycopg.errors.CheckViolation)):
                with self.subTest(args=args[:2] + (args[3],)), self.assertRaises(err):
                    with conn.transaction():
                        conn.execute(dup, args)
            # The same id in another league is a different event.
            self.assertEqual(self.upsert(conn, dict(a, league="nba")), ledger.SportsUpsert(1, 0))


class Access(SportsSqlCase):
    def test_owner_reads_others_do_not_nobody_writes(self) -> None:
        with _rolled_back() as conn:
            a = _row()
            self.upsert(conn, a)
            with conn.cursor() as cur:
                with conn.transaction():
                    _act_as(cur, "authenticated", self.owner)
                    self.assertEqual(cur.execute("SELECT count(*) FROM sports_events WHERE espn_id = %s",
                                                 (a["espn_id"],)).fetchone()[0], 1)
                    self.assertGreater(cur.execute("SELECT count(*) FROM sports_leagues").fetchone()[0], 0)
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                        cur.execute("UPDATE sports_events SET status = 'final' WHERE espn_id = %s", (a["espn_id"],))
                with conn.transaction():
                    _act_as(cur, "authenticated", uuid.uuid4())
                    self.assertEqual(cur.execute("SELECT count(*) FROM sports_events WHERE espn_id = %s",
                                                 (a["espn_id"],)).fetchone()[0], 0)
                with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                    _act_as(cur, "anon")
                    cur.execute("SELECT 1 FROM sports_events WHERE espn_id = %s", (a["espn_id"],))

    def test_ctx_sports_today_ignores_test_rows(self) -> None:
        with _rolled_back() as conn:
            tz_now = "(now() AT TIME ZONE coalesce((SELECT timezone FROM app_settings LIMIT 1), 'America/Chicago'))"
            noon = conn.execute(f"SELECT (date_trunc('day', {tz_now}) + interval '12 hours') "
                                "AT TIME ZONE coalesce((SELECT timezone FROM app_settings LIMIT 1), "
                                "'America/Chicago')").fetchone()[0]
            before = conn.execute("SELECT ctx_sports_today()->'counts'").fetchone()[0]
            self.upsert(conn, _row(starts_at=noon.isoformat(), status="live", espn_status="STATUS_IN_PROGRESS"))
            after = conn.execute("SELECT ctx_sports_today()").fetchone()[0]
            self.assertEqual(after["counts"], before)
            self.assertFalse(any("TL Away" in x.get("what", "") for x in after["live"] + after["owner_five"]))

    def test_add_test_sports_event_needs_the_marker(self) -> None:
        with _rolled_back() as conn, conn.cursor() as cur:
            _act_as(cur, "authenticated", self.owner)
            with self.assertRaises(psycopg.errors.RaiseException):
                cur.execute("SELECT add_test_sports_event('not-the-marker', 'nfl', 'H', 'A', now())")


if __name__ == "__main__":
    unittest.main()
