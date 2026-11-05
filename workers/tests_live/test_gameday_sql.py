"""db/068 against the real database: Game Day's read functions, the Brief's
slate, Wags' context and the marker-gated test helpers.

Every test runs inside ONE transaction that is always rolled back. Rows are
inserted directly (the worker's writer is ESPN's, not ours) on days in 2031,
where no real row exists, so every read returns only this test's rows; the
real-kind rows the brief needs (it ignores test rows by design) never outlive
the rollback (the test_feed_ledger precedent). Skips until db/068 is pasted.
A throwaway e2e marker (an e2e_markers row inside the transaction) stands in
for the web's.
"""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import date

import psycopg

from tests_live.test_feed_rank_sql import RankSqlCase, _act_as, _rolled_back

SAT, SUN, DEAD = date(2031, 11, 8), date(2031, 11, 9), date(2031, 11, 12)
MARKER = f"tl-068-{uuid.uuid4()}"
COLS = ("league, kind, espn_id, parent_espn_id, event_name, session, starts_at, time_tbd, home, away, participants, "
        "broadcast, national_tv, neutral_site, venue, status, espn_status, score, content_hash, is_test, "
        "link_state, link_reason")


def local(day: date, hhmm: str) -> str:
    """A Chicago wall-clock time as SQL (the database converts)."""
    return f"(timestamp '{day.isoformat()} {hhmm}' AT TIME ZONE 'America/Chicago')"


class GamedaySqlCase(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regprocedure('public.gameday_slate(date, boolean, boolean)')").fetchone()[0] is None:
                raise unittest.SkipTest("db/068 not pasted")

    def add(self, conn: psycopg.Connection, *, league: str = "nfl", kind: str = "game", at: str,
            home: str | None = "TL Home", away: str | None = "TL Away", tv: list[str] | None = None,
            national: bool = True, test: bool = False, participants: dict | None = None, tbd: bool = False,
            status: str = "scheduled", score: dict | None = None, parent: str | None = None,
            session: str | None = None, name: str | None = None, neutral: bool | None = None,
            venue: str | None = None, link: tuple[str, str] | None = None, suffix: str = "") -> int:
        eid = ("e2e-" if test else "tl068-") + uuid.uuid4().hex[:12] + suffix
        espn = {"live": "STATUS_IN_PROGRESS", "final": "STATUS_FINAL"}.get(status, "STATUS_SCHEDULED")
        return conn.execute(
            f"INSERT INTO sports_events ({COLS}) VALUES (%s, %s, %s, %s, %s, %s, {at}, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, md5(%s), %s, %s, %s) RETURNING id",
            (league, kind, eid, parent, name or f"{away} at {home}", session, tbd, home, away,
             json.dumps(participants) if participants is not None else None, tv if tv is not None else ["ESPN"],
             national, neutral, venue, status, espn, json.dumps(score) if score is not None else None, eid, test,
             link[0] if link else None, link[1] if link else None)).fetchone()[0]

    def slate(self, conn: psycopg.Connection, day: date, all_: bool = False, tests: bool = False) -> dict[int, dict]:
        return {r[0]["id"]: r[0] for r in conn.execute("SELECT s FROM gameday_slate(%s, %s, %s) s", (day, all_, tests))}

    def brief(self, conn: psycopg.Connection, day: date) -> list[str] | None:
        out = conn.execute("SELECT gameday_brief(%s)", (day,)).fetchone()[0]
        return None if out is None else out["lines"]


class Objects(GamedaySqlCase):
    READERS = ("gameday_slate(date, boolean, boolean)", "gameday_live(bigint[])", "gameday_event(bigint)",
               "gameday_brief(date)", "ctx_sports_brief()", "ctx_sports_today()", "gameday_day(timestamptz, boolean)",
               "sports_tv_tier(text[])")
    HELPERS = ("add_test_gameday_event(text, text, timestamptz, text, text, text, text[], jsonb, jsonb, boolean, "
               "text, text, text, text, text, text, boolean, text)",
               "set_test_sports_live(text, bigint, text, jsonb, text, text, integer)")

    def test_grants_definers_and_the_providers(self) -> None:
        with _rolled_back() as conn:
            for fn in self.READERS:
                with self.subTest(fn=fn):
                    self.assertEqual(conn.execute(
                        "SELECT has_function_privilege('anon', %s, 'EXECUTE'), "
                        "has_function_privilege('authenticated', %s, 'EXECUTE'), "
                        "has_function_privilege('valemont_readonly', %s, 'EXECUTE'), "
                        "(SELECT prosecdef FROM pg_proc WHERE oid = %s::regprocedure)",
                        (f"public.{fn}",) * 4).fetchone(), (False, True, True, False))
            for fn in self.HELPERS:
                with self.subTest(fn=fn):
                    self.assertEqual(conn.execute(
                        "SELECT has_function_privilege('anon', %s, 'EXECUTE'), "
                        "has_function_privilege('authenticated', %s, 'EXECUTE'), "
                        "has_function_privilege('valemont_readonly', %s, 'EXECUTE'), "
                        "(SELECT prosecdef FROM pg_proc WHERE oid = %s::regprocedure)",
                        (f"public.{fn}",) * 4).fetchone(), (False, True, False, True))
            self.assertEqual(conn.execute(
                "SELECT key, label, brief_section, enabled, sort FROM context_providers "
                "WHERE key IN ('sports_today', 'sports_brief') ORDER BY sort").fetchall(),
                [("sports_today", "Sports today", False, True, 46), ("sports_brief", "Game Day", True, True, 47)])
            self.assertEqual(conn.execute("SELECT name FROM migration_log WHERE name = '068_gameday'").fetchone(),
                             ("068_gameday",))


class Slate(GamedaySqlCase):
    def test_the_five_the_week_all_sports_and_the_day_boundaries(self) -> None:
        with _rolled_back() as conn:
            bama = self.add(conn, league="cfb", at=local(SAT, "14:30"), home="LSU Tigers", away="Alabama Crimson Tide",
                            tv=["ABC"], participants={"home": {"id": "99", "rank": 14, "conference_id": "8"},
                                                      "away": {"id": "333", "rank": 5, "conference_id": "8"}})
            late = self.add(conn, league="nba", at=local(SUN, "04:59"))
            early = self.add(conn, league="nba", at=local(SUN, "05:00"))
            tbd = self.add(conn, league="cfb", at="timestamptz '2031-11-08 05:00:00+00'", tbd=True, tv=[],
                           national=False)
            nhl = self.add(conn, league="nhl", at=local(SAT, "18:00"), tv=["TNT"])
            week = self.add(conn, at=local(date(2031, 11, 14), "12:00"))
            nhl_week = self.add(conn, league="nhl", at=local(date(2031, 11, 14), "12:00"))
            beyond = self.add(conn, at=local(date(2031, 11, 16), "12:00"))
            test = self.add(conn, at=local(SAT, "12:00"), test=True)

            five = self.slate(conn, SAT)
            self.assertTrue({bama, late, early, tbd, week} <= five.keys())
            self.assertFalse({nhl, nhl_week, beyond, test} & five.keys())
            self.assertEqual(five[late]["day"], SAT.isoformat())     # 04:59: still Saturday night
            self.assertEqual(five[early]["day"], SUN.isoformat())    # 05:00: Sunday
            self.assertEqual((five[tbd]["day"], five[tbd]["tbd"]), (SAT.isoformat(), True))  # ESPN's date
            self.assertNotIn("tv", five[tbd])                        # blank broadcast: the page says Not listed
            self.assertEqual({k: five[bama][k] for k in ("ar", "hr", "sec", "bama", "tier", "tv")},
                             {"ar": 5, "hr": 14, "sec": True, "bama": True, "tier": 1, "tv": ["ABC"]})
            self.assertFalse({"title", "score", "test", "clock", "detail"} & five[bama].keys(), "compact: no nulls")

            everything = self.slate(conn, SAT, all_=True)
            self.assertIn(nhl, everything)
            self.assertNotIn(nhl_week, everything, "All sports adds tonight and live, never the other leagues' week")
            self.assertEqual(self.slate(conn, SAT, tests=True)[test].get("test"), True)

    def test_rls_the_owner_reads_a_stranger_reads_nothing_anon_cannot_call(self) -> None:
        with _rolled_back() as conn, conn.cursor() as cur:
            a = self.add(conn, at=local(SAT, "12:00"))
            with conn.transaction():
                _act_as(cur, "authenticated", self.owner)
                self.assertIn(a, {r[0]["id"] for r in cur.execute("SELECT s FROM gameday_slate(%s) s", (SAT,))})
                self.assertIsNotNone(cur.execute("SELECT gameday_event(%s)", (a,)).fetchone()[0])
            with conn.transaction():
                _act_as(cur, "authenticated", uuid.uuid4())
                self.assertEqual(cur.execute("SELECT count(*) FROM gameday_slate(%s) s", (SAT,)).fetchone()[0], 0)
                self.assertIsNone(cur.execute("SELECT gameday_event(%s)", (a,)).fetchone()[0])
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                _act_as(cur, "anon")
                cur.execute("SELECT gameday_slate(%s)", (SAT,))


class LiveAndEvent(GamedaySqlCase):
    def test_the_poll_returns_only_the_asked_ids_trimmed_and_at_most_60(self) -> None:
        with _rolled_back() as conn:
            golf = self.add(conn, league="pga", kind="golf_round", at=local(SAT, "07:00"), home=None, away=None,
                            name="TL Open", session="R2", status="live", parent="tl068open",
                            score={"round": 2, "leaders": [{"name": "A", "pos": "1"}, {"name": "B"}],
                                   "followed": [{"name": "Scottie Scheffler", "pos": "T5", "thru": 12}]})
            game = self.add(conn, at=local(SAT, "12:00"), status="live", score={"home": 7, "away": 3})
            other = self.add(conn, at=local(SAT, "12:00"))
            rows = {r[0]["id"]: r[0] for r in conn.execute("SELECT l FROM gameday_live(%s) l", ([golf, game],))}
            self.assertEqual(set(rows), {golf, game})
            self.assertNotIn(other, rows)
            self.assertEqual(rows[golf]["score"], {"round": 2, "leader": {"name": "A", "pos": "1"},
                                                   "followed": [{"name": "Scottie Scheffler", "pos": "T5", "thru": 12}]})
            self.assertEqual(rows[game]["score"], {"home": 7, "away": 3})
            many = list(range(-60, 0)) + [game]  # 61 ids, the real one last
            self.assertEqual(conn.execute("SELECT count(*) FROM gameday_live(%s)", (many,)).fetchone()[0], 0,
                             "only the first 60 ids are read")

    def test_the_event_kalshi_venue_f1_weekend_and_golf_standings(self) -> None:
        with _rolled_back() as conn:
            game = self.add(conn, at=local(SAT, "12:00"), neutral=True, venue="TL Stadium",
                            link=("not_linked", "TL reason"))
            unlisted = self.add(conn, at=local(SAT, "13:00"), link=("not_listed", "after the latest listed event"))
            ev = conn.execute("SELECT gameday_event(%s)", (game,)).fetchone()[0]
            self.assertEqual(ev["kalshi"], {"state": "not_linked", "reason": "TL reason", "ticker": None,
                                            "series": "KXNFLGAME"})
            self.assertEqual((ev["venue"], ev["neutral_site"], ev["title"]), ("TL Stadium", True, "TL Away vs TL Home"))
            self.assertEqual(conn.execute("SELECT gameday_event(%s)->'kalshi'->>'state'", (unlisted,)).fetchone()[0],
                             "not_listed")
            qual = self.add(conn, league="f1", kind="race_session", at=local(SAT, "13:00"), home=None, away=None,
                            name="TL GP", session="Qual", parent="tl068gp")
            self.add(conn, league="f1", kind="race_session", at=local(SUN, "13:00"), home=None, away=None,
                     name="TL GP", session="Race", parent="tl068gp")
            sessions = conn.execute("SELECT gameday_event(%s)->'sessions'", (qual,)).fetchone()[0]
            self.assertEqual([s["session"] for s in sessions], ["Qual", "Race"])
            self.add(conn, league="pga", kind="golf_round", at="timestamptz '2031-11-07 05:00:00+00'", tbd=True,
                     home=None, away=None, name="TL Open", session="R1", status="final", parent="tl068open",
                     score={"round": 1, "leaders": [{"name": "Lead"}], "followed": []}, suffix="-r1")
            r2 = self.add(conn, league="pga", kind="golf_round", at="timestamptz '2031-11-08 05:00:00+00'", tbd=True,
                          home=None, away=None, name="TL Open", session="R2", parent="tl068open", suffix="-r2")
            st = conn.execute("SELECT gameday_event(%s)->'standings'", (r2,)).fetchone()[0]
            self.assertEqual((st["session"], st["score"]["leaders"][0]["name"]), ("R1", "Lead"))


class Brief(GamedaySqlCase):
    def test_saturday_alabama_first_national_games_f1_and_golf(self) -> None:
        with _rolled_back() as conn:
            self.add(conn, league="cfb", at=local(SAT, "14:30"), home="LSU Tigers", away="Alabama Crimson Tide",
                     tv=["ABC"], participants={"home": {"id": "99", "rank": 14}, "away": {"id": "333", "rank": 5}})
            self.add(conn, league="cfb", at=local(SAT, "19:00"), home="NoIds Home", away="NoIds Away", tv=["FS1"])
            self.add(conn, league="cfb", at=local(SAT, "11:00"), home="Local Home", away="Local Away",
                     tv=["ESPN+"], national=False)
            self.add(conn, league="cfb", at=local(SAT, "11:00"), home="Test Home", away="Test Away", tv=["CBS"],
                     test=True, participants={"home": {"id": "333"}, "away": {"id": "1"}})
            self.add(conn, league="f1", kind="race_session", at=local(SAT, "13:00"), home=None, away=None,
                     name="TL GP", session="Qual", parent="tl068gpb", tv=["Apple TV"])
            self.add(conn, league="f1", kind="race_session", at=local(SUN, "13:00"), home=None, away=None,
                     name="TL GP", session="Race", parent="tl068gpb", tv=["Apple TV"])
            self.add(conn, league="pga", kind="golf_round", at="timestamptz '2031-11-07 05:00:00+00'", tbd=True,
                     home=None, away=None, name="TL Open", session="R1", status="final", parent="tl068ob",
                     tv=["Golf Chnl"], suffix="-r1",
                     score={"round": 1, "leaders": [{"name": "Lead Er", "to_par": "-9"}],
                            "followed": [{"name": "Brooks Koepka", "pos": "T9", "to_par": "-3", "thru": 18}]})
            self.add(conn, league="pga", kind="golf_round", at="timestamptz '2031-11-08 05:00:00+00'", tbd=True,
                     home=None, away=None, name="TL Open", session="R2", parent="tl068ob", tv=["Golf Chnl"],
                     suffix="-r2")
            self.assertEqual(self.brief(conn, SAT), [
                "CFB: No. 5 Alabama Crimson Tide at No. 14 LSU Tigers, 2:30 PM on ABC.",
                "CFB: NoIds Away at NoIds Home, 7:00 PM on FS1.",
                "F1, TL GP: Qualifying today 1:00 PM; Race Sun 1:00 PM.",
                "Golf, TL Open: R2 today; Lead Er leads at -9 after R1; Brooks Koepka T9 (-3, thru 18).",
            ])

    def test_sunday_leads_nfl_caps_at_five_and_a_dead_night_says_nothing(self) -> None:
        with _rolled_back() as conn:
            for i in range(6):
                self.add(conn, league="nba", at=local(SUN, "12:00"), home=f"NBA Home {i}", away=f"NBA Away {i}",
                         tv=["ABC"])
            self.add(conn, at=local(SUN, "15:25"), home="NFL Home", away="NFL Away", tv=["ESPN+"])
            lines = self.brief(conn, SUN)
            self.assertEqual(len(lines), 5)
            self.assertEqual(lines[0], "NFL: NFL Away at NFL Home, 3:25 PM on ESPN+.")
            self.assertIsNone(self.brief(conn, DEAD))
            self.add(conn, league="nba", at=local(DEAD, "19:00"), tv=["MNMT"], national=False)
            self.assertIsNone(self.brief(conn, DEAD), "a regional-only night is a dead night")

    def test_the_provider_reads_today_and_the_context_keeps_its_keys(self) -> None:
        with _rolled_back() as conn:
            same = conn.execute("SELECT ctx_sports_brief() IS NOT DISTINCT FROM gameday_brief(gameday_today())"
                                ).fetchone()[0]
            self.assertTrue(same)
            ctx = conn.execute("SELECT ctx_sports_today()").fetchone()[0]
            self.assertTrue({"counts", "live", "owner_five", "owner_five_more", "other_tonight", "this_week",
                             "alabama_next", "about", "date"} <= ctx.keys())


class Helpers(GamedaySqlCase):
    def test_the_test_helpers_need_the_marker_and_touch_test_rows_only(self) -> None:
        with _rolled_back() as conn, conn.cursor() as cur:
            real = self.add(conn, at=local(SAT, "12:00"))
            conn.execute("INSERT INTO e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(%s, 'UTF8')), 'hex'))",
                         (MARKER,))
            _act_as(cur, "authenticated", self.owner)
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                cur.execute("SELECT add_test_gameday_event('not-the-marker', 'nfl', now())")
            tid = cur.execute(
                "SELECT add_test_gameday_event(%s, 'cfb', now(), 'TL H', 'TL A', 'scheduled', ARRAY['CBS'], "
                "'{\"home\": {\"id\": \"333\"}}'::jsonb, p_link_state => 'not_linked', p_link_reason => 'TL')",
                (MARKER,)).fetchone()[0]
            got = cur.execute("SELECT is_test, national_tv, link_state, espn_id LIKE 'e2e-%%' FROM sports_events "
                              "WHERE id = %s", (tid,)).fetchone()
            self.assertEqual(got, (True, True, "not_linked", True))
            cur.execute("SELECT set_test_sports_live(%s, %s, 'live', '{\"home\": 3, \"away\": 0}', 'Q1 9:00', '9:00', 1)",
                        (MARKER, tid))
            self.assertEqual(cur.execute("SELECT l FROM gameday_live(%s) l", ([tid],)).fetchone()[0],
                             {"id": tid, "status": "live", "detail": "Q1 9:00", "clock": "9:00",
                              "score": {"home": 3, "away": 0}})
            with self.assertRaises(psycopg.errors.RaiseException), conn.transaction():
                cur.execute("SELECT set_test_sports_live(%s, %s, 'final', '{}')", (MARKER, real))


if __name__ == "__main__":
    unittest.main()
