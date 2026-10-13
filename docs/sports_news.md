# Sports News (Chat 3 Phase 3, Step 0)

Design note for the Sports page, the sports profile and seeds, the LIV term,
event boosts and following people through moves. Written 2026-10-12 on
branch `chat3-phase3-sports` (off `chat3-phase2-ranking`). **Approved by the
owner 2026-10-12 17:27 Chicago, with the decisions below**; where this note
and those decisions differ, the decisions win. Step 1 is
`db/042_sports.sql`. It builds on `docs/feed_ranking.md` (Phase 2); where
this note is silent, that one holds. Verified dates and names:
`docs/sports_calendar_2026.md`.

## Owner decisions (2026-10-12 17:27 Chicago)

1. **D1 approved:** `ENTITY_CAP = 6` on Sports page one, `ENTITY_CAP_BOOSTED
   = 9` while a window for that entity is active.
2. **D2 approved:** sport-wide windows on Sports only; entity windows on both
   pages.
3. **D3 approved:** db/042 deletes the fixed 76ers and Ferrari tier-2 follow
   rows; the worker derives tier 2 for each tier-1 person's current team.
4. **D4 approved:** 30 NBA teams, 11 F1 teams and 3 tours as entities, no
   follow.
5. **D5 approved:** no repeat of a dismissed (person, kind, to) for 30 days.
6. **D6: no Hero World Challenge window.**
7. **D7: yes,** Chattanooga week boosts like the others.
8. **D8: yes, the summarizer's sport wins for multi-sport sources,** and the
   source's sport is the fallback when the summarizer returns `none`.
9. **LIV:** the tour split **includes LIV's business future** (funding,
   bankruptcy, merger, the PGA Tour–PIF deal). So "Is this the end of LIV
   Golf?" (1278) is spared. `TOUR_SPLIT_TERMS` (§3) gains `funding`,
   `future`, `end of liv`, `survive*` and `investor*`; `bankrupt*`, `merger`
   and `pif` were already in it.
   - **No keyword is added for the sublease story** (2728); the owner's
     Hidden taps handle filler.
10. **Moves coverage:** the summary queue takes **stories whose title names a
    followed person first** (a tier-1 or tier-2 person follow, or a derived
    team follow), before the normal publish-time order. This is tested in
    Step 4 (`tests/`: the queue order; `tests_live/`: the SQL pick).

## 0. Decisions put to the owner (Step 0, as proposed)

These went beyond the Phase 3 brief or differed from it. The owner's answers
are above.

- **D1. Sports page one is 26 of 30 Alabama today.** Measured with
  `feed_for_you('sports', 30, 0)` through `db_inspect` at about 22:00 UTC on
  2026-10-12.
  - Every sports score is exactly 1.00. The sports pillar has no seeds and no
    reactions, so affinity is 0, and Alabama's tier-1 follow outweighs
    everything else.
  - The seeds (§2) will spread scores out, but not enough on a weekend with
    65 Alabama stories in the window.
  - **Recommend a per-entity cap on Sports page one:** at most
    `ENTITY_CAP = 6` stories whose matched follows include the same tier-1
    entity. The overflow moves below page one, the same way `CROSS_CAP`
    works on Home.
  - **An active window for that entity raises its cap to `ENTITY_CAP_BOOSTED
    = 9`.** That makes "stronger in game week" visible as more Alabama
    stories on page one, placed higher (§6).
  - This follows the brief's "breadth beats repetition".
- **D2. Sport-wide windows apply on Sports only; entity windows apply on both
  pages.**
  - An F1 race weekend boosts F1 stories on Sports only.
  - Alabama's game week also boosts the Alabama stories that reach Home as
    tier-1 cross-pillar stories, still under Home's `CROSS_CAP = 5`.
- **D3. LeBron's "his team" follow is derived from his current link.**
  - db/039 seeded "Philadelphia 76ers tier 2" and "Ferrari tier 2" as fixed
    rows. A confirmed move would leave the old team followed.
  - **Recommend:** db/042 deletes those two follow rows, and the worker
    derives tier 2 for the current team of every tier-1 person, sports
    pillar only.
  - The result is the same today. After a confirmed move, the follow moves
    with him.
- **D4. Move targets must already exist as entities**, as your brief says:
  "both entities must resolve". So db/042 seeds, with no follow:
  - the 30 NBA teams, from nba.com/teams;
  - the 11 F1 teams, from formula1.com/en/teams;
  - three golf tours: PGA Tour, LIV Golf, DP World Tour.

  NFL and CFB get no team entities, because no person is followed there.
- **D5. A dismissed move is not proposed again for 30 days** (same person,
  kind and destination). Without this, the same story syndicated by five
  outlets re-proposes a dismissed move straight away.
- **D6. Hero World Challenge** (Dec 3–6, unofficial, no FedExCup points) is
  not a major, the Players or a signature event, so it gets **no window
  unless you say so.** No golf window is seeded for 2026
  (`docs/sports_calendar_2026.md` §3).
- **D7. Chattanooga week** (FCS opponent, Nov 21) gets the same game-week
  boost as the others. Recommend yes: it is still an Alabama game week.
- **D8. Out of scope, flagged only.** `al-com-alabama` is a `cfb` source,
  and a source's sport overrides Haiku's. So AL.com's NFL stories ("Jameis
  Winston, Jahmyr Gibbs highlight exciting Week 5 in the NFL") file under CFB
  and match Alabama through Haiku's "Alabama" entity.
  - **Recommend:** for multi-sport sources, Haiku's sport wins (a
    `sport_from = "model"` flag on the source).
  - It is one line in `sources.toml` and `ingest.py`, and it affects chips.
    Yes or no?

## 1. The Sports page

- **Route `/sports`.** Nav: Sports News, `built = true` in `lib/nav.ts`.
- **Tabs:** For you and Latest, as on Home.
  - **For you:** `feed_for_you('sports', …)`.
  - **Latest:** first-seen order, newest first.
  - Both exclude Neutral and Not interested taps, as Home does.
- **Chips:** All · CFB · NFL · NBA · F1 · Golf, from `stories.sport`. They
  are single-select, and the choice lives in the URL as `?sport=cfb|nfl|nba|f1|golf`
  (no parameter = All). The tab is in the URL too (`?tab=latest`), so a
  reload or a shared link keeps both.
  - **A chip filters before ranking, not after.** `feed_for_you` gains
    `p_sport text DEFAULT NULL`, and the floor, the caps and page one are
    computed within the chip's candidates. Otherwise the Golf chip's page
    one would be whatever golf happened to survive among All's top 30.
- **Stories with `sport` NULL appear under All only.** Today that is 124 of
  1,385 sports stories in the window. All come from multi-sport business and
  betting outlets:

  | Source | Stories |
  |---|---|
  | boardroom | 49 |
  | front-office-sports | 36 |
  | action-network | 31 |
  | sportico | 7 |
  | legal-sports-report | 1 |

  Haiku is told that sports business is `none`, so this is by design.
- **Cards and reactions:** the same card and the same Love / Neutral / Not
  interested controls, swipes and Undo toast as Home. A tap on `/sports` is
  written with `pillar = 'sports'`. The sports profile is built only from
  `pillar = 'sports'` reactions, and Home's only from `'home'`
  (`v_feed_reaction_latest` is keyed by story and pillar). The web test
  checks that a sports Love leaves Home's profile unchanged.
- **/settings/feed** gains a Sports section with the same plain-words profile
  as Home, plus "Proposed changes" (§5).

## 2. Sports seeds

Each phrase comes from your brief (`docs/chat3_owner_brief.md`, quoted with
its line). They go into `app_settings.seed_interests → feed → sports`, set
only while both arrays are still empty, as db/039 did for Home.

**Love (8):**

| # | Seed phrase (exact) | From the brief |
|---|---|---|
| 1 | `Alabama Crimson Tide football news` | l.49–50 "#1 by far: Alabama Crimson Tide. Alabama news always ranks near the top" |
| 2 | `LeBron James news, whatever team he's on` | l.53 "NBA: LeBron James first, whatever team he's on" |
| 3 | `Lewis Hamilton and Charles Leclerc at Ferrari` | l.55 "F1: Lewis Hamilton and Charles Leclerc first (both at Ferrari)" |
| 4 | `Scuderia Ferrari F1 team news` | l.56 "then Ferrari, then all F1" |
| 5 | `Scottie Scheffler and Brooks Koepka golf news` | l.57 "Golf: Scottie Scheffler and Brooks Koepka first" |
| 6 | `NFL trades, injuries and the playoff picture` | l.51–52 "League-wide news, trades, injuries, playoff picture" |
| 7 | `golf majors and big mid-majors: the Players and signature events` | l.58–59 "I watch every major and the big mid-majors (Players, signature events)" |
| 8 | `F1 race weekend: qualifying, the race and its storylines` | l.56 "Race weekends boost F1" |

**Not interested (2):**

| # | Seed phrase (exact) | From the brief |
|---|---|---|
| 1 | `plain box-score recaps with no storyline` | l.83–84 "plain box-score recaps with no storyline" (the same phrase as Home's) |
| 2 | `LIV Golf filler: LIV team events and lesser-known LIV players` | l.60–61 "LIV news only when it involves top players or the tour split" |

On NI 2: an embedding cannot carry "unless". A seed reading "LIV news that
doesn't involve top players…" would embed as *LIV news*, and so push down
the Rahm and McIlroy stories you want. So the seed names only the filler, and
the exception is the scoring term in §3.

`K_SEED = 12` is unchanged. With 8 love phrases, the first eight Love taps
take the seed share from 1.0 to 0.6.

## 3. The LIV term (a score term, not a filter)

```
liv_story  = story matches alias "liv" or "liv golf" (whole word, title + Haiku entities)
top_player = story matches a golf PERSON entity that has a follow
             (tier 1: Scheffler, Koepka; tier 3: the OWGR top 15, db/042)
tour_split = title + topics match TOUR_SPLIT_TERMS
liv term   = LIV_PENALTY = -1.0   when liv_story AND NOT top_player AND NOT tour_split
             0                    otherwise
```

- **−1.0 is about 26 h of freshness.** It cancels a tier-1 follow's +1.00.
  The term is stored in `components.liv`, and so it shows on
  /settings/feed's audit.
- **The term applies in both pillars.** A LIV story with sport NULL counts
  too: id 2728, a law-firm sublease story, came in with sport NULL.
- **How "top player" is decided:**
  - membership in the follow table, nothing judged per story;
  - **the OWGR top 15 as fetched 2026-10-12** (calendar doc §4), plus
    Scheffler and Koepka;
  - it changes only by a new migration from a fresh OWGR fetch.
  - Aliases are each player's full name. A surname is added only where it is
    distinctive in golf: mcilroy, fitzpatrick (full name only, "matt
    fitzpatrick", since his brother also plays), fleetwood, gotterup,
    schauffele, morikawa, hovland, rahm, henley, spaun. Cameron Young,
    Wyndham Clark, Sam Burns and Si Woo Kim match on full name only. Every
    alias is golf-only (`entities.sport = 'golf'`).
- **`TOUR_SPLIT_TERMS`**, lowercase, whole word or phrase, in `feed/rank.py`:
  `pga tour`, `dp world tour`, `european tour`, `tour card`, `tour status`,
  `membership`, `reinstat*`, `merger`, `framework agreement`, `pif`,
  `bankrupt*`, `ryder cup`, and (owner decision 9, LIV's business future)
  `funding`, `future`, `end of liv`, `survive*`, `investor*`.

**Applied to the 18 LIV stories in the database today:**

| id | Title (shortened) | top player | split | LIV term |
|---|---|---|---|---|
| 3051 | DeChambeau's LIV Escape Route Is Gone | — | — | **−1.0** |
| 3004 | death of LIV Golf … Ryder Cup headache | — | ryder cup | 0 |
| 2930 | European tour says anyone signing with LIV… | — | european tour | 0 |
| 2846 | Kinnings Forces LIV Golfers To Choose Tour Status | — | tour status | 0 |
| 2750 | DP World Tour decision … LIV Golf's future | — | dp world tour | 0 |
| 2728 | LIV Golf Bankruptcy Lands Law Firm in a Florida Sublease Hazard | — | bankrupt | 0 |
| 2503 | DP World Tour to deny 2027 membership to LIV 2.0 golfers | — | dp world tour | 0 |
| 1466 | Can LIV Golf survive without Jon Rahm? | Rahm | — | 0 |
| 1342 | Rahm will not return to LIV… | Rahm | — | 0 |
| 1333 | Rahm Planning to Leave LIV Post-Bankruptcy | Rahm | bankrupt | 0 |
| 1285 | LIV's Meronk wins Dunhill Links, secures European tour card | — | european tour | 0 |
| 1282 | Reed … road back to PGA Tour | — | pga tour | 0 |
| 1278 | Golf, but quieter: Is this the end of LIV Golf? | — | end of liv (decision 9) | 0 |
| 1277 | Top takeaways from LIV Golf's bankruptcy filings | — | bankrupt | 0 |
| 1272 | Rahm leaving LIV Golf… | Rahm | — | 0 |
| 972 | McIlroy Earns Praise … As LIV Path Clears | McIlroy | dp world tour | 0 |
| 970 | McIlroy's Meritocracy Rule Opens Door For LIV Golfers | McIlroy | — | 0 |
| 949 | Mickelson & DeChambeau's $1 Billion Pitch | — | — | **−1.0** |

- **The context:** LIV is in bankruptcy, so this month most LIV news *is*
  the tour split, and the term rightly spares it.
- **Two calls were arguable at Step 0.** The owner ruled on both (decision 9):
  - 1278 "Is this the end of LIV Golf?" is LIV's business future, so it is
    spared.
  - 2728, the law-firm sublease, stays spared by `bankrupt*`, and the
    owner's Hidden taps handle it.
- With the decision-9 terms, 3051 and 949 are the two stories marked down,
  judged on their titles. The 1466 match on `survive` was already spared, by
  Rahm.

## 4. How boosts enter the score

The same log units as the rest (`docs/feed_ranking.md` §2.6). A boost of `h`
hours makes a story rank as if it were `h` hours fresher:

```
boost_h = max(boost_hours) over windows active at now() that the story matches
          (0 when none; max, never a sum)
rank    = score + ln2 * boost_h / HALF_LIFE_H - ln2 * age_h / HALF_LIFE_H
```

**A window matches a story when:**
- `window.sport = story.sport`; and
- `window.entity_id` is NULL, **or** the window's entity is among the
  story's matched follows (`story_scores.components.follows`, or for a
  derived team follow, the team's name).

**Pillars (D2):** an entity window applies on both pages; a sport-wide window
on Sports only.

**Applied at read time** in `feed_for_you`, from `boost_windows` and `now()`,
so a window that opens or closes changes the order with no rescore. The
stored `score` stays time-free. `rank.order_for_you`, the Python twin,
gains the same term, and the test pinning SQL literals to `rank.py` covers
it.

**Sizes** (stored per window in `boost_hours`, so changing one is a row, not
code):

| Window | boost_hours | e-folds |
|---|---|---|
| Alabama game week | 12 | +0.46 |
| F1 race weekend | 12 | +0.46 |
| Golf major (none left in 2026) | 18 | +0.69 |
| Players or signature event (none left in 2026) | 12 | +0.46 |

## 5. Move detection and proposals

**The summary call gains one required field:**

```json
"moves": [{"person": "...", "from": "...", "to": "...",
           "kind": "trade|signing|release|retirement"}]
```

- It is usually empty. The prompt says to report a move **only when the
  story states it has happened** (signed, traded, released, retired). No
  rumours, no "could", no "reportedly in talks", no move from years ago
  told as background.
- For release and retirement, `to` is null.

**Validation in the worker** (Step 2). A move that fails any rule is
dropped, with no proposal:

1. **`person`** resolves (alias, whole word, sport = the story's sport) to
   exactly one `person` entity that has a follow.
2. **`to`**, for a trade or a signing, resolves to exactly one `team` or
   `tour` entity of the same sport (D4). It must be null for a release or a
   retirement.
3. **`from`**, when given, must resolve to the person's **current** link
   target. A story retelling last summer's move then proposes nothing.
4. **The move must change something.** `to` must differ from the current
   target.
5. **At most one open proposal per person.** A partial unique index on
   `(person_entity_id) WHERE decided_at IS NULL` enforces it in the
   database. A second candidate is dropped while one is open.
6. **D5:** no repeat of a dismissed (person, kind, to) within 30 days.

**Storage (`entity_link_proposals`, db/042):**
- It holds person, kind, from and to entities, `story_id` and `created_at`.
- `decided_at` and `decision` (`confirmed` | `dismissed`) are written once.
  A trigger refuses any other UPDATE, any second decision, and every DELETE.
- The owner gets SELECT.

**Deciding:** `decide_entity_link_proposal(p_id bigint, p_decision text)`
is SECURITY INVOKER, with the owner session.
- It writes the decision.
- **On confirm,** it inserts the new `entity_links` row (`valid_from` =
  the database's Chicago date, `confirmed_by_owner`). It ends the old link
  by inserting an `entity_link_ends` row (link_id UNIQUE, ended_on,
  proposal_id). Release and retirement end the old link and add no new one.
- **Append-only:** `entity_links` gains the frozen trigger it lacks today,
  so no link is ever edited. "Current" is `v_entity_links_current`: links
  with no end row.
- Because the function is INVOKER, the owner needs INSERT policies on
  `entity_links` and `entity_link_ends`. The policies are owner-only. They
  also mean the owner could insert directly through PostgREST, which is
  acceptable: it is the owner.

**The phone test with no fake real-world move:**
- `entities.is_test` is added, writable only through `add_test_entity()`
  with the verified e2e marker (the `add_test_story` pattern).
- `scripts.feed_test_proposal` creates "Test Player" `plays_for` "Test Team
  A", plus an open proposal to "Test Team B".
- /settings/feed shows it with the outlined `test` tag. Confirm moves the
  test player, and that is the "updates who plays for" check.
- **Test entities are never loaded as follows or move targets.** So nothing
  real can match them, and LeBron's real link is never touched.

**Cost (Haiku 5.5: $0.10 in, $0.50 out, $0.01 cache read per MTok;
`core/ai.py`):**

| Item | Tokens | Cost |
|---|---|---|
| Empty `"moves":[]` per story | about 6 output | |
| Per 10-story call | about 60 output | $0.00003 |
| System prompt addition | about 200 | cached: about $0.000002 a call; one cache rewrite when it changes |
| A real move | about 40 output | rare |

- **At today's 140 summarize calls** (the busiest day so far, the first full
  ingest), that is about **$0.004 a day, under $0.15 a month.** Today's
  summarize total was $0.089 for 140 calls (`ai_usage`, 2026-10-12).
- **Limit:** only summarized stories carry `moves`. Today 45% of sports
  stories in the window have a summary; `boardroom` has 0 of 49.
  Unsummarized stories match follows by title only and never propose a
  move.

## 6. Alabama: game-week boost and the floor together

The order, inside `feed_for_you`:

1. `rank = score + boost − decay`. The boost applies before anything else,
   so a game-week Alabama story is simply a fresher one.
2. **The floor** (unchanged, `FLOOR_MAX = 3`, `FLOOR_POSITION = 6`): the best
   3 Alabama stories under 48 h rank at least as high as the 6th-best
   non-floor story. With the boost, more of them clear that bar on their own.
   The floor then lifts only those that don't, so it still guarantees the
   top 3 in the top ~9.
3. **The entity cap** (D1, Sports only): at most 6 Alabama stories on page
   one, 9 while a window for Alabama is active. Floored stories always keep
   their place within the cap.
4. **Home:** `CROSS_CAP = 5` still bounds Alabama on Home. The boost changes
   which Alabama stories make it and how high, not how many.

What "stronger in game week" means on the phone:
- **On Sports:** up to 9 Alabama stories on page one instead of 6, each
  about 12 h fresher in rank.
- **On Home:** the same up-to-5 Alabama stories, placed higher.

The Tennessee window is active from today (calendar doc §5). The Step 2
report gives Sports page one with and without the windows, from one
snapshot.

## 7. Schema summary (db/042)

| Object | What |
|---|---|
| `boost_windows` | id, sport, entity_id (nullable FK), label, starts_at, ends_at (CHECK > starts_at), boost_hours (CHECK 0 < h ≤ 48), source_url, fetched_at. Owner SELECT; worker writes; seeded from the calendar doc §5. |
| `entity_link_proposals` | §5, write-once decision trigger, owner SELECT, partial unique index per person. |
| `entity_link_ends` | append-only, link_id UNIQUE. |
| `entity_links` | frozen trigger (new), owner INSERT policy. |
| `entities.is_test`, `add_test_entity()` | §5. |
| Entities | 14 OWGR people (tier 3, golf); 30 NBA teams, 11 F1 teams, 3 tours (no follow); `Ferrari` already exists. |
| `follows` | delete the 76ers and Ferrari tier-2 rows (D3). |
| Sports seeds | §2. |
| `feed_for_you` | adds `p_sport`, the boost, the entity cap. A changed signature means DROP and CREATE; `ctx_feed` calls it by name, so it is unaffected. |
| `decide_entity_link_proposal()` | §5. |
| `ctx_sports_feed()` | provider `sports_feed`, `brief_section = false`. It returns Sports' top 5 (title, source, sport, summary), active windows (label, ends_at) and open proposals (test ones never). |
| Self-test | A rolled-back DO block covering every trigger and function: boost only inside a window, the cap, a confirm ending the old link, a second decision refused, and `is_test` invisibility in `ctx_sports_feed`. |

## 8. Known limits

- **Alias matching is literal.** "Hamilton" outside F1 never matches, because
  of the sport check. But Haiku's entity "Alabama" on an AL.com NFL story
  does match (D8).
- **OWGR and the calendars are snapshots.** Nothing refreshes them
  automatically, so a new season or a ranking shift needs a migration. That
  is deliberate: every row names its source and fetch date.
- **Moves depend on summaries.** At 45% sports summary coverage, a move that
  is only in unsummarized stories goes unproposed until a summarized story
  carries it.

## 9. Scope addition: /news and categories (owner, 2026-10-12 21:56)

- **/news** is Home's whole feed (pillar home): For you / Latest and the
  category chips All, Deals & Capital, Hospitality & Real Estate, Sports
  Business, Film/TV & Music, Luxury & Marine, Betting & Markets, kept in the
  URL (?category=, with ?feed= and the page). A "Sports" link goes to
  /sports. Nav: News, inside Home. Reactions there are pillar home: they
  train Home's profile.
- **Home** shows For you's top 10 and "See all news". Its tabs moved to
  /news; an old Home feed URL (?feed=latest, ?latest=N, ?foryou=N)
  redirects to the same view there.
- **stories.category** (db/042, CHECK on deals | hospitality |
  sports_business | film_music | luxury_marine | betting | other; NULL until
  categorized). feed_for_you gains p_category (default NULL), filtered before
  ranking like the sport chip. In v_feed_stories.
- **The summary call** returns one category from the same enum. A story
  keeps the first category it gets.
- **The backfill is Haiku, not a keyword mapping** (owner, 2026-10-12
  23:38). After the paste, a one-off categorize pass
  (`scripts.feed_categorize`; workers/feed/categorize.py) sends every
  uncategorized Home story's headline, summary and outlet, 25 a call, with
  the summary call's own definition and enum (feed/category.PROMPT). It is
  non-critical under the AI budget guard, drops any answer outside the enum
  (never a guess), and writes NULL categories only. It reports counts, ten
  samples per category, and the cost recorded in ai_usage (expected about
  $0.05). A topics-mapping draft from 2026-10-13 was withdrawn.