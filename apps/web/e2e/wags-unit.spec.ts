// Wags and the AI budget twin, no browser and no server: the budget policy
// (core/ai.py's thresholds), the once-a-month alerts, the mock gate (VERCEL
// unset AND WAGS_MOCK=1, nothing else), web costing (the same arithmetic as
// core/ai.py cost_usd; workers/tests/test_ai.py WebTwinPricing checks the
// rates themselves), a thread rebuilt from its rows, and page context.
// Run: pnpm test:unit

import { expect, test } from "@playwright/test";

import { alertDue, decide, mockAllowed, monthlyBudget } from "../src/lib/ai/policy";
import { CHEAPEST_MODEL, PRICING, WAGS_MODEL, costUsd } from "../src/lib/ai/pricing";
import { pageSlice, parsePageContext } from "../src/lib/wags/page-context";
import { forModel, rowsToMessages, type MessageRow } from "../src/lib/wags/thread";

test.describe("budget policy", () => {
  test("under 80% runs as asked", () => {
    expect(decide(15.99, 20, WAGS_MODEL, false)).toEqual({ kind: "normal", model: WAGS_MODEL });
  });
  test("from 80% a non-critical call drops to Haiku", () => {
    expect(decide(16, 20, WAGS_MODEL, false)).toEqual({ kind: "downgrade", model: CHEAPEST_MODEL });
    expect(decide(19.99, 20, WAGS_MODEL, false).kind).toBe("downgrade");
  });
  test("at or over 100% a non-critical call is refused with a sentence", () => {
    const d = decide(20, 20, WAGS_MODEL, false);
    expect(d.kind).toBe("refuse");
    if (d.kind === "refuse") {
      expect(d.message).toBe("This month's AI budget is spent ($20.00 of $20.00). Wags is back on the 1st, or when the budget is raised.");
      expect(d.message).not.toMatch(/\b(402|error|code)\b/i);
    }
    expect(decide(25, 20, WAGS_MODEL, false).kind).toBe("refuse");
  });
  test("a critical call is never downgraded or refused", () => {
    expect(decide(16, 20, WAGS_MODEL, true)).toEqual({ kind: "normal", model: WAGS_MODEL });
    expect(decide(30, 20, WAGS_MODEL, true)).toEqual({ kind: "normal", model: WAGS_MODEL });
  });
  test("a Haiku call at 80% stays Haiku, not a 'downgrade'", () => {
    expect(decide(17, 20, CHEAPEST_MODEL, false)).toEqual({ kind: "normal", model: CHEAPEST_MODEL });
  });
  test("the budget comes from AI_MONTHLY_BUDGET_USD like core/ai.py", () => {
    expect(monthlyBudget({})).toBe(10);
    expect(monthlyBudget({ AI_MONTHLY_BUDGET_USD: " 20 " })).toBe(20);
    for (const raw of ["0", "-5", "ten", "NaN", "Infinity"]) {
      expect(() => monthlyBudget({ AI_MONTHLY_BUDGET_USD: raw }), raw).toThrow(/positive number/);
    }
  });
});

test.describe("budget alerts: once per threshold per month, shared with the worker", () => {
  const none = { alerted_80: false, alerted_100: false };
  test("nothing under 80%", () => expect(alertDue(15, 20, none)).toBeNull());
  test("80% once", () => {
    expect(alertDue(16, 20, none)).toBe("ai_budget_80");
    expect(alertDue(17, 20, { alerted_80: true, alerted_100: false })).toBeNull();
  });
  test("100% once, even after the 80% alert", () => {
    expect(alertDue(20, 20, { alerted_80: true, alerted_100: false })).toBe("ai_budget_100");
    expect(alertDue(21, 20, { alerted_80: true, alerted_100: true })).toBeNull();
  });
});

test.describe("the mock model gate", () => {
  test("only off Vercel and only when asked", () => {
    expect(mockAllowed({ WAGS_MOCK: "1" })).toBe(true);
    expect(mockAllowed({})).toBe(false);
    expect(mockAllowed({ WAGS_MOCK: "true" })).toBe(false);
    expect(mockAllowed({ WAGS_MOCK: "0" })).toBe(false);
  });
  test("every Vercel deployment uses the real model, whatever WAGS_MOCK says", () => {
    for (const env of ["production", "preview", "development"]) {
      expect(mockAllowed({ VERCEL: "1", VERCEL_ENV: env, WAGS_MOCK: "1" }), env).toBe(false);
    }
  });
});

test.describe("costing, the same arithmetic as core/ai.py", () => {
  const u = (tokensIn: number, tokensOut: number, cacheRead = 0, cacheWrite5m = 0) => ({ tokensIn, tokensOut, cacheRead, cacheWrite5m });
  test("the ai_smoke call: 18 in, 4 out on Haiku = $0.000004 (ai_usage id 64)", () => {
    expect(costUsd("claude-haiku-5-5", u(18, 4))).toBe(0.000004);
  });
  test("Sonnet 5.5 rates, cache reads at 0.05x input (verified 2026-10-10)", () => {
    expect(costUsd("claude-sonnet-5-5", u(1_000_000, 0))).toBe(2);
    expect(costUsd("claude-sonnet-5-5", u(0, 1_000_000))).toBe(10);
    expect(costUsd("claude-sonnet-5-5", u(0, 0, 1_000_000))).toBe(0.1);
    expect(costUsd("claude-sonnet-5-5", u(0, 0, 0, 1_000_000))).toBe(2.5);
  });
  test("a typical Wags turn: 3,000 cached, 200 fresh in, 400 out", () => {
    // 200*2 + 400*10 + 3000*0.10 = 4,700 micro-dollars
    expect(costUsd("claude-sonnet-5-5", u(200, 400, 3000))).toBe(0.0047);
  });
  test("half a micro-dollar rounds up, like ROUND_HALF_UP", () => {
    expect(costUsd("claude-haiku-5-5", u(5, 0))).toBe(0.000001); // 0.5 micro
    expect(costUsd("claude-haiku-5-5", u(4, 0))).toBe(0); // 0.4 micro
  });
  test("Haiku switches rate card above 100K prompt tokens", () => {
    expect(costUsd("claude-haiku-5-5", u(100_000, 0))).toBe(0.01);
    expect(costUsd("claude-haiku-5-5", u(100_001, 0))).toBe(0.050001);
  });
  test("an unpriced model throws rather than guessing", () => {
    expect(() => costUsd("claude-opus-9", u(1, 1))).toThrow(/No pricing/);
    expect(Object.keys(PRICING)).toContain(WAGS_MODEL);
  });
});

const row = (id: number, role: MessageRow["role"], parts: unknown[], extra: Partial<MessageRow> = {}): MessageRow => ({
  id,
  role,
  content: "",
  parts,
  model: null,
  incomplete: false,
  cost_usd: null,
  created_at: "2026-10-10T16:00:00Z",
  ...extra,
});

const proposal = (id: string) => ({
  type: "tool-propose_goal",
  toolCallId: id,
  state: "input-available",
  input: { title: "Sign the SBC permit", horizon: "weekly", area: "business" },
});

test.describe("a thread rebuilt from its rows", () => {
  test("a tool row answers its proposal; the tool row itself is not a message", () => {
    const ms = rowsToMessages([
      row(1, "user", [{ type: "text", text: "what now" }]),
      row(2, "assistant", [{ type: "text", text: "Lock this in." }, proposal("c1")], { model: "claude-sonnet-5-5", cost_usd: "0.0047" }),
      row(3, "tool", [{ toolCallId: "c1", output: { status: "confirmed", summary: "Added to this week." } }]),
    ]);
    expect(ms.map((m) => m.role)).toEqual(["user", "assistant"]);
    const part = ms[1].parts[1] as { state: string; output: unknown };
    expect(part.state).toBe("output-available");
    expect(part.output).toEqual({ status: "confirmed", summary: "Added to this week." });
    expect(ms[1].metadata).toEqual({ model: "claude-sonnet-5-5", incomplete: undefined, costUsd: 0.0047 });
  });

  test("the model's copy answers an unanswered proposal as not_answered, and the UI copy stays pending", () => {
    const ui = rowsToMessages([row(1, "user", [{ type: "text", text: "hi" }]), row(2, "assistant", [proposal("c2")])]);
    const model = forModel(ui);
    expect((model[1].parts[0] as { state: string; output: { status: string } }).output.status).toBe("not_answered");
    expect((ui[1].parts[0] as { state: string }).state).toBe("input-available");
  });

  test("an interrupted tool call is dropped and an empty assistant turn is skipped", () => {
    const ui = rowsToMessages([
      row(1, "user", [{ type: "text", text: "hi" }]),
      row(2, "assistant", [{ type: "step-start" }, { ...proposal("c3"), state: "input-streaming" }], { incomplete: true }),
      row(3, "user", [{ type: "text", text: "again" }]),
    ]);
    expect(forModel(ui).map((m) => m.role)).toEqual(["user", "user"]);
    expect(ui[1].metadata?.incomplete).toBe(true);
  });
});

test.describe("page context", () => {
  test("parses a good one and refuses anything off", () => {
    expect(parsePageContext({ route: "/ventures/sail-beach-club", label: "Ventures · Sail Beach Club", pillar: "ventures", slug: "sail-beach-club" })).toEqual({
      route: "/ventures/sail-beach-club",
      label: "Ventures · Sail Beach Club",
      pillar: "ventures",
      slug: "sail-beach-club",
    });
    expect(parsePageContext({ route: "//evil.example", label: "x" })).toBeNull();
    expect(parsePageContext({ route: "/goals", label: "" })).toBeNull();
    expect(parsePageContext({ route: "/goals", label: "Goals", pillar: "bets", slug: "Bad Slug" })).toEqual({ route: "/goals", label: "Goals" });
    expect(parsePageContext(null)).toBeNull();
  });
  test("a venture page's slice is that venture; a pillar page's is the pillar", () => {
    const payload = { ventures: { active: [{ slug: "sbc", name: "SBC" }, { slug: "x", name: "X" }] }, goals: { week: { done: 1 } } };
    expect(pageSlice(payload, { route: "/ventures/sbc", label: "Ventures · SBC", pillar: "ventures", slug: "sbc" })).toEqual({ slug: "sbc", name: "SBC" });
    expect(pageSlice(payload, { route: "/goals", label: "Goals", pillar: "goals" })).toEqual({ week: { done: 1 } });
    expect(pageSlice(payload, { route: "/design", label: "Design tokens" })).toBeNull();
  });
});
