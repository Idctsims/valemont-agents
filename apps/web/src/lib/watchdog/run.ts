// Watchdog decision logic, with its I/O behind small interfaces so it can be
// tested without Supabase, push services or a clock. The route
// (src/app/api/watchdog/route.ts) is the only place that builds the real
// store, holding the Supabase secret key; this file never sees it.
//
// No `server-only` import here and only type imports from the push sender:
// the unit tests (e2e/watchdog-unit.spec.ts) run under plain Node.
//
// Fault isolation, after the 2026-10-09 03:00–03:05 UTC incident (Supabase's
// API gateway clock ahead of PostgREST: every request "JWT issued at future",
// and the route turned each one into a 500):
//
//   read    heartbeat + own row. One retry. Still failing: 503 {"status":
//           "error"}: the watchdog genuinely cannot see the worker. Nothing
//           is pushed or stamped; healthchecks.io is told "fail".
//   alert   dedupe lookup + push. Every failure here is recorded, never
//           raised: the answer is still 200 "down", the incident stays open
//           (alert_state not advanced), and the next call retries.
//   stamp   always attempted, carrying last_error. Failure is logged; the
//           decided status is still returned.
//
// Every step has a time budget, so the whole call stays well under
// cron-job.org's 30 s timeout. One structured log line per call names the
// outcome, each phase's duration and the failing phase.

import type { PushMessage, PushSummary } from "@/lib/push/send";

export const DOWN_AFTER_MS = 5 * 60 * 1000;
export const REALERT_AFTER_MS = 30 * 60 * 1000;

export type WatchdogStatus = "ok" | "down" | "error";

export type WatchdogState = {
  ownerId: string;
  heartbeatOkAt: string | null;
  /** The route's own incident flag (job_health 'watchdog' alert_state). */
  alerted: boolean;
};

export interface WatchdogStore {
  read(): Promise<WatchdogState>;
  /** created_at of the newest 'watchdog_down' notification, or null. */
  lastDownAlertAt(): Promise<string | null>;
  stamp(row: { alertState: "ok" | "alerted"; lastError: string | null; at: string }): Promise<void>;
}

export type Pusher = (ownerId: string, message: PushMessage) => Promise<PushSummary>;

/** healthchecks.io dead-man's switch: "success" after a run that worked, "fail" when it could not see. */
export type Pinger = (kind: "success" | "fail") => Promise<void>;

export type Budgets = {
  readMs: number;
  lookupMs: number;
  pushMs: number;
  stampMs: number;
  pingMs: number;
  retryDelayMs: number;
};

/** Worst case ~ 2×4 + 0.5 + 3 + 6 + 4 + 3 ≈ 24.5 s, under cron-job.org's 30 s; normally ~1 s. */
export const DEFAULT_BUDGETS: Budgets = {
  readMs: 4_000,
  lookupMs: 3_000,
  pushMs: 6_000,
  stampMs: 4_000,
  pingMs: 3_000,
  retryDelayMs: 500,
};

export type WatchdogDeps = {
  store: WatchdogStore;
  push: Pusher;
  now: () => number;
  ping?: Pinger;
  log?: (line: string) => void;
  budgets?: Budgets;
};

export type WatchdogResult = { status: WatchdogStatus; httpStatus: number };

const DOWN: PushMessage = {
  kind: "watchdog_down",
  title: "Worker down",
  body: "",
  deepLink: "/settings/health",
  tag: "watchdog",
};
const BACK: PushMessage = {
  kind: "watchdog_recovered",
  title: "Worker back",
  body: "The Railway worker's heartbeat is current again.",
  deepLink: "/settings/health",
  tag: "watchdog",
};

class Timeout extends Error {}

/** Resolve or reject with `work`, or reject with Timeout after `ms`. */
function within<T>(ms: number, work: () => Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Timeout(`timed out after ${ms} ms`)), ms);
  });
  return Promise.race([work(), deadline]).finally(() => clearTimeout(timer));
}

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export async function runWatchdog(deps: WatchdogDeps): Promise<WatchdogResult> {
  const { store, push, now } = deps;
  const b = deps.budgets ?? DEFAULT_BUDGETS;
  const log = deps.log ?? ((line: string) => console.log(line));
  const ms: Record<string, number> = {};
  const errors: { phase: string; message: string }[] = [];
  const timed = async <T>(phase: string, budget: number, work: () => Promise<T>): Promise<T> => {
    const start = Date.now();
    try {
      return await within(budget, work);
    } finally {
      ms[phase] = (ms[phase] ?? 0) + (Date.now() - start);
    }
  };
  const ping = async (kind: "success" | "fail") => {
    if (!deps.ping) return;
    try {
      await timed("ping", b.pingMs, () => deps.ping!(kind));
    } catch (err) {
      errors.push({ phase: "ping", message: message(err) }); // never fails the request
    }
  };
  const finish = (result: WatchdogResult): WatchdogResult => {
    log(JSON.stringify({ watchdog: result.status, http: result.httpStatus, ms, errors }));
    return result;
  };

  // -- read: one retry, then 503
  const t = now();
  let state: WatchdogState;
  try {
    state = await timed("read", b.readMs, () => store.read());
  } catch (first) {
    errors.push({ phase: "read", message: message(first) });
    await sleep(b.retryDelayMs);
    try {
      state = await timed("read", b.readMs, () => store.read());
    } catch (second) {
      errors.push({ phase: "read", message: message(second) });
      await ping("fail");
      return finish({ status: "error", httpStatus: 503 });
    }
  }

  // -- alert: recorded, never raised
  const lastBeat = state.heartbeatOkAt ? Date.parse(state.heartbeatOkAt) : 0;
  const down = t - lastBeat > DOWN_AFTER_MS;
  let alertState: "ok" | "alerted" = state.alerted ? "alerted" : "ok";
  let pushError: string | null = null;

  const send = async (msg: PushMessage, onSent: () => void) => {
    try {
      const sent = await timed("push", b.pushMs, () => push(state.ownerId, msg));
      if (sent.status === "sent" || sent.status === "partial") onSent();
      else pushError = `push: ${sent.status} (${sent.delivered}/${sent.total} delivered)`;
    } catch (err) {
      pushError = `push: ${message(err)}`;
    }
    if (pushError) errors.push({ phase: "push", message: pushError });
  };

  if (down) {
    let lastAlert = 0;
    try {
      const last = await timed("lookup", b.lookupMs, () => store.lastDownAlertAt());
      lastAlert = last ? Date.parse(last) : 0;
    } catch (err) {
      // Unknown history: push only if not already alerted, so a lookup outage
      // cannot turn into a push every 5 minutes.
      errors.push({ phase: "lookup", message: message(err) });
      lastAlert = state.alerted ? t : 0;
    }
    if (!state.alerted || t - lastAlert > REALERT_AFTER_MS) {
      const minutes = lastBeat ? Math.round((t - lastBeat) / 60000) : null;
      await send(
        {
          ...DOWN,
          body:
            minutes === null
              ? "No heartbeat on record from the Railway worker."
              : `No heartbeat from the Railway worker for ${minutes} minutes.`,
        },
        () => {
          alertState = "alerted";
        },
      );
    }
  } else if (state.alerted) {
    await send(BACK, () => {
      alertState = "ok";
    });
  }

  // -- stamp: always attempted, failure logged
  try {
    await timed("stamp", b.stampMs, () =>
      store.stamp({ alertState, lastError: pushError, at: new Date(t).toISOString() }),
    );
  } catch (err) {
    errors.push({ phase: "stamp", message: message(err) });
  }

  // The route did its job (it could see, and it decided): healthchecks hears "success".
  await ping("success");
  return finish({ status: down ? "down" : "ok", httpStatus: 200 });
}
