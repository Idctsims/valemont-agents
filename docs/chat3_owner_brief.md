CHAT 3 — LEARNING ENGINE, HOME FEED, SPORTS NEWS

Read MASTER_PLAN.md, BUILD_BRIEF.md and CLAUDE.md from the project
files before anything else. This chat covers Chat 3 in section 6:
source registry + RSS ingest + dedupe + Haiku batch summaries +
embeddings; Love / Neutral / Not Interested with an interest profile
and daily re-rank; Sports News for CFB, NFL, NBA, F1 and Golf on the
same engine. Nothing from later chats.

Where things stand:
- Chat 2 (Command core) is COMPLETE (Oct 11): Goals, Ventures HQ,
  Capital Tracker (paper), the context layer + Wags, and the Morning
  Brief (first real push 09:45 Oct 11). CLAUDE.md Current State has
  the detail.
- Migrations applied through db/031; next file is db/032.
- The Claude API is live: $20/month cap, budget guard on worker
  (core/ai.py) and web (lib/ai/). Wags runs about $5–7/month; the
  Brief about $0.30/month.
- Context providers registry exists (context_providers.brief_section).
  Chat 3 registers a feed provider so Wags and the Brief see it; that
  enables the Brief's "top 3 stories I'm most likely to Love".
- Railway is on the Free plan, about 40 MB RAM; the trial ends ~Oct 18
  (manual redeploy).
- Patterns from Chat 2 to keep: {ok, error} server actions, the e2e
  marker and is_test pattern, signed-in specs that touch only their
  own rows, the screenshot review gate, no git stash in commit flows.

SOURCES (all free unless noted; I pay only for Bloomberg):
- Keep: Bloomberg (I subscribe), Variety, Deadline, The Hollywood
  Reporter, Boardroom, Axios Pro Rata, Semafor Business, Sportico,
  Front Office Sports, Complex, The Ringer.
- Drop: Puck and The Information (paywalled, no subscription).
- Add, business and culture: The Next Miami, Axios Miami, Bisnow
  South Florida, Hotel Dive, Skift, Boat International, Robb Report,
  TechCrunch, Crunchbase News, StrictlyVC, Music Business Worldwide,
  Billboard (business), Tubefilter, Legal Sports Report.
- Sports: ESPN, Action Network, The Race, Golf Digest, Boardroom,
  Front Office Sports, official league feeds, plus AL.com (Alabama),
  Roll Bama Roll, ProFootballTalk, Liberty Ballers (76ers),
  Formula1.com, RaceFans, PGA Tour, Golf Channel, No Laying Up,
  Yahoo Sports.
- Verify each source's RSS before relying on it; where there's no
  feed, use the Google News RSS query per source (MASTER_PLAN §4
  fallback). Report which sources needed the fallback.

SPORTS I FOLLOW (follow PEOPLE, not just teams, so moves and
retirements flow through automatically):
- CFB: all Power 4 programs (SEC, Big Ten, Big 12, ACC) plus Notre
  Dame. #1 by far: Alabama Crimson Tide. Alabama news always ranks
  near the top, and game weeks boost it.
- NFL: all teams, no favorite. League-wide news, trades, injuries,
  playoff picture.
- NBA: LeBron James first, whatever team he's on (Philadelphia 76ers
  for 2026-27), then the 76ers as his team; all league news welcome.
- F1: Lewis Hamilton and Charles Leclerc first (both at Ferrari),
  then Ferrari, then all F1. Race weekends boost F1.
- Golf: Scottie Scheffler and Brooks Koepka first (Koepka is back on
  the PGA Tour in 2026), then the world's top players. I watch every
  major and the big mid-majors (Players, signature events), so those
  weeks boost golf. LIV news only when it involves top players or the
  tour split.

MY SEED INTERESTS (day-one profile):
- Topics I LOVE, read through the deal and business lens (who's
  buying, raising, launching, partnering, and the business side of
  culture):
  * experiential hospitality: beach clubs, members' clubs, waterfront
    venues, hotels;
  * yachts and marine;
  * Miami and Biscayne Bay development, Florida coastal permitting;
  * VC, family offices, fund raises, notable deals;
  * sports business: team ownership, stakes, valuations, media
    rights, athlete businesses;
  * music business, artist management, creator economy deals;
  * film/TV dealmaking and talent;
  * luxury and lifestyle brands;
  * prediction markets and sports-betting regulation (Kalshi), since
    it directly affects my Betting pillar.
- Topics I DON'T care about: celebrity gossip and relationships, SEO
  listicles and filler, partisan horse-race and culture-war politics
  (policy only when it moves money), generic "AI will change
  everything" takes (Tech Hub covers AI in Chat 4), crypto price
  chatter (Crypto has its own strip in Chat 10), and plain box-score
  recaps with no storyline.
- Anything else the feed should know:
  * Summaries: 2 sentences max, plain and direct.
  * Top-ranked items get one "Why it matters to you" line tied to my
    ventures (SBC, Perfect Timing, Excursion, Clipd, Valemont Grow,
    Sims & Vale) only when there's a real connection; otherwise none.
    Never force it.
  * Breadth beats repetition: 5 outlets covering the same story is
    ONE item (dedupe by story, link the best source).
  * Mobile-first, fast to scan; I read this on my phone every morning.

What I need from you, in order:
1. Your clarifying questions first, including where embeddings run
   (Supabase Edge Functions on the correct account vs the Railway
   worker on the free plan) and the AI cost of daily summaries
   against my $20/month cap.
2. GitHub search for anything worth reusing in this chat's scope (RSS
   ingest and dedupe, feed ranking and recommendation, embeddings on
   a small budget, Supabase pgvector patterns). Tell me what you'd
   reuse and why.
3. One copy-paste Claude Code prompt per phase, each with a "done
   when" checklist I can test on my phone. PowerShell 5.1, no secrets
   in output, merge commits, honest reports.
4. Accounts and keys introduced in the phase that needs them.
5. Remember the Mon Oct 12 goals rollover push, the Tue Oct 13 props
   archive, and the ~Oct 18 Railway redeploy.

Don't move past a phase until I report its checklist green.
