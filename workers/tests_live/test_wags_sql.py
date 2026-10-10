"""db/029 the context layer and Wags: every provider on seeded rows (paper and
live never summed, test entries excluded), a provider that raises reported
under its key without sinking the snapshot, the snapshot's shape and its
dedupe by hash, append-only messages and snapshots (and the one cascade they
allow), RLS for owner / stranger / anon, the rate-limit counter and the AI
budget state shared with core/ai.py.

Every test runs inside ONE transaction that is always rolled back (as in
test_ventures_sql): these are the owner's real tables, so nothing written
here may survive. Test rows are titled or slugged `tests-live-*`.

Skips until db/029 is pasted.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid
from contextlib import contextmanager
from typing import Iterator

import psycopg

from core.paths import load_env

load_env()


@contextmanager
def _rolled_back() -> Iterator[psycopg.Cursor]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        try:
            yield cur
        finally:
            conn.rollback()


def _act_as(cur: psycopg.Cursor, role: str, sub: uuid.UUID | str | None = None) -> None:
    claims = {"role": role} | ({"sub": str(sub)} if sub else {})
    cur.execute("SELECT set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(sub) if sub else "",))
    cur.execute(f"SET LOCAL ROLE {role}")


def _refused(cur: psycopg.Cursor, sql: str, params: tuple = ()) -> str:
    """Run sql inside a savepoint; return the error text (fails if it ran)."""
    cur.execute("SAVEPOINT probe")
    try:
        cur.execute(sql, params)
    except psycopg.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT probe")
        return str(exc)
    cur.execute("ROLLBACK TO SAVEPOINT probe")
    raise AssertionError(f"not refused: {sql}")


class WagsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            with _rolled_back() as cur:
                cur.execute("SELECT to_regclass('public.wags_messages')")
                if cur.fetchone()[0] is None:
                    raise unittest.SkipTest("db/029 not pasted")
                cur.execute("SELECT owner_id FROM app_settings")
                cls.owner = cur.fetchone()[0]
        except unittest.SkipTest:
            raise
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")

    def one(self, cur: psycopg.Cursor, sql: str, params: tuple = ()) -> object:
        cur.execute(sql, params)
        return cur.fetchone()[0]

    def thread(self, cur: psycopg.Cursor) -> uuid.UUID:
        return self.one(cur, "INSERT INTO wags_threads (owner_id, origin_page) VALUES (%s, '/goals') RETURNING id",
                        (self.owner,))

    def message(self, cur: psycopg.Cursor, thread: uuid.UUID, role: str = "user", content: str = "tests-live hi") -> int:
        return self.one(cur, "INSERT INTO wags_messages (thread_id, owner_id, role, content) VALUES (%s, %s, %s, %s) RETURNING id",
                        (thread, self.owner, role, content))


# ------------------------------------------------------------------ registry

class Registry(WagsTestCase):
    def test_three_providers_in_order_each_a_stable_invoker_reader(self) -> None:
        with _rolled_back() as cur:
            cur.execute("""
                SELECT c.key, p.proname, p.prosecdef, p.provolatile::text, c.enabled, c.brief_section
                  FROM context_providers c JOIN pg_proc p ON p.oid = c.fn::oid
                 WHERE c.key IN ('goals', 'ventures', 'capital')
                 ORDER BY c.sort""")
            rows = cur.fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows],
                         [("goals", "ctx_goals"), ("ventures", "ctx_ventures"), ("capital", "ctx_capital")])
        for key, _, definer, volatility, enabled, brief in rows:
            self.assertFalse(definer, f"{key}: SECURITY INVOKER, so RLS applies")
            self.assertEqual(volatility, "s", f"{key}: STABLE")
            self.assertTrue(enabled and brief, key)

    def test_the_owner_cannot_register_a_provider(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            _refused(cur, "INSERT INTO context_providers (key, label, fn) VALUES ('evil', 'Evil', 'public.is_owner()'::regprocedure)")
            self.assertEqual(self.one(cur, "SELECT count(*) FROM context_providers WHERE key IN ('goals','ventures','capital')"), 3)


# ------------------------------------------------------------------ snapshot

class Snapshot(WagsTestCase):
    def test_shape_and_the_owners_clock(self) -> None:
        with _rolled_back() as cur:
            snap = self.one(cur, "SELECT context_snapshot()")
            today = self.one(cur, "SELECT goal_local_today()::text")
        for key in ("goals", "ventures", "capital", "built_at", "local_date", "local_time", "weekday", "iso_week", "timezone"):
            self.assertIn(key, snap)
        for key in ("goals", "ventures", "capital"):
            self.assertNotIn("error", snap[key], f"{key} raised: {snap[key]}")
        self.assertEqual(snap["local_date"], today)
        self.assertRegex(snap["iso_week"], r"^\d{4}-W\d{2}$")
        self.assertRegex(snap["local_time"], r"^\d{2}:\d{2}$")

    def test_a_provider_that_raises_is_reported_and_sinks_nothing(self) -> None:
        with _rolled_back() as cur:
            cur.execute("""
                CREATE FUNCTION public.ctx_tests_live_broken() RETURNS jsonb LANGUAGE plpgsql STABLE AS
                $$ BEGIN RAISE EXCEPTION 'tests-live: provider down'; END $$""")
            cur.execute("INSERT INTO context_providers (key, label, fn, sort) "
                        "VALUES ('tests_live_broken', 'Broken', 'public.ctx_tests_live_broken()'::regprocedure, 15)")
            snap = self.one(cur, "SELECT context_snapshot()")
        self.assertEqual(snap["tests_live_broken"], {"error": "tests-live: provider down"})
        for key in ("goals", "ventures", "capital"):
            self.assertNotIn("error", snap[key])

    def test_a_disabled_provider_is_left_out(self) -> None:
        with _rolled_back() as cur:
            cur.execute("UPDATE context_providers SET enabled = false WHERE key = 'capital'")
            snap = self.one(cur, "SELECT context_snapshot()")
        self.assertNotIn("capital", snap)
        self.assertIn("goals", snap)

    def test_record_dedupes_by_data_not_by_clock(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            first = self.one(cur, "SELECT context_snapshot_record()")
            again = self.one(cur, "SELECT context_snapshot_record()")
            self.assertEqual(first["id"], again["id"], "same pillar state, same row")
            cur.execute("INSERT INTO goals (title, horizon, period_start) VALUES "
                        "('tests-live dedupe', 'weekly', date_trunc('week', goal_local_today())::date)")
            changed = self.one(cur, "SELECT context_snapshot_record()")
            self.assertNotEqual(changed["id"], first["id"], "a change in the data is a new row")
            stored = self.one(cur, "SELECT payload FROM context_snapshots WHERE id = %s", (changed["id"],))
            self.assertIn("tests-live dedupe", json.dumps(stored))
            self.assertRegex(self.one(cur, "SELECT hash FROM context_snapshots WHERE id = %s", (changed["id"],)),
                             r"^[0-9a-f]{64}$")

    def test_the_worker_records_under_the_owner(self) -> None:
        # Phase 5: the Morning Brief calls it with no session.
        with _rolled_back() as cur:
            rec = self.one(cur, "SELECT context_snapshot_record()")
            self.assertEqual(self.one(cur, "SELECT owner_id FROM context_snapshots WHERE id = %s", (rec["id"],)), self.owner)


# ----------------------------------------------------------------- providers

class Goals(WagsTestCase):
    def test_this_week_counts_titles_trims_and_venture_links(self) -> None:
        with _rolled_back() as cur:
            before = self.one(cur, "SELECT ctx_goals()")
            vid = self.one(cur, "INSERT INTO ventures (owner_id, name, slug, stage) VALUES (%s, 'tests-live V', 'tests-live-v', 'idea') RETURNING id",
                           (self.owner,))
            week = "date_trunc('week', goal_local_today())::date"
            cur.execute(f"INSERT INTO goals (owner_id, title, horizon, period_start, area, venture_id) VALUES "
                        f"(%s, 'tests-live open', 'weekly', {week}, 'business', %s)", (self.owner, vid))
            cur.execute(f"INSERT INTO goals (owner_id, title, horizon, period_start, status, completed_at) VALUES "
                        f"(%s, 'tests-live done', 'weekly', {week}, 'done', now())", (self.owner,))
            cur.execute(f"INSERT INTO goals (owner_id, title, horizon, period_start) VALUES "
                        f"(%s, %s, 'weekly', {week})", (self.owner, "tests-live " + "x" * 150))
            after = self.one(cur, "SELECT ctx_goals()")
        self.assertEqual(after["week"]["held"] - before["week"]["held"], 3)
        self.assertEqual(after["week"]["done"] - before["week"]["done"], 1)
        goals = {g["title"]: g for g in after["week"]["goals"]}
        if "tests-live open" in goals:  # the list is capped at 15
            self.assertEqual(goals["tests-live open"]["venture"], "tests-live-v")
            self.assertNotIn("carried", goals["tests-live open"], "a zero carry count is left out")
        long = [t for t in goals if t.startswith("tests-live x")]
        for t in long:
            self.assertLessEqual(len(t), 90)
            self.assertTrue(t.endswith("…"))
        for key in ("week_of", "month", "long_term", "last_4_weeks"):
            self.assertIn(key, after)


class Ventures(WagsTestCase):
    def test_active_detail_and_the_names_of_the_rest(self) -> None:
        with _rolled_back() as cur:
            vid = self.one(cur, "INSERT INTO ventures (owner_id, name, slug, stage, next_action, blockers) VALUES "
                                "(%s, 'tests-live Active', 'tests-live-active', 'building', 'Call the city', %s) RETURNING id",
                           (self.owner, "b" * 400))
            cur.execute("INSERT INTO venture_workstreams (owner_id, venture_id, name, next_action) VALUES (%s, %s, 'Permits', 'File it')",
                        (self.owner, vid))
            cur.execute("INSERT INTO venture_workstreams (owner_id, venture_id, name, state) VALUES (%s, %s, 'Parked one', 'parked')",
                        (self.owner, vid))
            cur.execute("INSERT INTO venture_dates (owner_id, venture_id, label, due_on) VALUES (%s, %s, 'Late thing', goal_local_today() - 3)",
                        (self.owner, vid))
            cur.execute("INSERT INTO venture_dates (owner_id, venture_id, label, due_on) VALUES (%s, %s, 'Soon thing', goal_local_today() + 3)",
                        (self.owner, vid))
            for i in range(4):
                cur.execute("INSERT INTO venture_log (owner_id, venture_id, entry) VALUES (%s, %s, %s)",
                            (self.owner, vid, f"tests-live log {i}"))
            cur.execute("INSERT INTO ventures (owner_id, name, slug) VALUES (%s, 'tests-live Bare', 'tests-live-bare')", (self.owner,))
            cur.execute("INSERT INTO ventures (owner_id, name, slug, stage, archived_at) VALUES "
                        "(%s, 'tests-live Gone', 'tests-live-gone', 'paused', now())", (self.owner,))
            ctx = self.one(cur, "SELECT ctx_ventures()")
        [v] = [a for a in ctx["active"] if a["slug"] == "tests-live-active"]
        self.assertEqual(v["stage"], "building")
        self.assertEqual(v["next_action"], "Call the city")
        self.assertLessEqual(len(v["blockers"]), 200)
        self.assertEqual([w["name"] for w in v["workstreams"]], ["Permits"], "active workstreams only")
        dates = {d["label"]: d for d in v["dates"]}
        self.assertTrue(dates["Late thing"].get("overdue"))
        self.assertNotIn("overdue", dates["Soon thing"])
        self.assertEqual([entry["entry"] for entry in v["log"]], ["tests-live log 3", "tests-live log 2", "tests-live log 1"])
        self.assertIn("tests-live Bare", ctx["not_set_up"])
        self.assertIn("tests-live Gone", ctx["archived"])
        self.assertNotIn("tests-live-gone", [a["slug"] for a in ctx["active"]])


class Capital(WagsTestCase):
    def test_paper_and_live_stay_separate_and_test_entries_stay_out(self) -> None:
        with _rolled_back() as cur:
            cur.execute("INSERT INTO bankroll_entries (owner_id, mode, kind, amount, note) VALUES "
                        "(%s, 'live', 'deposit', 123.45, 'tests-live live')", (self.owner,))
            cur.execute("INSERT INTO bankroll_entries (owner_id, mode, kind, amount, note) VALUES "
                        "(%s, 'paper', 'deposit', 10.00, 'tests-live paper')", (self.owner,))
            cur.execute("INSERT INTO bankroll_entries (owner_id, mode, kind, amount, note, is_test) VALUES "
                        "(%s, 'paper', 'deposit', 999.00, 'tests-live TEST', true)", (self.owner,))
            ctx = self.one(cur, "SELECT ctx_capital()")
            paper_value = self.one(cur, "SELECT sum(value) FROM capital_value_as_of('paper', now())")
            live_value = self.one(cur, "SELECT sum(value) FROM capital_value_as_of('live', now())")
        modes = ctx["modes"]
        self.assertEqual(set(modes), {"paper", "live"})
        self.assertEqual(float(modes["paper"]["total"]), float(paper_value))
        self.assertEqual(float(modes["live"]["total"]), float(live_value))
        self.assertTrue(modes["paper"]["label"].startswith("PAPER"))
        self.assertTrue(modes["live"]["label"].startswith("LIVE"))
        self.assertNotIn("total", ctx, "nothing sums the two modes")
        notes = [e.get("note") for e in modes["paper"]["last_entries"]]
        self.assertIn("tests-live paper", notes)
        self.assertNotIn("tests-live TEST", notes, "test entries are not real money")
        self.assertLessEqual(len(modes["paper"]["last_entries"]), 5)
        self.assertEqual([e.get("note") for e in modes["live"]["last_entries"]][0], "tests-live live")


# -------------------------------------------------------------- append-only

class AppendOnly(WagsTestCase):
    def test_messages_and_snapshots_refuse_update_delete_truncate(self) -> None:
        with _rolled_back() as cur:
            t = self.thread(cur)
            m = self.message(cur, t)
            snap = self.one(cur, "SELECT context_snapshot_record()")
            self.assertIn("append-only", _refused(cur, "UPDATE wags_messages SET content = 'edited' WHERE id = %s", (m,)))
            self.assertIn("append-only", _refused(cur, "DELETE FROM wags_messages WHERE id = %s", (m,)))
            self.assertIn("append-only", _refused(cur, "TRUNCATE wags_messages"))
            _refused(cur, "UPDATE context_snapshots SET payload = '{}' WHERE id = %s", (snap["id"],))
            _refused(cur, "DELETE FROM context_snapshots WHERE id = %s", (snap["id"],))
            # A plain TRUNCATE is refused by the foreign key from wags_messages
            # first; CASCADE gets past that, so it proves the trigger itself.
            self.assertIn("append-only", _refused(cur, "TRUNCATE context_snapshots CASCADE"))

    def test_deleting_a_thread_takes_its_messages_and_touches_nothing_else(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            t = self.one(cur, "INSERT INTO wags_threads (origin_page) VALUES ('/') RETURNING id")
            other = self.one(cur, "INSERT INTO wags_threads (origin_page) VALUES ('/') RETURNING id")
            for tid in (t, other):
                cur.execute("INSERT INTO wags_messages (thread_id, role, content) VALUES (%s, 'user', 'tests-live')", (tid,))
            # The owner has no DELETE on messages: the cascade is the only way.
            _refused(cur, "DELETE FROM wags_messages WHERE thread_id = %s", (t,))
            cur.execute("DELETE FROM wags_threads WHERE id = %s", (t,))
            self.assertEqual(self.one(cur, "SELECT count(*) FROM wags_messages WHERE thread_id = %s", (t,)), 0)
            self.assertEqual(self.one(cur, "SELECT count(*) FROM wags_messages WHERE thread_id = %s", (other,)), 1)

    def test_a_message_moves_its_thread_to_the_top(self) -> None:
        with _rolled_back() as cur:
            t = self.thread(cur)
            cur.execute("UPDATE wags_threads SET updated_at = now() - interval '1 day' WHERE id = %s", (t,))
            self.message(cur, t)
            self.assertTrue(self.one(cur, "SELECT updated_at > now() - interval '1 minute' FROM wags_threads WHERE id = %s", (t,)))

    def test_a_user_message_is_one_to_4000_characters(self) -> None:
        with _rolled_back() as cur:
            t = self.thread(cur)
            self.assertIn("wags_messages_user_length", _refused(cur,
                          "INSERT INTO wags_messages (thread_id, owner_id, role, content) VALUES (%s, %s, 'user', %s)",
                          (t, self.owner, "x" * 4001)))
            self.message(cur, t, content="x" * 4000)
            self.message(cur, t, role="assistant", content="y" * 9000)  # answers are not capped here


# ---------------------------------------------------------------------- RLS

class Rls(WagsTestCase):
    def test_a_stranger_sees_nothing_and_cannot_write_into_the_owners_thread(self) -> None:
        with _rolled_back() as cur:
            t = self.thread(cur)
            self.message(cur, t)
            self.one(cur, "SELECT context_snapshot_record()")
            _act_as(cur, "authenticated", uuid.uuid4())
            for table in ("wags_threads", "wags_messages", "context_snapshots", "context_providers"):
                self.assertEqual(self.one(cur, f"SELECT count(*) FROM {table}"), 0, table)
            _refused(cur, "INSERT INTO wags_messages (thread_id, role, content) VALUES (%s, 'user', 'tests-live intrusion')", (t,))

    def test_anon_has_no_privilege_at_all(self) -> None:
        with _rolled_back() as cur:
            for table in ("wags_threads", "wags_messages", "context_snapshots", "context_providers"):
                self.assertFalse(self.one(cur, "SELECT has_table_privilege('anon', %s, 'SELECT,INSERT,UPDATE,DELETE')",
                                          (f"public.{table}",)), table)
            self.assertFalse(self.one(cur, "SELECT has_function_privilege('anon', 'public.context_snapshot()', 'EXECUTE')"))

    def test_the_owner_renames_and_archives_but_cannot_rewrite_a_thread(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            t = self.one(cur, "INSERT INTO wags_threads (origin_page) VALUES ('/') RETURNING id")
            cur.execute("UPDATE wags_threads SET title = 'tests-live renamed', archived_at = now() WHERE id = %s", (t,))
            self.assertEqual(self.one(cur, "SELECT title FROM wags_threads WHERE id = %s", (t,)), "tests-live renamed")
            _refused(cur, "UPDATE wags_threads SET created_at = now() - interval '1 year' WHERE id = %s", (t,))
            _refused(cur, "UPDATE wags_threads SET origin_page = '/capital' WHERE id = %s", (t,))


# -------------------------------------------------------- rate and budget

class RateAndBudget(WagsTestCase):
    def test_the_rate_counter_counts_recent_user_messages_only(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            before = self.one(cur, "SELECT wags_user_messages_since(60)")
            t = self.one(cur, "INSERT INTO wags_threads (origin_page) VALUES ('/') RETURNING id")
            for role in ("user", "user", "assistant", "user"):
                cur.execute("INSERT INTO wags_messages (thread_id, role, content) VALUES (%s, %s, 'tests-live')", (t, role))
            self.assertEqual(self.one(cur, "SELECT wags_user_messages_since(60)") - before, 3)

    def test_budget_state_is_the_workers_month_and_rows(self) -> None:
        with _rolled_back() as cur:
            state = self.one(cur, "SELECT ai_budget_state()")
            # core/ledger.py ai_spend_month_to_date's query, verbatim in spirit.
            spent = self.one(cur, """
                SELECT coalesce(sum(cost_usd), 0) FROM ai_usage
                 WHERE created_at >= (date_trunc('month', now() AT TIME ZONE coalesce(
                     (SELECT timezone FROM app_settings), 'America/Chicago'))
                     AT TIME ZONE coalesce((SELECT timezone FROM app_settings), 'America/Chicago'))""")
            self.assertEqual(float(state["spent_usd"]), float(spent))
            self.assertIn("alerted_80", state)
            cur.execute("INSERT INTO notifications (owner_id, kind, title, status, sent_at) "
                        "VALUES (%s, 'ai_budget_80', 'tests-live', 'sent', now())", (self.owner,))
            self.assertTrue(self.one(cur, "SELECT ai_budget_state()")["alerted_80"])
            # A queued (never sent) alert does not count, as in the worker.
            cur.execute("INSERT INTO notifications (owner_id, kind, title) VALUES (%s, 'ai_budget_100', 'tests-live')", (self.owner,))
            self.assertFalse(self.one(cur, "SELECT ai_budget_state()")["alerted_100"])


if __name__ == "__main__":
    unittest.main()
