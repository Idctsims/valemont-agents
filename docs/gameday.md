# Game Day (Chat 7 Phase 3)

The Game Day pillar (`/gameday`), the game detail page (`/gameday/[id]`),
tonight's slate in the Morning Brief, and Wags answering from the same rows.
Migration `db/068` (read functions only); web `apps/web/src/lib/gameday/`,
`src/components/gameday/`, `src/app/(app)/gameday/`; worker `core/brief.py`
(the `sports_brief` section). Data: `sports_events` (`docs/sports_data.md`),
links: `docs/kalshi_mapping.md`.

**Status, 2026-11-05: built and tested on branch `chat7-phase3`. db/068 is
written, NOT pasted. Not merged** (owner's gate: Phase 2's 22:00 UTC
`kalshi_map` reading green first; then the db/068 paste and a full
`tests_live -v`, metered).

## 1. Owner decisions (2026-11-04/05)

1. **Tonight** = the game day, **05:00 to 05:00 America/Chicago**. Sections:
   Live now · Tonight · This week (the 7 game days after tonight).
2. **Order inside a day:** live first; Alabama; then the owner's five in
   order CFB, NFL, NBA, F1, golf; inside CFB ranked → SEC → national TV;
   then the other leagues (registry order), behind the **All sports** chip
   (the `/sports` chip pattern; the choice kept in the URL, `?sports=all`).
3. **Card:** matchup, start time or **TBD** (`time_tbd`, never the stored
   placeholder), network, score once live; a **gold mark** per owner's
   service; a blank broadcast reads **Not listed**. No Kalshi price (Chat 8).
4. **Detail:** score and clock; where to watch; the Kalshi event (a link to
   kalshi.com, or **Not linked** + the stored reason, or **Not listed
   yet**); venue and neutral site; an empty **Your bets** (bets attach in
   Chat 9); golf: the leader and Scheffler/Koepka's position and thru when in
   the field; F1: every stored session of the weekend.
5. **Brief:** at most 5 lines; Alabama always when they play; then the
   biggest national-TV games in the five; Sundays lead with the NFL; race
   weekends add session times; golf days add the followed players; a dead
   night says nothing.
6. **Wags** answers "what's on tonight", "when does Bama play", "where can I
   watch X" from real rows.
7. **Ranked** = ESPN's `curatedRank` as given (since the committee's first
   release that is the CFP ranking; AP before). No AP fetch; no "CFP" label,
   since no stored row says which poll it is.
8. **National-TV tiers** (approved Nov 5): 1 broadcast (ABC, CBS, NBC, FOX)
   > 2 national cable (ESPN, ESPN2, ESPNU, FS1, SEC Network, BTN, ACC
   Network, CBSSN, CW, USA Net, TNT, truTV, NFL Net, NBA TV, Golf Chnl) > 3
   anything else (streaming-only and regional) > 4 none.

## 2. Step 0 findings (2026-11-05, read only)

- **No worker parser change was needed.** Phase 1 already stores, per CFB
  game, `participants.{home,away}.rank` (36 of 136 rows ranked),
  `conference_id` (136/136; **SEC = `8`**) and the ESPN team `id`
  (**Alabama = `333`**, abbr `ALA`).
- `rank` is ESPN's `curatedRank`. Compared on Nov 5 with ESPN's rankings
  endpoint, it equals the **CFP committee** ranking (ALA 5, LSU 14), not
  the AP poll (9, 15).
- ESPN marks **ESPN+ (23), MW+ (2) and Peacock (1)** CFB games
  `national_tv`, hence the tiers (decision 8) rather than the flag alone.
- 97 distinct broadcast names were stored (listed in the Step 0 report);
  21 CFB rows had none yet (future games); tennis has none at all.

## 3. The services map

One file, `apps/web/src/lib/gameday/services.ts`, a comment per judgment
call so one line flips it. The page's gold mark and Wags' `streams_on` read
it; the database never knows it.

| ESPN name | service | |
|---|---|---|
| Netflix · HBO Max / Max · Paramount+ / Paramount Plus · Showtime · Starz · Prime Video / Amazon Prime Video / Prime | the same | exact |
| CBS | Paramount+ | **YES** (owner): Paramount+ streams the local CBS feed |
| TNT, truTV, TBS | HBO Max | **YES** (owner): TNT Sports on HBO Max |
| CBSSN | none | **NO** (owner) |
| Prime Video (Local) | none | **NO** (owner): in-market NHL feeds |

The mark is a solid pill, `bg-accent-strong` / `text-on-accent-strong`
(a pair `check:contrast` holds to AA in both themes).

## 4. db/068

No table, column or trigger. Every reader is `SECURITY INVOKER`, `STABLE`,
`search_path ''`; RLS on `sports_events` (db/065) does the owner check.
Readers: `authenticated` and `valemont_readonly`; `anon` nothing.

| function | what |
|---|---|
| `gameday_day(starts_at, tbd)` | the game day: a timed event's Chicago date with 00:00–04:59 the night before (**wall clock**: `local − 5 h`); a TBD event's **Eastern** date (ESPN files it at midnight Eastern, 23:00 Chicago the evening before, so the 05:00 rule would put it a day early) |
| `sports_tv_tier(broadcast)` | decision 8; the web twin `lib/gameday/tiers.ts`, compared by `tests/test_gameday.py` |
| `gameday_slate(day, all, tests)` | the page: one compact `jsonb` per row (nulls and false left out): the five for the day and the 7 after, plus what is live; `all` adds the other leagues for the day and live, never their week (~580 rows a week); test rows only with `tests` (the marker) |
| `gameday_live(ids)` | the poll: only the asked ids (the first 60), only `status`, `detail`, `clock`, `score` |
| `gameday_event(id)` | the detail: the row, full score, `kalshi {state, reason, ticker, series}`, venue, neutral site, an F1 weekend's sessions, a golf tournament's latest scored round |
| `ctx_sports_today()` | db/065's keys (`counts`, `live`, `owner_five`, `owner_five_more`) on the game day, with the time, every broadcaster and the score; plus `other_tonight`, `this_week`, `alabama_next` |
| `gameday_brief(day)` / `ctx_sports_brief()` | the slate, worded; provider **`sports_brief`** ("Game Day", sort 47, a brief section) |
| `add_test_gameday_event`, `set_test_sports_live` | `SECURITY DEFINER`, the owner + the verified marker, test rows only |

`sports_today` stays a non-brief provider (Wags); the Brief gets its own
provider, the pattern of `tech_brief`, `screening_brief`, `arsenal_brief`.

**The brief's rule** (`gameday_brief`): Alabama's game, if any; one F1 line
(today's and the rest of the weekend's stored sessions) and one golf line
(today's round; the leader after the last scored round; each followed
player's position, to par and thru) when there is one; the rest of the 5
slots go to the five's `national_tv` games, NFL first on a Sunday, then by
tier, best rank and start. Printed Alabama, games, F1, golf. NULL when
there is nothing. `core/brief.py` prints these lines **as given** (a
`DATA_LINE_SECTIONS` member, like the Arsenal's) and, because they replace
the model's after validation, `fit_data_lines` trims a data section from
the end (never below one line) if the brief would pass 260 words.

## 5. The pages

- `/gameday`: `PageHeader` "Game Day"; chips **The five** | **All
  sports**; Live now (only when something is) · Tonight (date) · This week
  (a heading per day: Tomorrow, then "Sat, Nov 7"). Empty states: "Nothing
  on tonight across the five. All sports shows the rest."; "Nothing
  scheduled across the five in the next 7 days."
- A row (`components/gameday/game-row.tsx`): hairline, the whole row a
  link; mono `NFL · 7:15 PM` (or the status once started); away over home
  with `No. N` ranks and "at"/"vs"; the score in a right column once live;
  networks or **Not listed**, then the gold marks.
- `/gameday/[id]`: back link; the score header (`EventScore`, live-polled);
  Where to watch; Leaderboard (golf); This weekend (F1; practice is not
  stored); Kalshi; Venue (+ Neutral site); Your bets.
- Nav: Game Day built at **`/gameday`** (the registry had reserved
  `/game-day`; the owner's spec names `/gameday/[id]`).

**Live updates (no Realtime).** The client polls `pollLive(ids)` (a Server
Action → `gameday_live`) every 60 s **only while the tab is visible and a
row on screen is live or due** (a timed game from 5 min before to 3 h after
its start, the worker's own hot window, so a tip-off flips to Live without
a refresh). Nothing live, nothing polled; a hidden tab polls once on return.
The detail page does the same for its one game.

## 6. Egress (measured)

On a scratch copy of production's rows (read 2026-11-05 ~20:30 UTC), the
JSON each call returns (PostgREST serves the same objects):

| read | rows | bytes |
|---|---:|---:|
| page, the five, Thu Nov 5 | 142 | 29,214 |
| page, All sports, Thu Nov 5 | 240 | 49,820 |
| page, the five, Sat Nov 7 | 183 | 37,737 |
| page, All sports, Sat Nov 7 | 293 | 60,442 |
| live poll, 20 games | 20 | 955 (~48 B a game) |
| live poll, 1 game | 1 | 35 |
| detail (Alabama at LSU) | 1 | 908 |
| `ctx_sports_today()` (Wags, every turn) | | 6,665 |

The first cut returned typed columns: 71,859 bytes for the five on Nov 5.
Compact `jsonb` without nulls is 2.5× smaller. **Pending:** the same
numbers measured on production after the paste (db_inspect,
`octet_length(json_agg(s)::text)`).

## 7. Tests

- **db/068's self-test** (on paste, rolled back; days in 2031, no real row
  there): tiers; the 04:59/05:00 and TBD days; the slate (five, All sports,
  week, test rows, compact keys); the poll (trimmed, only asked ids);
  the event (Kalshi, F1 weekend, golf standings); the brief (Saturday:
  Alabama first, a national CFB game with no team ids, F1, golf; Sunday:
  NFL first, 5 lines with the F1 line kept; a dead night and a
  regional-only night: NULL; test rows never); the helpers; grants; the
  providers.
- **Mutation-checked** (2026-11-05), each applied to the file and run on a
  clean clone: 18 mutations, every one red. One more (dropping the
  `[1:5]` slice) survives by design: the room count already caps the lines.
  The run found a real bug, fixed: `NOT (cfb AND '333' IN (NULL, NULL))`
  is NULL, so a CFB game with no team ids vanished from the brief, and the
  same NULL sorted it above Alabama in `ctx_sports_today`.
- **`tests/test_gameday.py`, 12:** the tier twin, Alabama/SEC ids in both,
  db/068's shape (invoker readers, two marker-gated definers), the
  `sports_brief` template, data lines replacing the model's, the fill, the
  word-cap trim, the fallback, a dead night.
- **`tests_live/test_gameday_sql.py`, 9:** grants and providers; the slate
  (boundaries, All sports, test rows, compactness); RLS (owner, stranger,
  anon); the poll; the event; the brief (Saturday, Sunday, dead and
  regional-only nights); the context; the helpers. **Passed 9/9 on the
  scratch copy; on production pending the paste.**
- **`e2e/gameday-unit.spec.ts`** (in `pnpm test:unit`): the game day
  (04:59/05:00 CST and CDT, the DST fall-back morning, TBD), labels, TBD
  rendering, sections, the order, matchups, the services map and its
  judgment calls, tiers, the poll set, patches, Wags' annotation.
- **`e2e/gameday.spec.ts`** (owner, phone, test rows behind the marker):
  Alabama on top, TBD, Not listed, the gold mark, All sports, a live score
  changing without a reload, the detail page, a 404. Logged out: 4/4.
- **`e2e/screens-gameday.spec.ts`**: phone 390 and desktop 1440, both
  themes, the real slate, no sideways scroll.

The scratch database: PG 17, production's `public` schema (`pg_dump
--schema-only` as `valemont_readonly`) and the sports tables' rows (`COPY`
as the same role), with Supabase's `auth.uid()`, roles and default function
grants stubbed. db/051's grants are not in a schema-only dump, so the
db/065/db/066 privilege tests are meaningless there (they fail on grants
only, never on db/068).

## 8. Found on the way (not changed here)

- **db/051 `e2e_marker_ok` answers NULL, not false, while `e2e_markers` is
  empty** (`hash = (SELECT … LIMIT 1)` is NULL), and `IF NOT NULL` does not
  raise: every marker-gated helper would then accept any marker. Production
  holds 1 marker row (read Nov 5), so it is not open today. db/068's two
  helpers `coalesce` it. The one-line fix to `e2e_marker_ok` itself is the
  owner's call (its own migration).

## 9. Pending (not verified)

- The db/068 paste, its NOTICE, the `migration_log` row, and the verify
  queries at the file's end.
- Full `tests_live -v`, metered, after the paste (the merge gate).
- The measured KB on production (§6).
- **The kalshi.com link form:** `/markets/<series>/<series>/<event>`,
  lower-case. kalshi.com answers scripted requests 429, so it was not
  opened from here; the owner's tap on a linked game is the check.
- The owner's phone checks (the done-when list): tonight across the five,
  TBD, the gold mark; Alabama on top; a live score updating; the detail
  page; the next Morning Brief's slate; Wags' "what's on tonight?"; the
  screenshot gate.
- TBS, FS2, NHL Net and ESPNEWS are not in the tier lists (the owner
  approved the list as proposed); add them if they show up.
