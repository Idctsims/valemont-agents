# Re-categorize: film_music and other (2026-10-13, second pass)

Owner, 2026-10-13 16:37, after the first re-run (`../recategorize-2026-10-13/`)
sent sports media rights to `film_music` and league economics to `other`.
`workers/feed/category.py` `PROMPT` now says:

- **film_music**: film, TV, music, entertainment streaming, creators,
  talent; NOT sports rights or sports broadcasting (those are
  sports_business).
- **sports_business** adds: sports media rights and broadcasting deals on
  any platform; league economics and structure (schedule length,
  eligibility and NIL rulings, expansion); the business of a sport's
  industry. Still NOT games, coaching or player performance.

## The run

```
..\venv\Scripts\python.exe -m scripts.feed_categorize --recategorize film_music,other
```

- **Id set:** 730 stories (`film_music` and `other`, read once).
- **Result:** 30 Haiku calls, **73 moved, 657 unchanged**. Every move is in
  `run.txt` (the script's full output; it is the log).
- **Cost:** **$0.015236**, `ai_usage` purpose `feed_recategorize`, 30 rows
  (ids after 783), read back via db_inspect.

Home categories after (kept, real): film_music 470, other 207, deals 128,
hospitality 112, sports_business 106, luxury_marine 69, betting 14, NULL 24.

## The six named cases

| id | story | now |
|---|---|---|
| 4867 | Netflix Takes World Cup Rights in Spain | sports_business |
| 5064 | Nielsen: Post-World Cup Soccer Audiences Surge | sports_business |
| 5206 | Prime Video Adds Golf's Revived Skins Game | sports_business |
| 5687 | Court Skeptical of College Athletes' Eligibility (NCAA) | sports_business |
| 5734 | Chris Berman: ... Slowed NFL's 18-Game Push | sports_business |
| 1340 | Daylight Saving Time Remains Complicated for Golf Industry | **other (did not move)** |

## Regressions: games and personalities back into sports_business

The longer sports_business definition pulled game coverage back in, which
the first re-run had moved out and which the definition still excludes.
Moved `other -> sports_business` (or `film_music -> sports_business`) and,
on the definition, wrong:

- 776 John Harbaugh's Giants Are Contenders
- 1114 Black Out: The Bledisloe Cup, a New Tackle Law, and Jason Kelce
- 1149 Caitlin Clark Blasts Attacks on Kelsey Mitchell
- 1155 Gilbert Arenas Pushes Back on Colorism Accusation
- 2713 What Has the NBA Lost?
- 2900 Advantage England, Spain's Dominance, and Molly Bartrip
- 2976 Jaylen Brown Jokes Adidas Should Give Anthony Edwards ...
- 3137 NFL Week 5 Recap! Plus, UGA Takes the Top Spot
- 3157 What are your boldest takes for Bills-Rams 'MNF'
- 3162 The Most Confusing Teams in the NBA!
- 3260 Man City's Weird Return, Liverpool's Wirtz Problem
- 3328 Hot Dogs Are on the Ice, and Teams Are Falling Apart
- 4955 Week 5 Coaching Blunders, Who Are the 2026 Seahawks
- 4981 Week 6 Waiver Wire (fantasy)
- 4982 Are Cricket Scores Good for Rugby?
- 4984 The Man Behind the NBA's Most Audacious Superteam
- 5043 The Ball Knower's Notebook: Josh Allen's Legs Are Inevitable

Arguable (media personalities, not rights deals): 777, 1312, 1324, 3114,
5214, 5623. Arguably film_music (documentaries): 5380, 5384.

Moves that fit the new definition: 838, 1309, 1317, 1326, 1339, 2524,
4910, 5387, 5696 and the five named cases above.

Nothing here was corrected by hand; the owner decides.
