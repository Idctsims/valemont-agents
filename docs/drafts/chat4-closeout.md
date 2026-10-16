# DRAFT: Chat 4 close-out

**Status: draft, not applied.** Chat 4 is NOT complete. It closes only when
the six readings below are in (owner, 2026-10-16). When they land, fill each
`⟨reading⟩`, apply part A to CLAUDE.md and part B to MASTER_PLAN §6 (Chat 4),
mark Chat 4 complete in both, and delete this file, in one docs PR.

## The six readings that close Chat 4

| # | Reading | When | Result |
|---|---|---|---|
| 1 | Recs shift toward the Loves after the reactions: the next refresh's For you against day one's (`screening_rec_runs.top`, run 2 vs the next full run) | the next `screening_recs` run (04:30 Chicago Oct 16) | **Read 2026-10-16 ~21:30 UTC: shifted toward the Loves but collapsed (11/12 rom-com, 9 film / 3 TV). Finding recorded; fix = a diversity cap PR on Oct 17.** See "Readings 1–2" below. |
| 2 | Nothing marked seen appears in recs: For you against `v_title_state.seen` | across the next 2 refreshes | **1 of 2 refreshes read: none seen in it** (run 15). Second refresh: pending (Oct 17's daily run). |
| 3 | The real release push fires the first morning a watchlisted title drops | that morning, 08:30 Chicago | ⟨reading⟩ (not read this session: no watchlisted title has dropped yet; the Oct 16 08:30 job said "nothing on the shelves comes out today") |
| 4 | Worker RAM at rest, 6 h curve on #50's worker (boot 01:11 UTC Oct 16) | after 07:15 UTC Oct 16 | **CLOSED 2026-10-16 (owner): new baseline ~141 MB at rest** (#53's worker, 07:15–20:52 UTC, 818 readings, slope −0.07 MB/h). #50's worker was replaced at 05:03 UTC. See "Reading 4" below. |
| 5 | The egress meter's Oct 16 total (the `egress` job's note) | after 05:00 UTC Oct 17 | ⟨reading⟩ (so far: ~28.7 MB at 21:27 UTC Oct 16) |
| 6 | Phase 2 repo watch items: a Not interested on a repo topic drops that kind; no repo surfaces twice outside the resurface rule | the Oct 15–16 runs | **No repeats: passes. The topic drop: waiting on the owner's first repo Not interested (Oct 16 night); read the Oct 17–18 runs.** See "Reading 6" below. |

### Owner's calls on these readings (2026-10-16)

- **Reading 4, RAM: CLOSED. New baseline ~141 MB at rest** (#53's worker, 07:15–20:52 UTC, 818 readings, slope −0.07 MB/h). Closes Phase 3's and Phase 4's RAM items.
- **Reading 1, recs: shifted toward the Loves, but collapsed** (the refresh's top 12: 11 of 12 rom-coms, 9 films / 3 TV). The finding is recorded. **Fix: a diversity cap, its own PR on Oct 17.** Reading 2's second refresh is still pending.
- **Reading 6, the repo topic drop: waiting on the owner's first Not interested on a repo** (tonight). Read the Oct 17–18 runs after it. The no-repeat half passed.
- No code on Oct 16 for any of these.

### Readings taken 2026-10-16 (~21:30 UTC; db_inspect and the Railway logs; nothing written)

**Reading 4: RAM, flat; the new baseline ~141 MB at rest.**
- **Which worker.** #50's worker (deployment `ffae2443`, boot 01:10 UTC) ran only until 05:03 UTC, when #53's merge redeployed. So it has no readings after 07:15. The curve below is **#53's worker, deployment `0fa0c500`** (boot 05:04 UTC; it carried #50's code plus the Arsenal), which ran to 20:51 UTC, when #54 redeployed.
- **Source.** The `heartbeat` job's RSS note in the Railway logs, once a minute: 949 readings, 05:04–20:52 UTC.
- **At rest (heartbeat), 07:15–20:52 UTC, 818 readings:**
  - median **141 MB**, p10 136, p90 144, min 136, max 156 (the max is a heartbeat that landed inside an ingest);
  - hourly medians 136–144 MB;
  - **OLS slope −0.07 MB/h** (flat).
- **The first 6 h after 07:15 (07:15–13:15):** 136–156 MB, slope −1.2 MB/h (falling, not climbing).
- **Peaks:**
  - `feed_ingest` after 07:15: 144–156 MB (27 runs);
  - the day's highest, 160 MB at 06:36 UTC (`feed_ingest`, before the window, while the Arsenal's first batch was verifying);
  - `arsenal_verify` 149 MB at 06:04.
- **Verdict:** flat, nothing past ~160 MB at rest. **141 MB at rest is the new baseline**, closing Phase 3's and Phase 4's RAM items (owner's rule: a leak check, not a budget).
- **Since then:** the worker redeployed at 20:51 (#54), 21:02 (#55) and 21:24 UTC (#56; deployment `4e3c4eae`, the current one). Not read here.

**Readings 1–2: recs.**
- **Runs.** Day one is run 2 (full, 20:07 UTC Oct 15; 18 loves, pool 271).
  - The seen-it pass and the reactions came 01:47–05:16 UTC Oct 16: 13 Loves (Notting Hill, To All the Boys I've Loved Before, How to Lose a Guy in 10 Days, American Wedding, Pretty Woman; 1923, 1883, Marshals, Dutton Ranch, Shameless; The Godfather Part II and III) and 6 Not interested (Bonanza, Deadwood, Law & Order, True Beauty, Grace and Frankie, BEEF).
  - **The one full refresh since is run 15** (05:12 UTC Oct 16 = 00:12 Chicago, at #53's boot). The 04:30 Chicago run was skipped by design: "today's full run already happened". Pool 426, 30 loves, 3 not-interested at the time.
  - Reranks since: 16 (05:16) and 23 (21:28 UTC).
- **Day one's top 12 (run 2):** finance and legal dramas and the Yellowstone family. Too Big to Fail, The Big Short and Wall Street (because Margin Call); 1923, Marshals and Dutton Ranch (Yellowstone); Relay and Law & Order (Michael Clayton); American Psycho, Black Rabbit, The Gilded Age, Notting Hill. 6 movies, 6 TV.
- **The refresh's top 12 (run 15):** **11 of 12 romance or teen rom-coms**: The Wedding Date and Serendipity (because Notting Hill); The Kissing Booth 1–3, To All the Boys: P.S. I Still Love You, Love Is in the Air and True Beauty (To All the Boys I've Loved Before); Holidate and About Fate (In a Holidaze); Mad About You and Grace and Frankie (American Wedding). **9 movies, 3 TV.**
- **What moved toward the Loves.**
  - The rom-com Loves (Notting Hill, To All the Boys, American Wedding, How to Lose a Guy; plus the seed In a Holidaze) took over the list.
  - The Western Loves (1923, 1883, Marshals, Dutton Ranch) and the crime Loves (The Godfather II and III, Shameless) pulled nothing into the refresh's top 12.
  - The latest rerank (run 23, after the owner's 21:27 shelf moves) rebalances toward crime: State of Grace and The Sopranos (because The Godfather Part II); 25th Hour, BMF, Power Book II: Ghost and Black Rabbit (because Power, finished 21:27). It keeps 5 rom-coms.
  - **Reading:** the shift is real, but run 15 swung almost wholly to one cluster. Worth the owner's eye.
  - `top` is the stored score order, 9 movies to 3 TV. Whether For you's "4 of each kind" held on the page was not read here: pending.
- **Seen in recs (reading 2):** none. No title in run 15's (or run 23's) top 12 has `v_title_state.seen`.
  - Several are on the owner's **Finished** shelf now: The Kissing Booth 1–3, Holidate. All were finished after the run that listed them: run 15 is 05:12 UTC, and the finishes are 21:27:55–21:28:47 UTC.
  - Kissing Booth 3 (finished 21:27:55) is correctly absent from run 23 (21:28:16).
  - KB 1–2, Holidate and the Sopranos Love (21:28:58) came after run 23, and no rerank has run since. db/053's live exclusions should keep them off the page; that page read is pending.
  - **1 of the 2 refreshes read; the second is Oct 17's daily run.**

**Reading 6: repo watch items.**
- **No repo surfaced twice: passes.** 24 surfacings over Oct 14–16, 8 a day, 24 distinct repos.
- **"A Not interested on a repo topic drops that kind": cannot be read.**
  - The 24 repo stories have **0 reactions** of any kind.
  - The only tech reactions since Oct 14 are 2 Loves (Oct 14, not on repo stories) and 1 open.
  - The check needs a Not interested on a repo first. It stays pending.

**Today's egress (meter):** ~28.7 MB read by the worker on the Chicago day of Oct 16, as of 21:27 UTC (the `egress` job's note, carried across today's restarts). That counts the worker only: `tests_live` and `db_inspect` are not metered by it. The full Oct 16 total is read after 05:00 UTC Oct 17 (reading 5).

**Also open, not in the six (for the owner to place):**
- Phase 1 watch item 3: the why-it-matters Haiku cost for one full Chicago day (`ai_usage`, purpose `feed_why`). Pending since Oct 14; never read.
- Phase 4 checks not run: `e2e/screening-recs.spec.ts`, the screenshot gate, the test release push.
- Phase 4 checks not yet reported: In the news, the Brief's Screening line with Wags' "what to watch tonight", Trailers For you.
- The Oct 19 review items (CLAUDE.md, Reminders): Love effects, the shadow `drop`, duplicate merges, the summarizer's out-of-attempts stories, repo why lines that only name Claude Code.

---

## A. CLAUDE.md, the Current State rewrite (proposed)

Replace the Chat 4 detail in "Current State" (the Tech Hub block, the
per-phase build-order lines and the egress bullet) with:

> - **Chat 4 complete (⟨date⟩): Tech Hub and Screening Room.** Detail and evidence: MASTER_PLAN §6, Chat 4.
>   - **Live:** `/tech` (official changelogs and pages, Hacker News, GitHub repos with a "why this matters to you" line; at most 3 free-credit pushes a Chicago day); `/screening` (search, Want / Watching / Finished, Upcoming, where to stream with "Tonight on your services", trailers from YouTube RSS and TMDB, For you from the owner's taste with "Because you loved", the seen-it pass, release alerts at 08:30 Chicago, In the news on a watchlisted title).
>   - **Migrations:** db/045–db/053 applied (per file below). Next new file: `db/054`.
>   - **Worker:** RAM baseline ⟨reading 4⟩ MB at rest (owner's rule: a leak check, not a budget; investigate past ~160 MB). Egress ⟨reading 5⟩ MB/day by the meter (Free tier ~165; one push a day past 150).
>   - **Rules learned in Chat 4, kept:**
>     - A self-test fixture never writes NULL into a NOT NULL column, defaulted or not, and every story it builds sets `lead_item_id` (db/053's two failed pastes).
>     - Vectors leave the database in binary, never `::text` (`ledger._binary_vectors`; a cursor opened before registration must be passed too). A test pins it.
>     - A test reads its own rows (filter by its ids, in the database); full `tests_live -v` is the merge gate at ~2 MB a run.
>     - Every system job's note ends with "~X KB read" (`core/egress.py`).
>   - **Carried to Chat 5:** ⟨whatever of the "also open" list above is still open⟩.

## B. MASTER_PLAN §6, the Chat 4 summary (proposed)

### Migrations (all applied; verify detail in CLAUDE.md, Current State)

| File | Applied (UTC) | What |
|---|---|---|
| `db/045_tech_hub.sql` | Oct 14 02:32:51 (second paste; the first failed on the test source's slug) | The `tech` pillar, exclusive; source kinds `page`, `hn`; `official`, `config`; backfill and date precision; `feed_pillar_guard`; `feed_for_you` with category and official; `tech_credit_pushes` (3 a day, never twice); `ctx_tech`, `ctx_tech_brief` |
| `db/046_tech_official_decay.sql` | Oct 14 02:57:59 | Official tech stories decay at a 72 h half-life; push eligibility (`tech_push_eligible`) |
| `db/047_feed_hide_late_items.sql` | Oct 14 04:45:18 | 579 stories made only of late items hidden (filtered, never deleted) |
| `db/048_feed_dates.sql` | Oct 14 06:04:04 | Undated items (`none` precision) never push; the lead's precision for the web; the backfill window |
| `db/049_repos.sql` | Oct 14 21:07:08 | GitHub repos: `repos`, append-only snapshots and surfacings, 8 a day and the resurface rule by trigger |
| `db/050_repo_measured_baseline.sql` | Oct 14 21:12:56 | The resurface baseline is measured, never an estimate |
| `db/051_screening.sql` | Oct 15 01:35:19 | The Screening Room: the `screening` pillar, `titles`, `title_trailers`, append-only `watch_status` |
| `db/052_watch_current_grants.sql` | Oct 15 01:49:29 | `v_watch_current` select-only |
| `db/053_screening_recs.sql` | Oct 15 19:36:32 (third paste; two self-test fixture bugs, nothing applied) | Recommendations, the seen-it pass, release alerts, title entities and In the news, one trailer across two sources |

### Pull requests (all merged with merge commits)

| PR | Merged (UTC) | What |
|---|---|---|
| #28 | Oct 14 00:01 | Chat 3 close-out (db/044, staging-first, later reversed) |
| #29 | Oct 14 02:47 | Phase 1: Tech Hub (/tech) |
| #30 | Oct 14 03:11 | Push eligibility, official decay, new sources |
| #31 | Oct 14 04:44 | Summary caps per pillar group; summarize by rank; 48 h retirement |
| #32 | Oct 14 05:20 | The 72 h rule on every fetch; db/047 |
| #33 | Oct 14 06:18 | docs: Oct 19 watch item (out-of-attempts stories) |
| #34 | Oct 14 06:22 | Feed dates + db/048 |
| #35 | Oct 14 06:25 | docs: Phase 1 GREEN |
| #36 | Oct 14 21:06 | docs: Phase 1 watch items 1 and 2 closed |
| #37 | Oct 14 21:23 | Phase 2: GitHub repos (db/049) |
| #38 | Oct 14 21:43 | Trending sanity rules, estimate label, why cap |
| #39 | Oct 14 22:08 | Repo why: can/could with a named target |
| #40 | Oct 15 01:38 | docs: Phase 2 GREEN |
| #41 | Oct 15 01:38 | Star surge label; repo why in the second person |
| #42 | Oct 15 02:03 | Phase 3: Screening Room core (db/051) |
| #43 | Oct 15 02:11 | e2e/screening fix |
| #44 | Oct 15 02:18 | Repo why: second-person targets |
| #45 | Oct 15 02:18 | docs: db/052; Phase 3 owner checks |
| #46 | Oct 15 19:53 | Phase 4: recommendations (db/053) |
| #47 | Oct 15 19:58 | Egress fix 1: candidates in worker memory |
| #48 | Oct 16 00:05 | Egress follow-up: binary vectors, lean pools, test reads, the meter |
| #49 | Oct 16 00:05 | docs: db/053, #46, egress, the full tests_live gate |
| #50 | Oct 16 01:10 | Rescore a story that gained a pillar (stale cross-in flag) |
| #51 | Oct 16 01:28 | docs: Phase 3 trailers closed green; the RAM rule |
| #52 | ⟨⟩ | docs: Phase 4 owner checks; this draft |

### Owner decisions

**Planning (Oct 13):** no staging project (reversing the same day's "staging first"; the rolled-back self-test is the guard); Reddit dropped (self-service Data API closed Nov 11, 2025); Tech Hub on the 30-minute feed cycle; tech pushes only for free credits and promos, at most 3 a Chicago day; official items within one cycle, summarized in the same cycle; zero cross-in between Tech and Home/Sports.

**Phase 1 (Oct 13–14):** ChatGPT release notes left inactive behind Cloudflare (replaced by the developer community and the SDK releases); HN always community, never pushes; why-it-matters also for Tech's top 8 within the shared 20-call cap; the 7-day tech window with official items decaying at 72 h (db/046); push eligibility: not a first-fetch backfill and published within 24 h of first seen (db/046); the 72 h rule on every fetch and late-item stories hidden (db/047); undated items never push (db/048); summary caps per pillar group, summarize in rank order, 48 h retirement (#31). GREEN Oct 14; watch items 1 (latency, an inference recorded as one) and 2 (RAM, new baseline 138 MB) closed; 3 (why cost) ⟨⟩.

**Phase 2 (Oct 14):** a third append-only table for surfacings; the resurface baseline is measured, never estimated (db/050); repo why may say can/could only with a named target, in the second person (#39, #41, #44); weights as shipped, no tuning until 2–3 daily runs reacted to. GREEN Oct 14: screenshot gate, Wags' repo question, day one 7/8 concrete.

**Phase 3 (Oct 14–16):** TMDB's logo supplied by the owner; "Tonight" counts ads and free offers on the owner's services; search hits shelve directly. Owner checks green Oct 15; Trailers item CLOSED GREEN Oct 16 (7/7 live within one cycle, median 21.2 min); RAM ⟨reading 4⟩.

**Phase 4 (Oct 15–16):** the seeds and taste notes; seeds are seen and never recommended; the quality floor 6.8 / 200; "movies as much as TV" as a rule (4 of each); Finished and loved is the strongest signal; any reaction removes a For you card, with Undo; the Screening brief section is one line; In the news links out; #46 merged early and kept ("fine, keep it"). Owner checks green Oct 15 (day one, the seen-it pass, reactions, shelves); readings 1–3 ⟨⟩.

**The egress incident (Oct 15–16):** Supabase Free egress over quota by 0.76 GB (5.76 of 5 GB in the cycle ending Oct 18; ~1.7–1.9 GB a day since Oct 12). Cause: vectors read as text (~4.7 KB each), chiefly ingest's 48 h candidates every 30 min (~17 MB a run), then full tests_live runs (~90 MB each). Decisions: no Pro upgrade; full tests_live paused until fix 4; fix 1 alone first, with a daily reload and drift tests; fixes 3, 4, 2 and an egress meter (one push a day past 150 MB) in one follow-up; fix 5 only if the numbers say so; fix 6 (slower ingest) no. Measured: ingest 16.6 MB at boot → ~28–79 KB a run; a boot load 3.2 MB in binary; a full tests_live run ~2.2 MB, so full runs are the merge gate again. Oct 16 total ⟨reading 5⟩.

**Other rules (Oct 16):** a story that gains a pillar is rescored in the same ingest chain (the stored flag is fixed; the twin keeps reading it); the RAM band is a leak check, not a budget.

## C. Chat 5 starting point (proposed MASTER_PLAN / CLAUDE.md text)

> **Chat 5, the Arsenal, starts from the Screening Room hook.** Finished shows feed Arsenal cards (MASTER_PLAN, the build order's cross-pillar note: "finished shows feed Arsenal cards, which is why Screening (Chat 4) lands before the Arsenal (Chat 5)"; Chat 5's checklist: "Finishing a show produces new Arsenal cards tagged to it"). The hook is **`watch_status.finished_at`**:
> - `watch_status` is append-only (db/051); a move to `finished` is a new row whose `finished_at` the database sets (`watch_status_stamp`), so the date is never the client's. A re-watch is another row.
> - Read it through `v_watch_current` (security_invoker; the latest row per title: `status`, `finished_at`, `moved_at`, `is_test`) joined to `titles` (name, kind, genres, keywords, overview). Exclude `is_test`.
> - "Finished since the Arsenal last looked" is `finished_at > ⟨the Arsenal's own watermark⟩`; a title finished twice appears once per finish (one row each in `watch_status`).
> - The read-only role already reads both (db/051, db/052); the owner's session reads them through RLS. A finished title the owner also Loved is the strongest taste signal (Phase 4), which the Arsenal may weigh too.
> - No new screening table is needed for the hook; the Arsenal's own tables and any watermark are Chat 5's migration (next free number at the time).
