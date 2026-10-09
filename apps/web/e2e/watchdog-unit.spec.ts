// Unit tests for the watchdog's decision logic (src/lib/watchdog/run.ts).
// No browser, no server, no Supabase: the store, the push sender, the
// healthchecks ping and the clock are fakes. Run: pnpm test:unit
//
// The failure these reproduce is real. On 2026-10-09 at 03:00 and 03:05 UTC
// Supabase's API gateway clock ran ahead of PostgREST, every request failed
// with "JWT issued at future" (PGRST303), and the route answered 500 to both
// cron calls: once on the plain "ok" path, once on the "down" path.

import { expect, test } from "@playwright/test";

import type { PushMessage, PushSummary } from "../src/lib/push/send";
import { runWatchdog, type WatchdogState } from "../src/lib/watchdog/run";

const NOW = Date.parse("2026-10-09T03:05:00Z");
const FRESH = new Date(NOW - 60_000).toISOString();
const STALE = new Date(NOW - 8 * 60_000).toISOString();
const SKEW = "JWT issued at future";
const BUDGETS = { readMs: 200, lookupMs: 200, pushMs: 200, stampMs: 200, pingMs: 200, retryDelayMs: 10 };

type Step<T> = T | Error | "hang";

function harness(o: {
  reads: Step<WatchdogState>[];
  lastDown?: Step<string | null>;
  push?: Step<PushSummary>;
  stamp?: Step<void>;
}) {
  const calls = {
    reads: 0,
    pushes: [] as PushMessage[],
    stamps: [] as { alertState: string; lastError: string | null }[],
    pings: [] as string[],
    logs: [] as string[],
  };
  const play = <T>(step: Step<T> | undefined, fallback: T): Promise<T> => {
    if (step === "hang") return new Promise<T>(() => {});
    if (step instanceof Error) return Promise.reject(step);
    return Promise.resolve(step === undefined ? fallback : step);
  };
  const deps = {
    now: () => NOW,
    store: {
      read: () => play(o.reads[Math.min(calls.reads++, o.reads.length - 1)], undefined as never),
      lastDownAlertAt: () => play(o.lastDown, null),
      stamp: (row: { alertState: "ok" | "alerted"; lastError: string | null; at: string }) => {
        calls.stamps.push({ alertState: row.alertState, lastError: row.lastError });
        return play(o.stamp, undefined);
      },
    },
    push: (_owner: string, message: PushMessage) => {
      calls.pushes.push(message);
      return play<PushSummary>(o.push, { status: "sent", delivered: 1, total: 1 });
    },
    ping: (kind: "success" | "fail") => {
      calls.pings.push(kind);
      return Promise.resolve();
    },
    log: (line: string) => calls.logs.push(line),
    budgets: BUDGETS,
  };
  return { deps, calls };
}

const state = (heartbeatOkAt: string, alerted = false): WatchdogState => ({
  ownerId: "owner",
  heartbeatOkAt,
  alerted,
});

test.describe.configure({ timeout: 3_000 });

test("1. a read that errors once (the 03:00 shape) is retried: 200 ok", async () => {
  const { deps, calls } = harness({ reads: [new Error(SKEW), state(FRESH)] });
  expect(await runWatchdog(deps)).toEqual({ status: "ok", httpStatus: 200 });
  expect(calls.reads).toBe(2);
  expect(calls.stamps).toEqual([{ alertState: "ok", lastError: null }]);
});

test("2. down path, the device lookup errors (the 03:05 shape): 200 down, stamped, alert left open", async () => {
  const { deps, calls } = harness({
    reads: [state(STALE)],
    push: new Error(`Could not read subscriptions: ${SKEW}`),
  });
  expect(await runWatchdog(deps)).toEqual({ status: "down", httpStatus: 200 });
  expect(calls.pushes.map((m) => m.kind)).toEqual(["watchdog_down"]);
  expect(calls.stamps).toHaveLength(1);
  expect(calls.stamps[0].alertState).toBe("ok"); // not sent: next call retries
  expect(calls.stamps[0].lastError).toContain(`push: Could not read subscriptions: ${SKEW}`);
});

test("3. down path, the push throws: 200 down, stamped with last_error, alert left open", async () => {
  const { deps, calls } = harness({ reads: [state(STALE)], push: new Error("fetch failed") });
  expect(await runWatchdog(deps)).toEqual({ status: "down", httpStatus: 200 });
  expect(calls.stamps).toEqual([{ alertState: "ok", lastError: "push: fetch failed" }]);
  expect(calls.pings).toEqual(["success"]); // the route itself worked
});

test("4. down path, a push that never resolves is cut off at the budget: 200 down", async () => {
  const { deps, calls } = harness({ reads: [state(STALE)], push: "hang" });
  const started = Date.now();
  expect(await runWatchdog(deps)).toEqual({ status: "down", httpStatus: 200 });
  expect(Date.now() - started).toBeLessThan(1_000);
  expect(calls.stamps[0].lastError).toBe(`push: timed out after ${BUDGETS.pushMs} ms`);
});

test("5. a read that fails twice: 503, nothing pushed, nothing stamped, healthchecks told", async () => {
  const { deps, calls } = harness({ reads: [new Error(SKEW), new Error(SKEW)] });
  expect(await runWatchdog(deps)).toEqual({ status: "error", httpStatus: 503 });
  expect(calls.pushes).toEqual([]);
  expect(calls.stamps).toEqual([]);
  expect(calls.pings).toEqual(["fail"]);
  expect(calls.logs.join("\n")).toContain(`"phase":"read"`);
  expect(calls.logs.join("\n")).toContain(SKEW);
});

test("6. ok path: no push, 200 ok, healthchecks pinged", async () => {
  const { deps, calls } = harness({ reads: [state(FRESH)] });
  expect(await runWatchdog(deps)).toEqual({ status: "ok", httpStatus: 200 });
  expect(calls.pushes).toEqual([]);
  expect(calls.pings).toEqual(["success"]);
});

test("7. a stamp that fails still returns the decided status", async () => {
  const { deps, calls } = harness({ reads: [state(STALE)], stamp: new Error(SKEW) });
  expect(await runWatchdog(deps)).toEqual({ status: "down", httpStatus: 200 });
  expect(calls.pushes.map((m) => m.kind)).toEqual(["watchdog_down"]);
  expect(calls.logs.join("\n")).toContain(`"phase":"stamp"`);
});
