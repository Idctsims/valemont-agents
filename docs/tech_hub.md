# Tech Hub (Chat 4, Phase 1)

A new feed pillar, `tech`, on the existing feed engine (sources, items,
stories, clustering, Haiku summaries, categories, reactions, interest
profiles, why-it-matters). Owner's brief: the Chat 4 Phase 1 prompt,
2026-10-13. Decisions made while building are recorded here with their date.

## 0. Baseline (2026-10-13, before any Phase 1 code)

Branch `chat4-phase1-tech` at `e72636a` (Step 0, docs only), from `main`
at `c0c64e7`. `vercel whoami`: `idctsims`.

| Suite | Command (from `workers/`) | Result |
|---|---|---|
| `tests/` | `..\venv\Scripts\python.exe -m unittest discover -s tests -t .` | **747 OK** (1.3 s) |
| `tests_live/` | `..\venv\Scripts\python.exe -m unittest discover -v -s tests_live -t .` | **319 OK, 0 skipped**, 0 failures, 0 errors (667.7 s) |

No skip occurred, so there is none to name. The verbose log's last line is
`OK` with no `(skipped=N)`, and no test line reads `skipped`.

## 1. Owner decisions (2026-10-13, Chat 4 planning)

- No staging project; Reddit dropped (Step 0, commit `e72636a`).
- Tech Hub ingests on the existing 30-minute feed cycle.
- Pushes only for free credits and promos, at most 3 per Chicago day.
- Official items appear within one cycle and are summarized in the same
  cycle by a direct `core/ai.complete()` call, non-critical; until the
  summary lands the card shows its headline with an "Official" tag.
- Zero cross-in: tech stories never on Home or /news; Home and Sports
  stories never on /tech.

## 2. Sources, verified live on 2026-10-13

Every URL was fetched with the worker's User-Agent and parsed before it went
into `workers/feed/sources.toml`. Fixtures of each page are in
`workers/tests/fixtures/tech/` (trimmed to their first entries).

| Slug | Source | Kind | What was found |
|---|---|---|---|
| `anthropic-news` | anthropic.com/news | page (links) | 200, no feed (`/rss.xml`, `/news/rss.xml` 404). 10 dated entries parsed. |
| `anthropic-news-feed` | Olshansk/rss-feeds `feed_anthropic_news.xml` | rss | 200, 269 entries, newest Oct 8. Lower-weight fallback (priority 60 vs 10), deduped by URL. |
| `claude-blog` | claude.com/blog | page (links) | 200, redirects to claude.com/resources/articles; no feed (`/rss.xml` 404). |
| `claude-api-notes` | Claude Platform release notes | page (sections) | docs.claude.com/en/release-notes/api redirects to platform.claude.com/docs/en/release-notes/overview (909 KB); one item per dated h3. |
| `claude-apps-notes` | support.claude.com release notes | page (sections) | 200; one item per dated h3, the bold lead as title. |
| `claude-code` | github.com/anthropics/claude-code releases.atom | rss | 200, Atom, newest v2.1.296 (Oct 9). |
| `openai-news` | openai.com/news/rss.xml | rss | 200, native RSS, 1,288 entries (785 KB): read to its first 50. |
| `openai-api-changelog` | developers.openai.com/api/docs/changelog | page (blocks) | platform.openai.com/docs/changelog redirects here; no feed (`/changelog/rss.xml` 404). Dates have no year. |
| `chatgpt-release-notes` | help.openai.com ChatGPT release notes | page | **403, Cloudflare challenge** (`cf-mitigated: challenge`). Not bypassed. **Inactive, owner's decision pending.** No mirror feed exists in Olshansk/rss-feeds. |
| `cursor-changelog` | cursor.com/changelog/rss.xml | rss | 200, 50 entries, dates at 00:00 GMT (date precision). |
| `nextjs-blog` | nextjs.org/feed.xml | rss | 200, 83 entries. |
| `vercel` | vercel.com/atom | rss | 200, **3.7 MB, 1,656 entries**, changelog and blog together (`/changelog/rss.xml` redirects to it; `/changelog/atom` 404): read to its first 50. |
| `supabase-blog` | supabase.com/rss.xml | rss | 200, 430 entries. |
| `supabase-changelog` | supabase.com/changelog | page (links) | 200, no feed (`/changelog/rss.xml`, `/changelog.xml` 404); the date follows each link. |
| `mcp-blog` | blog.modelcontextprotocol.io/index.xml | rss | 200, 26 entries, newest Aug 22. **MCP has a news feed (this) and a spec changelog page; the spec's releases are the next row.** |
| `mcp-spec-releases` | github.com/modelcontextprotocol/modelcontextprotocol releases.atom | rss | 200, 9 releases, newest 2026-07-28. |
| `hn-front` | HN Algolia `search?tags=front_page&numericFilters=points>=100` | hn | 200 JSON. Threshold 100, checked again per hit. |
| `hn-show` | HN Algolia `search_by_date?tags=show_hn&numericFilters=points>=50` | hn | 200 JSON. Threshold 50. |

17 active, 16 official (every row but the two HN ones).

## 3. Design

**The pillar is exclusive (db/045).** `sources.pillars` and `stories.pillars`
CHECK that a row with `tech` has nothing else. Clustering compares a tech item
only with tech stories (and any other item never with one), so a join can
never put a story in two worlds. `feed_pillar_guard` refuses a tech score or
reaction on a non-tech story and a home or sports one on a tech story.
`feed_for_you` never lists a cross-in involving tech, in either direction.
The worker's twin (`rank.placements`) never scores across the line, and a
follow with no pillar means Home and Sports, never Tech. A drop rule with no
pillar likewise stays Home's and Sports': "promo code" is a commerce filter
on Home and exactly the free-credits news on Tech.

**Not new tables.** Tech stories are `stories`, items are `content_items`,
reactions are `feed_reactions`, profiles are `interest_profiles`. The one new
table is `tech_credit_pushes`, the push record the cap needs.

**Sources live in `sources.toml`, not in db/045.** Every feed source is
synced from the toml at boot, so db/045 widens the CHECKs and the tech
sources arrive with the worker. That is how Home's and Sports' sources work.

**The page watcher** (`feed/watch.py`) reads only the listing container (a
start/end marker pair when configured), at most `config.max_bytes` of a
streamed response, through stdlib `html.parser`. No new dependency. Three
modes: `links` (an anchor per entry), `sections` (dated headings), `blocks`
(repeated containers with a year-less date badge). A page item's link keeps
its fragment (`#october-9-2026`, `#h_…`), so release-notes entries are
distinct. A configured marker that is missing is a parse failure, which counts
against the source and alerts after six in a row.

**Speed.** One cycle: ingest (30 min), then `feed_tech_summarize` runs FIRST
in the chain, its queue official-first, one direct Haiku call per 10 stories,
one same-run retry for an official story that fails validation. Until then
`feed_for_you('tech')` lists the unscored story at score 0 (Home and Sports
still list scored stories only). `feed_score` builds a pillar's first profile
when Home and Sports already have one, so Tech's first stories are scored in
the cycle they arrive, not at 04:00.

**Window.** Tech's For you window is 168 hours (Home and Sports keep 72):
official changelogs move slower than news.

**Backfill.** A tech source's first fetch keeps its 5 newest dated items
whatever their age, so every official source shows something on day one.
Every first-fetch item is marked `backfill` and never counted in the latency
report: it was first seen at deploy, not when it was published.

**Precision and latency.** `content_items.fetched_at` is the item's
first_seen_at. `published_precision` is `date` for a listing page and for a
feed that stamps entries at midnight (Cursor, the Olshansk mirror, Vercel,
Supabase blog), else `time`. The latency rule is first_seen − published ≤ 30
min for `time` items. A `date` item cannot be timed to the minute; its rule
is first seen on the stated day or the next (US sources date in Pacific time).
`scripts/tech_latency.py` reports both, read-only.

**Categories.** Haiku answers one of models_features, pricing_credits,
tooling, agents, stack, community, other. An official story takes that
answer. A community (Hacker News) story is `community` whatever the model
said, so the Community chip is the community's and an HN post never
triggers a push. `repos` is reserved for Phase 2 (GitHub).

**The free-credits push.** pricing_credits AND a yes to the owner's question
("Does this offer free credits, a free tier increase, or a promo the reader
can claim?", stored as `stories.claimable_offer`). "Free credits:
<headline>", deep link `/tech/story/<id>`. The database decides the cap:
`tech_credit_pushes`' trigger refuses a 4th real push on the owner's day and
a second push of any story. The worker asks first, under the trigger's lock,
then sends and records in one transaction, so a refused push is never sent
and a failed send records nothing. `scripts/tech_test_push.py` forces an
`is_test` push that is never counted.

**Context.** `ctx_tech` (Wags; the official items published in the last 7
days, the most-loved categories, the top of For you in them).
`ctx_tech_brief` (the brief): official items that LANDED (first seen, not
backfill) in the last 24 h, or NULL. `core/brief.py` now leaves out any
section whose provider returned NULL, and holds `tech_brief` to one line.
Opened from /tech, Wags' page context is the `tech` slice.

**Memory.** Pages are streamed and capped, big feeds cut after 50 entries,
the response body dropped as soon as it is parsed, and `memory.release()`
still runs after each ingest. The worker's RSS delta is measured after deploy
from the job log lines (pending).

## 4. Decisions made while building, for the owner's review

1. **ChatGPT release notes are blocked** by a Cloudflare challenge. Not
   bypassed. The row is in the toml, inactive. Options: leave it off (OpenAI
   news and the API changelog cover launches), or name another source.
2. **Vercel's only feed mixes changelog and blog.** Kept as one source; Haiku
   categorizes each entry.
3. **HN stories are always `community`** (see Categories), so they never
   push.
4. **The Wags slice on /tech** is `ctx_tech` (added to the page-context map).
5. **why-it-matters** now also asks about Tech's top 8 (same venture rule,
   same 20-call daily cap shared with Home).

## 5. Order of operations

1. Owner pastes db/045 on production (`db/045_tech_hub.sql`). Expect the
   NOTICE `045 self-test passed (rolled back)`; the verify block's
   expectations are at the end of the file.
2. Record the migration (`migration_log.applied_at`) in CLAUDE.md and
   MASTER_PLAN in the same session.
3. `tests_live -v` on this branch (needs db/045: the worker's queries read
   its columns).
4. Merge and deploy (Railway rebuilds from `/workers/**`, Vercel from
   `apps/web`). The worker must not run this code before step 1.
5. After the first cycles: latency report, RSS readings, the owner's phone
   checks.
