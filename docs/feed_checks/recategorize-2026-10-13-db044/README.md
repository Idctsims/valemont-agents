# After db/044: the misfit re-run and the uncategorized pass (2026-10-13)

Owner, 2026-10-13 (Chat 3 close-out). db/044 (applied 22:27:30 UTC) added 7
broadcasters (ESPN, NBC Sports, CBS Sports, FOX Sports, Amazon Prime
Video, Netflix, TNT Sports) and 4 leagues (MLB, NWSL, WNBA, NCAA) to the
entities the business-of-sports gate matches. With it, `category.decide`
(owner-approved): a story gated by a sports entity alone moves to
sports_business on yes and keeps its enum answer on no.

Both runs used branch `chat3-closeout` locally (the production worker
still runs PR #27's code until the close-out merges).

## 1. The 14 misfits from `../recategorize-2026-10-13-final/README.md`

```
..\venv\Scripts\python.exe -m scripts.feed_categorize --ids 1309,2524,1339,2920,5214,2733,5064,5687,2816,2818,2820,2821,1340,711
```

1 Haiku call, **12 moved, 2 unchanged**, **$0.000416** (`ai_usage` 848,
purpose `feed_recategorize`). Full output: `run-ids.txt`.

| id | story | move |
|---|---|---|
| 1309 | Pat McAfee joins ESPN's Super Bowl coverage | film_music -> sports_business |
| 2524 | McAfee's Super Bowl alt-cast | film_music -> sports_business |
| 1339 | Sports key to Skydance's content engine | film_music -> sports_business |
| 2920 | Disney's Super Bowl alt-casts | film_music -> sports_business |
| 5214 | Tirico on NBC's Jordan interview | film_music -> sports_business |
| 2733 | Dodgers outspend MLB's final four | other -> sports_business |
| 5064 | Nielsen post-World Cup audiences | other -> sports_business |
| 5687 | NCAA eligibility case | other -> sports_business |
| 2816, 2818, 2820, 2821 | four LeBron James features | sports_business -> other |
| 1340 | DST and the golf industry | unchanged (other) |
| 711 | Club Sportico (advertising) | unchanged (betting) |

## 2. One categorize pass over uncategorized Home stories

```
..\venv\Scripts\python.exe -m scripts.feed_categorize
```

46 stories (summarized by Phase 2 code, which set no category, plus new
ones since), 2 Haiku calls, **46 written**, **$0.001051** (`ai_usage`
849-850, purpose `feed_categorize`): deals 8, hospitality 3,
sports_business 1, film_music 20, luxury_marine 2, betting 0, other 12.
Full output with samples: `run-null.txt`.

## Counts after (Home, kept, real; db_inspect)

film_music 492, other 262, deals 137, hospitality 115, luxury_marine 71,
sports_business 60, betting 15, **uncategorized 0**.

## Still open (left as they are)

- 1340 (DST and the golf industry): answered "not the business of sports"
  three times.
- 711 (Club Sportico): in `betting`, not a betting story.
