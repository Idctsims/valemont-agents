import "server-only";

import { MockLanguageModelV3 } from "ai/test";

import type { VibePick } from "./vibe";

// More like…'s e2e stand-in for Claude (docs/screening.md §8). Loaded only
// when policy.mockAllowed() says so (VERCEL unset AND WAGS_MOCK=1), and the
// route runs it only with the verified e2e marker, so no deployment reaches
// it and no e2e run spends a cent. Whatever the anchor, four picks: two real
// titles TMDB lists, one invented title (it must be dropped), and Gladiator,
// which must be dropped when Gladiator is the anchor.

const PICKS: VibePick[] = [
  { title: "Troy", year: 2004, kind: "movie", reason: "Ancient epic, brutal duels, tragic heroes" },
  { title: "Kingdom of Heaven", year: 2005, kind: "movie", reason: "Ridley Scott's other sweeping epic" },
  { title: "The Mock Epic That Was Never Made", year: 1999, kind: "movie", reason: "Invented: must never show" },
  { title: "Gladiator", year: 2000, kind: "movie", reason: "The anchor itself: must never show" },
];

export function mockVibeModel() {
  return new MockLanguageModelV3({
    provider: "mock",
    modelId: "mock",
    doGenerate: async () => ({
      content: [{ type: "text", text: JSON.stringify({ picks: PICKS }) }],
      finishReason: { unified: "stop", raw: "end_turn" },
      usage: {
        inputTokens: { total: 120, noCache: 120, cacheRead: 0, cacheWrite: 0 },
        outputTokens: { total: 300, text: 300, reasoning: 0 },
      },
      warnings: [],
    }),
  });
}
