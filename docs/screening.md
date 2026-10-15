# Screening Room (Chat 4, Phase 3)

TMDB titles, the owner's shelves, where to stream tonight, and a trailers
feed on the existing feed engine. Owner's brief: the Chat 4 Phase 3 prompt,
2026-10-14 (BUILD_BRIEF §6). Recommendations are Phase 4.

## 0. Baseline (2026-10-14, before any Phase 3 code)

Branch `chat4-phase3-screening` from `main` at `8af2e6a` (#39). `vercel whoami`:
`idctsims`. `TMDB_READ_TOKEN` by name only: root `.env` yes, Railway yes,
Vercel Production yes, **Vercel Preview no** (a preview build's Screening Room
fails loudly), no `NEXT_PUBLIC_TMDB*` anywhere.

| Suite | Result |
|---|---|
| `tests/` | **868 OK** |
| `tests_live/ -v` | **360 OK, 0 skipped** (675.5 s) |
| `pnpm test:unit` | 122 passed (main) |

## 1. The owner's services (matched live, 2026-10-14)

`GET /3/watch/providers/{tv,movie}?watch_region=US` (277 TV, 296 movie
providers). Ads tiers count as the owner's; the same service resold as an
Amazon, Roku or Apple TV channel does not (a separate subscription); Amazon
Video (10) is the rent/buy store.

| Service | TMDB provider ids |
|---|---|
| Netflix | 8; 1796 (Standard with Ads); 175 (Netflix Kids, same subscription) |
| HBO Max | 1899 |
| Paramount+ (incl. Showtime) | 2303 (Premium, includes Showtime); 2616 (Essential, ads) |
| Showtime | **no standalone US provider on TMDB any more**: inside Paramount+ Premium (2303) |
| Starz | 43 |
| Prime Video | 9; 2100 (with Ads); 613 (Free with Ads) |

Not the owner's: 1825 HBO Max Amazon Channel, 582 Paramount+ Amazon Channel,
633 Paramount+ Roku, 1853 Paramount+ Apple TV, 1794 / 634 / 1855 Starz
channels, 10 Amazon Video. **Tonight** = every streaming offer (TMDB's
`flatrate`, `ads`, `free`) on one of these, one per service; other streaming,
rent and buy fold below. One mapping, twice: `workers/screening/services.py`
and `apps/web/src/lib/tmdb/services.ts`, pinned equal by tests on both sides.

## 2. Trailer sources (resolved and verified live, 2026-10-14)

Each handle's channel page gave its channel id; each feed
(`https://www.youtube.com/feeds/videos.xml?channel_id=…`, no key) answered 200
with 15 entries, newest Oct 13–14.

| Slug | Channel | Handle | channel_id |
|---|---|---|---|
| yt-netflix | Netflix | @Netflix | UCWOA1ZGywLbqmigxE4Qlvuw |
| yt-hbomax | HBO Max | @hbomax | UCx-KWLTKlB83hDI6UKECtJQ |
| yt-primevideo | Prime Video | @PrimeVideo | UCQJWtTnAHhEG5w4uN0udnUQ |
| yt-paramountplus | Paramount Plus | @ParamountPlus | UCrRttZIypNTA1Mrfwo745Sg |
| yt-starz | STARZ | @STARZ | UCXVxMuWK6l_pCyxEk07EIRw |
| yt-showtime | SHOWTIME | @Showtime | UCtwMWJr2BFPkuJTnSvCESSQ |
| yt-warnerbros | Warner Bros. | @warnerbros | UCjmJDM5pRKbUlVIzDYYWb6g |
| yt-universal | Universal Pictures | @UniversalPictures | UCq0OueAsdxH6b8nyAspwViw |
| yt-sony | Sony Pictures Entertainment | @SonyPictures | UCz97F7dMxBNOfGYu3rx8aCw |
| yt-paramountpictures | Paramount Pictures | @ParamountPictures | UCF9imwPMSGz4Vq1NiTWCC7g |
| yt-lionsgate | Lionsgate Movies | @Lionsgate | UCJ6nMHaJPZvsJ-HmUmj1SeA |
| yt-a24 | A24 | @A24 | UCuPivVjnfNo4mb3Oog_frZg |
| yt-20thcentury | 20th Century Studios | @20thCenturyStudios | UC2-BeLxzUBSs0uSrmzWhJuQ |
| yt-disney | Disney | @Disney | UC_5niPa-d35gg88HaS7RrIw |
| yt-marvel | Marvel Entertainment | @marvel | UCvC4D8onUfXzvjTOM-dBfEA |

**Did not resolve:** `@StreamOnMax` (404; HBO Max is `@hbomax`),
`@WarnerBrosPictures` (404; the film channel is `@warnerbros`, not the
corporate `@WarnerBrosEntertainment`), `@MarvelEntertainment` (404; `@marvel`).
`@MarvelStudios` exists but has not posted since 2018. On Oct 14 the Warner
Bros. feed was all Shorts, so it adds few trailers for now.

**The outage is real:** at ~23:50 UTC Oct 14 every one of the 15 feeds answered
404, hours after all 15 had answered 200. Section 4 is why that is one state.

## 3. Design

- **Pillar `screening`, exclusive like tech (db/051).** A source or story in
  screening is in nothing else (CHECKs); `feed_pillar_guard` now knows two
  exclusive worlds and refuses a cross-line score or reaction either way;
  clustering compares an item only with its own world's stories; the
  Home/Sports summarizer, its backlog alert and the stale retire leave
  trailers out (they are never summarized). `feed_for_you` never lists one on
  Home, Sports or Tech (the pillar arrays never overlap). Ranking is Phase 4:
  `rank.PILLARS` has no screening, so trailers get no score and no profile.
- **Trailers only** (`screening/trailers.py` `is_trailer`): a watch-page
  video (the feed links Shorts to `/shorts/`) titled Trailer or Teaser, unless
  it is about one (reaction, breakdown, recap, explained, featurette, clip,
  behind the scenes, review). On the six saved feeds: Netflix 10 of 15
  entries, A24 2, HBO Max 3, Prime 2, Marvel 1, Warner Bros. 0.
- **The Phase 1 rules apply:** a first fetch keeps its 5 newest trailers as
  backfill (`BACKFILL_WORLDS`), the 72 h too-old rule, date precision.
- **Matching** (chained `screening_match`, first after ingest): the headline
  is cleaned to the title ("Guarding Stars | Leighton Meester … | Official
  Trailer | Netflix" -> "Guarding Stars"; "Season 4", "S11", a trailing
  "Trailer" stripped; a season hints TV) and searched (`/search/multi`); only
  an EXACT normalized name is accepted, a movie only within a year of the
  post, and a trailing sequel number falls back to the series. Anything else
  is `unmatched`, shown and flagged. **On the 18 trailers of the six saved
  feeds, against live TMDB: 14 matched (78%)**, 19 calls; unmatched: City of
  God (HBO Max's series, not the 2003 film: unmatched by design), Avengers #1
  (a comics trailer), Allen, The Disciple: A Wu-Tang Fable.
- **titles / title_trailers / watch_status (db/051).** `titles` is an app
  table the web (owner session, the first time a title is shelved) and the
  worker (matches, the daily refresh) write. `watch_status` is append-only:
  every move a row, a re-watch a new row, `finished_at` set by the trigger on
  every move to finished (Chat 5's Arsenal hook reads it); `v_watch_current`
  is the shelves.
- **Daily `screening_refresh`** (05:30 Chicago, and at boot unless today's
  run succeeded): every shelf title's details again: US providers, US
  release date, next episode for TV, trailer key.
- **TMDB clients.** Worker `screening/tmdb.py`, web `src/lib/tmdb/client.ts`
  (server-only; the only reader of the token, `pnpm check:tmdb-token`; a scan
  of the built client bundle on Oct 14 found the token name and the TMDB API
  host in 0 of 42 files). Endpoint shapes from celiao/tmdbsimple. Region US,
  language en-US; at most 20 requests a second, backoff on 429; a missing
  token or a 401 fails loudly by name.
- **Web.** `/screening` (search via `/api/screening/search`, debounced;
  Upcoming, next 30 days; Watching, Want, Finished last 20; one tap moves a
  title) and its Trailers tab (newest first, reactions write pillar
  screening). `/screening/title/[kind]/[id]`: poster, overview, US release
  or next episode, the trailer inline (youtube-nocookie; the CSP adds that
  one `frame-src` and `img-src https://image.tmdb.org`, nothing broader),
  Tonight on your services first, the rest folded. Attribution: TMDB on
  `/screening` and every title page; JustWatch wherever providers show.
- **Context.** `ctx_screening` for Wags (shelves, upcoming 30 days, the 10
  newest matched trailers); `/screening` opens Wags on that slice.
  `ctx_screening_brief` is a registered brief section that returns NULL
  (silent) until Phase 4.

## 4. The YouTube outage collapse

YouTube's feed server sometimes 404s every feed for hours. In one ingest
run, a failing YouTube source is held, not recorded; once every source has
run, if ALL YouTube sources (at least 3) failed, it is one outage: no
per-source failure counted, no per-source alert, one log line; after 6
outage runs in a row (3 h) ONE push ("YouTube feeds are down"), re-armed when
YouTube answers. Some YouTube sources failing is ordinary: each is recorded
like any other source. A non-YouTube failure is never held.

## 5. Decisions made while building, for the owner

1. **TMDB's logo** was added by the owner as `apps/web/public/tmdb-logo.svg`
   (2026-10-14; themoviedb.org blocks scripted downloads). It is their
   stacked logo, a plain SVG (no script, no external reference); the
   attribution shows it beside the required notice.
2. **Tonight counts `ads` and `free` offers** on the owner's services, not
   only `flatrate` (TMDB lists Paramount+ Essential and Prime with Ads there).
3. **Test titles** use TMDB ids from 900,000,000 up (never a real id) and
   render from their database row, since TMDB has no such title.
4. **Search hits shelve directly** (Want / Watching on the result row), so a
   title can go on a shelf without opening its page.

## 6. Order of operations

1. Owner pastes `db/051_screening.sql`. Expect the NOTICE `051 self-test
   passed (rolled back)`; verify SELECTs (a)-(f) at the end of the file.
   **Before the merge**: the worker reads the new tables and pillar.
2. Record it the same session (CLAUDE.md, MASTER_PLAN).
3. `tests_live -v` (`test_screening_sql` skips by name until step 1).
4. Owner: add `TMDB_READ_TOKEN` to `apps/web/.env.local` for local e2e
   (never printed), and optionally to Vercel Preview.
5. Merge (merge commit) and deploy.
6. Phone checks, `e2e/screening.spec.ts`, the screenshot gate
   (`e2e/screens-screening.spec.ts`), RAM against Phase 2's reading.

## 7. Phase 4: recommendations (owner's brief, 2026-10-15)

Migration `db/053_screening_recs.sql`; the math in
`workers/screening/recs.py` (pure, tested in `tests/test_screening_recs.py`);
the jobs in `workers/screening/rec_jobs.py`; the web in
`src/lib/screening/recs.ts`, the For you, seen-it, banner and title-reaction
components under `components/screening/`, and `/screening/seen`.

### 7.0 Baseline (2026-10-15 02:20-02:33 UTC, before any Phase 4 code)

`tests/` 902 OK; `tests_live -v` 369 OK, 0 skipped (703 s). Branch
`chat4-phase4-recs` from `main` at `b8f3cfc`.

### 7.1 Seeds and the profile

- **Seeds** (`screening_seeds`, TMDB ids verified live on 2026-10-15): TV
  Succession, Billions, Ozark, Yellowstone, The Gentlemen, Tulsa King; film
  The Wolf of Wall Street, Margin Call, Michael Clayton, Heat, The Godfather,
  Uncut Gems; rom-com Hitch, Crazy Rich Asians, Love & Basketball. Each is a
  `title_reactions` row (love, seen, origin `seed`): it shapes the profile and
  is never recommended.
- **Taste notes** are love phrases in `app_settings.seed_interests`
  (`feed.screening`): "power, money and dynasty dramas", "crime and business
  thrillers", "romantic comedies". The fourth, "movies as much as TV", is a
  rule, not a topic: For you keeps the best 4 of each kind (`recs.balance`;
  the same rule in SQL, `screening_for_you`).
- **Vectors:** one gte-small embedding per title of its genres, TMDB keywords
  and overview (`recs.embed_text`), stored on `titles.embedding`, embedded
  once.
- **Signals** (`recs.signal_of`): seed 1.0, Love 1.0, "Seen, loved" 1.0,
  Finished 0.5, **Finished and loved 1.5 (the strongest)**, a trailer Love
  0.5; Not interested 1.0, "Seen, not for me" 1.0, a trailer Not interested
  0.5; Neutral 0. The feed engine's blend: the notes weigh `6 / (6 + loves)`,
  fading as real loves arrive.

### 7.2 Candidates, ranking, "because"

- **Pool** (`screening_candidates`): TMDB recommendations and similar for
  every seed and loved title (up to 40), discover by the top genres and
  keywords, and trending (week, movie and TV). At most 450 titles fetched and
  embedded a run.
- **Quality floor (tunable, recorded):** `vote_average >= 6.8` and
  `vote_count >= 200` (`recs.QUALITY_MIN_AVG`, `QUALITY_MIN_COUNT`; the same
  numbers in `screening_for_you`).
- **Excluded**, by the worker and again live by the database at read time:
  seen (seed, the seen-it pass, finished), on any shelf, or reacted to (Love,
  Neutral, Not interested). A tap removes a card before the re-rank.
- **Score** = 4.0 x similarity + 0.4 when it streams tonight on one of the
  owner's services + 0.3 x freshness (out, or a new season, in the last year,
  fading to 0 at two; trending adds 0.5). Similarity = 0.5 x the cosine to the
  love profile + 0.5 x the cosine to the NEAREST loved title, minus the cosine
  to the not-interested profile, all on mean-centered vectors. The
  nearest-title half is there because fifteen seeds across three moods
  average to none of them.
- **"Because you loved X"** is the loved title nearest by embedding (ties:
  the lower id). No Claude call.
- **Re-rank:** a title reaction, a shelf move or a screening trailer reaction
  queues ONE `screening_rerank` (60 s debounce, by trigger; test titles never
  queue). It re-scores the stored pool and fetches at most 3 new titles; no
  TMDB lists.
- **Schedule:** `screening_recs` daily 04:30 Chicago, and at boot unless
  today's run succeeded. Each run appends a `screening_rec_runs` row with its
  profile counts and its top 12: the record behind "the next refresh shifts
  toward the Loves".

### 7.3 The seen-it pass

About 30 titles near the seeds (10 each of tv, film and rom-com, popular
enough to have been seen), written once by the first run
(`screening_seen_pass`). One at a time on the phone: "Seen, loved" (love +
seen), "Seen, not for me" (not interested + seen), "Haven't seen" (nothing
learned), or Skip. Resumable; re-openable from Settings, Feed profile ("Go
through it again"). Seen is sticky.

### 7.4 Trailer source #2 (TMDB videos)

The recommendations run reads TMDB videos (Trailer or Teaser, YouTube,
official) for the best 15 upcoming, now-playing and on-the-air titles by the
profile (similarity at least 0.10) and every Want and Watching title, at most
2 per title. They are items of the `tmdb-videos` source (kind `tmdb`,
screening only, never fetched by the 30-minute ingest) with the YouTube watch
URL. `content_items.youtube_key` (generated) is UNIQUE, so a trailer from
either path lands once and the other insert is a no-op. Trailers For you
ranks the last 60 days' trailers by their title's score, then the unmatched
ones newest first; Latest is unchanged.

**YouTube RSS, read 2026-10-15 19:25 UTC:** the outage that began ~23:50 UTC
Oct 14 did not reach 24 h. All 15 channel feeds answer (last ok 19:20 UTC);
36 trailers ingested since 05:20 UTC Oct 15, 23 matched to a title.

### 7.5 Release alerts, the brief, Wags

- **At most one push a day, at 08:30 Chicago** (`screening_release_push`; at
  boot until 20:00 if today's has not gone), only when a Want or Watching
  title is out today (a movie), has an episode today, or newly streams on one
  of the owner's services (`title_provider_arrivals`, logged by the 05:30
  refresh). "Tonight: <title> on <service>", any others in the body; the tap
  opens the title. `release_pushes` holds one real push per day (trigger) and
  `release_alerts` never repeats a title and event (unique index).
- **Test push:** `-m scripts.screening_test_push` arms one for the next 08:30
  (`--now` sends at once). Recorded is_test, exempt from both rules.
- **Brief:** `ctx_screening_brief` speaks when there is a release today or a
  For you pick, held to one line (`ONE_LINE_SECTIONS`); NULL (silent)
  otherwise.
- **Wags:** `ctx_screening` adds `for_you_top5`, `today`, `recent_loves` and
  each shelf title's `tonight`, with the instruction to answer "what should I
  watch tonight" from the owner's services first.

### 7.6 "In the news"

A Want or Watching title gets an entity of kind `title` (by trigger;
`entities.title_id`). `title_news` lists up to 3 Home stories of the last 30
days that name it: a one-word title only through a story entity equal to it
("Heat" in a weather headline never counts), a longer one also as whole words
in the headline or summary (regex metacharacters escaped; accents and
punctuation tested in `tests_live`). Title entities have no follows and every
other entity reader filters by kind, so Home's feed is unchanged
(`tests_live` `NoLeaks`).

### 7.7 Day one (dry run, 2026-10-15 02:52 UTC, nothing written)

`-m scripts.screening_recs --dry-run`: pool 610, 258 at the floor, 342 TMDB
calls. For you, in order, with the "because" and tonight's service: Too Big
to Fail (Margin Call; HBO Max), The Big Short (Margin Call), 1923
(Yellowstone; Paramount+), Wall Street (Margin Call), Relay (Michael Clayton;
Netflix), Dutton Ranch (Yellowstone; Paramount+), Law & Order (Michael
Clayton; Netflix), American Psycho (Margin Call; Prime Video), Black Rabbit
(Succession; Netflix), 1883 (Yellowstone; Paramount+), The Wedding Date
(Hitch; Starz), BMF (Ozark; Prime Video). The real first run, after the
paste, records its own list in `screening_rec_runs`; the owner grades that
one.

### 7.8 Decisions made while building, for the owner

1. **Any reaction on a For you card removes it** (Love too: the loved title
   feeds the profile), with Undo.
2. **"Movies as much as TV" is a rule** (4 of each in the 12), not a phrase.
3. **The Screening brief section is one line**, like Tech.
4. **"In the news" links out** to the article: Home has no story page.

### 7.9 Order of operations

1. Owner pastes `db/053_screening_recs.sql`. Expect the NOTICE `053
   self-test passed (rolled back)`; verify SELECTs (a)-(g) at the end.
   **Before the merge**: the worker and the web read the new schema (the
   05:30 refresh's `titles_upsert` writes the new columns).
2. Record it the same session (CLAUDE.md, MASTER_PLAN).
3. `tests_live -v` (the db/053 classes skip by name until step 1).
4. Merge (merge commit) and deploy; the worker's boot run makes day one and
   the seen-it pass.
5. Phone checks (the done-when list), `e2e/screening-recs.spec.ts`, the
   screenshot gate (`e2e/screens-screening-recs.spec.ts`), RAM at rest
   against Phase 3's reading.
