# Categories: the shipped state (2026-10-13, final round)

Owner, 2026-10-13: "Final categories round; we ship after this whatever the
result." This README records what landed and the misfits left for later.
No further tuning rounds.

## The rule as shipped (`workers/feed/category.py`)

- The enum and its definitions (`PROMPT`) are unchanged from the 16:37
  round.
- Both calls (summary and categorize) ask every story one yes/no question,
  returned as `money` (a required boolean):

  > Is this primarily about the BUSINESS of sports: money, ownership,
  > deals, media rights and broadcasting, audiences and ratings,
  > valuations, or how a league or sport is structured and governed
  > (schedules, eligibility, NIL, expansion)? Games, coaching, player
  > performance and fantasy are no.

- `decide`: the answer decides (yes -> `sports_business`, no -> `other`)
  for a story that has a sport (`stories.sport`), was answered
  `sports_business`, or was answered `film_music` or `other` and has a
  sports entity among its entities. A sports entity is a league, team,
  program or tour from the entities table, matched by name or alias as the
  WHOLE entity string, case-insensitive (`sports_entity`; exact, because F1
  team aliases are common words: williams, mercedes, audi). `betting` keeps
  its own chip. Anything else keeps the enum answer; a decided story with
  no boolean answer is left as it was, never guessed.
- New stories get this from the summary call once Phase 3 is deployed.

## The final re-run

```
..\venv\Scripts\python.exe -m scripts.feed_categorize --money
```

- **Id set** (`categorize.money_set` over `ledger.feed_money_jobs`): the
  gate applied to each story's current category: **104** stories.
- **Result:** 5 Haiku calls, **9 moved, 95 unchanged**; every move is in
  `run.txt` (the script's full output; it is the log).
- **Cost:** **$0.002409**, `ai_usage` purpose `feed_recategorize`, 5 rows
  (ids after 827), read back via db_inspect.

Moves: 5734 (NFL 18-game push), 3506 (Harris eligible for NFL), 5386 (NWSL
commissioner) and 2786 into `sports_business`; 1559 (NCAA concussion
funding) out to `other`; and four LeBron James pieces into
`sports_business` (2816, 2818, 2820, 2821).

**Shipped counts** (Home, kept, real): film_music 477, other 249, deals 129,
hospitality 112, luxury_marine 69, sports_business 55, betting 15, NULL 35
(new stories since the passes; production categorizes them once Phase 3 is
deployed).

## The categories day, in cost

| pass | stories | calls | cost |
|---|---|---|---|
| first categorize (NULL only) | 1,106 | 45 | $0.022812 |
| re-run sports_business, betting | 161 | 7 | $0.003232 |
| re-run film_music, other | 730 | 30 | $0.015236 |
| money question | 136 | 6 | $0.003066 |
| business-of-sports question (this) | 104 | 5 | $0.002409 |
| **total** | | **93** | **$0.046755** |

## Misfits left for later

1. **The entities table has no broadcasters, and few teams outside the NBA
   and F1.** ESPN, NBC, Fox, Prime Video, MLB, MLS, NWSL, WNBA, NCAA, NFL
   teams (except via "NFL"), and Nielsen are absent, so the widened gate
   never reached these stories, which keep their enum answer:
   - in `film_music`, broadcasting stories: 1309 and 2524 (Pat McAfee on
     ESPN), 1339 (Skydance's sports strategy), 2920 (Disney's Super Bowl
     alt-casts), 5214 (Tirico on NBC's Jordan interview);
   - in `other`, money and structure stories: 2733 (Dodgers outspend MLB's
     final four; entities "Dodgers", "MLB"), 5064 (Nielsen soccer
     audiences), 5687 (the NCAA eligibility case).
   A later fix is entity rows (broadcasters, MLB/NFL/WNBA/NWSL leagues and
   teams, NCAA), one migration, then one more run of this pass.
2. **Haiku's yes on player-centred pieces.** Four LeBron James interviews
   and features moved into `sports_business` (2816 picking the Sixers, 2818
   golf, 2820 Jay-Z, 2821 free agency and legacy), against "player
   performance ... are no". 2829 (Pat Connaughton's real-estate business)
   and 2809 (Jaylen Brown on NBA wealth) are right.
3. **1340** (Daylight Saving Time and the golf industry) is answered no in
   both question rounds; it stays `other`.
4. **711** (Club Sportico: advertising when your team loses) sits in
   `betting` from the money round; not a betting story.

Nothing here was corrected by hand.
