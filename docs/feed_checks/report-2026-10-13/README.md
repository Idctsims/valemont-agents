# Feed calibration report (Chat 3 Phase 1, Step 7)

**Window: 2026-10-12 03:35:56 UTC → 2026-10-13 02:07:10 UTC = 22.52 h (NOT 24 h).**
Per-day figures are shown raw and scaled by ×24/22.52 = ×1.0657.
Every figure came from `db_inspect` (the read-only role), with the window end
fixed at 02:07:10 UTC. Baseline at the window start: 28 MB DB, 736 stories,
873 items, last ai_usage id 164.

Files here: `near_misses.tsv` (all 2,744, both titles), `joins.tsv` (all 358),
`shadow_drop.tsv` (all 59 non-none).

## 1. Ingest

| | raw (22.52 h) | scaled per day |
|---|---|---|
| items ingested | 1,875 | 1,998 |
| stories created | 1,517 | 1,617 |
| joins | 358 | 382 |
| stories filtered by a rule | 63 | 67 |

- **Reconciles with the baseline:** 873 + 1,875 = 2,748 items and 736 + 1,517
  = 2,253 stories, the totals now, so nothing was deleted in the window.
  1,454 kept + 63 filtered = 1,517.
- **Sources:** 45 of 46 active sources produced items. The exception is
  crunchbase-news, which had 0 items but fetched cleanly every time (last
  status `not_modified`). All 46 had a good fetch in the last 2 h.
- **Google News decode failures:** 0 of 384 items.
- **Items by source:** the top six were yahoo-cfb 325, yahoo-nfl 289, variety
  89, bloomberg 77, complex 75 and golf-digest 74. The full list is in the
  session log.
- **Filtered by rule:**
  - `url_regex:75` (Complex shop/tag pages): 23
  - `title_regex:19` (numbered listicles): 5
  - `title_regex:22` (promo code): 5
  - `title_regex:10` (how to watch): 4
  - `title_regex:56` (birdie/eagle clips): 4
  - `tv channel`, `review:` and `live updates`: 3 each
  - `prediction:`, `betting promos` and `where to watch`: 2 each
  - 7 more rules: 1 each

## 2. Clustering

- **Joins by rule:**

  | rule | joins | avg cosine | avg fuzz |
  |---|---|---|---|
  | cosine | 199 | 0.956 | 73 |
  | both | 145 | 0.989 | 98 |
  | fuzz | 14 | 0.919 | 95 |

- **Stories:** 149 stories have more than one item; the largest has 9.
  **Cap overflows: 0.**
- **10 sampled merges** (the first 10 by md5(id), not chosen by hand): **9 of
  10 are the same event**, by my reading. The miss is Austin Hitt's winning
  putt joined to a story that began with his eagle on No. 10: the same
  tournament, a different moment.
- **Near misses 0.88–0.94: 2,744** (2,924 scaled per day), all listed in
  `near_misses.tsv` with both titles.

  | cosine | near misses |
  |---|---|
  | 0.88–0.89 | 877 |
  | 0.89–0.90 | 609 |
  | 0.90–0.91 | 508 |
  | 0.91–0.92 | 351 |
  | 0.92–0.93 | 242 |
  | 0.93–0.94 | 157 |

  - 565 of the 2,744 compare against a filtered story, so they are noise.
- **Is 0.94 right?** I labelled a reproducible random 20 from the top band
  (0.93–0.94, kept stories only), with "same" meaning the same news event:
  - 4 are the same event: JMU joins the American; Ravens–Falcons live; the
    Hargrave Achilles injury; RJ Mitte.
  - 1 is borderline: Purdy's MVP odds.
  - 15 are different articles about the same game or topic: previews against
    live chats, odds pieces, separate Brennan videos.
  - So 0.94 is holding. Lowering it would mostly merge different articles.

## 3. Shadow `drop` (nothing filtered)

Of the 1,454 kept stories from the window, **1,068 have no summary yet**
(§4), so their category is unknown.

| category | stories |
|---|---|
| none | 327 |
| listicle | 30 |
| recap_no_storyline | 17 |
| gossip | 8 |
| partisan_politics | 3 |
| ai_hype | 1 |

All 59 non-none stories are in `shadow_drop.tsv`. My reading of each
category:

- **listicle (30):**
  - Golf Digest course-directory pages: 19. Right to drop, and a rule would
    catch them better.
  - Schedules and power rankings: 5. Right.
  - **Boat International yacht pieces: 3, WRONG.** Yachts are one of your
    loves: the largest yachts at the Fort Lauderdale show, and the GX42 and
    B.Now 67M launches.
  - F1 midterm grades: arguable.
  - The other 2: MotoGP rider rankings and "6 winners and losers". Right.
- **recap_no_storyline (17):** mostly right. Arguably wrong:
  - 1290, Broncos: Payton calling plays is a storyline;
  - 2293, Mike Evans' viral trash talk;
  - 2369, betting lessons;
  - 2411, a blog post.
- **gossip (8):**
  - Right: 1307 (Below Deck) and 2358 (Tatum and Brown).
  - Arguable: 1328 (Shaq on Barkley), 1467 (Spiranac) and 2185 (Dolly
    Parton's sister and Blackburn).
  - Wrong: 1563 (John Oliver on Skydance, media business), 2277 (Beach Boys)
    and 1569 (a NASCAR wreck).
- **partisan_politics (3):**
  - Right: 1390 (registration gains) and 1438 (donations in the governor's
    race).
  - Arguable: 1308 (a court order on government ads).
- **ai_hype (1): wrong.** 1387 is a concrete product deal (Grok with Suno),
  which the prompt says is `none`.

**Clearly wrong: about 10 of 59 (17%), 3 of them a loved topic.**

## 4. Summaries

| | raw |
|---|---|
| stories asked | 777 |
| accepted | 768 (98.8%) |
| retired as headline-only (2 failed tries) | 9 |
| reached a second attempt | 55 |
| passed on the second attempt | 46 (83.6%) |
| decode failures | 0 |

- **"Passed on the second attempt"** includes both the same-run retry and a
  retry in the next run. The database doesn't record which.
- **The daily cap of 600 filled at 10:08 UTC on Oct 12** (05:08 Chicago).
  - There have been no summaries since then, about 16 h.
  - 1,084 stories are queued.
  - New stories arrive at about 1,617 a day, against the 600 cap.
  - `job_health` still shows `feed_summarize` as ok, so the stall is
    invisible on the health page.

## 5. AI cost (ai_usage, ids after 164)

| | raw (22.52 h) | scaled per day | × 30 |
|---|---|---|---|
| feed_summarize (107 calls) | $0.065275 | $0.069564 | $2.09 |
| feed_why (10 calls) | $0.002600 | $0.002771 | $0.08 |
| feed total | $0.067875 | $0.072335 | $2.17 |

- **The feed_why calls were my local `--why` runs during Phase 2.**
  Production has no `feed_why` job yet, because Phase 2 isn't deployed.
- **The scaled summarize figure is cap-bound and misleads.** Per story it
  costs $0.0000840:
  - at the cap (600 a day): $0.0504 a day, **$1.51 a month**;
  - uncapped at today's arrival rate (1,617 a day): $0.136 a day, **$4.07 a
    month**.
- **Total AI spend this month: $0.1907, 0.95% of the $20 cap.** Alerts at 80%
  and 100%: not sent.
- **Projected month:**
  - feed: $1.6 to $4.2;
  - Morning Brief: about $0.37 (one call at $0.012 in the window);
  - Wags: no calls in the window, so not projected here.

## 6. Database size

| | |
|---|---|
| baseline | 28 MB |
| now | 40.84 MB (42,822,803 bytes; MB = 2^20, as the `db_size` job counts) |
| change | +12.84 MB raw; +13.68 MB a day scaled |
| largest tables | content_items 8.6 MB, stories 3.8 MB, story_scores 2.0 MB |
| days to the 400 MB alert | (400 − 40.84) / 13.68 = **26.3 days, about Nov 8** |

This is a linear projection from the first day. Two things should slow
growth:
- the 72-hour embedding clear has not run on any feed item yet; the first
  ones age out around Oct 14;
- story_scores is pruned after 7 days.

The 28 MB baseline's unit was not stated. I've read it as 2^20 bytes.

## 7. Worker RAM

- **Pending:** the database doesn't hold it. Waiting for the owner's Railway
  dashboard figure.
- **The last measured figure was about 40 MB** (2026-10-09, before the feed).
  Embeddings run in the Supabase Edge Function, not on the worker.
- **Free-plan projection,** from railway.com/pricing fetched 2026-10-13
  (memory about $10 per GB-month, CPU about $20 per vCPU-month, $1 of credit a
  month, a 0.5 GB limit):
  - at 40 MB: about $0.39 a month for memory;
  - the $1 covers memory plus CPU while RAM × $10/GB + average vCPU × $20 stays
    under $1, for example 60 MB and 0.02 vCPU, which comes to $0.99.

## 8. job_health (latest state; the table keeps no history)

| job | last OK (UTC) | failures | alert |
|---|---|---|---|
| feed_ingest | Oct 13 02:08 | 0 | ok |
| feed_summarize | Oct 13 02:08 | 0 | ok |
| feed_prune | Oct 12 08:15 | 0 | ok |
| heartbeat, health_monitor, job_queue, watchdog | current | 0 | ok |
| db_size, capital_snapshot, goals_rollover, goals_monday_push, morning_brief, morning_brief_push | on schedule | 0 | ok |

- **No job has a skip or error note.** `last_error` is NULL on all of them.
- **Notifications in the window:** only two, "Set your week" (#206) and the
  Morning Brief (#207). That means no "needs attention" incident and no
  recovery, so no job failed twice in a row or went stale.
- **`feed_profile`, `feed_score` and `feed_why` have no rows.** They are Phase
  2 jobs, and Phase 2 isn't deployed.
- **Skips can't be counted here.** The table keeps only the latest note.

## 9. What went wrong in the window, recovered or not

1. **The 600-a-day summary cap starved the feed.** 73% of the window's kept
   stories have no summary, and nothing on the health page shows it. Still
   open.
2. **Shadow `drop` misclassified about 17% of its non-none stories**, three
   of them yacht pieces (a love).
3. **19 Golf Digest course-directory pages are kept stories on Sports.** A
   title or URL rule would catch them.
4. **The `yahoo-f1` Google News query brings in NASCAR.** 4 such stories
   carry the f1 sport, so they will show under the F1 chip.
5. **feed_why costs in the window are from manual local runs,** not
   production.
6. Nothing else went wrong:
   - no source failures;
   - no decode failures;
   - no health incidents;
   - no cap overflows;
   - the ai_usage id gaps (153 ids for 118 rows) are sequence values used by
     rolled-back test inserts, not lost rows.
