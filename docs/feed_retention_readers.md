# Who reads `stories.centroid` and `feed_similarity_log`

**Written 2026-11-02, for the owner's decision on two retention changes that are not made yet.** The changes would clear `stories.centroid` on old stories and cut `feed_similarity_log` to 14 days. Both are **on hold** (owner, 2026-11-02).

**The decision this doc supports:** whether clearing the centroid, or shortening the log, would break anything that reads them.

**How the readers were found** (2026-11-02, `main` at `36a63c6` plus the `feed-retention-21d` branch):
- A grep of `workers/`, `apps/web/`, `db/` and `docs/`.
- In the live database, every public function body and view definition, checked via `db_inspect`: `pg_proc.prosrc` and `pg_get_viewdef`.
- Result: **no database function or view mentions either name, and `apps/web` reads neither.** Every reader is worker code in `core/ledger.py`.

## `stories.centroid`

It is about 776 bytes on every story, and nothing ever clears it. On Nov 2 that was about 19 MB of the 43 MB `stories` table.

| Reader | What it reads | Stories in reach |
|---|---|---|
| `ledger.feed_join_story` (ingest) | `SELECT centroid … FOR UPDATE`: the running mean when an item joins a story | Stories still taking joins: the clustering candidates of the last 48 h by last seen (`cluster.CANDIDATE_HOURS`). A NULL centroid restarts the mean from the joining item (`_vec_parse(...) or tuple(vector)`). |
| `FEED_SUMMARY_QUEUE_SQL`, `FEED_TECH_SUMMARY_QUEUE_SQL` (only with `vectors`) | The summarizers' rank order | Unsummarized stories with a try left. In practice these are recent: anything 48 h stale leaves the queue (`summarize.STALE_AFTER_H`). |
| `ledger.feed_story_centroids` | The same rank order, read by id | The same queue. |
| `FEED_MEAN_SQL` (`feed_profile`) | The pillar's mean story vector, averaged inside the database | Kept stories first seen in the last 7 days (`rank.MEAN_WINDOW_D`). |
| `FEED_TO_SCORE_SQL` (`feed_profile`, `feed_score`) | The vector each story is scored on | Kept stories first seen in the last 72 h (`rank.WINDOW_H`). `feed_for_you` reads the stored `story_scores`, not the centroid, which is how Tech still lists stories for 168 h. |
| `FEED_SIGNALS_SQL` (`feed_profile`) | **Every reacted story's centroid, at any age**: the interest profile is built from it | Every story with a reaction. These stories are protected from the prune, so **their centroid must never be cleared.** |
| The test helper (`ledger` around line 3566) and `tests_live/test_feed_ledger.py` | Writes and reads its own rows | Test rows only. |

**What this means for clearing (if you approve it later):**
- The safe set is a story with:
  - no protection (no reaction, proposal, push, repo surfacing or trailer);
  - first seen more than 8 days ago;
  - no summary still pending.
- Every reader above stops looking at a story long before then, except `FEED_SIGNALS_SQL`, which reads only protected stories.
- **If you later react to a cleared story** (an old card still on a page), `FEED_SIGNALS_SQL` skips it (`centroid IS NOT NULL`), so that one reaction would not shape your profile. That's the one real cost.
- Estimated saving: ~19 MB now, and about 20 MB at steady state under the 21-day window.

## `feed_similarity_log`

About 105k rows and 14.7 MB on Nov 2. It is pruned at 30 days, and its rows cascade with their story or item.

| Reader | What it reads |
|---|---|
| `ledger._log_similarity` | Writes only: every join, near miss and cap overflow from ingest. `would_join` stopped with PR #63. |
| `ledger.FEED_PRUNE_SQL` `similarity_log_deleted` | Deletes rows after 30 days. |
| `ledger.feed_prune_dry_run` | Counts only. |
| `tests_live` (`test_feed_ledger`, `test_feed_review_sql`) | Their own rows. |
| By hand: `docs/drafts/feed-review-2026-10.md`, the `docs/feed_ranking.md` and MASTER_PLAN mentions, db/033's purpose | **Calibration:** the join thresholds (0.94 cosine / 90 fuzz) are re-tuned from real joins and near misses, read through `db_inspect`. |

**Nothing in the product reads it.** No ranking, page or brief does; it exists only for calibration.
- A 14-day window still holds about 2 weeks of pairs: around 2,500 joins and 26,000 near misses at the October rate. That is plenty for a threshold read.
- The only loss is the ability to look back 15–30 days at a pair.
- Estimated saving: ~7–8 MB at steady state.
- `db/033`'s header comment ("pruned after 30 days") would need updating; there is no CHECK or function to change.
