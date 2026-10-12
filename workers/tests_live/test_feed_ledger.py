"""The feed's ledger SQL against the real database (db/032, db/033): the
48-hour candidate query (FEED_CANDIDATES_SQL), founding and joining stories
(lead by priority, never a url-less item; title follows the lead; centroid
running mean; the similarity log), the exact-dedupe race, the summarizer's
queue and saves, the nightly prune (FEED_PRUNE_SQL: never a kept story), the
sources sync (never the test source), and db/033's access rules.

Every test runs inside ONE transaction that is always rolled back, through
the real ledger functions (conn=...). Rows are written as the `_e2e` test
source, or as throwaway sources created and rolled back inside the test.

Skips until db/033 is pasted.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import psycopg

from core import ledger
from core.paths import load_env

load_env()


def unit(i: int) -> list[float]:
    v = [0.0] * 384
    v[i] = 1.0
    return v


@contextmanager
def _rolled_back() -> Iterator[psycopg.Connection]:
    """One outer transaction, always rolled back.

    The SELECT 1 matters: in psycopg 3, conn.transaction() on an IDLE
    connection starts a real transaction and COMMITS it on exit; only inside
    an open transaction is it a savepoint. Ledger functions use
    conn.transaction(), so a test whose first statement was one of them
    committed for real. On 2026-10-11 the Sync test did exactly that: its
    first feed_sync_sources() committed the 50 sources.toml rows (the same
    rows the worker's boot sync writes; nothing else leaked)."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("SELECT 1")
        try:
            yield conn
        finally:
            conn.rollback()


def _item(url: str | None = "auto", *, title: str = "tests-live item", hours_ago: float = 0,
          ext: str | None = None) -> ledger.FeedItemIn:
    if url == "auto":
        url = f"https://tests-live.invalid/{uuid.uuid4().hex}"
    return ledger.FeedItemIn(
        external_id=ext or f"tl:{uuid.uuid4().hex}", url=url,
        google_url=None if url else "https://news.google.com/rss/articles/tl",
        title=title, teaser="A teaser.",
        published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        decode_failed=url is None)


class FeedLedgerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            with _rolled_back() as conn:
                ok = conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'content_items' "
                    "AND column_name = 'title_embedding')").fetchone()[0]
                if not ok:
                    raise unittest.SkipTest("db/034 not pasted")
                cls.owner = conn.execute("SELECT owner_id FROM app_settings").fetchone()[0]
        except unittest.SkipTest:
            raise
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")

    def make_source(self, conn: psycopg.Connection, *, priority: int = 100, slug: str | None = None,
                    is_test: bool = True) -> ledger.FeedSource:
        slug = slug or f"tl-{uuid.uuid4().hex[:10]}"
        conn.execute(
            """INSERT INTO sources (slug, name, pillars, kind, url, priority, active, is_test)
               VALUES (%s, %s, ARRAY['home'], 'rss', 'https://tests-live.invalid/feed', %s, false, %s)""",
            (slug, f"TL {slug}", priority, is_test))
        return ledger.feed_source(slug, conn=conn)


class Candidates(FeedLedgerCase):
    def test_the_48_hour_window_and_the_two_worlds(self) -> None:
        with _rolled_back() as conn:
            src = self.make_source(conn)
            fresh = ledger.feed_new_story(src, _item(), unit(0), unit(0), filtered_reason=None, seed=None, conn=conn)[0]
            old = ledger.feed_new_story(src, _item(), unit(1), unit(1), filtered_reason=None, seed=None, conn=conn)[0]
            edge = ledger.feed_new_story(src, _item(), unit(2), unit(2), filtered_reason=None, seed=None, conn=conn)[0]
            # `old` began 49 h ago but was seen a minute ago (a recent join):
            # the window is on FIRST seen, so it is still out. No chain can
            # keep a story absorbing items past 48 h.
            conn.execute("UPDATE stories SET first_seen_at = now() - interval '49 hours', "
                         "last_seen_at = now() - interval '1 minute' WHERE id = %s", (old,))
            conn.execute("UPDATE stories SET first_seen_at = now() - interval '47 hours', "
                         "last_seen_at = now() - interval '47 hours' WHERE id = %s", (edge,))
            test_ids = {c.story_id for c in ledger.feed_candidates(48, is_test=True, conn=conn)}
            self.assertIn(fresh, test_ids)
            self.assertIn(edge, test_ids)
            self.assertNotIn(old, test_ids)
            # The real world never sees test stories.
            real_ids = {c.story_id for c in ledger.feed_candidates(48, is_test=False, conn=conn)}
            self.assertFalse(real_ids & {fresh, edge, old})

    def test_members_come_back_with_their_title_embeddings(self) -> None:
        with _rolled_back() as conn:
            src = self.make_source(conn)
            sid, _ = ledger.feed_new_story(src, _item(title="First"), unit(0), unit(5), filtered_reason=None,
                                           seed=None, conn=conn)
            ledger.feed_join_story(src, _item(title="Second"), unit(1), unit(6), sid, seed=None, logged=[], conn=conn)
            cand = next(c for c in ledger.feed_candidates(48, is_test=True, conn=conn) if c.story_id == sid)
            self.assertEqual(cand.item_count, 2)
            self.assertEqual([t for t, _ in cand.members], ["First", "Second"])
            # The TITLE embedding, not the title + teaser one.
            self.assertAlmostEqual(cand.members[0][1][5], 1.0, places=3)
            self.assertAlmostEqual(cand.members[1][1][6], 1.0, places=3)
            self.assertEqual(len(cand.members[0][1]), 384)

    def test_the_query_text_is_the_one_ledger_runs(self) -> None:
        self.assertIn("st.first_seen_at > now() - make_interval(hours => %(hours)s)", ledger.FEED_CANDIDATES_SQL)
        self.assertNotIn("last_seen_at", ledger.FEED_CANDIDATES_SQL)
        self.assertIn("i.title_embedding IS NOT NULL", ledger.FEED_CANDIDATES_SQL)
        self.assertIn("st.is_test = %(is_test)s", ledger.FEED_CANDIDATES_SQL)


class FoundAndJoin(FeedLedgerCase):
    def test_lead_title_centroid_and_log(self) -> None:
        with _rolled_back() as conn:
            low, high = self.make_source(conn, priority=10), self.make_source(conn, priority=40)
            # Founded by the high-priority-number outlet; then a url-less item; then the low one.
            sid, first = ledger.feed_new_story(high, _item(title="Founder headline"), unit(0), unit(0),
                                               filtered_reason=None, seed=(0.75, None), conn=conn)
            self.assertEqual(conn.execute("SELECT lead_item_id FROM stories WHERE id = %s", (sid,)).fetchone()[0], first)
            nolink = ledger.feed_join_story(high, _item(url=None, title="Undecoded"), unit(1), unit(1), sid, seed=None,
                                            logged=[ledger.SimilarityRow(sid, "join", 0.9, 50, "cosine")], conn=conn)
            self.assertIsNotNone(nolink)
            lead = ledger.feed_join_story(low, _item(title="Better outlet headline"), unit(0), unit(0), sid,
                                          seed=(0.81, None),
                                          logged=[ledger.SimilarityRow(sid, "join", 0.95, 92, "both")], conn=conn)
            row = conn.execute(
                "SELECT lead_item_id, title, item_count, centroid::text, seed_score FROM stories WHERE id = %s",
                (sid,)).fetchone()
            self.assertEqual(row[0], lead)
            self.assertEqual(row[1], "Better outlet headline")
            self.assertEqual(row[2], 3)
            centroid = [float(x) for x in row[3].strip("[]").split(",")]
            self.assertAlmostEqual(centroid[0], 2 / 3, places=2)
            self.assertAlmostEqual(centroid[1], 1 / 3, places=2)
            self.assertAlmostEqual(row[4], 0.81, places=2)
            kinds = conn.execute("SELECT kind, joined_by FROM feed_similarity_log WHERE story_id = %s ORDER BY id",
                                 (sid,)).fetchall()
            self.assertEqual(kinds, [("join", "cosine"), ("join", "both")])

    def test_a_story_whose_only_item_failed_to_decode_has_no_lead(self) -> None:
        with _rolled_back() as conn:
            src = self.make_source(conn)
            sid, _ = ledger.feed_new_story(src, _item(url=None), unit(0), unit(0), filtered_reason=None, seed=None, conn=conn)
            self.assertIsNone(conn.execute("SELECT lead_item_id FROM stories WHERE id = %s", (sid,)).fetchone()[0])

    def test_an_existing_url_founds_nothing(self) -> None:
        with _rolled_back() as conn:
            src = self.make_source(conn)
            url = f"https://tests-live.invalid/{uuid.uuid4().hex}"
            ledger.feed_new_story(src, _item(url), unit(0), unit(0), filtered_reason=None, seed=None, conn=conn)
            before = conn.execute("SELECT count(*) FROM stories WHERE is_test").fetchone()[0]
            self.assertIsNone(ledger.feed_new_story(src, _item(url), unit(1), unit(1), filtered_reason=None, seed=None, conn=conn))
            self.assertEqual(conn.execute("SELECT count(*) FROM stories WHERE is_test").fetchone()[0], before)

    def test_seeded_items_are_seen_and_never_stories(self) -> None:
        with _rolled_back() as conn:
            src = self.make_source(conn)
            n = ledger.feed_insert_seeded(src, [_item(ext="tl:s1"), _item(ext="tl:s2")], conn=conn)
            self.assertEqual(n, 2)
            known, _ = ledger.feed_known(src.id, ["tl:s1", "tl:s2", "tl:s3"], [], conn=conn)
            self.assertEqual(known, {"tl:s1", "tl:s2"})
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_items WHERE source_id = %s AND story_id IS NULL AND seeded",
                (src.id,)).fetchone()[0], 2)


class Summaries(FeedLedgerCase):
    def test_queue_saves_and_test_stories_never_queue(self) -> None:
        with _rolled_back() as conn:
            real = self.make_source(conn, is_test=False)
            test = self.make_source(conn)
            rsid, _ = ledger.feed_new_story(real, _item(title="Real story"), unit(0), unit(0), filtered_reason=None, seed=None, conn=conn)
            fsid, _ = ledger.feed_new_story(real, _item(title="Filtered"), unit(1), unit(1), filtered_reason="title_regex:1",
                                            seed=None, conn=conn)
            tsid, _ = ledger.feed_new_story(test, _item(title="Test story"), unit(2), unit(2), filtered_reason=None, seed=None, conn=conn)
            queued = {j.story_id for j in ledger.feed_summary_queue(5000, 2, conn=conn)}
            self.assertIn(rsid, queued)
            self.assertNotIn(fsid, queued)
            self.assertNotIn(tsid, queued)
            usage = conn.execute(
                """INSERT INTO ai_usage (purpose, model, tokens_in, tokens_out, cost_usd)
                   VALUES ('feed_summarize', 'claude-haiku-5-5', 10, 10, 0) RETURNING id""").fetchone()[0]
            before = ledger.feed_summarized_today(conn=conn)
            ledger.feed_save_summaries([ledger.SummaryOut(rsid, "A real summary.", ("Ross",), ("hotels",), "nfl", "none")],
                                       usage, conn=conn)
            row = conn.execute("SELECT summary, entities, topics, sport, drop_category, summary_attempts, ai_usage_id, "
                               "filtered_reason FROM stories WHERE id = %s", (rsid,)).fetchone()
            self.assertEqual(row, ("A real summary.", ["Ross"], ["hotels"], "nfl", "none", 1, usage, None))
            self.assertEqual(ledger.feed_summarized_today(conn=conn), before + 1)
            self.assertNotIn(rsid, {j.story_id for j in ledger.feed_summary_queue(5000, 2, conn=conn)})

    def test_the_queue_is_newest_published_first(self) -> None:
        with _rolled_back() as conn:
            real = self.make_source(conn, is_test=False)
            # The newer story is founded FIRST (lower id), in one transaction
            # (equal first_seen_at): the old order (first_seen, then id)
            # would put the older story first. Publish time must win.
            newer, _ = ledger.feed_new_story(real, _item(hours_ago=1), unit(1), unit(1), filtered_reason=None,
                                             seed=None, conn=conn)
            older, _ = ledger.feed_new_story(real, _item(hours_ago=30), unit(0), unit(0), filtered_reason=None,
                                             seed=None, conn=conn)
            order = [j.story_id for j in ledger.feed_summary_queue(5000, 2, conn=conn) if j.story_id in (older, newer)]
            self.assertEqual(order, [newer, older])
            # Owner decision 10 (2026-10-12): a followed name first, then publish time.
            self.assertIn("ORDER BY coalesce(lower(st.title) ~ %(priority)s::text, false) DESC, i.published_at DESC",
                          ledger.FEED_SUMMARY_QUEUE_SQL)

    def test_an_invalid_result_counts_an_attempt_and_two_retire_it(self) -> None:
        with _rolled_back() as conn:
            real = self.make_source(conn, is_test=False)
            sid, _ = ledger.feed_new_story(real, _item(), unit(0), unit(0), filtered_reason=None, seed=None, conn=conn)
            ledger.feed_save_summaries([ledger.SummaryOut(sid, None)], None, conn=conn)
            self.assertIn(sid, {j.story_id for j in ledger.feed_summary_queue(5000, 2, conn=conn)})
            ledger.feed_save_summaries([ledger.SummaryOut(sid, None)], None, conn=conn)
            self.assertNotIn(sid, {j.story_id for j in ledger.feed_summary_queue(5000, 2, conn=conn)})
            self.assertEqual(conn.execute("SELECT summary, summary_attempts FROM stories WHERE id = %s",
                                          (sid,)).fetchone(), (None, 2))

    def test_live_drop_files_the_story(self) -> None:
        with _rolled_back() as conn:
            real = self.make_source(conn, is_test=False)
            sid, _ = ledger.feed_new_story(real, _item(), unit(0), unit(0), filtered_reason=None, seed=None, conn=conn)
            usage = conn.execute("""INSERT INTO ai_usage (purpose, model, tokens_in, tokens_out, cost_usd)
                                    VALUES ('feed_summarize', 'claude-haiku-5-5', 1, 1, 0) RETURNING id""").fetchone()[0]
            ledger.feed_save_summaries([ledger.SummaryOut(sid, "S.", drop="gossip", filtered_reason="drop:gossip")],
                                       usage, conn=conn)
            self.assertEqual(conn.execute("SELECT filtered_reason FROM stories WHERE id = %s", (sid,)).fetchone()[0],
                             "drop:gossip")
            self.assertEqual(conn.execute("SELECT count(*) FROM v_feed_stories WHERE id = %s", (sid,)).fetchone()[0], 0)


class Prune(FeedLedgerCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            # From Chat 3 Phase 3 the prune also keeps a move proposal's story.
            if conn.execute("SELECT to_regclass('public.entity_link_proposals')").fetchone()[0] is None:
                raise unittest.SkipTest("the prune reads entity_link_proposals: db/042 not pasted")

    def test_the_prune_never_touches_a_kept_story(self) -> None:
        with _rolled_back() as conn:
            src = self.make_source(conn)
            kept, kept_item = ledger.feed_new_story(src, _item(), unit(0), unit(0), filtered_reason=None, seed=None, conn=conn)
            gone, gone_item = ledger.feed_new_story(src, _item(), unit(1), unit(1), filtered_reason="title_regex:1",
                                                    seed=None, conn=conn)
            young, _ = ledger.feed_new_story(src, _item(), unit(2), unit(2), filtered_reason="title_regex:1", seed=None, conn=conn)
            # Age the kept and the first filtered story to 40 days.
            for sid in (kept, gone):
                conn.execute("UPDATE stories SET first_seen_at = now() - interval '40 days', "
                             "last_seen_at = now() - interval '40 days' WHERE id = %s", (sid,))
            counts = ledger.feed_prune(conn=conn)
            self.assertEqual(set(counts), set(ledger.FEED_PRUNE_SQL))
            alive = {r[0] for r in conn.execute("SELECT id FROM stories WHERE id = ANY(%s)", ([kept, gone, young],))}
            self.assertEqual(alive, {kept, young})
            self.assertEqual(conn.execute("SELECT count(*) FROM content_items WHERE id = %s", (gone_item,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM content_items WHERE id = %s", (kept_item,)).fetchone()[0], 1)

    def test_embeddings_older_than_72_hours_are_cleared_and_old_seeded_items_go(self) -> None:
        with _rolled_back() as conn:
            src = self.make_source(conn)
            _, fresh = ledger.feed_new_story(src, _item(), unit(0), unit(0), filtered_reason=None, seed=None, conn=conn)
            # An old item: inserted directly with an old fetch time.
            sid = conn.execute("INSERT INTO stories (pillars, title, is_test) VALUES ('{home}', 'old', true) "
                               "RETURNING id").fetchone()[0]
            old = conn.execute(
                """INSERT INTO content_items (source_id, external_id, url, title, published_at, fetched_at,
                                             story_id, embedding, title_embedding, is_test)
                   VALUES (%s, %s, %s, 'old', now() - interval '4 days', now() - interval '4 days', %s,
                           %s::extensions.halfvec, %s::extensions.halfvec, true) RETURNING id""",
                (src.id, f"tl:{uuid.uuid4().hex}", f"https://tests-live.invalid/{uuid.uuid4().hex}", sid,
                 "[" + ",".join(["0.05"] * 384) + "]", "[" + ",".join(["0.04"] * 384) + "]")).fetchone()[0]
            seeded = conn.execute(
                """INSERT INTO content_items (source_id, external_id, url, title, published_at, fetched_at,
                                             seeded, is_test)
                   VALUES (%s, %s, %s, 'seed', now() - interval '31 days', now() - interval '31 days', true, true)
                   RETURNING id""",
                (src.id, f"tl:{uuid.uuid4().hex}", f"https://tests-live.invalid/{uuid.uuid4().hex}")).fetchone()[0]
            ledger.feed_prune(conn=conn)
            emb = {r[0]: (r[1], r[2]) for r in conn.execute(
                "SELECT id, embedding IS NOT NULL, title_embedding IS NOT NULL FROM content_items WHERE id = ANY(%s)",
                ([fresh, old],)).fetchall()}
            self.assertEqual(emb, {fresh: (True, True), old: (False, False)})
            self.assertEqual(conn.execute("SELECT count(*) FROM content_items WHERE id = %s", (seeded,)).fetchone()[0], 0)


class Sync(FeedLedgerCase):
    def test_upsert_deactivate_and_never_the_test_source(self) -> None:
        from feed.sources import load_sources
        with _rolled_back() as conn:
            specs = load_sources()
            n, _ = ledger.feed_sync_sources(specs, conn=conn)
            self.assertEqual(n, len(specs))
            active = {r[0] for r in conn.execute("SELECT slug FROM sources WHERE active AND NOT is_test")}
            self.assertEqual(active, {s.slug for s in specs if s.active})
            # Drop one from the list: it goes inactive, never deleted.
            _, gone = ledger.feed_sync_sources([s for s in specs if s.slug != "variety"], conn=conn)
            self.assertGreaterEqual(gone, 1)
            self.assertEqual(conn.execute("SELECT active FROM sources WHERE slug = 'variety'").fetchone()[0], False)
            self.assertEqual(conn.execute("SELECT active, is_test FROM sources WHERE slug = '_e2e'").fetchone(),
                             (False, True))
            self.assertEqual(conn.execute(
                "SELECT include_categories, gnews_window_days FROM sources WHERE slug = 'semafor-business'").fetchone(),
                (["Business", "Media"], 2))


class FrozenTitleEmbedding(FeedLedgerCase):
    def test_the_title_embedding_can_be_cleared_never_changed(self) -> None:
        with _rolled_back() as conn:
            src = self.make_source(conn)
            _, item = ledger.feed_new_story(src, _item(), unit(0), unit(1), filtered_reason=None, seed=None, conn=conn)
            conn.execute("SAVEPOINT p")
            with self.assertRaisesRegex(psycopg.Error, "frozen"):
                conn.execute("UPDATE content_items SET title_embedding = %s::extensions.halfvec WHERE id = %s",
                             ("[" + ",".join(["0.03"] * 384) + "]", item))
            conn.execute("ROLLBACK TO SAVEPOINT p")
            conn.execute("UPDATE content_items SET title_embedding = NULL, embedding = NULL WHERE id = %s", (item,))
            self.assertEqual(conn.execute("SELECT title_embedding IS NULL AND embedding IS NULL FROM content_items "
                                          "WHERE id = %s", (item,)).fetchone()[0], True)


class NothingSurvives(FeedLedgerCase):
    def test_a_ledger_transaction_as_the_first_statement_is_still_rolled_back(self) -> None:
        """The 2026-10-11 leak, pinned: a ledger function that opens
        conn.transaction() as the FIRST statement must not commit. Canary: one
        seeded item from the `_e2e` test source (harmless if it ever leaks)."""
        ext = f"tl:canary:{uuid.uuid4().hex}"
        with _rolled_back() as conn:
            e2e = ledger.feed_source("_e2e", conn=conn)
        with _rolled_back() as conn:
            ledger.feed_insert_seeded(e2e, [_item(ext=ext)], conn=conn)
        with psycopg.connect(os.environ["DATABASE_URL"]) as fresh:
            self.assertEqual(fresh.execute("SELECT count(*) FROM content_items WHERE external_id = %s",
                                           (ext,)).fetchone()[0], 0)


class Access033(FeedLedgerCase):
    def test_similarity_log_owner_read_only_anon_nothing_readonly_reads(self) -> None:
        with _rolled_back() as conn:
            for role, priv, want in (("anon", "SELECT", False), ("authenticated", "INSERT", False),
                                     ("valemont_readonly", "SELECT", True)):
                self.assertEqual(conn.execute(
                    "SELECT has_table_privilege(%s, 'public.feed_similarity_log', %s)", (role, priv)).fetchone()[0],
                    want, (role, priv))
            src = self.make_source(conn)
            sid, item = ledger.feed_new_story(src, _item(), unit(0), unit(0), filtered_reason=None, seed=None,
                                              near_misses=[], conn=conn)
            conn.execute("INSERT INTO feed_similarity_log (item_id, story_id, kind, cosine, fuzz) "
                         "VALUES (%s, %s, 'near_miss', 0.8, 40)", (item, sid))
            conn.execute("SELECT set_config('request.jwt.claims', %s, true)",
                         (json.dumps({"role": "authenticated", "sub": str(uuid.uuid4())}),))
            conn.execute("SET LOCAL ROLE authenticated")
            self.assertEqual(conn.execute("SELECT count(*) FROM feed_similarity_log").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
