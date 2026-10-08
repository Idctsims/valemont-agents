// The site map from docs/BUILD_BRIEF.md, as data. Navigation, section pages
// and the sixteen pillar routes are all generated from this one list.

export type SectionKey = "command" | "culture" | "tech" | "sports" | "crypto";

export type Pillar = {
  /** Position in the brief's site map, 1–16. */
  n: number;
  slug: string;
  name: string;
  /** What the pillar does, in one plain sentence. */
  summary: string;
  /** What will appear on the page once it is built. */
  holds: string[];
  /** The build chat(s) that deliver it (docs/MASTER_PLAN.md §6). */
  arrives: string;
  /** Moves or tracks money, paper by default (CLAUDE.md §1). */
  money?: boolean;
};

export type Section = {
  key: SectionKey;
  name: string;
  summary: string;
  pillars: Pillar[];
};

export const SECTIONS: Section[] = [
  {
    key: "command",
    name: "Command",
    summary: "The day, the ventures and the money, read in one pass.",
    pillars: [
      {
        n: 1,
        slug: "home",
        name: "Home",
        summary: "The Morning Brief, this week's goals and a feed that learns what you read.",
        holds: [
          "Morning Brief, written before you wake",
          "Weekly and long-term goals with carry-over",
          "News ranked by your Love and Not Interested taps",
        ],
        arrives: "Chats 2 and 3",
      },
      {
        n: 2,
        slug: "wags",
        name: "Wags",
        summary: "An advisor with context across every pillar, one tap from any page.",
        holds: [
          "Saved threads",
          "Answers that cite your goals, ventures, bets and bots",
          "Opens with the page you came from loaded",
        ],
        arrives: "Chat 2",
      },
      {
        n: 3,
        slug: "ventures",
        name: "Ventures HQ",
        summary: "One card per venture: stage, next action, blockers and dates.",
        holds: [
          "Nine ventures, from Sail Beach Club to freelance web dev",
          "A running log per venture",
          "Anything due today surfaces in the Brief",
        ],
        arrives: "Chat 2",
      },
      {
        n: 4,
        slug: "capital",
        name: "Capital Tracker",
        summary: "Total, daily change and breakdown by source, paper and live kept apart.",
        holds: [
          "Paper bankroll, bot equity and crypto positions",
          "History chart from nightly snapshots",
          "Live capital alongside paper once a bot goes live",
        ],
        arrives: "Chat 2",
        money: true,
      },
    ],
  },
  {
    key: "culture",
    name: "Culture",
    summary: "References, screens and style: the material for the room.",
    pillars: [
      {
        n: 5,
        slug: "arsenal",
        name: "The Arsenal",
        summary: "A daily drop of references, each with a line ready to use.",
        holds: [
          "Two or three references a day, rotating categories",
          "Power Word of the Day",
          "Arm Me: describe the room, get three references",
        ],
        arrives: "Chat 5",
      },
      {
        n: 6,
        slug: "screening-room",
        name: "Screening Room",
        summary: "Watchlist, release dates, trailers and where to stream.",
        holds: [
          "Currently watching shelf",
          "Recommendations seeded from Succession, Billions and Ozark",
          "Finished shows feed the Arsenal",
        ],
        arrives: "Chat 4",
      },
      {
        n: 7,
        slug: "lookbook",
        name: "The Lookbook",
        summary: "Pinterest boards, uploads and discovery, organised by your categories.",
        holds: [
          "Boards synced from Pinterest",
          "Share-sheet uploads, tagged automatically",
          "Style DNA from what you save",
        ],
        arrives: "Chat 6",
      },
    ],
  },
  {
    key: "tech",
    name: "Tech",
    summary: "Model releases, pricing changes and the repos worth your time.",
    pillars: [
      {
        n: 8,
        slug: "tech-hub",
        name: "Tech Hub",
        summary: "Claude and OpenAI news, plus GitHub repos filtered to what you can use.",
        holds: [
          "Official changelogs, Hacker News and three subreddits",
          "Trending repos with why each one matters to you",
          "Credits, promos and pricing changes",
        ],
        arrives: "Chat 4",
      },
    ],
  },
  {
    key: "sports",
    name: "Sports",
    summary: "Tonight's games, the slips riding on them and how each leg stands.",
    pillars: [
      {
        n: 9,
        slug: "news",
        name: "Sports News",
        summary: "CFB, NFL, NBA, F1 and golf, summarised and linked to the source.",
        holds: [
          "Filter chips by sport",
          "Reactions train the sports feed separately",
          "Summary plus link, never the full article",
        ],
        arrives: "Chat 3",
      },
      {
        n: 10,
        slug: "game-day",
        name: "Game Day",
        summary: "Every game you care about: time, channel, matchup and your bets on it.",
        holds: [
          "One schedule across five sports",
          "Each NFL and NBA game linked to its Kalshi event",
          "Tap a game to open its live tracker",
        ],
        arrives: "Chat 7",
      },
      {
        n: 11,
        slug: "betting",
        name: "Betting",
        summary: "Daily parlays priced against Kalshi, a slip analyzer and manual entry.",
        holds: [
          "Safer, Mid and Lotto tiers, each leg with edge net of fees",
          "Screenshot a slip, get per-leg probabilities and swaps",
          "Every generated parlay committed before games start",
        ],
        arrives: "Chats 8 and 9",
        money: true,
      },
      {
        n: 12,
        slug: "live-tracker",
        name: "Live Bet Tracker",
        summary: "Every active leg: stat against line, status and live probability.",
        holds: [
          "Hit, on pace, in danger or dead, per leg",
          "Push alerts on hits and danger",
          "Settled slips move to history with P/L",
        ],
        arrives: "Chat 9",
        money: true,
      },
    ],
  },
  {
    key: "crypto",
    name: "Crypto",
    summary: "Long-term reads, two paper bots and the 15-minute BTC window.",
    pillars: [
      {
        n: 13,
        slug: "long-term",
        name: "Long-Term Home",
        summary: "BTC, ETH, SOL and XRP with an accumulate, hold or wait call and why.",
        holds: [
          "Live prices",
          "Accumulation signals with reasoning",
          "CoinDesk, The Block and Decrypt strip",
        ],
        arrives: "Chat 10",
      },
      {
        n: 14,
        slug: "core-bot",
        name: "Core Paper Bot",
        summary: "A strategy bot on Alpaca paper, with every entry and exit explained.",
        holds: [
          "On/off toggle that stops it within one cycle",
          "P/L, win rate, drawdown and equity curve",
          "What it learned, logged",
        ],
        arrives: "Chat 10",
        money: true,
      },
      {
        n: 15,
        slug: "memecoin-bot",
        name: "Memecoin Paper Bot",
        summary: "DEX tokens screened for rugs and liquidity before any paper entry.",
        holds: [
          "RugCheck and GoPlus screens, both required",
          "Paper fills on real Jupiter quotes",
          "Live mode sends you the call; you execute in FOMO",
        ],
        arrives: "Chat 11",
        money: true,
      },
      {
        n: 16,
        slug: "btc-15",
        name: "Kalshi 15-Min BTC",
        summary: "A direction call for each 15-minute window, locked before it opens.",
        holds: [
          "Live BTC price and the current Kalshi market",
          "Entered and Exit logging",
          "Exit alerts only when the position is in profit",
        ],
        arrives: "Chat 12",
        money: true,
      },
    ],
  },
];

export function getSection(key: string): Section | undefined {
  return SECTIONS.find((s) => s.key === key);
}

export function getPillar(section: string, slug: string): Pillar | undefined {
  return getSection(section)?.pillars.find((p) => p.slug === slug);
}

export const pad2 = (n: number) => String(n).padStart(2, "0");
