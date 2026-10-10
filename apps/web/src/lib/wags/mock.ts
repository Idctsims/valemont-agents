import "server-only";

import type { LanguageModelV3StreamPart } from "@ai-sdk/provider";
import { simulateReadableStream } from "ai";
import { MockLanguageModelV3 } from "ai/test";

// The e2e stand-in for Claude. Loaded only when policy.mockAllowed() says so
// (VERCEL unset AND WAGS_MOCK=1), so no deployment can ever reach it, and no
// e2e run spends a cent. Scripted by the last user message:
//   "propose goal: <title>"  -> a sentence, then a propose_goal call for <title>
//   "propose silently: <t>"   -> the propose_goal call alone, no text
//   contains "slow"           -> a long answer, streamed slowly (stop button)
//   anything else            -> names the context pillars and page it was
//                                given, so a spec can see the context arrive.

type Prompt = { role: string; content: unknown }[];

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((p) => (p && typeof p === "object" && "text" in p ? String(p.text) : "")).join("\n");
  }
  return "";
}

const usage = (input: number, output: number) => ({
  inputTokens: { total: input, noCache: input, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: output, text: output, reasoning: 0 },
});

function textParts(text: string, id = "t1"): LanguageModelV3StreamPart[] {
  const words = text.split(/(?<= )/);
  return [
    { type: "text-start", id },
    ...words.map((w): LanguageModelV3StreamPart => ({ type: "text-delta", id, delta: w })),
    { type: "text-end", id },
  ];
}

export function mockModel() {
  return new MockLanguageModelV3({
    provider: "mock",
    modelId: "mock",
    doStream: async ({ prompt }) => {
      const messages = prompt as unknown as Prompt;
      const system = messages.filter((m) => m.role === "system").map((m) => textOf(m.content)).join("\n");
      const lastUser = [...messages].reverse().find((m) => m.role === "user");
      const said = lastUser ? textOf(lastUser.content) : "";

      const goal = said.match(/propose (goal|silently):\s*(.+)$/im);
      if (goal) {
        // An optional trailing "@slug" links the goal to that venture.
        const at = goal[2].match(/\s@([a-z0-9]+(?:-[a-z0-9]+)*)\s*$/);
        const title = (at ? goal[2].slice(0, at.index) : goal[2]).trim().slice(0, 200);
        // "propose silently:" is the proposal-only turn the route must give
        // words to (Phase 5 Step 0 item 4): a tool call and no text at all.
        const silent = goal[1].toLowerCase() === "silently";
        return {
          stream: simulateReadableStream({
            chunkDelayInMs: 5,
            chunks: [
              { type: "stream-start", warnings: [] },
              ...(silent ? [] : textParts("Lock this in for the week.")),
              {
                type: "tool-call",
                toolCallId: `mock-${Date.now().toString(36)}`,
                toolName: "propose_goal",
                input: JSON.stringify({ title, horizon: "weekly", area: "business", venture_slug: at?.[1] ?? null }),
              },
              { type: "finish", finishReason: { unified: "tool-calls", raw: "tool_use" }, usage: usage(1200, 40) },
            ] satisfies LanguageModelV3StreamPart[],
          }),
        };
      }

      const slow = /slow/i.test(said);
      const pillars = ["goals", "ventures", "capital"].filter((k) => system.includes(`"${k}"`));
      const page = said.match(/Opened from: ([^\]\n]+)/)?.[1]?.trim();
      const reply = slow
        ? Array.from({ length: 80 }, (_, i) => `Point ${i + 1} of a long answer.`).join(" ")
        : `**Mock Wags.** Context: ${pillars.join(", ") || "none"}. Page: ${page ?? "none"}.`;
      return {
        stream: simulateReadableStream({
          initialDelayInMs: 20,
          chunkDelayInMs: slow ? 120 : 10,
          chunks: [
            { type: "stream-start", warnings: [] },
            ...textParts(reply),
            { type: "finish", finishReason: { unified: "stop", raw: "end_turn" }, usage: usage(1200, 60) },
          ] satisfies LanguageModelV3StreamPart[],
        }),
      };
    },
  });
}
