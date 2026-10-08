import "server-only";

import type { SupabaseClient } from "@supabase/supabase-js";
import webpush, { WebPushError } from "web-push";

// The web app's sender: the owner's own session, so every read and write here
// passes through db/019's RLS policy. workers/core/push.py is the other
// sender; both log to `notifications` and send this same payload, which
// src/app/sw.ts renders.

export type PushMessage = {
  kind: string;
  title: string;
  body?: string;
  /** Same-origin path the notification opens. */
  deepLink?: string;
  tag?: string;
};

export type PushSummary = {
  status: "sent" | "partial" | "failed" | "no_devices";
  delivered: number;
  total: number;
};

const TTL_SECONDS = 60 * 60 * 12;

function vapid() {
  const publicKey = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY;
  const privateKey = process.env.VAPID_PRIVATE_KEY;
  const subject = process.env.VAPID_SUBJECT;
  if (!publicKey || !privateKey || !subject) {
    throw new Error(
      "NEXT_PUBLIC_VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY and VAPID_SUBJECT must be set (apps/web/.env.local, or Vercel).",
    );
  }
  return { publicKey, privateKey, subject };
}

/** Host plus the last six characters. Endpoints are capabilities; never log one whole. */
export function shortEndpoint(endpoint: string): string {
  try {
    return `${new URL(endpoint).host}/…${endpoint.slice(-6)}`;
  } catch {
    return "…";
  }
}

export async function pushToOwner(
  supabase: SupabaseClient,
  ownerId: string,
  message: PushMessage,
): Promise<PushSummary> {
  const details = vapid();

  // Owner filter stated explicitly, not left to RLS: the watchdog calls this
  // with the secret-key client, which bypasses RLS.
  const { data: subs, error: readError } = await supabase
    .from("push_subscriptions")
    .select("id, endpoint, p256dh, auth")
    .eq("owner_id", ownerId)
    .eq("active", true);
  if (readError) throw new Error(`Could not read subscriptions: ${readError.message}`);
  if (!subs?.length) return { status: "no_devices", delivered: 0, total: 0 };

  const { data: note, error: noteError } = await supabase
    .from("notifications")
    .insert({
      owner_id: ownerId,
      kind: message.kind,
      title: message.title,
      body: message.body ?? null,
      deep_link: message.deepLink ?? null,
    })
    .select("id")
    .single();
  if (noteError) throw new Error(`Could not log the notification: ${noteError.message}`);

  const payload = JSON.stringify({
    title: message.title,
    body: message.body ?? "",
    url: message.deepLink ?? "/",
    ...(message.tag ? { tag: message.tag } : {}),
  });

  const outcomes = await Promise.all(
    subs.map(async (s) => {
      try {
        await webpush.sendNotification(
          { endpoint: s.endpoint, keys: { p256dh: s.p256dh, auth: s.auth } },
          payload,
          { TTL: TTL_SECONDS, timeout: 10_000, vapidDetails: details },
        );
        await supabase
          .from("push_subscriptions")
          .update({ last_success_at: new Date().toISOString() })
          .eq("id", s.id);
        return { ok: true, label: "" };
      } catch (err) {
        const code = err instanceof WebPushError ? err.statusCode : undefined;
        // 404/410: the push service says this subscription is gone for good.
        const gone = code === 404 || code === 410;
        await supabase
          .from("push_subscriptions")
          .update({ failed_at: new Date().toISOString(), ...(gone ? { active: false } : {}) })
          .eq("id", s.id);
        const label = `${shortEndpoint(s.endpoint)}: ${code ?? "error"}`;
        console.error(`push ${label}${gone ? ", marked inactive" : ""}`);
        return { ok: false, label };
      }
    }),
  );

  const delivered = outcomes.filter((o) => o.ok).length;
  const status = delivered === subs.length ? "sent" : delivered ? "partial" : "failed";
  const errors = outcomes.filter((o) => !o.ok).map((o) => o.label);
  await supabase
    .from("notifications")
    .update({ status, error: errors.join("; ") || null, sent_at: new Date().toISOString() })
    .eq("id", note.id);

  return { status, delivered, total: subs.length };
}
