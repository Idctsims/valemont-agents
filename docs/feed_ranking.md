# Feed ranking (Chat 3 Phase 2, Step 0)

Design note for reactions, the interest profile, ranking, "why it matters",
and the feed in Wags and the Morning Brief. Written 2026-10-12 (UTC) on
branch `chat3-phase2-ranking`. **Approved by the owner 2026-10-11 with the
changes in "Owner decisions" below**; where this note and those decisions
differ, the decisions win. Step 1 is `db/039_feed_ranking.sql`.

## Owner decisions (2026-10-11)

1. Tier-1 sports stories on For you: approved, **capped at 5 on page one (of
   30)**. In db/039: `story_scores.cross_pillar`, `CROSS_CAP = 5` in
   `feed_for_you`, self-tested on paste and to be tested in tests_live.
2. Alabama floor capped at 3; 3. seed decay per profile; 4. recency at read
   time; 5. `entities.sport`; 7. 7-day `story_scores` prune: **approved as
   written.**
6. **Seeds are the owner's, verbatim** (db/039 section 8, replacing the draft
   in §2.3): 9 loves and 6 not interested, Home only. Sports seeds stay
   empty until Phase 3. The owner's Phase 1 brief is to be committed verbatim
   as `docs/chat3_owner_brief.md` (**pending: the text has not been provided
   to this session**). `K_SEED` stays 12: with 9 love phrases the
   pseudo-count reading is approximate; the decay arithmetic in §2.3 is
   unchanged.
7. **The "10 Not Interested" check:** the held-out story must be **on page
   one of For you before the taps** and **off page one after `--rebuild`**.
   Before and after positions are reported. The same rule in the test.
   - **Amended by the owner, 2026-10-12: the live check is the Love
     direction.** The Not Interested direction is shown two ways instead:
     by the seeds, since with zero taps the best-placed gossip story in For you
     is #48 (`feed_for_you('home')`, 2026-10-12 after `--rebuild`), so no
     don't-care topic had a held-out story on page one to push off; and by
     the unit-test property (`tests/test_feed_rank.py` `TenNotInterested`).
   - **The live Love check:** a held-out story of one loved topic, **off page
     one of For you (position 31–80) before the taps**, and ten Love taps on
     other stories of that topic that touch no follow (`follow_tier` NULL).
     ~~Pass = the held-out story is on page one after `--rebuild`.~~ The
     Bordeaux wine story (id 1090) is the control. Before and after
     positions of both are reported.
   - **Pass rule amended by the owner BEFORE any tap (2026-10-12 16:22
     Chicago), frozen now.** The page-one rule was dropped because ten loves
     lift the whole topic, and a held-out story can lose page-one places to
     its own siblings while the topic gains (a synthetic pool: the topic 6 → 12
     on page one, the held-out story 50 → 35; "13" in `fe455e1` was a
     miscount, corrected in the next commit. The rule below is unchanged).
     `tests/test_feed_rank.py` `TenLoves` holds the amended rule on that
     pool: held-out 50 → 35 against control 51 → 62 (26 ≥ 15), and the topic
     6 → 12.
     **PASS = (a) AND (b), both measured against the 21:16 UTC "before"
     snapshot:**
     - **(a)** the held-out story's climb exceeds the control's movement by
       **≥ 15 positions**: `(64 − held_after) − (86 − control_after) ≥ 15`.
       Positions are 1-based in `feed_for_you('home', 1000, 0)`, and a climb
       is positive.
     - **(b)** the topic's page-one count rises **above 9**: `> 9` of
       positions 1–30.
     - **The topic, frozen:** a story with `follow_tier` NULL whose
       lowercased `topics` plus `title` match
       `international_sales|media_rights|format|co_production|licensing|distribution|acquisition|tv_distribution|film_sales`.
       On the snapshot that is 9 on page one (positions 14, 17, 18, 19, 20,
       21, 22, 27, 30: ids 2313, 2321, 2173, 2349, 2278, 2372, 2375, 2309,
       2351). The same classifier is applied after, including to stories
       ingested since.
     - **The pick, made before the rule:** held-out **2308** (Cineflix
       "Hildur", Variety, #64), control **1090** (#86). Targets: 2313, 2321,
       2173, 2349, 2278, 2375, 2309, 2374, 2307, 2312, spare 764. The owner
       loves only the targets they would actually read and names them. The
       count of loves is reported, and the rule is unchanged whatever it is.
     - **Snapshot:** `docs/feed_checks/love-2026-10-12/before-2116Z.tsv`
       (`feed_for_you('home', 1000, 0)` through `db_inspect`, 491 rows, after
       `--rebuild`; 0 reactions in the profile). The file was written at
       21:15:41 UTC; "21:16" is the database clock read just after it. It is
       the one snapshot every position above comes from.
     - **After:** run `--rebuild` once, read `feed_for_you` the same way, and
       report both conditions with the numbers. No second rebuild, no
       re-measure.
   - **RESULT, 2026-10-12: FAIL on both (a) and (b).** Recorded as-is: no
     re-run, no re-pick (owner).
     - One `--rebuild` at about 21:36 UTC (home: love 1, not interested 4,
       opened 1, neutral 4; seed weight love 0.92, NI 0.75). One read,
       `docs/feed_checks/love-2026-10-12/after-2136Z.tsv`, 483 rows,
       written 21:36:09 UTC. Scored by `score.py` beside it, which applies
       the frozen classifier to both files.
     - **(a):** held-out 2308 went **64 → 159**, control 1090 went
       **86 → 99**. `(64 − 159) − (86 − 99) = −82`, against ≥ 15 needed.
     - **(b):** the topic on page one went **9 → 3**, against > 9 needed. After:
       2349 (#18), 2351 (#25), 2372 (#28).
     - **The taps, as `feed_reactions` records them** (read via `db_inspect`
       before the rebuild, ids 104–124, 21:25–21:33 UTC):
       - **Love:** 1 target, 2349.
       - **Not interested:** 4 targets, 2173, 2278, 2375 and 2309.
       - **Neutral:** 4 targets, 2313, 2321, 2374 and 2312.
       - **Untouched:** 1 target, 2307.
       - **Off-target:** Love then Undo on 1556, 2382, 763 and 839, which
         nets to no signal; and opens on 1400, 2321, 2349 and 2374.

       The owner reported "Love on #4 only; skipped the other 9". In the
       ledger, 8 of those 9 carry a Not interested or Neutral reaction.
       Neutral and Not interested both remove a card from For you, and four
       of the targets dismissed that way were page-one topic stories.
     - **So this run did not measure the ten-Love property:** it had one
       Love, and four Not interested on the topic. The unit property is
       `TenLoves`. What the run does show: four Not interested taps on the
       topic moved the held-out story down 95 places, against the control's 13.
8. **Stale Google News items** are dropped at ingest when published more than
   `gnews_window_days + 2` days before the fetch: a code rule
   (`feed/ingest.py` `STALE_GRACE`, tests `GoogleNews.test_stale_*`), no
   title rule. The Axios Miami 2024 author page is the case.

The worked example (§6) ran the formula on **406 real stories** through the
read-only role. The seed phrases were embedded with the live embed function.
The profiles are made up.

---

## 0. Decisions put to the owner (Step 0, as proposed)

These either went beyond the brief or differed from it; the owner's answers
are above.

1. **For you pulls tier-1 sports stories into Home** (§2.8). Home reads only
   the `home` pillar today, and every Alabama story is in `sports` (AL.com,
   Roll Bama Roll, ESPN CFB, Yahoo CFB). Without this, "Alabama near the top
   of For you" can never happen.
2. **The Alabama floor is capped at 3 stories** (§2.7). This weekend 50
   stories are tagged Alabama (Georgia beat Alabama). Applied to all of them,
   the floor puts about 40 Alabama stories at position 5, so page one becomes
   all Alabama. The demo showed this before the cap went in.
3. **Seed decay runs per profile, not per pillar** (§2.3). The love seeds
   decay with love reactions and the NI seeds with NI reactions. Ten Not
   Interested taps should not weaken the love seeds.
4. **Recency is applied when the feed is read, not stored** (§2.6). A stored
   score with recency baked in goes stale by the hour. Stories scored last
   night would then compete against stories scored a minute ago on unequal
   terms.
5. **`entities.sport`** (§1.3) narrows an alias to one sport, so "Alabama"
   (state news), "Hamilton" (the musical) and "Georgia" (the state) do not
   match.
6. **Seed phrases live in `app_settings.seed_interests`**, the jsonb column
   db/020 already created for this. No new table is needed. **I drafted the
   loves (§2.3); the Phase 1 brief's own wording is not in the repo. Please
   correct them.**
7. **Superseded `story_scores` rows are pruned after 7 days** (§1.5). Without
   that, nightly rescoring adds about 70 MB in 90 days to the 500 MB tier.

---

## 1. Schema (db/039_feed_ranking.sql)

Every table has RLS on and anon gets nothing. The owner's policies are
`TO authenticated USING ((SELECT public.is_owner()))`, as in db/032. The worker
writes through `DATABASE_URL`. `valemont_readonly` reads everything (db/018's
defaults; views are granted explicitly, as db/028 does). Every trigger and
function ships with a rolled-back self-test (§5, the db/032 and db/035
pattern).

### 1.1 `feed_reactions`: append-only

| column | type | notes |
|---|---|---|
| id | bigserial PK | |
| owner_id | uuid NOT NULL DEFAULT auth.uid() | CHECK by policy: `= auth.uid()` and `is_owner()` |
| story_id | bigint NOT NULL REFERENCES stories ON DELETE **RESTRICT** | Nothing can delete a reacted story (§1.7) |
| pillar | text NOT NULL CHECK IN ('home','sports') | The feed the tap came from |
| reaction | text NOT NULL CHECK IN ('love','neutral','not_interested','open','undo') | |
| created_at | timestamptz NOT NULL DEFAULT now() | Set by the database |

- **Policies:** owner INSERT (`WITH CHECK owner_id = auth.uid() AND is_owner()`)
  and owner SELECT. There is no UPDATE or DELETE policy.
- **Trigger `feed_reactions_frozen`** refuses UPDATE and DELETE from any role.
  **TRUNCATE is refused** by `reject_truncate()` (db/020).
- **No `is_test` column.** A reaction is a test reaction when its story is a
  test story. Every reader joins `stories` and filters `NOT is_test`. e2e taps
  only test stories (`add_test_story`).
- **View `v_feed_reaction_latest`** (security_invoker) gives one row per
  (story, pillar). That row is the latest reaction that is not `open`, plus
  `opened` (any open exists) and `last_opened_at`. A latest reaction of
  `undo` means "no reaction": the card comes back.
- Index `(story_id, pillar, created_at DESC)`.

### 1.2 `interest_profiles`: worker-written, owner-read

One row per rebuild per pillar, append-only by convention. The reader takes
the latest row per pillar, which keeps a history of how the profile moved.

| column | type |
|---|---|
| id, pillar, built_at | bigserial, text, timestamptz DEFAULT now() |
| mean_vec, love_vec, ni_vec | halfvec(384); `love_vec` and `ni_vec` NULL when there is no signal |
| topic_weights, source_weights, entity_weights | jsonb `{name: weight}`, only names with evidence |
| reaction_count | int: stories with a love or NI signal |
| love_count, ni_count, open_count, neutral_count | int, for /settings/feed |
| seed_weight_love, seed_weight_ni | real: k/(k+n) at build time |

About 10 KB a row with 3 vectors. Pruned to 90 days (≈ 2 MB).

### 1.3 `entities`, `entity_links`, `follows`

- **`entities`:** id, kind (person | team | program | league | tour |
  company), name UNIQUE, aliases text[], **sport** (NULL, or a sport code;
  decision 5).
- **`entity_links`:** from_id, to_id, kind (`plays_for`, `drives_for`,
  `member_of`), valid_from date, confirmed_by_owner bool. A trade becomes a
  new row with a later `valid_from`. Old rows are never edited.
- **`follows`:** entity_id, tier (1, 2 or 3), pillar (`home`, `sports`, or
  NULL for both), floor bool.

Owner-read in this phase. Follow editing in the UI is not in this phase's
scope.

**Matching:** an alias matches as a whole word, case-insensitive, against the
story title and its Haiku `entities`. If `entities.sport` is set, the story's
sport must equal it.

**Seed rows:**

| entity | kind | sport | tier | pillar | aliases (lowercase) |
|---|---|---|---|---|---|
| Alabama Crimson Tide | program | cfb | 1 (**floor**) | both | alabama, crimson tide, bama, alabama football |
| LeBron James | person | nba | 1 | both | lebron, lebron james |
| Philadelphia 76ers | team | nba | 2 | sports | 76ers, sixers |
| Lewis Hamilton | person | f1 | 1 | both | lewis hamilton, hamilton |
| Charles Leclerc | person | f1 | 1 | both | leclerc |
| Ferrari | team | f1 | 2 | sports | ferrari, scuderia ferrari |
| Scottie Scheffler | person | golf | 1 | both | scheffler |
| Brooks Koepka | person | golf | 1 | both | koepka |
| Kalshi | company | — | 1 | home | kalshi |
| SEC, Big Ten, Big 12, ACC | league | cfb | 3 | sports | each conference's name plus its member programs (Alabama is excluded; its own row is tier 1) |
| NFL, NBA, Formula 1, Golf | league | per sport | 3 | sports | match on the story's `sport` alone |

Links: LeBron `plays_for` 76ers; Hamilton and Leclerc `drives_for` Ferrari
(both valid_from 2026-01-01, confirmed).

### 1.4 `stories` additions

`why_it_matters text` (≤ 160 characters, nullable) and `why_generated_at
timestamptz`. `why_generated_at` is set even when the answer is null, so a
story is asked once.

### 1.5 `story_scores`

story_id (FK, ON DELETE CASCADE), pillar, `score real`, `components jsonb`,
`profile_id bigint` (the profile it was scored against), scored_at.

- **`score` is the time-free base** (§2.6). Recency is added at read time.
- **Latest per (story, pillar) wins** through `v_story_scores_latest`.
- **The nightly prune deletes superseded rows older than 7 days.** At about
  2,000 rows a day and roughly 400 B each, keeping them all would cost about
  70 MB in 90 days.

### 1.6 Read path: `feed_for_you(p_pillar, p_limit, p_offset)`

SECURITY INVOKER STABLE. It returns the For you order with recency applied at
`now()` and the Alabama floor applied on page one. It excludes stories whose
latest reaction is `neutral` or `not_interested`. Latest uses the same
exclusion.

### 1.7 `ctx_feed` and the prune

- **`ctx_feed()`** (SECURITY INVOKER STABLE) returns Home's top 3 from
  `feed_for_you('home', 3, 0)`: id, title, source, summary, why_it_matters,
  url. Test stories are invisible (v_feed_stories excludes them), and so are
  reactions on test stories. Registry row: `brief_section = true`, sorted
  after capital. Paper and live rules do not apply. The read-only role may
  execute it, as with `ctx_goals`.
- **Prune** (extends `FEED_PRUNE_SQL`): delete stories whose `last_seen_at`
  is more than 90 days old **and** that have no `feed_reactions` row of any
  kind. The FK RESTRICT is the backstop: a reacted story cannot be deleted
  even if the query is wrong.

---

## 2. The scoring formula

Every weight is a named constant in `workers/feed/rank.py`. The SQL read path
takes `HALF_LIFE_H`, `FLOOR_POSITION` and `FLOOR_MAX` from the same constants
(a test pins the SQL literals to the Python values, as WebTwinPricing does).

### 2.1 Which vector: `stories.centroid`

The ranking vector is the **story centroid**: the running mean of its member
items' title + teaser embeddings (db/032; it is not the title-only clustering
vector).

- **It never clears.** `content_items.embedding` is nulled after 72 h, so the
  lead item's vector is gone by the time an old reaction needs rebuilding into
  a profile. A profile rebuilt from reactions older than 72 h therefore needs
  the centroid.
- **It stays on-story.** Items join only at title cosine ≥ 0.94 (or fuzz ≥ 90
  with cosine ≥ 0.90), with at most 25 members. On today's data, 83 of 965
  kept story-pillar rows have more than one item. For the rest, the centroid **is** the
  lead item's vector.

### 2.2 Anisotropy fix: centering

```
mean_vec[p] = mean(centroid) over pillar p's candidate stories first seen in
              the last MEAN_WINDOW_D = 7 days            (stored nightly)
c(x)        = x − mean_vec[p]                            (before every cosine)
```

Measured on 400 random pairs of today's candidates:

| | p5 | p50 | p95 |
|---|---|---|---|
| raw cosine | 0.679 | 0.737 | 0.803 |
| centered cosine | −0.153 | −0.025 | +0.196 |

After centering, unrelated means about 0 instead of 0.74. The same mean
centers seeds, reactions and candidates, so all three live in one space.
Between rebuilds, new stories use the stored mean.

### 2.3 Profiles and seeds

Per pillar, from `v_feed_reaction_latest`, real stories only:

| signal | goes to | weight |
|---|---|---|
| latest = love | love profile | `W_LOVE = 1.0` |
| latest = not_interested | NI profile | `W_NI = 1.0` |
| no latest reaction (or undo), but opened | love profile | `W_OPEN = 0.3` |
| latest = neutral | nothing | — |

An open on a story that also has a love or NI counts once, as the love or NI.

```
R_love = unit( Σ w·c(story) / Σ w )    over love signals
R_ni   = unit( Σ w·c(story) / Σ w )    over NI signals
S_love = unit( mean c(seed) )          over the pillar's love seed phrases
S_ni   = unit( mean c(seed) )          over its don't-care phrases

s_love = K_SEED / (K_SEED + n_love)    n_love = stories with a love signal (opens excluded)
s_ni   = K_SEED / (K_SEED + n_ni)

love_profile = s_love·S_love + (1 − s_love)·R_love    (S_love alone while n_love = 0)
ni_profile   = s_ni·S_ni     + (1 − s_ni)·R_ni
```

**Both parts are unit vectors before blending,** so `s` is the real share. A
mean of many diverse vectors is short; without this, the seeds' share would
depend on how diverse the reactions were.

**`K_SEED = 12`.** The reasons:

- **A pseudo-count:** each of the 12 love phrases counts as one reaction's
  worth of evidence, which is the usual reading of k in k/(k+n).
- **Fast enough:** at 10–20 reactions a day, the seeds are half the profile
  after the first day, about 20% at 50 reactions (about a week), and 8% at
  150.
- **Passes the brief's test:** after ten NI taps the NI seed share is
  12/22 = 0.55, and the demo (§6.2) moves a held-out same-topic story 73 to
  108 places down. A smaller k makes the first few taps swing the feed hard.
  A larger one makes the "ten taps" check fail on a typical topic.

**Drafted seed phrases** (`app_settings.seed_interests`; **the owner
corrects these**):

- **Home love (12):**
  - media rights deal for a sports league
  - Hollywood studio merger and box office business
  - venture capital funding round for a startup
  - private equity acquisition of a company
  - Miami real estate development project
  - luxury hotel development and hospitality deal
  - superyacht industry news
  - music industry catalog sale and streaming business
  - creator economy and YouTube business
  - sports betting regulation and prediction markets like Kalshi
  - sports team valuation and ownership sale
  - NIL deals in college sports
- **Home don't-care (8):** the 7 `seed_phrase_drop` phrases for celebrity,
  politics, AI hype and crypto price (db/032), plus "plain game recap with the
  final score".
- **Sports love (8):**
  - Alabama Crimson Tide football
  - LeBron James and the 76ers
  - Lewis Hamilton and Charles Leclerc at Ferrari
  - Scottie Scheffler and Brooks Koepka
  - college football playoff race
  - NFL trade or coaching change
  - F1 race strategy analysis
  - major golf championship
- **Sports don't-care (5):**
  - betting picks and predictions for a game
  - fantasy football start/sit advice
  - plain game recap with the final score
  - player prop bets
  - ranking listicle

Seeds are re-embedded at every rebuild: 4 embed calls, deterministic.

### 2.4 Affinity

```
affinity = cos(c(story), love_profile) − cos(c(story), ni_profile)
```

A missing profile contributes 0. On today's candidates the spread is
p5 −0.29, p50 −0.03, p95 +0.37.

### 2.5 The additive terms

All are in e-fold units (§2.6): **+0.69 is worth one half-life of freshness,
18 h.**

| term | rule | bound | in hours |
|---|---|---|---|
| `W_AFF × affinity` | `W_AFF = 4.0` | practical ±1.5 | p5 to p95 ≈ 69 h apart |
| follow boost | max over matched follows: `TIER_BOOST = {1: 1.00, 2: 0.50, 3: 0.15}` | 1.00 | tier 1 ≈ 26 h |
| source weight | `learned(source)`, `B_SOURCE = 0.30` | ±0.30 | ±8 h |
| topic weight | mean of `learned(topic)` over the story's topics that have one, `B_TOPIC = 0.40` | ±0.40 | ±10 h |
| entity weight | mean of `learned(entity)` over Haiku entities, `B_ENTITY = 0.30` | ±0.30 | ±8 h |

The learner, the same for all three:

```
learned(x) = B · (L − N) / (L + N + M_PRIOR),   M_PRIOR = 5
L = Σ love weights (1.0) + opens (0.3) on stories carrying x
N = Σ NI weights (1.0)
```

It is bounded in (−B, B) and shrunk toward 0 until the evidence builds up. One
love on a source gives +0.05, ten give +0.20, and the value never exceeds
0.30.

- The **max** over follows is used, not the sum, so a LeBron + 76ers story
  gets tier 1, not tier 1 + tier 2.
- Topic and entity weights take the **mean**, so a story with six topics
  cannot stack six weights.

**Topics are Haiku's free text.** On today's data they fragment
(`game_recap` 40, `game_result` 21, `week_recap` 3). A learned topic weight
therefore sees part of the evidence, and the embedding affinity carries most
of the "less of this" signal. That is why B_TOPIC is small. Topics are not
normalized in this phase.

### 2.6 Recency, and why the score is exp(base) × recency

```
base     = W_AFF·affinity + follow + source + topic + entity    (stored: story_scores.score)
recency  = 0.5 ^ (age_h / HALF_LIFE_H),  HALF_LIFE_H = 18 (Home)
           age_h from the lead item's published_at (already capped at fetch time)
rank     = exp(base) × recency  ≡  base − ln2 · age_h / 18      (computed at read)
```

"× recency" on a raw sum breaks when the sum is negative: a disliked story
would climb toward 0 as it aged. Exponentiating first keeps every score
positive, so a disliked story falls with age like any other. In log form the
whole formula is additive, which is what makes the "in hours" column above
exact.

Sports gets the same constant until Phase 3 says otherwise.

### 2.7 The Alabama floor (capped)

On page one of For you (`PAGE_SIZE = 30`), the best `FLOOR_MAX = 3`
floor-followed stories under `FLOOR_MAX_AGE_H = 48` rank at least as high as
the `FLOOR_POSITION = 6`th-best other story (the 80th percentile of page
one). In practice they land within the top 9. Floored stories keep their
relative order. The rest rank on their own score, which carries the +1.00
tier-1 boost.

**Why the cap** (decision 2): 50 stories were tagged Alabama in today's data.
Uncapped, the floor lifted every one of them to about position 5 and pushed
everything else off page one. That was seen in the demo before the cap went
in.

### 2.8 Candidate set

- **For you, Home:** kept, linked, real stories first seen in the last 72 h
  in the `home` pillar, **plus any story matching a tier-1 follow whose pillar
  includes home** (decision 1).
- A reaction tapped on Home is written with pillar `home`, whatever the
  story's own pillars.

---

## 3. Jobs (Step 2, summary)

- **`feed_profile`**, 04:00 America/Chicago, and at boot if no profile exists
  for today (Chicago date). Per pillar it:
  1. rebuilds `mean_vec`;
  2. re-embeds the seeds;
  3. rebuilds the profiles and the learned weights;
  4. rescores every kept story from the last 72 h.

  New stories are scored against the current profile when `feed_summarize`
  saves them; stories with no summary are scored at ingest. It is
  `run_tracked`, so it has a `job_health` row.
- **`scripts.feed_profile --rebuild`** does the same by hand and prints counts
  only (reactions per kind, stories scored, seed weights).
- **`feed_why`**, after each rescore and whenever a new story enters Home's
  top 8, for stories with `why_generated_at IS NULL`.
  - Haiku via `core.ai.complete()`, non-critical, venture context from
    `ctx_ventures()`.
  - Up to 8 stories per call, at most `WHY_DAILY_CALLS = 20` calls a day.
  - The validator accepts the line only if it names a venture exactly as
    `ctx_ventures` spells it, is one sentence, and is at most 20 words.
    **Anything else is stored as NULL.** The prompt says null is the expected
    answer.
- **`feed_prune`** adds the 90-day unreacted-story delete and the 7-day
  `story_scores` and 90-day `interest_profiles` trims.

## 4. Web (Step 3, summary)

- **Home:** For you and Latest tabs. Love / Neutral / Not interested buttons
  are 44 px targets outside the row link. Swipe right is Love, swipe left is
  Not interested.
- **Undo:** Neutral and Not interested remove the card with an Undo toast;
  Undo writes an `undo` row.
- **Open beacon:** `/api/feed/open` takes a `sendBeacon` on outbound click.
  It is owner-only, allows 60 a minute (database clock), and dedupes one open
  per story per pillar per 10 min.
- **/settings/feed** shows the profile in plain words: top loved and disliked
  topics, sources and entities; follows by tier; reaction counts; seed weight
  remaining; last rebuild time; and the history of `interest_profiles` rows.

## 5. Tests (Step 4, summary)

As the brief lists. Also:

- the SQL constants match `rank.py`;
- the self-test DO blocks for `feed_reactions_frozen`, the latest view (undo
  restores), `feed_for_you` (floor cap, exclusion) and `ctx_feed`
  (is_test invisibility);
- a reacted story survives a prune (FK RESTRICT).

---

## 6. Worked example: 10 real stories, made-up profiles

**Run:** 2026-10-12 ~04:40 UTC, read-only role. There were 406 For you
candidates: 340 home-pillar stories plus 66 tier-1 sports matches.

**Caveat:** today's stories are all from the first ingest, between 01:30 and
03:42 UTC, so recency barely separates them. On a normal day it does more.

**Made-up profile, Home** (n = 9, so seed weight 12/21 = 0.57):

- **Love (4):**
  - "Megan Fox Announces Paid Content Subscription…"
  - "Thai Horror 'Rinrada' Scores Asia, Middle East…"
  - "NBA Franchise Valuations Ranking List…"
  - "NBA Valuations 2026: Warriors Lead at $14.5B…"
- **NI (3):**
  - "Drake Puts On a Show, and the Pats Win…"
  - "F1 fans think Kim Kardashian's presence…"
  - "Kim Kardashian's private jet…"
- **Opened (2):**
  - "Dutch hospitality firm dives into South Beach…"
  - "Historic Tides Hotel to reopen…"

Seeds are the drafted Home phrases (§2.3).

### 6.1 Ten stories

| pos | story | source | age h | affinity | 4·aff | follow | source w | topic w | base | recency | rank | note |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Miami Retail Construction Pipeline Hits Highest Level In 3 Years | bisnow-south-florida | 2.5 | +0.464 | +1.86 | 0 | 0 | 0 | +1.86 | 0.91 | 5.82 | |
| 2 | Creator Dhar Mann on Partnering With Hollywood Studios… | variety | 3.6 | +0.375 | +1.50 | 0 | +0.05 | 0 | +1.55 | 0.87 | 4.09 | |
| 3 | What to know about the landmark Warner Bros. Discovery sale | techcrunch | 13.7 | +0.467 | +1.87 | 0 | 0 | 0 | +1.87 | 0.59 | 3.81 | older, so below #2 despite more affinity |
| 4 | Five Organizations Launch Asian Producers Alliance In Busan Market | deadline | 1.6 | +0.260 | +1.04 | 0 | 0 | 0 | +1.04 | 0.94 | 2.66 | |
| 5 | LeBron on 10-point Sixers debut: Still adjusting… | espn-nba | 4.4 | −0.025 | −0.10 | +1.00 | 0 | 0 | +0.90 | 0.84 | 2.07 | tier 1 (LeBron; 76ers tier 2, max not sum) |
| 6 | Alabama Crimson Tide vs Tennessee Volunteers Opening Odds… | roll-bama-roll | 10.3 | +0.028 | +0.11 | +1.00 | 0 | 0 | +1.11 | 0.67 | 2.05 | Alabama, above the floor on its own |
| 7 | Georgia Bulldogs beat Crimson Tide in Tuscaloosa, fans throw trash… | yahoo-cfb | 3.3 | −0.043 | −0.17 | +1.00 | 0 | 0 | +0.83 | 0.88 | 2.02 | **floored** (raw 2.01; tied the 6th other story) |
| 204 | Gilbert Arenas Pushes Back on Colorism Accusation… | complex | 37.0 | −0.073 | −0.29 | 0 | +0.05 | 0 | −0.24 | 0.24 | 0.19 | mildly disliked and 37 h old |
| 237 | Drake Puts On a Show, and the Pats Win… | the-ringer | 5.1 | −0.429 | −1.72 | 0 | −0.05 | −0.07 | −1.83 | 0.82 | 0.13 | one of the made-up NI stories |
| 406 | Avery Lotz | axios-miami | 19,647 | −0.003 | −0.01 | 0 | 0 | 0 | −0.01 | 0.00 | 0.00 | an author page Google News dated 2024; recency sinks it |

**Positions 8 to 16** for context:

- 8 is the second floored Alabama story ("Georgia new No. 1 in AP poll").
- 9 is "IPO Boom Fuels Wall Street's India Bets" (affinity +0.20).
- 10 is "Theatrical Is Alive: YTD Domestic B.O. Surges".
- 11 to 16 are five more Alabama stories and one Georgia story. Those
  Alabama stories are ranked on their own score, carried by the tier-1 boost.

**Magnitudes to sanity-check:**

- A strong topical match (affinity +0.46, so +1.86) outranks a tier-1 follow
  with neutral affinity (+1.00) at equal age.
- A tier-1 follow outranks an affinity +0.20 story.
- An NI-like story (−1.8) sits about 3.7 e-folds below the top. That gap
  equals about 96 h of freshness, so a fresh disliked story stays below a
  liked one from the day before.

**One correction this exposed:** "Avery Lotz" (an Axios Miami author page)
shows that Google News can lead with very old pages. Recency handles it in
ranking. Owner decision 8 keeps such items out at ingest instead (a code
rule on Google News item age, no title rule).

### 6.2 Preview of the "10 Not Interested" property

The made-up profile above plus ten NI signals on one Haiku topic, scored
against a **held-out** story with the same topic:

| topic | NI stories | held-out story | position | affinity | topic w |
|---|---|---|---|---|---|
| `celebrity_news` | 9 distinct (one twice) | Sean 'Diddy' Combs' Son Says Father Refused to 'Sugarcoat'… | 181 → **254** of 406 | −0.091 → −0.206 | 0 → −0.27 |
| `game_recap` | 4 distinct, repeated to 10 | Ravens-Falcons Reactions, Week 5 Recap… | 156 → **264** of 406 | −0.267 → −0.493 | −0.07 → −0.19 |

The NI seed share falls from 0.57 to 0.39 in both. Most of the drop comes from
the embedding (affinity roughly doubles in the negative direction). The topic
weight adds the rest.

The real check tonight runs on real taps through `--rebuild`. I'll report
before and after positions from `feed_for_you` through `db_inspect`.

---

## 7. Known limits

- **gte-small after centering still separates topic better than intent.** A
  "Kalshi" business story and a "Kalshi promo" post sit close together. Rules
  and the follow table handle what embeddings can't.
- **Profiles are global per pillar.** A love on one NBA valuation story lifts
  other NBA stories a little, through the embedding. That is intended, and it
  is visible on /settings/feed.
- **No time decay on reactions in v1.** Tastes drift; a `REACTION_HALF_LIFE_D`
  constant can be added later without schema change, because `created_at` is
  stored.
