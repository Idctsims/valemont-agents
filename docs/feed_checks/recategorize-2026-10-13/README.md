# Re-categorize: sports_business and betting (2026-10-13)

Owner review, 2026-10-13 16:19 Chicago: the first categorize pass (1,106 Home
stories, $0.022812) put game coverage in `sports_business` and fantasy and
stock-market pieces in `betting`. The definitions were tightened in
`workers/feed/category.py` (`PROMPT`, sent by both the summary call and the
categorize pass):

- **sports_business**: the business of sport: ownership, stakes, valuations,
  media rights, athlete businesses, league economics, stadiums, sponsorship,
  NIL; NOT games, coaching or player performance (those are `other`).
- **betting**: the betting business: sportsbooks, prediction markets,
  Kalshi, betting regulation; NOT fantasy sports, NOT the stock market.

## The run

```
..\venv\Scripts\python.exe -m scripts.feed_categorize --recategorize sports_business,betting
```

- **Id set:** every kept, real Home story whose category was
  `sports_business` or `betting` when read, once, before any call: **161**
  (145 + 16).
- **Writes:** compare-and-set (`ledger.feed_recategorize`: only where the
  category is still the one read). The record of every move is `run.txt`
  in this folder, the script's full output (id, old -> new, headline). (The
  pass also logs each move at INFO, but the script configures no logging, so
  that line went nowhere in this run; `run.txt` is the log.)
- **Result:** 7 Haiku calls, **82 moved, 79 unchanged**.
- **Cost:** **$0.003232**, `ai_usage` purpose `feed_recategorize`, 7 rows
  (ids after 770), read back via db_inspect.

Home categories after (kept, real; db_inspect): film_music 489, other 241,
deals 123, hospitality 110, sports_business 65, luxury_marine 64, betting 14,
NULL 24 (new stories since the first pass; production does not categorize
until Phase 3 is deployed).

## Moves that look wrong under the new definition (for the owner)

Media rights went to `film_music`, not `sports_business`:

- 4867 Netflix Takes World Cup Rights in Spain
- 5206 Prime Video Adds Golf's Revived Skins Game To 15-Hour Live Sports Blitz
- 5064 Nielsen: Post-World Cup Soccer Audiences Surge Among Young Viewers

League economics went to `other`:

- 5734 Chris Berman: Damar Hamlin's Cardiac Arrest Slowed NFL's 18-Game Push
- 5687 Court Skeptical of College Athletes' Eligibility in Wisne-NCAA Case
- 1340 Daylight Saving Time Remains Complicated for Golf Industry

The likely cause: `film_music` lists "streaming" and "TV", which Haiku reads
as covering sports media rights. Not changed here; the owner decides.
