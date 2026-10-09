import { createHash, timingSafeEqual } from "node:crypto";

import { createClient } from "@supabase/supabase-js";

import { pushToOwner } from "@/lib/push/send";
import { runWatchdog, type WatchdogStore } from "@/lib/watchdog/run";

// External watchdog. cron-job.org calls this every 5 minutes with
// `Authorization: Bearer <WATCHDOG_TOKEN>`. It lives on Vercel, outside the
// Railway worker, so it still works when the worker itself is dead. The
// decision logic, fault isolation and time budgets are in
// src/lib/watchdog/run.ts; this file authenticates the call and builds the
// real store and pinger.
//
// Responses: 200 ok | 200 down (even if the push failed: it is recorded in
// job_health.last_error and retried next call) | 503 error (could not read
// the heartbeat at all) | 401 unauthorized | 500 misconfigured.
//
// THE ONLY FILE THAT MAY READ SUPABASE_SECRET_KEY (enforced by
// scripts/check-secret-key.mjs in `pnpm lint`). The secret key bypasses RLS;
// it is used here because a cron call has no user session. Responses carry
// {status} and nothing else.

export const dynamic = "force-dynamic";
// The phase budgets in run.ts cap a run near 25 s; this is the hard ceiling.
export const maxDuration = 30;

function reply(status: string, code: number) {
  return Response.json({ status }, { status: code, headers: { "Cache-Control": "no-store" } });
}

/** Constant-time: compares fixed-length digests, so length leaks nothing either. */
function tokenMatches(header: string | null): boolean {
  const expected = process.env.WATCHDOG_TOKEN;
  if (!expected || !header?.startsWith("Bearer ")) return false;
  const a = createHash("sha256").update(header.slice("Bearer ".length)).digest();
  const b = createHash("sha256").update(expected).digest();
  return timingSafeEqual(a, b);
}

export async function GET(request: Request) {
  if (!tokenMatches(request.headers.get("authorization"))) return reply("unauthorized", 401);

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const secret = process.env.SUPABASE_SECRET_KEY;
  if (!url || !secret) {
    console.error("watchdog: NEXT_PUBLIC_SUPABASE_URL or SUPABASE_SECRET_KEY is not set");
    return reply("error", 500);
  }
  const db = createClient(url, secret, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  const store: WatchdogStore = {
    async read() {
      const [{ data: settings, error: e1 }, { data: jobs, error: e2 }] = await Promise.all([
        db.from("app_settings").select("owner_id").single(),
        db.from("job_health").select("job, last_ok_at, alert_state").in("job", ["heartbeat", "watchdog"]),
      ]);
      if (e1 || e2 || !settings) throw new Error(e1?.message ?? e2?.message ?? "no app_settings row");
      return {
        ownerId: settings.owner_id,
        heartbeatOkAt: jobs?.find((j) => j.job === "heartbeat")?.last_ok_at ?? null,
        alerted: jobs?.find((j) => j.job === "watchdog")?.alert_state === "alerted",
      };
    },
    async lastDownAlertAt() {
      const { data } = await db
        .from("notifications")
        .select("created_at")
        .eq("kind", "watchdog_down")
        .order("created_at", { ascending: false })
        .limit(1)
        .maybeSingle();
      return data?.created_at ?? null;
    },
    async stamp({ alertState, lastError, at }) {
      const { error } = await db.from("job_health").upsert({
        job: "watchdog",
        expected_interval_s: 300,
        last_started_at: at,
        last_ok_at: at,
        last_error: lastError,
        consecutive_failures: 0,
        alert_state: alertState,
        updated_at: at,
      });
      if (error) throw new Error(error.message);
    },
  };

  // healthchecks.io dead-man's switch (server-only, Sensitive). Pinged after
  // every run that could see the worker; "/fail" when it could not. If the
  // pings stop (cron dead, token broken, route or Vercel down), healthchecks
  // alerts on its own, outside Railway, Vercel and cron-job.org alike.
  const pingUrl = process.env.WATCHDOG_PING_URL;
  const ping = pingUrl
    ? async (kind: "success" | "fail") => {
        const res = await fetch(kind === "fail" ? `${pingUrl}/fail` : pingUrl, { method: "POST" });
        if (!res.ok) throw new Error(`healthchecks answered ${res.status}`);
      }
    : undefined;

  const result = await runWatchdog({
    store,
    push: (ownerId, message) => pushToOwner(db, ownerId, message),
    now: Date.now,
    ping,
  });
  return reply(result.status, result.httpStatus);
}
