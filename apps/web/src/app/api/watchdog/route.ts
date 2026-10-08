import { createHash, timingSafeEqual } from "node:crypto";

import { createClient } from "@supabase/supabase-js";

import { pushToOwner } from "@/lib/push/send";

// External watchdog. cron-job.org calls this every 5 minutes with
// `Authorization: Bearer <WATCHDOG_TOKEN>`. It lives on Vercel, outside the
// Railway worker, so it still works when the worker itself is dead, which is
// the one failure the worker's own health monitor cannot see.
//
//   heartbeat last_ok_at older than 5 min -> push "worker down"
//     (again at most every 30 min while it stays down)
//   heartbeat fresh again after an alert  -> one push "worker back"
//
// It also stamps its own job_health row ('watchdog', every 300 s), which the
// worker's monitor watches, so a dead cron job is noticed too.
//
// THE ONLY FILE THAT MAY READ SUPABASE_SECRET_KEY (enforced by
// scripts/check-secret-key.mjs in `pnpm lint`). The secret key bypasses RLS;
// it is used here because a cron call has no user session. Responses carry
// {status} and nothing else.

export const dynamic = "force-dynamic";

const DOWN_AFTER_MS = 5 * 60 * 1000;
const REALERT_AFTER_MS = 30 * 60 * 1000;

type Status = "ok" | "down" | "unauthorized" | "error";

function reply(status: Status, code = 200) {
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

  try {
    const now = Date.now();
    const [{ data: settings, error: e1 }, { data: jobs, error: e2 }] = await Promise.all([
      db.from("app_settings").select("owner_id").single(),
      db.from("job_health").select("job, last_ok_at, alert_state").in("job", ["heartbeat", "watchdog"]),
    ]);
    if (e1 || e2 || !settings) throw new Error(e1?.message ?? e2?.message ?? "no app_settings row");

    const heartbeat = jobs?.find((j) => j.job === "heartbeat");
    const self = jobs?.find((j) => j.job === "watchdog");
    const lastBeat = heartbeat?.last_ok_at ? Date.parse(heartbeat.last_ok_at) : 0;
    const down = now - lastBeat > DOWN_AFTER_MS;
    const alerted = self?.alert_state === "alerted";
    let alertState: "ok" | "alerted" = alerted ? "alerted" : "ok";

    if (down) {
      const { data: last } = await db
        .from("notifications")
        .select("created_at")
        .eq("kind", "watchdog_down")
        .order("created_at", { ascending: false })
        .limit(1)
        .maybeSingle();
      const lastAlert = last ? Date.parse(last.created_at) : 0;
      if (!alerted || now - lastAlert > REALERT_AFTER_MS) {
        const minutes = lastBeat ? Math.round((now - lastBeat) / 60000) : null;
        const sent = await pushToOwner(db, settings.owner_id, {
          kind: "watchdog_down",
          title: "Worker down",
          body: minutes === null
            ? "No heartbeat on record from the Railway worker."
            : `No heartbeat from the Railway worker for ${minutes} minutes.`,
          deepLink: "/settings/health",
          tag: "watchdog",
        });
        if (sent.status === "sent" || sent.status === "partial") alertState = "alerted";
      }
    } else if (alerted) {
      const sent = await pushToOwner(db, settings.owner_id, {
        kind: "watchdog_recovered",
        title: "Worker back",
        body: "The Railway worker's heartbeat is current again.",
        deepLink: "/settings/health",
        tag: "watchdog",
      });
      if (sent.status === "sent" || sent.status === "partial") alertState = "ok";
    }

    const stamp = new Date(now).toISOString();
    const { error: e3 } = await db.from("job_health").upsert({
      job: "watchdog",
      expected_interval_s: 300,
      last_started_at: stamp,
      last_ok_at: stamp,
      last_error: null,
      consecutive_failures: 0,
      alert_state: alertState,
      updated_at: stamp,
    });
    if (e3) throw new Error(e3.message);

    return reply(down ? "down" : "ok");
  } catch (err) {
    // Message only: never the key, never a row.
    console.error(`watchdog failed: ${err instanceof Error ? err.message : "unknown error"}`);
    return reply("error", 500);
  }
}
