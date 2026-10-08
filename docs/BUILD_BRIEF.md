# VALEMONT COMMAND — MASTER BUILD BRIEF (LOCKED)
(Working name — rename anytime)

## WHO I AM & HOW TO WORK WITH ME
I'm Tsims, CEO of Valemont Group. This is my personal "everything dashboard" — the command center I open every day on my phone and computer. You are my lead architect. Your job is to make this real, not to tell me what can't be done.

Operating rules:
1. BUILD MODE. When you hit an obstacle (API limits, missing data, cost, platform rules), give me the workaround — not a reason to stop. If something can't be done one way, give me the next best way and keep moving.
2. The sports betting features are a firm requirement. I know it's gambling and nothing is guaranteed. Don't relitigate it — build it to be as sharp as possible.
3. Never set a dollar target as the success metric. Success = the tools work, are accurate, and I use them daily.
4. Ask before you build. At the start of every chat, ask me your clarifying questions FIRST, then give me the Claude Code prompts.
5. Reuse before rebuilding. Audit what exists in the Valemont Agents repo and search GitHub for proven repos before writing from scratch. Tell me what you're reusing and why.
6. Deliver work as copy-paste prompts for Claude Code in Cursor, one phase at a time, each with a clear "done when" checklist I can test.

## NO MVP. NO "V1 THEN ADD LATER."
- The first version ships with EVERY pillar and feature in this brief. Nothing gets cut, deferred, or labeled "phase 2."
- Build ORDER exists only to sequence dependencies correctly — it is not a feature cut. Every pillar gets built before we call the first version done.
- If something is hard, solve it. Don't propose shipping without it.
- The only future items are ones I haven't defined yet: Agentic OS, Jev, and anything I add after research.

## STEP ZERO: THE MASTER PLAN (before any code is written)
Before a single line of code, produce a complete, bulletproof build and execution plan. We review it together, close every hole, and I approve it. Only then do we build. The plan must include:

1. Repo decision: audit the existing Valemont Agents repo; recommend build-on vs. new monorepo, with what carries over.
2. Full architecture: frontend, API routes, Railway workers, Supabase, Claude API, third-party APIs, and how they connect. Include a diagram.
3. Complete data model: every Supabase table, its columns, relationships, and row-level security. Paper vs. live data separated from day one.
4. API & services inventory: every external API/service, what it's used for, free vs. paid tier, rate limits, cost estimate per month, and the keys/accounts I need to create. Include a fallback for each in case one fails or gets limited.
5. Dependency map: which pillars depend on which shared systems, so nothing gets built before what it needs.
6. Build sequence: the optimal order, broken into chats, each chat broken into phases, each phase with a "done when" checklist.
7. Risk register: every likely failure point (API changes, rate limits, data gaps, Kalshi rules, Pinterest access, live data latency, Vercel/Railway limits, PWA quirks on iPhone, Windows/PowerShell gotchas) with the mitigation planned in advance.
8. Testing plan: how each pillar gets verified before we move on, plus an end-to-end test before calling the build done.
9. Environment setup checklist: everything I install, configure, and sign up for before Chat 1, in order.

## TECH FOUNDATION (non-negotiable)
- Frontend: Next.js (App Router) + Tailwind + TypeScript, deployed on Vercel
- Database/Auth: Supabase (single-user, just me — locked behind my login)
- Background workers: Railway (bots, live trackers, scheduled jobs — anything 24/7 can't live on Vercel serverless)
- AI: Claude API (summaries, vision/screenshot reading, analysis, Morning Brief, Wags, auto-tagging)
- Installable PWA: add-to-home-screen on iPhone, works great on desktop, push notifications
- My machine: Windows, native PowerShell only (no WSL2 — virtualization is disabled). All commands must work in PowerShell.
- MCPs available in Cursor: GitHub, Context7, Memory, Supabase

## DESIGN DIRECTION
- Must NOT look like generic AI-generated UI (no default shadcn gray cards, no purple gradients, no emoji headers).
- Closer to how Valemont was built, tuned to my personality.
- I'll provide inspo screenshots in the Foundation chat before any UI is built. Extract the design system (colors, type, spacing, card style, motion) into a tokens file; every page uses those tokens.
- Mobile-first: every page fully usable one-handed on my phone.

## SITE MAP
COMMAND
1. Home (Morning Brief + Goals + Personalized Feed)
2. Wags (AI advisor — own page + accessible from every page)
3. Ventures HQ
4. Capital Tracker

CULTURE
5. The Arsenal (reference library)
6. Screening Room
7. The Lookbook

TECH
8. Tech Hub

SPORTS
9. Sports News
10. Game Day
11. Betting (picks + slip analyzer + manual entry)
12. Live Bet Tracker

CRYPTO
13. Long-Term Home
14. Core Paper Trading Bot
15. Memecoin Paper Trading Bot
16. Kalshi 15-Min BTC

## PILLAR SPECS

### 1. HOME
A. Morning Brief (top of page)
- AI-written briefing generated every morning (scheduled on Railway): today's goals, today's calendar highlights if connected, top 3 stories I'm most likely to Love, today's strongest parlay, BTC's read, Ventures HQ items due today, today's Arsenal drop, and tonight's games from Game Day. Readable in under a minute.

B. Goals
- Weekly + long-term goals, set Mondays. Check off, carry over unfinished ones, history of past weeks.

C. Personalized News Feed
- Seed sources: Variety, Deadline, The Hollywood Reporter, Boardroom, Puck, Axios Pro Rata, The Information, Semafor Business, Bloomberg, Sportico, Front Office Sports, Complex, The Ringer. Expandable.
- Pull via RSS/news APIs. Short AI summary + link to the original. No scraping or republishing full articles — summary + link is the design.
- Love / Neutral / Not Interested on every article.
- Self-learning: store every reaction, build an interest profile (topics, sources, keywords, embeddings), re-rank daily. Seeded with my starting interests on day one.

### 2. WAGS (AI advisor)
- My in-house consigliere: a chat that has context across every pillar — goals, ventures, bets, model performance, bots, crypto reads, Arsenal, Lookbook.
- Full page + a floating button on every page that opens Wags with the current page's context loaded.
- Can answer "what should I focus on today," "how's the model doing this month," "pressure-test this idea," "summarize my week."
- Conversation history saved. Tone: direct, sharp, trusted-advisor energy.

### 3. VENTURES HQ
- One card per venture: Sail Beach Club, Perfect Timing Management, Clipd, NCLEXCompass, Senior Care Systems, Valemont Grow, Excursion, Sims & Vale Capital, freelance web dev.
- Each card: status/stage, next action, blockers, key dates/deadlines, notes. Tap in for detail view with running log.
- "Today" items surface in Morning Brief. Add/archive ventures anytime.

### 4. CAPITAL TRACKER
- Built fully in the first version. Runs on PAPER data from day one (paper bankroll, paper bot equity, tracked crypto positions), clearly labeled PAPER.
- When any bot or betting flow switches to live, it automatically shows real capital alongside/instead of paper — no rebuild.
- Shows total, daily change, breakdown by source, history chart.

### 5. THE ARSENAL (reference library, Billions-style)
- Daily drop: 2–3 references across rotating categories — history/military, literature/myth, film/TV, sports, finance, music/culture, philosophy.
- Each card: origin in two lines, what it means, a ready-to-use line showing how to deploy it in conversation, and the room it plays best in (boardroom, pitch, negotiation, homies).
- "Arm Me" box: I describe a situation, it gives 3 fitting references with deploy-ready lines.
- Power Word of the Day with the same deploy-it format.
- Save references to My Arsenal; weekly quiz on saved ones.

### 6. SCREENING ROOM
- Movie + TV tracker: release dates, trailers, where to stream, watchlist, currently-watching shelf (use TMDB or similar).
- Recs that learn from Love / Neutral / Not Interested, seeded with Succession, Billions, Ozark, Yellowstone, The Gentlemen, Tulsa King.
- When I finish a show, pull its best references/power moves into The Arsenal.

### 7. THE LOOKBOOK
- Pinterest sync via official Pinterest API v5 (OAuth to my account): my boards and pins flow in automatically, organized by board/category.
- Discovery feed inside the dashboard: aesthetic + fit inspo from Unsplash/Pexels-type APIs and any other viable sources, with Love / Not Interested learning.
- "Add from anywhere": share/upload images from Instagram, X, camera roll straight into the Lookbook.
- Galleries by custom categories I create (Fits, Watches, IG Aesthetic, Spaces, etc.).
- AI auto-tagging with Claude vision (style, colors, pieces) so categories organize themselves.
- Style DNA: summary of my aesthetic from my saves, plus fit and IG post suggestions that match it.

### 8. TECH HUB
- Anthropic/Claude + OpenAI/ChatGPT news: new features, models, free credits, promos, pricing changes.
- Sources: official blogs/changelogs, Hacker News, r/ClaudeAI, r/OpenAI, r/LocalLLaMA, The Information.
- GitHub: trending + most-starred repos filtered to what I can use (AI agents, trading, Next.js, automation, marketing). One-line "why this matters to you" + learning buttons.

### 9. SPORTS NEWS
- College Football, NFL, NBA, F1, Golf.
- Sources: ESPN, Action Network, The Race, Golf Digest, Boardroom, Front Office Sports, official league feeds.
- Same summary + link + learning format.

### 10. GAME DAY
- One schedule for every game I care about across CFB, NFL, NBA, F1, Golf: times, channels, matchups.
- My active bets attached to each game. Tap a game → jump straight into its Live Tracker view.
- Tonight's slate feeds the Morning Brief.

### 11. BETTING (core feature)
A. Daily Parlay Generator
- Multiple parlays daily across any available sport, 3-leg up to 20-leg lottos.
- Sportsbook: Kalshi, via Kalshi's API for markets and prices. Check Kalshi's current combo/leg rules; if anything caps leg size, generate within the cap and also show bigger lottos as "build manually" slips.
- Model: reuse the Kalshi model started in Valemont Agents (moneylines, spreads, player props), augmented with proven open-source models from GitHub. Covers all my sports.
- Each parlay: every leg, model probability per leg, combined probability, Kalshi price/implied odds, edge vs. market, one-line reason per leg. Tiers: Safer (3–5), Mid (6–10), Lotto (11–20).
- Handle correlated (same-game) legs properly instead of naively multiplying.
- Track the model's historical hit rate by tier and sport.

B. Slip Analyzer
- Upload screenshots of parlays (Twitter, friends' models, other books). Claude vision parses legs, runs each through our model.
- Output: per-leg probability, overall probability, weakest legs, suggested swaps.

C. Manual Slip Entry
- Upload screenshots of slips I placed elsewhere (PrizePicks, etc.). Auto-parse; I confirm/edit.

D. "I'm playing this" button
- Any generated, analyzed, or entered slip → Live Tracker with stake and payout.

### 12. LIVE BET TRACKER
- Every active bet live: current stat vs. line (e.g., 47/75 rec yds, Q3), leg status (hit / on pace / in danger / dead), live probability per leg and whole slip.
- Data: live stats API (free ESPN endpoints first, paid feed as fallback) + live odds where available.
- Push alerts on leg hits and danger. Settled bets → history with P/L; results feed model performance tracking and Capital Tracker.

### 13. CRYPTO — LONG-TERM HOME
- Live prices: BTC, ETH, SOL, XRP (expandable).
- Accumulation signals: trend, on-chain/cycle metrics, DCA zones. Plain-language "accumulate / hold / wait" with reasoning.
- News strip: CoinDesk, The Block, Decrypt.

### 14. CORE PAPER TRADING BOT
- On/off toggle from dashboard (bot runs on Railway).
- Stats: P/L, win rate, drawdown, equity curve. Full trade history with reasoning for every entry/exit.
- Self-learning with a "what it learned" log.
- Paper/live mode switch built in, separate keys, hard risk limits — flips to real money with no rewrite.

### 15. MEMECOIN PAPER TRADING BOT
- Same features as #14 for memecoins. DEX data (DexScreener/Birdeye-type APIs). Rug/honeypot and liquidity checks before any entry.

### 16. KALSHI 15-MIN BTC
- Live BTC price + current Kalshi 15-minute BTC market.
- Model direction call for the window with confidence.
- "Entered" logs my position/price; "Exit" logs my exit.
- Profit-only smart exit alerts (push notification).
- Every call and result logged; accuracy visible.

## SHARED SYSTEMS (build once, use everywhere)
- Learning engine (Love / Neutral / Not Interested) — Home, Tech Hub, Sports News, Screening Room, Lookbook
- Screenshot/image → structured data (Claude vision) — Slip Analyzer, Manual Entry, Lookbook tagging
- Cross-pillar context layer — feeds Wags and Morning Brief
- PWA push notifications — exit alerts, bet status, picks ready, Morning Brief ready
- Railway scheduler — news pulls, Morning Brief, parlay generation, Arsenal drops, Lookbook sync, model retraining
- Paper vs. live data separation across all money-related tables

## FOR EVERY CHAT AFTER THE MASTER PLAN
1. Read this brief and the approved Master Plan.
2. Ask me your clarifying questions.
3. Search GitHub for reusable repos; recommend what to use.
4. Give me phased Claude Code prompts, each with a "done when" checklist.
5. List any API keys/accounts I need for that phase.
6. Confirm everything in that chat's scope is complete before we move on — nothing deferred.
