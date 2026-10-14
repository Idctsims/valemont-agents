"""The Tech Hub latency report (Chat 4 Phase 1 done-when; docs/tech_hub.md).

    ..\\venv\\Scripts\\python.exe -m scripts.tech_latency                 (from workers/)
    ..\\venv\\Scripts\\python.exe -m scripts.tech_latency --since 2026-10-14T03:00:00Z

READ-ONLY: it connects as valemont_readonly through scripts/db_inspect.py's
readonly_url() (CLAUDE.md §5), and rolls back.

For every OFFICIAL tech item first seen after --since (default: the first
non-backfill official item ever), not a first-fetch backfill:

    first_seen_at   content_items.fetched_at (when the worker first saw it)
    published_at    as the source states it
    precision       'time': the source gives a time, and the rule is
                    first_seen - published <= 30 min.
                    'date': the source gives a day only (a listing page, a
                    midnight-stamped feed), so minutes cannot be measured;
                    the rule is first seen on the stated day or the next
                    (US sources date in Pacific time, a day behind UTC late
                    in the evening).

Then, per official source: last ok fetch, its newest item, failures; and the
newest Claude and OpenAI items, to check the latest releases are present.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

import psycopg

from scripts.db_inspect import STATEMENT_TIMEOUT_MS, readonly_url

LIMIT = timedelta(minutes=30)

#: Official items first seen after `since`, not a first-fetch backfill, not
#: recorded-as-seen (seeded: too old when first seen), and not in a story
#: db/047 hid as stale (the late items stored before the 2026-10-14 fix).
ITEMS_SQL = """
    SELECT s.slug, i.title, i.published_at, i.published_precision, i.fetched_at, st.summary IS NOT NULL, s.kind
      FROM content_items i
      JOIN sources s ON s.id = i.source_id
      LEFT JOIN stories st ON st.id = i.story_id
     WHERE 'tech' = ANY (s.pillars) AND s.official AND NOT s.is_test
       AND NOT i.backfill AND NOT i.seeded
       AND coalesce(st.filtered_reason, '') NOT LIKE 'stale:%%'
       AND i.fetched_at > %(since)s
     ORDER BY s.kind, i.fetched_at, i.id
"""

#: The owner's two done-when paths (2026-10-14).
PATHS = {"rss": "Feed path (RSS / Atom)", "page": "Page-watcher path"}
#: The page-watcher check needs a post from one of these.
PAGE_CHECK = ("anthropic-news", "claude-blog", "openai-news")

DEFAULT_SINCE_SQL = """
    SELECT min(i.fetched_at) - interval '1 second'
      FROM content_items i JOIN sources s ON s.id = i.source_id
      LEFT JOIN stories st ON st.id = i.story_id
     WHERE 'tech' = ANY (s.pillars) AND s.official AND NOT s.is_test AND NOT i.backfill AND NOT i.seeded
       AND coalesce(st.filtered_reason, '') NOT LIKE 'stale:%%'
"""

SOURCES_SQL = """
    SELECT s.slug, s.active, s.last_ok_at, s.last_status, s.consecutive_failures, x.last_item_at,
           (SELECT count(*) FROM content_items i WHERE i.source_id = s.id AND NOT i.seeded) AS items
      FROM sources s LEFT JOIN v_feed_source_stats x ON x.source_id = s.id
     WHERE 'tech' = ANY (s.pillars) AND NOT s.is_test
     ORDER BY s.official DESC, s.slug
"""

NEWEST_SQL = """
    SELECT s.slug, i.title, i.published_at::date, i.backfill
      FROM content_items i JOIN sources s ON s.id = i.source_id
     WHERE s.slug = ANY (%(slugs)s) AND NOT i.seeded
     ORDER BY i.published_at DESC, i.id DESC
     LIMIT 6
"""

CLAUDE = ["anthropic-news", "anthropic-news-feed", "claude-blog", "claude-api-notes", "claude-apps-notes", "claude-code"]
OPENAI = ["openai-news", "openai-api-changelog", "chatgpt-release-notes"]


def verdict(published: datetime, precision: str, first_seen: datetime) -> tuple[bool, str]:
    if precision == "date":
        days = (first_seen.date() - published.date()).days
        return 0 <= days <= 1, f"date only; first seen {days} day(s) after the stated day"
    delta = first_seen - published
    return delta <= LIMIT, f"{delta.total_seconds() / 60:.1f} min"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", help="ISO time; default: the first official item ever first seen (non-backfill)")
    args = ap.parse_args(argv)
    failed = 0
    with psycopg.connect(readonly_url()) as conn, conn.cursor() as cur:
        try:
            cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
            since = args.since
            if since is None:
                cur.execute(DEFAULT_SINCE_SQL)
                since = cur.fetchone()[0]
            print(f"Official tech items first seen after {since} (backfill excluded):")
            if since is None:
                print("  none yet (no non-backfill official item has been seen)")
            else:
                cur.execute(ITEMS_SQL, {"since": since})
                rows = cur.fetchall()
                current = None
                for slug, title, pub, precision, seen, summarized, kind in rows:
                    if kind != current:
                        current = kind
                        print(f" {PATHS.get(kind, kind)}:")
                    ok, how = verdict(pub, precision, seen)
                    failed += not ok
                    print(f"  {'PASS' if ok else 'FAIL'}  {slug:22} {how:42} seen {seen:%Y-%m-%d %H:%M}Z  "
                          f"{'summary' if summarized else 'headline only'}  {title[:70]}")
                print(f"  {len(rows)} item(s), {failed} over the limit")
                page_hits = [r for r in rows if r[0] in PAGE_CHECK]
                print("  Page-watcher check (anthropic.com/news, claude.com/blog, openai.com/news): "
                      + (f"{len(page_hits)} item(s) above" if page_hits else "PENDING: none first seen yet"))
            print("\nOfficial and community tech sources:")
            cur.execute(SOURCES_SQL)
            for slug, active, ok_at, status, fails, last_item, items in cur.fetchall():
                print(f"  {slug:24} {'active' if active else 'INACTIVE':8} last ok {ok_at or 'never'}  "
                      f"status {status or '-'}  failures {fails}  items {items}  last item {last_item or '-'}")
            for label, slugs in (("Claude", CLAUDE), ("OpenAI", OPENAI)):
                print(f"\nNewest {label} items:")
                cur.execute(NEWEST_SQL, {"slugs": slugs})
                for slug, title, day, backfill in cur.fetchall():
                    print(f"  {day}  {slug:22} {title[:80]}{'  (backfill)' if backfill else ''}")
        finally:
            conn.rollback()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
