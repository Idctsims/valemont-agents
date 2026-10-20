# Feed review, due Oct 19 (read 2026-10-20)

**Owner's calls (2026-10-20), built on branch `feed-review-fixes`:**

| # | Call | Status |
|---|---|---|
| 1 | **The Love-effects watch is extended to Sun 2026-10-26.** The owner reacts on Home and Sports this week. | Recorded; read on Oct 26 |
| 2 | Retire the shadow drop: removed from the summary prompt and schema; `drop_category` kept as history (a new summary never overwrites it) | Built |
| 3a | Shadow only: cosine ≥ 0.92 plus two shared proper names is logged as `would_join` (db/058); clustering unchanged | Built; **report after a week** |
| 3b | The official "also announced" link | **Deferred** (owner, Oct 20); no code, no migration |
| 4 | Store each summary rejection's reason: `summary_rejections` (db/058, append-only) | Built |
| 5 | Dev-workflow why-lines must name what the repo lets you do; vague verbs and tool-name-only lines are refused | Built; day one re-run after deploy |

Read-only. Proposals only, no code. Every figure was read with `db_inspect` on
2026-10-20 unless it says otherwise.

**The window is Oct 12–19.** The worker stopped at 20:06 UTC Oct 19, when the
Railway trial ended, so the week ends there.

Railway's logs could not be read: the worker is removed and the CLI returned
no lines. Anything that needed them is marked **pending**.

## 1. Love effects on /settings/feed

**Finding.** Only the Tech pillar had real Loves in the window, so it is the
only place an effect can be measured.

**Reactions** (latest reaction per story, real stories only):

| Pillar | Real reactions in the window |
|---|---|
| Home | 1 Love, 4 Not interested, 4 Neutral, 4 Undo |
| Sports | none (its 2 Loves on Oct 13 were on test stories) |
| Tech | 7 Loves, 2 Not interested, all on Oct 16's repos |

**Home.** The profile did not change all week: daily rebuilds Oct 12–19 all
show 1 Love and 4 Not interested. The top topics stayed `content_acquisition`
and `tv_market` at 0.067. With one Love, there was nothing to rise.

**Tech: yes, Loved topics rose.**
- The profile rebuilt on Oct 17 (row 78, after the Oct 16 Loves) has top
  topic weights `codex` 0.167 and `claude_code` 0.146. The day before (row 69)
  it had `git_lfs` 0.023 and similar.
- Stories tagged `codex` or `claude_code`:
  - mean percentile rank among tech scores went from **0.754** (profile 69,
    n = 22) to **0.867** (profile 78, n = 25); 0.856 under profile 84 (Oct 19);
  - the same 19 tagged stories, scored under both profiles, gained **+0.535**
    in score, against **+0.024** for the 169 others.

**Proposed fix:** none to the ranking. To measure Home and Sports, the owner
reacts there; the watch extends one week to Oct 26 once the worker is back.
**Migration:** no.

## 2. The shadow `drop` classifier

**Finding: fewer clearly wrong labels, but the wrong ones now hit a tier-1
follow.**

- **Volume.** Oct 13–19, kept real stories:
  - 5,535 `none`;
  - 1,592 non-none: `recap_no_storyline` 753, `listicle` 500, `gossip` 214,
    `partisan_politics` 116, `ai_hype` 8, `crypto_price` 1;
  - 629 never classified.
- **Error rate.** A reproducible sample of 80 non-none (ordered by
  `md5(id)`), judged on whether dropping the story would be right:
  - **7 clearly wrong (~9%;** a sample this size can't tell that apart from
    the Oct 13 figure of ~17%, 10 of 59);
  - 7 more arguable.
- **Four of the 7 are Alabama, a tier-1 follow:**
  - 22005, DeBoer reacts;
  - 15549, 3 predictions;
  - 22099, 3 takeaways;
  - 22719, Alabama falls in the coaches poll.
- **The other 3 are business stories the owner's seeds love:**
  - 5373, Nick Cannon's podcast moves to BET as a TV show;
  - 7671, the Golden Beach mansion buyer;
  - 4872, Axios's affordable-housing development.
- The sample is in the session scratchpad; it can be re-drawn with the query
  above.

**Recommendation: retire it.**
- Live, it would drop followed-team stories and loved-topic business stories.
- Ranking already sinks recaps and listicles the owner doesn't react to.
- The URL rules already caught the clear junk (Golf Digest directories).

**Proposed fix:** drop the `drop` field from the summarizer's prompt and schema
(`feed/summarize.py`), leave `DROP_LIVE` false, keep the column as history.
**Migration:** no.

## 3. Duplicate merges

### 3a. Skins Game-style splits

**Finding: they continue.**
- Near-miss counts, Oct 13–19 (the log starts at cosine 0.88; a join takes
  0.94, or 0.90 with title fuzz ≥ 90):
  - 29,725 near misses;
  - 3,447 at cosine ≥ 0.92;
  - 1,077 of those with fuzz ≥ 70.
- In a sample of 30 at cosine 0.925–0.94, **about 10 are the same news split
  in two:**
  - Georgia's upset loss to Auburn (25949 / 22079);
  - A.J. Jackson's suspension (8728 / 6641);
  - Movistar Plus and Skydance (6385 / 4844);
  - Bills over Rams in OT (6483 / 4708);
  - Presley Gerber (26815 / 22792);
  - Aaron Philo's injury (13192 / 10835);
  - and same-game recaps: Packers–Cowboys, Saints–Giants, Korn Ferry.
- Their fuzz scores are 53–78, so neither join path fires.
- Two near misses in the sample were junk pages: a clothing shop's product
  pages (BSRABBIT) and PGATour.com's home page.

**Proposed fix:** a third join path: cosine ≥ 0.92 **and** at least two shared
proper names (the capitalized tokens the summarizer's copy check already
isolates). Run it in shadow first, logging each would-be join, then decide.
**Migration:** none for the shadow run. Going live needs db/058 to add
`entity` to `feed_similarity_log.joined_by`, whose CHECK allows only `cosine`,
`fuzz` and `both`. Separately, two URL rules for the junk pages. Rule rows go
in by migration (db/036–038, 043), so those need one too.

### 3b. Why OpenAI's 8317 and 8941 did not cluster

**Finding: the titles are paraphrases, and clustering reads titles only.**

| Story | Source | Title | First seen (UTC) | Published |
|---|---|---|---|---|
| 8317 | `openai-api-changelog` | "Simplified API usage tiers from five to three: Build, Launch, and Grow." | Oct 14 02:48 | dated Oct 6 |
| 8941 | `openai-community-announcements` | "Update to OpenAI API Rate Limits" | Oct 14 03:16 | Oct 6 18:41 |

- Both are first-fetch backfill items, from two different official sources.
- There is **no similarity-log row** for either item, so their title cosine
  was below 0.88. Title-only vectors cannot see that these two report one
  change.
- After summarization the two got **identical topics**: `rate_limits`,
  `usage_tiers`, `api_pricing`.

**Proposed fix:** a post-summary "same announcement" link for official tech
items: same organization, published within 24 h, identical or ≥ 2 shared
topics. The later story is shown under the earlier one ("also announced
in…"), not merged. Items are frozen, so a link is the only safe shape.
**Migration:** yes, a new append-only `story_links` table.

**DEFERRED (owner, 2026-10-20).** Not built. Revisit when official tech
duplicates recur.

## 4. Headline-only summaries (Variety, Deadline, THR)

**Finding: entertainment trades run out of attempts four to five times as
often as other Home sources.** The rejection reasons are **pending**.

| Source | Summarized | Out of attempts (2 calls) | Rate | Mean teaser, ok / failed |
|---|---|---|---|---|
| Variety | 450 | 35 | 7.2% | 345 / 338 chars |
| Deadline | 322 | 24 | 6.9% | 347 / 339 |
| THR | 289 | 11 | 3.7% | 146 / 176 |
| Other Home | 1,620 | 23 | 1.4% | 77 / 179 |

- Kept real Home stories first seen Oct 12–19.
- The reasons live only in Railway's `feed summarize: story N:` log lines, and
  the database keeps no rejected text or reason. Railway's logs could not be
  read (see the top).
- Teaser length does not separate failures from successes at Variety and
  Deadline (~340 characters either way), so "long teasers" alone is not the
  cause.
- The working guess is the copy check. Trade headlines quote show and deal
  names, and many such names are lowercase or numeric ("first-look deal",
  "Season 2"), which the capitalized-name exemption does not cover. **This is a
  hypothesis, not a measurement.**

**Proposed fix:** keep each rejection's reason in the database, so the next
review does not depend on log retention; then tune the copy check on the
evidence. **Migration:** yes, either a `summary_rejections` table or a
`last_rejection` column on `stories`.

## 5. Repo why-lines that only name Claude Code

**Finding: rare, but day one regressed.**

- **Since Oct 14:** 48 real surfacings with a line; **20 mention Claude Code.**
  - 18 of the 20 name a concrete action. For example, "search and download 3D
    assets from 23 sites in one call" and "check your Cursor and Claude Code
    MCP configs for poisoning and leaked secrets".
  - **2 name only the tool or a vague verb:**
    - anthropics/claude-code (Oct 17): "Your Claude Code is the agentic
      terminal tool you build with…";
    - chaenmasahiro0425/claude-security-scan (Oct 19): "…can sharpen your
      Claude Code workflow…".
  - So it is not a pattern: 2 of 20, about 10%.
- **Day one regressed after grading (a finding outside the brief):**
  - The 8 Oct 14 repos were graded 7/8 concrete after the hand regeneration
    (`docs/tech_hub.md` §7.2b; `ai_usage` 1234).
  - They now read **"General interest." on 7 of 8.** Only kuratlielia/arc-library
    kept its line.
  - All 8 were regenerated at **02:13 UTC Oct 15** (`ai_usage` 1312 and 1313).
    The queue only takes stories with `why_generated_at IS NULL`, and only the
    by-hand `feed_reset_repo_why` clears it. So a hand `--again` ran after the
    grading.
  - **CLOSED (owner, 2026-10-20).** The Oct 15 session that fixed the
    repo-line bug (`dc8cae6`, 02:14:05 UTC) ran it. That commit's message
    says so: regenerating the day's 8 with PR #41's second-person prompt
    stored 7 "General interest." over good lines, because the bare-key
    lookup refused targets like "your dev workflow". The exact command was
    not recovered: it is not in the owner's PowerShell history, nor among the
    commands in that session's transcript between 00:00 and 02:13 UTC.
  - **The guard:** every by-hand regeneration now writes a `regenerated`
    event naming what, which ids, each id's old → new kind, and how it was
    run (`ledger.record_regeneration`; `feed_profile --why/--repo-why
    --again`, `morning_brief --generate` over an existing brief). A static
    test fails if any other code resets a stored line without it.
- **Every day since:** 3, 2, 1, 2, 2 of 8 "General interest." (Oct 15–19).

**Proposed fixes:**
- The dev-workflow target: "name what it lets you do; 'sharpen' or 'improve
  your workflow' is not an action". **Migration:** no.
- Day one: one `--again` for the 7, if the owner wants them back.
  **Migration:** no.

## Summary

| # | Finding | Proposed fix | Migration |
|---|---|---|---|
| 1 | Tech Loves lifted their topics (percentile 0.754 → 0.867); Home and Sports had no real Loves | None; extend the watch to Oct 26 | No |
| 2 | ~9% clearly wrong in a sample of 80, 4 of 7 on tier-1 Alabama | Retire it: remove `drop` from the prompt | No |
| 3a | Same-news splits continue at cosine 0.92–0.94 with fuzz < 90 | Shadow a cosine ≥ 0.92 + two shared proper names join | Live: yes (db/058, the `joined_by` CHECK) |
| 3b | OpenAI 8317 / 8941: paraphrased titles, title-only clustering | A post-summary "also announced" link for official items | Yes (`story_links`) |
| 4 | Variety, Deadline and THR fail 4–5× as often; reasons pending | Keep each rejection's reason in the database, then tune | Yes |
| 5 | 2 of 20 Claude Code lines vague; day one regressed to 7/8 general | Tighten the target; one `--again` for day one | No |
