// The server-side Supabase client's one retry on the gateway's clock skew
// (src/lib/supabase/skew-retry.ts), no browser and no server: it retries once
// on 401 PGRST303, never on any other error, and gives up after one.
// Run: pnpm test:unit

import { expect, test } from "@playwright/test";

import { SKEW_RETRY_MS, isSkewRejection, withSkewRetry } from "../src/lib/supabase/skew-retry";

const SKEW = JSON.stringify({ code: "PGRST303", details: null, hint: null, message: "JWT issued at future" });
const ok = () => new Response("[]", { status: 200 });
const skew = () => new Response(SKEW, { status: 401 });

/** A fetch that answers from a script, and records every call. */
function scripted(...answers: (() => Response | Promise<Response>)[]) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const isReq = input instanceof Request;
    calls.push({
      url: isReq ? input.url : String(input),
      method: init?.method ?? (isReq ? input.method : "GET"),
      body: isReq ? await input.text() : init?.body,
    });
    const next = answers[Math.min(calls.length - 1, answers.length - 1)];
    return next();
  }) as typeof globalThis.fetch;
  return { fetch, calls };
}

function harness(...answers: (() => Response | Promise<Response>)[]) {
  const s = scripted(...answers);
  const slept: number[] = [];
  const logs: string[] = [];
  const wrapped = withSkewRetry(s.fetch, {
    sleep: async (ms) => void slept.push(ms),
    log: (line) => void logs.push(line),
  });
  return { ...s, slept, logs, wrapped };
}

test("recognises PGRST303 and only PGRST303", () => {
  expect(isSkewRejection(401, SKEW)).toBe(true);
  expect(isSkewRejection(401, "JWT issued at future")).toBe(true);
  expect(isSkewRejection(401, JSON.stringify({ code: "PGRST301", message: "JWT expired" }))).toBe(false);
  expect(isSkewRejection(403, SKEW)).toBe(false);
  expect(isSkewRejection(500, SKEW)).toBe(false);
});

test("a skew rejection is retried once after ~250 ms, logged as one line, and the retry's answer returned", async () => {
  const h = harness(skew, ok);
  const res = await h.wrapped("https://x.supabase.co/rest/v1/goals?select=id&title=eq.secret", { method: "GET" });
  expect(res.status).toBe(200);
  expect(h.calls).toHaveLength(2);
  expect(h.slept).toEqual([SKEW_RETRY_MS]);
  expect(SKEW_RETRY_MS).toBe(250);
  expect(h.logs).toEqual(["supabase: JWT issued at future (PGRST303) on GET /rest/v1/goals; retrying once in 250 ms"]);
  expect(h.logs[0], "the query (filter values) is never logged").not.toContain("secret");
});

test("a write is retried with the same body (the skew rejected it before it did anything)", async () => {
  const h = harness(skew, () => new Response("", { status: 201 }));
  const body = JSON.stringify({ title: "Sign the permit" });
  const res = await h.wrapped("https://x.supabase.co/rest/v1/goals", { method: "POST", body });
  expect(res.status).toBe(201);
  expect(h.calls.map((c) => [c.method, c.body])).toEqual([
    ["POST", body],
    ["POST", body],
  ]);
});

test("a Request object is retried from a fresh copy of its body", async () => {
  const h = harness(skew, ok);
  const req = new Request("https://x.supabase.co/rest/v1/rpc/carry_goal", { method: "POST", body: '{"p_goal":"1"}' });
  await h.wrapped(req);
  expect(h.calls.map((c) => c.body)).toEqual(['{"p_goal":"1"}', '{"p_goal":"1"}']);
});

test("it gives up after one retry: a second skew is returned as it came", async () => {
  const h = harness(skew, skew, ok);
  const res = await h.wrapped("https://x.supabase.co/rest/v1/goals", { method: "GET" });
  expect(res.status).toBe(401);
  expect(await res.json()).toMatchObject({ code: "PGRST303" });
  expect(h.calls).toHaveLength(2);
  expect(h.logs).toHaveLength(1);
});

test("never retries any other error", async () => {
  for (const other of [
    () => new Response(JSON.stringify({ code: "PGRST301", message: "JWT expired" }), { status: 401 }),
    () => new Response(JSON.stringify({ code: "42501", message: "permission denied" }), { status: 403 }),
    () => new Response(JSON.stringify({ code: "23505", message: "duplicate key" }), { status: 409 }),
    () => new Response("bad gateway", { status: 502 }),
    ok,
  ]) {
    const h = harness(other, ok);
    const res = await h.wrapped("https://x.supabase.co/rest/v1/goals", { method: "PATCH", body: "{}" });
    expect(h.calls, `status ${res.status}`).toHaveLength(1);
    expect(h.slept).toEqual([]);
    expect(h.logs).toEqual([]);
  }
});

test("a network failure is thrown as it came, not retried", async () => {
  const h = harness(() => Promise.reject(new TypeError("fetch failed")), ok);
  await expect(h.wrapped("https://x.supabase.co/rest/v1/goals")).rejects.toThrow("fetch failed");
  expect(h.calls).toHaveLength(1);
});

test("a one-shot stream body is never resent", async () => {
  const h = harness(skew, ok);
  const stream = new ReadableStream({ start: (c) => (c.enqueue(new TextEncoder().encode("{}")), c.close()) });
  const res = await h.wrapped("https://x.supabase.co/rest/v1/goals", { method: "POST", body: stream, duplex: "half" } as RequestInit);
  expect(res.status).toBe(401);
  expect(h.calls).toHaveLength(1);
});
