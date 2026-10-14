# Tech Hub (Chat 4, Phases 1 and 2)

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

Added on the owner's decision (2026-10-13, after db/045), each verified live
the same day:

| Slug | Source | Kind | What was found |
|---|---|---|---|
| `openai-community-announcements` | OpenAI Developer Community, Announcements | rss | `/c/announcements.rss` redirects to `/c/announcements/6.rss` (category id 6, "Announcements", in `categories.json`): 200, 25 entries, newest Oct 9. Official, priority 25 (below openai.com/news at 10). Replaces the ChatGPT release notes. |
| `openai-python` | github.com/openai/openai-python releases.atom | rss | 200, 10 releases, newest v3.29.0 (Oct 11). Official. |
| `anthropic-sdk-python` | github.com/anthropics/anthropic-sdk-python releases.atom | rss | 200, 10 releases, newest v1.13.0 (Oct 9). Official. |

`chatgpt-release-notes` stays inactive, no workaround (owner). Totals: 21 tech
sources, 20 active, 19 official (every row but the two HN ones).

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

**Official items decay last (db/046; owner, 2026-10-13).** Under db/045 an
official item decayed on the same 18-hour half-life as a Hacker News story,
so a busy HN day could push it off page one within a day or two. db/046
gives an official story on Tech a 72-hour half-life (community stories,
Home, Sports and every boost keep 18 h): at 48 h it has lost 0.46 rank
instead of 1.85, as much as a 12-hour-old HN story. It is not an absolute
guarantee of page one: 30 community stories that outrank it still fill the
page first, and "seen" (an open or a reaction) is not tracked as an
impression, so the ranking cannot know whether the owner has seen an item.

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

**Only a fresh item pushes (db/046; owner, 2026-10-14).** The first cycle on
production pushed a first-fetch backfill item (notification 323, story 8359,
Vercel "Drama 3 now available on AI Gateway", tech_credit_pushes row 20; it
stays as history and counts toward 2026-10-13's cap). A real push now needs
the story's lead item to be push-eligible: not a backfill (first seen on a
cycle after its source's first successful fetch) AND published within 24 h
of first seen. `tech_push_eligible()` decides it; the push trigger refuses
and `tech_credit_push_allowed()` answers false otherwise. The worker checks
the same rule first (`feed/tech.py` `push_eligible`) and never asks.

## 5b. Late items, and the latency check split (2026-10-14)

**The bug.** A source's first fetch skipped items older than 72 h without
recording them, so its next fetch stored them as new: 341 Home/Sports items
(23 sources) since Chat 3, and 326 tech items (13 sources) in the 03:12 UTC
cycle after the deploy, months-old releases with the Official tag. Fixed in
the worker: on EVERY fetch an item already older than 72 h when first seen
(96 h for a date-only item) is recorded as seen and never shown.
**Cleanup, db/047:** the 579 stories made only of such items (318
Home/Sports, 261 tech; 0 reacted to) are hidden as
`stale:first_seen_after_72h`, not deleted; 20 mixed stories stay. As filtered
stories they reach the existing 30-day filtered prune.

**Latency, split (owner, 2026-10-14):**
- Feed path (RSS / Atom): **GREEN** on claude-code v2.1.299 (first seen
  24.4 min after publication) and openai-python v3.30.0 (12.7 min).
- Page-watcher path: **PENDING** until an anthropic.com/news, claude.com/blog
  or openai.com/news post is first seen after the deploy.
  `scripts/tech_latency.py` reports both, and leaves out backfill, seeded and
  db/047-hidden items.

## 5c. Dates (db/048; owner, 2026-10-14)

- **Undated items** (the source gave no date) are stored as
  `published_precision = 'none'`, dated by first seen as before. They still
  show on Home and Sports, but never push (`tech_push_eligible` requires a
  stated date; the worker's `push_eligible` checks first) and are never a
  latency sample. Before, an RSS item that dropped its date on a later fetch
  looked fresh to every rule (0 such rows on 2026-10-14; the gap was in
  the code). On a first fetch an undated item is still recorded, never shown.
- **Date-only cards print their stated UTC day** (`cardWhen`): a listing that
  says "Oct 6" is stored at 00:00 UTC, which read as "Oct 5" in Chicago.
- **The backfill window:** For you counts from first seen, except a
  first-fetch backfill item, which counts from when it was published (a
  date-only one from the end of its stated day). At 06:01 UTC on Oct 14 this
  retires 25 of the 100 Tech For you cards: the MCP blog and spec posts of
  June to August, Cursor's September entries, and the Oct 2-6 items from
  Anthropic, the Claude blog and API notes, Supabase, the OpenAI API
  changelog and the OpenAI community (the "Oct 5" Pricing & credits card
  among them). More retire as each backfill item passes 7 days.

## 6. The Home and Sports summary cap (investigated 2026-10-14, read-only)

The cap counts STORIES whose `summarized_at` falls on or after Chicago
midnight (rejected attempts and, since db/045, tech stories included), not
calls or items. Kept new stories: 941 (Oct 11), 1,289 (Oct 12), 1,265 (Oct
13), against Chat 3's ~300/day sizing. Oct 11 and 12 stamped 445 and 897,
so ~1,000 carried over; on Oct 13 the summarizer ran at its ceiling (113-119
an hour, 60 a run) from 00:00 to 15:00 Chicago on stories first seen Oct
11-12, and reached 2,000 at 21:33 Chicago (02:33 UTC). No story was
summarized twice (3,082 one attempt, 300 two; 483 calls for 3,303 stories in
72 h). The saturation predates every deploy that day. Spend: $0.47 of $20
month-to-date; `feed_summarize` $0.18 on Oct 13 (307 calls). Remaining
backlog at 03:00 UTC: 115 stories, all from that night. No change made.

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

## 4. Decisions made while building, and the owner's answers (2026-10-13)

1. **ChatGPT release notes are blocked** by a Cloudflare challenge. Owner:
   leave it inactive, no workaround; replaced by the OpenAI Developer
   Community announcements, plus the OpenAI and Anthropic Python SDK
   releases (section 2).
2. **Vercel's only feed mixes changelog and blog.** Kept as one source; Haiku
   categorizes each entry.
3. **HN stories are always `community`** (see Categories), so they never
   push. Approved.
4. **The Wags slice on /tech** is `ctx_tech` (added to the page-context map).
   Approved on the condition that a cross-pillar question still gets the
   full snapshot. It does: `/api/wags` puts EVERY provider's slice in the
   cached system block; the page slice is only an extra note on the newest
   turn (`apps/web/src/app/api/wags/route.ts`, the system block and the
   `[Page detail: …]` note).
5. **why-it-matters** now also asks about Tech's top 8 (same venture rule,
   same 20-call daily cap shared with Home). Approved; its Haiku cost per
   day goes in the report (`ai_usage`, purpose `feed_why`).
6. **The 7-day window**: approved, with official items decaying last
   (db/046, section 3).
7. **`tech` and `tech_brief` share sort 50.** No reader orders by sort
   alone where the tie matters: `context_snapshot()` orders by `sort, key`
   (db/029) and the brief's section list by `sort, key`
   (`ledger.brief_sections`), and `tech` is not a brief section, so
   `tech_brief` is the only brief section at 50. Only old verify SELECTs
   order by sort alone, for display. No change.

## 5. Order of operations

1. Owner pastes db/045 on production (`db/045_tech_hub.sql`). Expect the
   NOTICE `045 self-test passed (rolled back)`; the verify block's
   expectations are at the end of the file. **Done 2026-10-14 02:32:51 UTC**
   (the first paste failed at the `_e2e_tech` slug and applied nothing; the
   test source is `_e2e-tech`). Then db/046 (official decay).
2. Record the migration (`migration_log.applied_at`) in CLAUDE.md and
   MASTER_PLAN in the same session.
3. `tests_live -v` on this branch (needs db/045: the worker's queries read
   its columns).
4. Merge and deploy (Railway rebuilds from `/workers/**`, Vercel from
   `apps/web`). The worker must not run this code before step 1.
5. After the first cycles: latency report, RSS readings, the owner's phone
   checks.

## 7. Phase 2: GitHub repos (db/049; owner's brief, 2026-10-14)

### 7.0 Baseline (2026-10-14, before any Phase 2 code)

Branch `chat4-phase2-repos` from `main` at `3f50954` (#35). `vercel whoami`:
`idctsims`. `GITHUB_PUBLIC_READ_TOKEN` checked by name only: set on Railway
and in the root `.env`; absent on Vercel; `GITHUB_TOKEN` set nowhere.

| Suite | Command (from `workers/`) | Result |
|---|---|---|
| `tests/` | `..\venv\Scripts\python.exe -m unittest discover -s tests -t .` | **807 OK** (1.5 s) |
| `tests_live/` | `..\venv\Scripts\python.exe -m unittest discover -v -s tests_live -t .` | **339 OK, 0 skipped** (637.8 s) |

### 7.1 Design

- **One daily job, `tech_repos`** (`workers/feed/repos.py`), 07:00 Chicago and
  at boot unless today's run already succeeded (its own `job_health` ok
  today). It is not part of the 30-minute ingest: the `github-repos` source
  (`kind = 'github'`) is never listed by `feed_active_sources`.
- **Discovery.** github.com/trending daily and weekly, parsed with stdlib
  `html.parser` on the reference repos' selectors (`article.Box-row`, the `h2`
  link, `p.col-9`, `itemprop=programmingLanguage`, the `/stargazers` link, the
  `float-sm-right` "stars today/this week" span). **0 cards, or a card with
  no owner/name or no star count, raises `TrendingParseFailed`**: a
  job_health failure, never an empty day (the reference scrapers swallow
  both). Then nine Search API queries, one per interest, created in the last
  30 days, by stars, 10 each, paced 2.5 s apart (24 a minute at most).
- **Enrichment.** Search results are REST repository objects already
  (topics, stars, pushed_at, license, archived, fork), so only trending-only
  repos get `GET /repos/{name}`. The README is asked LAST, only of repos that
  pass the metadata filters: its first 2,048 bytes, streamed and dropped.
- **Snapshots.** One `repo_star_snapshots` row per repo per owner's day, the
  day set by the trigger (no backdating). Repos first seen in the last 30
  days, or surfaced in the last 60, are re-read and snapshotted daily even
  when they are not candidates (at most 150), so their stars_7d can later be
  measured. `stars_7d` is MEASURED (today's snapshot minus the one exactly 7
  days ago) or an ESTIMATE, always labelled: trending's stars in period
  ("trending est."), every star of a repo under 7 days old ("since created"),
  or the age average ("est."). The labels live once in `repos.LABELS` and the
  web's `STARS_7D_LABELS`, pinned equal by `e2e/repos-unit.spec.ts`.
- **Selection.** Hard filters: archived, fork, no README, not pushed in 90
  days. Rank: the tech profile (`rank.score_story` with the repo's embedding
  and its GitHub topics in snake_case) + 0.35 × log10(1 + stars_7d) + 0.4 for
  a repo found by an interest search − 0.5 under 50 stars in 7 days. **The 8
  a day and the resurface rule are the database's** (`repo_surfacings`'
  trigger, `repo_surface_room`, `repo_may_surface`); the worker asks the rule
  first and a refusal rolls the pick back whole.
- **Into the feed engine.** Each pick is a tech story, category `repos`, title
  owner/name, topics = its GitHub topics (kept through the summary:
  `feed_save_summaries` never overwrites a repo story's topics, so a Not
  interested trains those topics' weights), one item from `github-repos`
  (published the day it surfaced, precision `date`, teaser = description +
  README head). The 30-minute chain summarizes it (Tech's community cap;
  not official, never pushes) and `feed_repo_why` (after `feed_why`) writes
  its line.
- **Why it matters for a repo** (`feed/why.py` `repo_why_job`): the same
  venture context and validator, plus "the dashboard" as a target; no
  concrete use, or a line the rules refuse, stores **"General interest."**
  (never NULL). Its own purpose `feed_repo_why`, at most 4 calls a day.
  Home's why queue never asks a repo story.
- **Context.** `ctx_tech` gains `repos_this_week` and `loved_repos`;
  `ctx_tech_brief` gains `top_repo` only when the owner loved a repo story in
  the last 14 days. The brief's fallback writes it into the one Tech line.
- **Web.** The Repos chip on /tech; a repo card adds stars, stars_7d with
  its label (`data-estimate` on an estimate), language and at most 3 topics.
- **The fetch log line** (owner, 2026-10-14, from Phase 1 watch item 1):
  every feed fetch logs `feed <slug> fetch: N items, newest: a | b | c`, the
  newest 3 GUIDs cut to 40 characters, or `not modified`. No table.

### 7.2 Decisions made while building (2026-10-14), for the owner

1. **A third table, `repo_surfacings`** (append-only), beside the two the
   brief named. The resurface rule needs every surfacing's day and
   first-shown stars_7d, and a repo can surface more than once, so one
   `repos.story_id` cannot hold it. It also lets the trigger enforce the cap
   and the rule. `repos.story_id` and `repos.why_it_matters` keep the latest.
2. **The ranking weights were tuned on a dry run** (real GitHub, the real
   embedder, the real tech profile read as `valemont_readonly`, every ledger
   write faked; nothing written). With velocity at 0.6 alone a PS5 porting
   tool and an Xcode replacement took day one; with the profile alone 8-star
   repos did. The shipped blend picked, for 2026-10-14: morluto/rea,
   bas3line/ascii, rehan-remade/universal-modder, zai-org/ZCode,
   kuratlielia/arc-library, cathrynlavery/diagram-design, juliobsz/skills,
   Louis-CFM/coucou. The "half I'd use" check is the owner's.
3. **A repo with no first-shown stars_7d (or 0) never resurfaces**: there is
   no baseline to double.
4. **"Today's run happened"** is the job's own success today, not today's
   snapshots, so a run that failed after snapshotting runs again.
5. **Search hits reuse the search result's REST object** instead of a second
   `GET /repos` per repo (same fields, about 90 fewer calls a day).
6. **A repo URL another item already holds** (a Hacker News link to the same
   repo) gets `#surfaced-<day>` so the unique URL index never drops a pick.

### 7.3 Order of operations

1. Owner pastes `db/049_repos.sql` on production. Expect the NOTICE
   `049 self-test passed (rolled back)`; the verify SELECTs are at the end of
   the file. **The paste must land before the merge**: the worker reads the
   new tables and the github kind, and /tech reads `repo_surfacings`.
2. Record it (`migration_log.applied_at`) in CLAUDE.md and MASTER_PLAN the
   same session.
3. `tests_live -v` on this branch (its db/049 classes skip by name until
   step 1).
4. Merge (merge commit) and deploy. The boot run surfaces day one.
5. The owner's phone checks, `e2e/repos.spec.ts`, the screenshot gate, the
   per-run call count (the job note) and RAM against 138 MB.
