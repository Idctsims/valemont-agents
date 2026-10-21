import "server-only";

import type { LanguageModelV3StreamPart } from "@ai-sdk/provider";
import { simulateReadableStream } from "ai";
import { MockLanguageModelV3 } from "ai/test";

import type { ArmCardIn } from "./arm";

// Arm Me's e2e stand-in for Claude (docs/arsenal.md §9.3). Loaded only when
// policy.mockAllowed() says so (VERCEL unset AND WAGS_MOCK=1), so no
// deployment can reach it and no e2e run spends a cent. It writes the same
// text the real model does under Output.array, {"elements":[...]}, slowly
// enough that a spec sees card 1 on the page before card 3 exists.
// Scripted by the situation:
//   "existing: <key>"   card 2 uses that canonical_key (an existing row comes
//                       back with its real tag)
//   contains "slow"     a longer pause between cards
//   anything else       three fresh test-keyed cards (e2e_mock_<run>_<n>)

const usage = (input: number, output: number) => ({
  inputTokens: { total: input, noCache: input, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: output, text: output, reasoning: 0 },
});

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((p) => (p && typeof p === "object" && "text" in p ? String(p.text) : "")).join("\n");
  }
  return "";
}

const MOCK_CARDS: Omit<ArmCardIn, "canonical_key">[] = [
  {
    category: "history_military", title: "Crossing the Rubicon (mock)", room: "boardroom",
    origin: "Caesar led one legion across the Rubicon in 49 BC, which made civil war unavoidable.",
    meaning: "A decision that cannot be walked back.", deploy_line: "Once we sign this, we have crossed the Rubicon.",
    source_work: "Julius Caesar, 49 BC", quote: null, quote_by: null, source_kind: "wikipedia",
    source_page: "Crossing the Rubicon", source_url: "https://en.wikipedia.org/wiki/Crossing_the_Rubicon",
    from_arsenal: false,
  },
  {
    category: "finance_business", title: "See's Candies (mock)", room: "pitch",
    origin: "Berkshire bought See's in 1972 for $25 million; it has raised prices every year since.",
    meaning: "Pricing power is the moat.", deploy_line: "Our members pay more every year and thank us. That is See's Candies.",
    source_work: "Buffett, 2007 Berkshire Hathaway letter", quote: null, quote_by: null, source_kind: "wikipedia",
    source_page: "See's Candies", source_url: "https://en.wikipedia.org/wiki/See%27s_Candies", from_arsenal: false,
  },
  {
    category: "film_tv", title: "Never tell anybody outside the family (mock)", room: "negotiation",
    origin: "Vito Corleone rebukes Sonny for showing his hand to Sollozzo.",
    meaning: "Keep your read of the deal to yourself.", deploy_line: "I'll keep my thinking inside the family for now.",
    source_work: "The Godfather (1972)", quote: "Never tell anybody outside the family what you're thinking again.",
    // No link given: the card says "No source given".
    quote_by: "Vito Corleone", source_kind: "wikiquote", source_page: "The Godfather", source_url: null,
    from_arsenal: false,
  },
];

export function mockArmModel() {
  const run = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;
  return new MockLanguageModelV3({
    provider: "mock",
    modelId: "mock",
    doStream: async ({ prompt }) => {
      const messages = prompt as unknown as { role: string; content: unknown }[];
      const said = textOf([...messages].reverse().find((m) => m.role === "user")?.content);
      const existing = said.match(/existing:\s*([a-z0-9_]+)/i)?.[1]?.toLowerCase() ?? null;
      const slow = /slow/i.test(said);
      const cards = MOCK_CARDS.map((c, i) => ({
        canonical_key: i === 1 && existing ? existing : `e2e_mock_${run}_${i + 1}`,
        ...c,
        from_arsenal: i === 1 && !!existing,
      }));
      // The JSON text in small pieces; a pause before each card after the first.
      const chunks: LanguageModelV3StreamPart[] = [{ type: "stream-start", warnings: [] }, { type: "text-start", id: "t" }];
      chunks.push({ type: "text-delta", id: "t", delta: '{"elements":[' });
      cards.forEach((card, i) => {
        const json = (i ? "," : "") + JSON.stringify(card);
        for (let at = 0; at < json.length; at += 40) chunks.push({ type: "text-delta", id: "t", delta: json.slice(at, at + 40) });
      });
      chunks.push({ type: "text-delta", id: "t", delta: "]}" }, { type: "text-end", id: "t" },
                  { type: "finish", finishReason: { unified: "stop", raw: "end_turn" }, usage: usage(2550, 650) });
      return {
        // ~10 chunks a card: slow is ~1 s between cards, a window a spec can see.
        stream: simulateReadableStream({ initialDelayInMs: 30, chunkDelayInMs: slow ? 100 : 15, chunks }),
      };
    },
  });
}
