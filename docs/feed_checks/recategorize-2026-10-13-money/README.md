# Re-categorize: the money question (2026-10-13, third pass)

Owner, 2026-10-13: stop tuning the enum. For Home stories with a sport, one
yes/no field in the categorize and summary calls decides:

> Is this primarily about money: ownership, deals, rights, valuations,
> league or industry economics?

yes -> `sports_business`, no -> `other`. No new column.

## The rule as built (`workers/feed/category.py` `decide`)

- The question is asked of every story (`money`, a required boolean in both
  calls' schemas).
- It decides a story that **has a sport, or that the model answered
  `sports_business`**. The second half was added because 8 of the ~17 game
  stories the owner named carry no sport tag (`stories.sport` covers cfb,
  nfl, nba, f1 and golf only), so a sport-only gate could not reach them.
- **`betting` keeps its own chip**: sportsbook and prediction-market stories
  are money too, and a literal yes -> sports_business would take them out of
  Betting (two sport-tagged ones existed: 1320, 2742).
- Every other story keeps its enum answer. A decided story with no boolean
  answer is dropped (left as it was), never guessed.

## The run

```
..\venv\Scripts\python.exe -m scripts.feed_categorize --money
```

- **Id set** (`ledger.feed_money_jobs`, read once): kept, real, categorized
  Home stories with a sport or in `sports_business`, not `betting`: **136**.
- **Result:** 6 Haiku calls, **63 moved, 73 unchanged**; every move is in
  `run.txt` (the script's full output; it is the log).
- **Cost:** **$0.003066**, `ai_usage` purpose `feed_recategorize`, 6 rows
  (ids after 820), read back via db_inspect.

Home categories after (kept, real): film_music 477, other 256, deals 129,
hospitality 112, luxury_marine 69, sports_business 48, NULL 35, betting 15.

## Against the owner's checks

- **The ~17 game stories: all left sports_business** (776, 1114, 1149,
  1155, 2713, 2900, 2976, 3137, 3157, 3162, 3260, 3328, 4955, 4981, 4982,
  4984, 5043; all now `other`).
- **1340 (Daylight Saving Time, golf industry): did NOT enter.** Golf-tagged,
  it was decided by the money answer, which was no; it stays `other`.

## Collateral, for the owner

Money stories that left sports_business (answered no, or answered another
category):

- to `other`: 2733 Dodgers Spend More on Players Than Rest of MLB's Final
  Four; 5064 Nielsen post-World Cup audiences; 5687 the NCAA eligibility
  case; 5734 the NFL's 18-game push; 2873 Atlanta Dream's WNBA Finals arena.
- to `film_music` (untagged, answered film_music, so the money gate did not
  apply): 1309 and 2524 McAfee on ESPN; 1339 Skydance's sports strategy;
  2920 Disney's Super Bowl alt-casts; 5214 Tirico on NBC's interview.
- 711 Club Sportico (advertising when your team loses) went to `betting`.

Moved in: 2809 Jaylen Brown on NBA wealth; 2829 Pat Connaughton's $600M real
estate business.

The structural limit: the gate keys on `stories.sport`, which covers only
the five tracked sports, so MLB, soccer, tennis, WNBA and rugby stories are
decided by the money answer only when the enum answer is sports_business.
Nothing here was corrected by hand.
