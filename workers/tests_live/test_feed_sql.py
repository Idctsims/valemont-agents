"""db/032 the feed's storage: RLS (anon nothing, the owner reads and cannot
write, a stranger sees nothing, valemont_readonly reads all), the unique
constraints (source + external id, canonical url), the lead rule (only an
item with a url can lead, and only an item of its own story), v_feed_stories
(no test story, no filtered story, no story without a linkable item), the
frozen item (only clearing its embedding), is_test consistency, the regex
check on feed_rules, and add_test_story's marker and owner checks.

The 48-hour candidate query and the prune run ledger SQL verbatim; they are
added with that SQL (Chat 3 Phase 1, Step 3).

Every test runs inside ONE transaction that is always rolled back.

Skips until db/032 is pasted.
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

TABLES = ("sources", "stories", "content_items", "feed_rules")
VEC = "[" + ",".join(["0.05"] * 384) + "]"


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
    cur.execute("SAVEPOINT probe")
    try:
        cur.execute(sql, params)
    except psycopg.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT probe")
        return str(exc)
    cur.execute("ROLLBACK TO SAVEPOINT probe")
    raise AssertionError(f"not refused: {sql}")


class FeedTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            with _rolled_back() as cur:
                cur.execute("SELECT to_regclass('public.content_items')")
                if cur.fetchone()[0] is None:
                    raise unittest.SkipTest("db/032 not pasted")
                cur.execute("SELECT owner_id FROM app_settings")
                cls.owner = cur.fetchone()[0]
        except unittest.SkipTest:
            raise
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")

    def one(self, cur: psycopg.Cursor, sql: str, params: tuple = ()) -> object:
        cur.execute(sql, params)
        return cur.fetchone()[0]

    def source(self, cur: psycopg.Cursor, slug: str | None = None) -> int:
        return self.one(cur, """
            INSERT INTO sources (slug, name, pillars, kind, url)
            VALUES (%s, 'tests-live source', ARRAY['home'], 'rss', 'https://tests-live.invalid/feed')
            RETURNING id""", (slug or f"tl-{uuid.uuid4().hex[:10]}",))

    def e2e_source(self, cur: psycopg.Cursor) -> int:
        return self.one(cur, "SELECT id FROM sources WHERE slug = '_e2e' AND is_test")

    def story(self, cur: psycopg.Cursor, *, is_test: bool = False, filtered: str | None = None) -> int:
        return self.one(cur, """
            INSERT INTO stories (pillars, title, is_test, filtered_reason)
            VALUES (ARRAY['home'], 'tests-live story', %s, %s) RETURNING id""", (is_test, filtered))

    def item(self, cur: psycopg.Cursor, source: int, story: int | None, *, url: str | None = "auto",
             is_test: bool = False, ext: str | None = None, embedding: bool = False) -> int:
        if url == "auto":
            url = f"https://tests-live.invalid/{uuid.uuid4().hex}"
        failed = url is None
        return self.one(cur, f"""
            INSERT INTO content_items (source_id, external_id, url, google_url, title, published_at,
                                       story_id, seeded, decode_failed, is_test, embedding)
            VALUES (%s, %s, %s, %s, 'tests-live item', now(), %s, %s, %s, %s,
                    {"%s::extensions.halfvec" if embedding else "NULL"})
            RETURNING id""",
                        (source, ext or f"tl:{uuid.uuid4().hex}", url,
                         "https://news.google.com/rss/articles/tl" if failed else None,
                         story, story is None, failed, is_test) + ((VEC,) if embedding else ()))

    def lead(self, cur: psycopg.Cursor, story: int, item: int) -> None:
        cur.execute("UPDATE stories SET lead_item_id = %s WHERE id = %s", (item, story))

    def linked_story(self, cur: psycopg.Cursor, **kw) -> tuple[int, int]:
        src = kw.pop("source", None) or self.source(cur)
        st = self.story(cur, **kw)
        it = self.item(cur, src, st, is_test=kw.get("is_test", False))
        self.lead(cur, st, it)
        return st, it


class Access(FeedTestCase):
    def test_rls_on_with_one_owner_read_policy_and_no_anon(self) -> None:
        with _rolled_back() as cur:
            for t in TABLES:
                self.assertTrue(self.one(cur, "SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (f"public.{t}",)), t)
                cur.execute("SELECT policyname, roles::text, cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = %s", (t,))
                self.assertEqual(cur.fetchall(), [(f"{t}_owner_read", "{authenticated}", "SELECT")], t)
            for t in TABLES + ("v_feed_stories",):
                self.assertFalse(self.one(cur, "SELECT has_table_privilege('anon', %s, 'SELECT,INSERT,UPDATE,DELETE')", (f"public.{t}",)), t)
                self.assertFalse(self.one(cur, "SELECT has_table_privilege('authenticated', %s, 'INSERT,UPDATE,DELETE,TRUNCATE')", (f"public.{t}",)), t)

    def test_valemont_readonly_reads_all_and_writes_nothing(self) -> None:
        with _rolled_back() as cur:
            for t in TABLES + ("v_feed_stories",):
                self.assertTrue(self.one(cur, "SELECT has_table_privilege('valemont_readonly', %s, 'SELECT')", (f"public.{t}",)), t)
                self.assertFalse(self.one(cur, "SELECT has_table_privilege('valemont_readonly', %s, 'INSERT,UPDATE,DELETE')", (f"public.{t}",)), t)

    def test_the_owner_reads_and_cannot_write(self) -> None:
        with _rolled_back() as cur:
            st, it = self.linked_story(cur)
            _act_as(cur, "authenticated", self.owner)
            self.assertEqual(self.one(cur, "SELECT count(*) FROM stories WHERE id = %s", (st,)), 1)
            self.assertEqual(self.one(cur, "SELECT count(*) FROM content_items WHERE id = %s", (it,)), 1)
            self.assertEqual(self.one(cur, "SELECT count(*) FROM v_feed_stories WHERE id = %s", (st,)), 1)
            self.assertGreaterEqual(self.one(cur, "SELECT count(*) FROM feed_rules"), 33)
            self.assertGreaterEqual(self.one(cur, "SELECT count(*) FROM sources"), 1)
            _refused(cur, "UPDATE stories SET title = 'x' WHERE id = %s", (st,))
            _refused(cur, "DELETE FROM stories WHERE id = %s", (st,))
            _refused(cur, "INSERT INTO feed_rules (kind, pattern) VALUES ('title_regex', 'x')")
            _refused(cur, "UPDATE sources SET active = false")

    def test_a_stranger_sees_nothing(self) -> None:
        with _rolled_back() as cur:
            st, _ = self.linked_story(cur)
            _act_as(cur, "authenticated", uuid.uuid4())
            for t in TABLES + ("v_feed_stories",):
                self.assertEqual(self.one(cur, f"SELECT count(*) FROM {t}"), 0, t)


class Uniques(FeedTestCase):
    def test_one_row_per_source_and_external_id(self) -> None:
        with _rolled_back() as cur:
            src = self.source(cur)
            st = self.story(cur)
            self.item(cur, src, st, ext="tl:same")
            self.assertIn("content_items_source_external", _refused(cur, """
                INSERT INTO content_items (source_id, external_id, url, title, published_at, story_id)
                VALUES (%s, 'tl:same', 'https://tests-live.invalid/other', 't', now(), %s)""", (src, st)))
            # Another source may carry the same external id.
            self.item(cur, self.source(cur), st, ext="tl:same")

    def test_one_row_per_canonical_url_across_sources(self) -> None:
        with _rolled_back() as cur:
            st = self.story(cur)
            self.item(cur, self.source(cur), st, url="https://tests-live.invalid/same")
            self.assertIn("content_items_url", _refused(cur, """
                INSERT INTO content_items (source_id, external_id, url, title, published_at, story_id)
                VALUES (%s, 'tl:x', 'https://tests-live.invalid/same', 't', now(), %s)""", (self.source(cur), st)))
            # Two failed decodes (url NULL) never collide.
            self.item(cur, self.source(cur), st, url=None)
            self.item(cur, self.source(cur), st, url=None)

    def test_slug_is_unique(self) -> None:
        with _rolled_back() as cur:
            self.source(cur, "tl-dupe")
            self.assertIn("sources_slug_key", _refused(cur, """
                INSERT INTO sources (slug, name, pillars, kind, url)
                VALUES ('tl-dupe', 'x', ARRAY['home'], 'rss', 'https://tests-live.invalid/f')"""))


class LeadRule(FeedTestCase):
    def test_an_item_without_a_url_cannot_lead(self) -> None:
        with _rolled_back() as cur:
            src = self.source(cur)
            st = self.story(cur)
            bad = self.item(cur, src, st, url=None)
            self.assertIn("url must not be NULL", _refused(cur, "UPDATE stories SET lead_item_id = %s WHERE id = %s", (bad, st)))

    def test_a_story_cannot_be_inserted_led_by_an_unlinked_item(self) -> None:
        with _rolled_back() as cur:
            src = self.source(cur)
            st = self.story(cur)
            bad = self.item(cur, src, st, url=None)
            self.assertIn("url must not be NULL", _refused(cur, """
                INSERT INTO stories (pillars, title, lead_item_id) VALUES (ARRAY['home'], 't', %s)""", (bad,)))

    def test_the_lead_must_be_an_item_of_its_own_story(self) -> None:
        with _rolled_back() as cur:
            src = self.source(cur)
            a, b = self.story(cur), self.story(cur)
            other = self.item(cur, src, b)
            self.assertIn("not a linkable item", _refused(cur, "UPDATE stories SET lead_item_id = %s WHERE id = %s", (other, a)))

    def test_a_story_appears_only_once_a_linkable_item_leads(self) -> None:
        with _rolled_back() as cur:
            src = self.source(cur)
            st = self.story(cur)
            self.item(cur, src, st, url=None)
            self.assertEqual(self.one(cur, "SELECT count(*) FROM v_feed_stories WHERE id = %s", (st,)), 0)
            good = self.item(cur, src, st, url="https://tests-live.invalid/lead")
            self.lead(cur, st, good)
            cur.execute("SELECT lead_url, source_slug FROM v_feed_stories WHERE id = %s", (st,))
            self.assertEqual(cur.fetchone()[0], "https://tests-live.invalid/lead")

    def test_deleting_the_lead_item_clears_the_lead_and_hides_the_story(self) -> None:
        with _rolled_back() as cur:
            st, it = self.linked_story(cur)
            cur.execute("DELETE FROM content_items WHERE id = %s", (it,))
            self.assertIsNone(self.one(cur, "SELECT lead_item_id FROM stories WHERE id = %s", (st,)))
            self.assertEqual(self.one(cur, "SELECT count(*) FROM v_feed_stories WHERE id = %s", (st,)), 0)

    def test_deleting_a_story_deletes_its_items(self) -> None:
        with _rolled_back() as cur:
            st, it = self.linked_story(cur)
            cur.execute("DELETE FROM stories WHERE id = %s", (st,))
            self.assertEqual(self.one(cur, "SELECT count(*) FROM content_items WHERE id = %s", (it,)), 0)


class Visibility(FeedTestCase):
    def test_the_view_never_shows_a_test_or_filtered_story(self) -> None:
        with _rolled_back() as cur:
            real, _ = self.linked_story(cur)
            test, _ = self.linked_story(cur, is_test=True, source=self.e2e_source(cur))
            filtered, _ = self.linked_story(cur, filtered="title_regex:1")
            cur.execute("SELECT id FROM v_feed_stories WHERE id = ANY(%s)", ([real, test, filtered],))
            self.assertEqual([r[0] for r in cur.fetchall()], [real])

    def test_view_invariants_hold_on_every_row(self) -> None:
        with _rolled_back() as cur:
            self.assertEqual(self.one(cur, "SELECT count(*) FROM v_feed_stories WHERE lead_url IS NULL"), 0)
            self.assertEqual(self.one(cur, """
                SELECT count(*) FROM v_feed_stories v
                 WHERE NOT EXISTS (SELECT 1 FROM content_items i WHERE i.story_id = v.id AND i.url IS NOT NULL)"""), 0)
            self.assertEqual(self.one(cur, """
                SELECT count(*) FROM stories st JOIN content_items i ON i.id = st.lead_item_id
                 WHERE i.url IS NULL"""), 0)


class TestConsistency(FeedTestCase):
    def test_is_test_must_match_source_and_story(self) -> None:
        with _rolled_back() as cur:
            real_src, test_src = self.source(cur), self.e2e_source(cur)
            real_st, test_st = self.story(cur), self.story(cur, is_test=True)
            self.assertIn("match its source", _refused(cur, """
                INSERT INTO content_items (source_id, external_id, url, title, published_at, story_id, is_test)
                VALUES (%s, 'tl:a', 'https://tests-live.invalid/a', 't', now(), %s, false)""", (test_src, real_st)))
            self.assertIn("match its source", _refused(cur, """
                INSERT INTO content_items (source_id, external_id, url, title, published_at, story_id, is_test)
                VALUES (%s, 'tl:b', 'https://tests-live.invalid/b', 't', now(), %s, true)""", (real_src, test_st)))
            self.assertIn("match its story", _refused(cur, """
                INSERT INTO content_items (source_id, external_id, url, title, published_at, story_id, is_test)
                VALUES (%s, 'tl:c', 'https://tests-live.invalid/c', 't', now(), %s, false)""", (real_src, test_st)))

    def test_a_test_source_can_never_be_active(self) -> None:
        with _rolled_back() as cur:
            _refused(cur, "UPDATE sources SET active = true WHERE slug = '_e2e'")


class FrozenItem(FeedTestCase):
    def test_only_clearing_the_embedding_is_allowed(self) -> None:
        with _rolled_back() as cur:
            src = self.source(cur)
            st = self.story(cur)
            it = self.item(cur, src, st, embedding=True)
            for sql in ("UPDATE content_items SET title = 'edited' WHERE id = %s",
                        "UPDATE content_items SET url = 'https://tests-live.invalid/moved' WHERE id = %s",
                        "UPDATE content_items SET published_at = now() - interval '1 day' WHERE id = %s",
                        f"UPDATE content_items SET embedding = '{VEC.replace('0.05', '0.06')}'::extensions.halfvec WHERE id = %s"):
                self.assertIn("frozen", _refused(cur, sql, (it,)))
            cur.execute("UPDATE content_items SET embedding = NULL WHERE id = %s", (it,))
            self.assertIsNone(self.one(cur, "SELECT embedding FROM content_items WHERE id = %s", (it,)))

    def test_a_trailer_items_embeddings_clear_like_any_other(self) -> None:
        """db/057: youtube_key is GENERATED (db/053) and reads NULL in a BEFORE
        trigger, so the freeze saw a change on every trailer item and the
        nightly prune failed (content_items 16900, Oct 18 and 19)."""
        with _rolled_back() as cur:
            st = self.story(cur)
            it = self.item(cur, self.source(cur), st, embedding=True,
                           url=f"https://www.youtube.com/watch?v=tl{uuid.uuid4().hex[:12]}")
            self.assertIsNotNone(self.one(cur, "SELECT youtube_key FROM content_items WHERE id = %s", (it,)))
            cur.execute("UPDATE content_items SET embedding = NULL, title_embedding = NULL WHERE id = %s", (it,))
            self.assertIsNone(self.one(cur, "SELECT embedding FROM content_items WHERE id = %s", (it,)))
            self.assertIn("frozen", _refused(cur, "UPDATE content_items SET title = 'edited' WHERE id = %s", (it,)))

    def test_shape_checks(self) -> None:
        with _rolled_back() as cur:
            src = self.source(cur)
            st = self.story(cur)
            # A failed decode must keep the Google link; a future date is refused.
            _refused(cur, """INSERT INTO content_items (source_id, external_id, url, title, published_at, story_id, decode_failed)
                             VALUES (%s, 'tl:d', NULL, 't', now(), %s, true)""", (src, st))
            _refused(cur, """INSERT INTO content_items (source_id, external_id, url, title, published_at, story_id)
                             VALUES (%s, 'tl:e', 'https://tests-live.invalid/e', 't', now() + interval '1 hour', %s)""", (src, st))
            # Unseeded items need a story; seeded ones have none.
            _refused(cur, """INSERT INTO content_items (source_id, external_id, url, title, published_at)
                             VALUES (%s, 'tl:f', 'https://tests-live.invalid/f', 't', now())""", (src,))
            self.item(cur, src, None)
            # A real summary names its ai_usage row.
            _refused(cur, """INSERT INTO stories (pillars, title, summary, summarized_at)
                             VALUES (ARRAY['home'], 't', 'A summary.', now())""")


class Rules(FeedTestCase):
    def test_seeds_are_present(self) -> None:
        with _rolled_back() as cur:
            cur.execute("SELECT kind, count(*) FROM feed_rules GROUP BY kind ORDER BY kind")
            counts = dict(cur.fetchall())
            self.assertGreaterEqual(counts.get("title_regex", 0), 23)
            self.assertGreaterEqual(counts.get("url_regex", 0), 1)
            self.assertGreaterEqual(counts.get("seed_phrase_drop", 0), 9)
            self.assertEqual(self.one(cur, "SELECT count(*) FROM feed_rules WHERE pattern = 'where to watch'"), 1)

    def test_an_invalid_regex_is_refused_and_a_phrase_is_not_compiled(self) -> None:
        with _rolled_back() as cur:
            _refused(cur, "INSERT INTO feed_rules (kind, pattern) VALUES ('title_regex', 'unclosed (group')")
            _refused(cur, "INSERT INTO feed_rules (kind, pattern) VALUES ('url_regex', '[bad')")
            cur.execute("INSERT INTO feed_rules (kind, pattern) VALUES ('seed_phrase_drop', 'tests-live (phrase')")

    def test_a_rule_is_unique_per_kind_pillar_and_pattern(self) -> None:
        with _rolled_back() as cur:
            self.assertIn("feed_rules_once", _refused(cur, "INSERT INTO feed_rules (kind, pattern) VALUES ('title_regex', 'box score')"))
            cur.execute("INSERT INTO feed_rules (kind, pillar, pattern) VALUES ('title_regex', 'home', 'box score')")


class TestStoryWriter(FeedTestCase):
    def test_refuses_a_wrong_marker_and_a_non_owner(self) -> None:
        with _rolled_back() as cur:
            _act_as(cur, "authenticated", self.owner)
            self.assertIn("marker refused", _refused(cur,
                "SELECT add_test_story('wrong', 'home', 't', NULL, 'https://e2e.invalid/a')"))
            cur.execute("RESET ROLE")
            _act_as(cur, "authenticated", uuid.uuid4())
            self.assertIn("owner only", _refused(cur,
                "SELECT add_test_story('wrong', 'home', 't', NULL, 'https://e2e.invalid/a')"))
            cur.execute("RESET ROLE")
            _act_as(cur, "anon")
            _refused(cur, "SELECT add_test_story('wrong', 'home', 't', NULL, 'https://e2e.invalid/a')")


if __name__ == "__main__":
    unittest.main()
