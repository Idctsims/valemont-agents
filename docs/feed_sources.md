# Feed sources (Chat 3 Phase 1, Step 0)

Surveyed **2026-10-11 ~22:30 UTC** (The Real Deal and the Pro Rata re-check
~23:30 UTC), from the owner's machine, with the ingest User-Agent
(`ValemontCommand/1.0 (+https://valemont-command.vercel.app/privacy)`), httpx
0.28.1 and feedparser 6.0.14. Every number below is one fetch at that moment,
not an average. This list becomes `workers/feed/sources.toml` in Step 3.

**Totals:** 50 sources listed, **46 active**: 36 official RSS/Atom and 10 Google
News fallbacks. **4 inactive**: 3 dead (The Next Miami, StrictlyVC, Axios Pro
Rata) and 1 by decision (No Laying Up).

## Status meanings

- **ok**: an official feed returned items, the newest within 7 days.
- **fallback**: no working official feed, so a Google News search
  (`site:<domain>`, narrowed by path where a section matters). For every
  fallback, 3 links were decoded with `googlenewsdecoder` 0.2.1. **Every decode
  tried returned the publisher URL** (30 of 30 in the first pass, then 3 for
  the narrowed Yahoo F1 query, 9 across three Real Deal queries, and all 4
  Pro Rata items).
- **dead**: nothing current to ingest.
- **inactive**: `active = false` in `sources.toml`. The row is kept, never
  deleted.

## Owner decisions (2026-10-11)

1. **`sources.include_categories text[]`.** Semafor ingests only items whose
   category is in `{Business, Media}`. Media exists as a tag (3 of 188 at
   survey time; Business 31). Not Technology.
2. **`sources.gnews_window_days`**, default 2. Axios Miami and The Real Deal
   Miami use 7. **`feed_rules` kind `url_regex`**, seeded with a rule dropping
   Axios Pro links.
   - The owner's rule named `pro.axios.com`, but the decoded links are
     `www.axios.com/pro/all-deals/…`, so a host-only rule would drop nothing.
     The seeded pattern covers both:
     `^https?://(pro\.axios\.com|(www\.)?axios\.com/pro/)`.
   - **Axios Pro Rata is dead.** All 4 items in its 7-day window decoded to
     `www.axios.com/pro/`, so 0 survive the rule. `active = false`.
3. **No Laying Up: `active = false`.** Its only feed is the podcast
   (`feeds.megaphone.fm/nolayingup`, 1,221 episodes, newest 118 h). The cards
   would be episodes, not articles, and the site has no article feed.
4. **Undated feeds (Formula1.com): seed and skip.** `published_at` is the time
   an item was first seen. On a source's **first** fetch, every item is
   recorded as seen and **nothing is ingested** from that fetch, because "first
   seen" would date the whole back catalogue to now. Later fetches ingest only
   new items. Tested in Step 6.
5. **"where to watch"** added to the `title_regex` drop list (THR's
   "Where to Watch the 49ers vs. Seahawks Game Online").
6. **StrictlyVC and The Next Miami: `active = false`.** The replacement for The
   Next Miami is **The Real Deal Miami**, a Google News fallback, with priority
   just below Bisnow South Florida.
   - `therealdeal.com` returns **403** to our User-Agent on every feed URL tried
     (`/miami/feed/`, `/miami/rss/`, `/feed/?region=miami`, `/feed/`).
   - `site:therealdeal.com/miami` at 2 days gave 1 item; **at 7 days, 31 items,
     newest 6 h**, 3 of 3 decoded to `therealdeal.com/miami/2026/10/…`.
   - Sample: "Steve Ross, Jeffrey Soffer's Miami airport hotel project grounded
     by FAA". Bisnow carries the same story ("Stephen Ross, Jeffrey Soffer Scrap
     Plans For Miami Airport Hotel Project"), a likely first multi-outlet merge.
   - The broader `site:therealdeal.com miami` pulled Chicago stories and videos,
     so the path stays narrowed.
7. **`selectolax<1.0` pinned** next to googlenewsdecoder (finding 1 below), and
   **`published_at` is capped at fetch time** (finding 3).

## Dead (3)

- **The Next Miami**: `/feed/`, `/feed` and `/rss.xml` return 404, and Google
  News has **0 items in 7 days** for `site:thenextmiami.com`.
- **StrictlyVC**: `strictlyvc.com/feed/` returns 10 items, but the newest is
  about 6.5 years old. The newsletter moved to `newsletter.strictlyvc.com`,
  which has no feed and only its home page in Google News. Searching the word
  "StrictlyVC" returns other outlets (TechCrunch) mentioning it, not the
  newsletter.
- **Axios Pro Rata**: the API and newsletter feeds return 404/403; its only
  Google News results are Axios Pro (paid) deal pages, which the url_regex rule
  drops (decision 2).

## Active fallbacks (10)

| slug | why | query window |
|---|---|---|
| `complex` | no feed (404) | 2 d |
| `the-ringer` | no feed (404) | 2 d; only 5 items, mostly podcast episode pages |
| `axios-miami` | 404/403 | **7 d** (15 items; 0 at 2 d) |
| `the-real-deal-miami` | 403 to our User-Agent | **7 d** (31 items; 1 at 2 d) |
| `boat-international` | no feed (404) | 2 d |
| `golf-digest` | `/feed/rss` 404 | 2 d |
| `nfl-com` | the old RSS URL redirects to the home page | 2 d, path `nfl.com/news` |
| `nba-com` | `/news/rss.xml` 403 | 2 d, path `nba.com/news` |
| `pga-tour` | no feed (404) | 2 d |
| `yahoo-f1` | no F1 feed (404) | 2 d, `site:sports.yahoo.com "Formula 1"`. The plain `site:sports.yahoo.com f1` query returned NFL and cricket stories, so it was narrowed |

Exact dedupe on `external_id` makes re-seeing an item across a 7-day window
harmless.

## Findings

1. **googlenewsdecoder 0.2.1 breaks on selectolax 1.x.** It imports
   `selectolax.parser`, the removed Modest backend, and fails at import.
   `selectolax<1.0` (0.4.13 works) is pinned alongside it.
2. **Semafor has no business-only feed**; `semafor.com/rss.xml` carries every
   section, one category per item. At survey time: Politics 46, Business 31,
   Gulf 23, Africa 23, Technology 18, Security 14, Energy 9, CEO Signal 3,
   China 3, Media 3. Hence `include_categories` (decision 1).
3. **ESPN dates items up to ~0.8 h in the future.** Ingest caps `published_at`
   at `fetched_at`, or "newest first" would pin ESPN to the top.
4. **Formula1.com's feed has no dates** (10 items, no `pubDate`). Hence seed
   and skip (decision 4).
5. **Action Network** is mostly picks content (314 items, e.g. "Ravens vs
   Falcons Predictions, Odds, Picks"). The "odds, picks" rule catches these;
   expect a high filtered rate there.
6. **Feeds narrowed to a section:**
   - `bloomberg` uses the Business feed (Markets also works);
   - `al-com-alabama` uses the `alabamafootball` category (the
     `alabama-football` slug returns 0 items);
   - `billboard-business` uses `/c/business/feed/`.
7. Low volume but alive: Boardroom (newest 102 h), Legal Sports Report (76 h).

## Sources

Ages are hours since the newest item at survey time. Items is what one fetch
returned; ingest takes at most 50.

| slug | name | pillar(s) | sport | kind | URL | items | newest | sample headline | status |
|---|---|---|---|---|---|---|---|---|---|
| `bloomberg` | Bloomberg | home | null | rss | https://www.bloomberg.com/feeds/business/news.rss | 20 | 48 min | McKinsey, Firmus, Jacinda Ardern: Australia Briefing | ok |
| `variety` | Variety | home | null | rss | https://variety.com/feed/ | 10 | 36 min | After Kelly Clarkson Slams Brooklyn Novelty Store, Shop Says the ‘One-of-a-Kind’ Singing B | ok |
| `deadline` | Deadline | home | null | rss | https://deadline.com/feed/ | 12 | 18 min | ‘Artificial’s Ike Barinholtz Teases What Makes His “Menacing” Elon Musk Performance “A Lit | ok |
| `thr` | The Hollywood Reporter | home | null | rss | https://www.hollywoodreporter.com/feed/ | 10 | 2 h | Where to Watch the 49ers vs. Seahawks Game Online | ok |
| `boardroom` | Boardroom | home/sports | null | rss | https://boardroom.tv/feed/ | 50 | 102 h | The Blueprint: Jessica Holtz Meets the Moment | ok |
| `axios-pro-rata` | Axios Pro Rata | home | null | gnews | https://news.google.com/rss/search?q=site%3Aaxios.com+pro+rata+when%3A7d&hl=en-US&gl=US&ceid=US:en | 4 | 69 h | Lambda's $14.5b, Endeavor Catalyst's new fund, and retail invests in sports stars | **dead** (all items are `axios.com/pro/` links, dropped by url_regex) |
| `semafor-business` | Semafor Business | home | null | rss | https://www.semafor.com/rss.xml | 188 | 0 min | US threatens to halt Ukraine arms | ok |
| `sportico` | Sportico | home/sports | null | rss | https://www.sportico.com/feed/ | 10 | 43 h | WNBA Finals Pairs League’s Most and Least Valuable Teams | ok |
| `front-office-sports` | Front Office Sports | home/sports | null | rss | https://frontofficesports.com/feed/ | 50 | 2 h | U.K. Prime Minister Vows Transparency Over His Manchester City Meetings | ok |
| `complex` | Complex | home | null | gnews | https://news.google.com/rss/search?q=site%3Acomplex.com+when%3A2d&hl=en-US&gl=US&ceid=US:en | 100 | 3 h | 50 Cent Shuts Down Any Chance of Peace With Diddy: 'No, Not With Him' | fallback |
| `the-ringer` | The Ringer | home | null | gnews | https://news.google.com/rss/search?q=site%3Atheringer.com+when%3A2d&hl=en-US&gl=US&ceid=US:en | 5 | 2 h | Danny Boyle on Depicting Murdoch, AI, Bond, and ‘28 Years Later’ | fallback |
| `the-next-miami` | The Next Miami | home | null | — | https://www.thenextmiami.com/feed/ (404); Google News 7d: 0 items | 0 | — | — | dead, **inactive** |
| `axios-miami` | Axios Miami | home | null | gnews | https://news.google.com/rss/search?q=site%3Aaxios.com%2Flocal%2Fmiami+when%3A7d&hl=en-US&gl=US&ceid=US:en | 15 | 60 h | Local artists' bus stop ad challenges Miami's development, gentrification | fallback |
| `bisnow-south-florida` | Bisnow South Florida | home | null | rss | https://www.bisnow.com/rss-feed/south-florida | 21 | 53 h | Stephen Ross, Jeffrey Soffer Scrap Plans For Miami Airport Hotel Project | ok |
| `the-real-deal-miami` | The Real Deal Miami | home | null | gnews | https://news.google.com/rss/search?q=site:therealdeal.com/miami+when:7d&hl=en-US&gl=US&ceid=US:en | 31 | 6 h | Steve Ross, Jeffrey Soffer’s Miami airport hotel project grounded by FAA | fallback (7 d) |
| `hotel-dive` | Hotel Dive | home | null | rss | https://www.hoteldive.com/feeds/news/ | 10 | 52 h | Braemar advances Ashford spinout with $372M Four Seasons sale | ok |
| `skift` | Skift | home | null | rss | https://skift.com/feed/ | 10 | 11 h | Travel AI’s Prove-It Phase, Visibility, and the Booking Gap | ok |
| `boat-international` | Boat International | home | null | gnews | https://news.google.com/rss/search?q=site%3Aboatinternational.com+when%3A2d&hl=en-US&gl=US&ceid=US:en | 49 | 19 h | First look inside first unit of ISA Yachts 40m Viper 130 Fast Naan | fallback |
| `robb-report` | Robb Report | home | null | rss | https://robbreport.com/feed/ | 10 | 2 h | This Bordeaux-Style Wine Tells the Story of a Sicilian Legend | ok |
| `techcrunch` | TechCrunch | home | null | rss | https://techcrunch.com/feed/ | 20 | 1 h | Will the HomeView be Apple’s first smart home success? | ok |
| `crunchbase-news` | Crunchbase News | home | null | rss | https://news.crunchbase.com/feed/ | 10 | 52 h | The Week’s 10 Biggest Funding Rounds: AI And Energy Top A Busy Lineup Of Large Rounds | ok |
| `strictlyvc` | StrictlyVC | home | null | — | https://strictlyvc.com/feed/ (last item ~6.5 years old); newsletter.strictlyvc.com: no feed | 1 | — | — | dead, **inactive** |
| `mbw` | Music Business Worldwide | home | null | rss | https://www.musicbusinessworldwide.com/feed/ | 10 | 55 h | From Sony Music’s 260,000 deepfake takedowns to Suno’s live music expansion… it’s MBW’s We | ok |
| `billboard-business` | Billboard Business | home | null | rss | https://www.billboard.com/c/business/feed/ | 10 | 5 h | Inside the Halftime Music Strategy For the NFL’s International Games: ‘The Biggest Challen | ok |
| `tubefilter` | Tubefilter | home | null | rss | https://www.tubefilter.com/feed/ | 12 | 48 h | Have you heard? Pokimane at Chipotle, a Rooster Teeth reunion, and a Mike Tomlin Minecraft | ok |
| `legal-sports-report` | Legal Sports Report | home | null | rss | https://www.legalsportsreport.com/feed/ | 10 | 76 h | DraftKings Sued By Seminole Tribe Over Sports Predictions In Florida | ok |
| `espn-cfb` | ESPN College Football | sports | cfb | rss | https://www.espn.com/espn/rss/ncf/news | 18 | 0 h (dated +0.5 h ahead) | Can Texas bounce back? Will the Big 12 go sideways? Connelly on an eventful Week 6 | ok |
| `espn-nfl` | ESPN NFL | sports | nfl | rss | https://www.espn.com/espn/rss/nfl/news | 21 | 0 h (dated +0.8 h ahead) | Texans trade for Cardinals' Harrison, reunite WR with QB Stroud | ok |
| `espn-nba` | ESPN NBA | sports | nba | rss | https://www.espn.com/espn/rss/nba/news | 16 | 0 h (dated +0.4 h ahead) | What does your NBA team need? The biggest hole on all 30 rosters | ok |
| `espn-f1` | ESPN F1 | sports | f1 | rss | https://www.espn.com/espn/rss/f1/news | 30 | 3 h | 'Chucky' Verstappen is back. Luckily for Antonelli and Mercedes, it's too late | ok |
| `espn-golf` | ESPN Golf | sports | golf | rss | https://www.espn.com/espn/rss/golf/news | 19 | 42 min | Rahm leaving LIV Golf in blow to future of bankrupt league | ok |
| `action-network` | Action Network | sports | null | rss | https://www.actionnetwork.com/feed | 314 | 12 min | Ravens vs Falcons Predictions, Odds, Picks — Expert SNF Preview | ok |
| `the-race` | The Race | sports | f1 | rss | https://www.the-race.com/rss/ | 15 | 5 h | Our verdict on Hamilton's Singapore strategy failure | ok |
| `golf-digest` | Golf Digest | sports | golf | gnews | https://news.google.com/rss/search?q=site%3Agolfdigest.com+when%3A2d&hl=en-US&gl=US&ceid=US:en | 47 | 1 h | Six consecutive pars to finish in Japan good enough to deliver Michael Brennan his third t | fallback |
| `nfl-com` | NFL.com | sports | nfl | gnews | https://news.google.com/rss/search?q=site%3Anfl.com%2Fnews+when%3A2d&hl=en-US&gl=US&ceid=US:en | 22 | 1 h | 2026 NFL season, Week 5: What We Learned from Sunday's games | fallback |
| `nba-com` | NBA.com | sports | nba | gnews | https://news.google.com/rss/search?q=site%3Anba.com%2Fnews+when%3A2d&hl=en-US&gl=US&ceid=US:en | 10 | 1 h | Starting 5: Kyrie's return sparks Dallas in China | fallback |
| `formula1` | Formula1.com | sports | f1 | rss | https://www.formula1.com/en/latest/all.xml | 10 | undated | What the teams said – Race day in Singapore | ok |
| `pga-tour` | PGA Tour | sports | golf | gnews | https://news.google.com/rss/search?q=site%3Apgatour.com+when%3A2d&hl=en-US&gl=US&ceid=US:en | 100 | 2 h | Ludvig Åberg, Shane Lowry seek to make big final-round move at Open de España presented by | fallback |
| `al-com-alabama` | AL.com Alabama Sports | sports | cfb | rss | https://www.al.com/arc/outboundfeeds/rss/category/alabamafootball/?outputType=xml | 50 | 1 h | Jahmyr Gibbs joins Barry Sanders in historic company with latest touchdown | ok |
| `roll-bama-roll` | Roll Bama Roll | sports | cfb | rss | https://www.rollbamaroll.com/rss/index.xml | 10 | 2 h | 10 “so close” graphs from the Georgia game | ok |
| `pft` | ProFootballTalk | sports | nfl | rss | https://www.nbcsports.com/profootballtalk.atom | 30 | 6 min | C.J. Stroud on reuniting with Marvin Harrison Jr.: It's perfect for him | ok |
| `liberty-ballers` | Liberty Ballers | sports | nba | rss | https://www.libertyballers.com/rss/index.xml | 10 | 20 h | Sixers Bell Ringer: Edgecombe’s growth, Walker’s strong preseason continue | ok |
| `racefans` | RaceFans | sports | f1 | rss | https://www.racefans.net/feed/ | 20 | 6 h | Norris incorrectly believed Hamilton “would have to let me past” after turn one move / For | ok |
| `golf-channel` | Golf Channel | sports | golf | rss | https://www.nbcsports.com/golf.atom | 7 | 26 h | Adam Scott, Justin Thomas move into contention at the PGA Tour’s Japan event | ok |
| `no-laying-up` | No Laying Up | sports | golf | rss | https://feeds.megaphone.fm/nolayingup | 1221 | 118 h | 1219: Deane Beman | ok, **inactive** (podcast episodes only) |
| `yahoo-nfl` | Yahoo Sports NFL | sports | nfl | rss | https://sports.yahoo.com/nfl/rss/ | 50 | 6 min | Packers dominate banged-up Bears as Tyson Bagent is benched late | ok |
| `yahoo-cfb` | Yahoo Sports College Football | sports | cfb | rss | https://sports.yahoo.com/college-football/rss/ | 50 | 6 min | Betting odds released for Michigan Football vs Penn State | ok |
| `yahoo-nba` | Yahoo Sports NBA | sports | nba | rss | https://sports.yahoo.com/nba/rss/ | 50 | 3 h | JJ Redick feels the Lakers are in a good spot so far this preseason | ok |
| `yahoo-golf` | Yahoo Sports Golf | sports | golf | rss | https://sports.yahoo.com/golf/rss/ | 50 | 36 min | Vaughn Taylor wins Furyk & Friends for first PGA Tour Champions victory | ok |
| `yahoo-f1` | Yahoo Sports F1 | sports | f1 | gnews | https://news.google.com/rss/search?q=site:sports.yahoo.com+%22Formula+1%22+when:2d&hl=en-US&gl=US&ceid=US:en | 100 | 1 h | F1 Qualifying Results: Full Starting Grid for Singapore Grand Prix | fallback |

## Embed function (Step 2, 2026-10-11)

`supabase/functions/embed` (gte-small, 384 dims, mean-pooled, normalized),
deployed Docker-free (`--use-api`) with `--no-verify-jwt`; it checks
`Authorization: Bearer EMBED_TOKEN` itself, in constant time.

**Batch size, measured against the live function** (real titles and teasers,
mean 219 characters, calls back to back):

| batch | ok | median | worst | per text |
|---|---|---|---|---|
| 4 | 10/10 | 1,048 ms | 1,177 ms | 262 ms |
| **8** | **50/50** (two runs) | 1,424–1,696 ms | 1,995 ms | ~200 ms |
| 12 | 5/10 | 1,563 ms | 2,386 ms | 130 ms |
| 16 | 3/10 | 1,907 ms | 1,914 ms | 119 ms |

Every failure was `546 WORKER_RESOURCE_LIMIT` (the Free plan's compute limit),
plus one 503. **The cap is 8** (owner, 2026-10-11): 9 or more texts get a 413.
`core/embed.py` sends 8 and halves the batch on a 546/503.

**Similarity baseline** (a handful of pairs, not a calibration):

| pair | cosine |
|---|---|
| same story: Bisnow vs The Real Deal (Ross/Soffer hotel) | 0.956 |
| same story: Harrison trade (ESPN vs PFT) | 0.883 |
| same topic, different story | 0.732 |
| unrelated | 0.733, 0.753 |
| seed "celebrity breakup" vs real celebrity gossip | 0.755 |
| seed "celebrity breakup" vs an NFL music-business story | 0.715 |
| seed "Bitcoin price prediction" vs an unrelated funding story | 0.760 |

gte-small is anisotropic: almost every pair scores above 0.7. Consequences
(owner, 2026-10-11):

- **The seed-phrase embedding filter is retired.** Its phrases cannot separate
  gossip from business news. Each story's best seed-phrase score (from 0.70)
  is still logged, for the record only; it never filters. The
  `seed_phrase_drop` rows stay in `feed_rules`, unused as a filter.
- **Replacement:** the Haiku summary call returns a `drop` category (gossip,
  partisan_politics, ai_hype, crypto_price, recap_no_storyline, listicle,
  none). It runs in **shadow** for the first 24 h: logged, nothing filtered.
  title_regex and url_regex are live from the first ingest.
- **Story join stays at cosine ≥ 0.86 OR rapidfuzz token_set_ratio ≥ 90.**
  Every join and every near miss (0.78–0.86) is logged with both titles for
  the 24 h calibration report.
