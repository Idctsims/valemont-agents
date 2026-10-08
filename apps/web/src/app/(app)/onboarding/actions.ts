"use server";

import { headers } from "next/headers";

import { requireOwner } from "@/lib/auth";
import { deviceLabel } from "@/lib/push/device-label";
import { pushToOwner, type PushSummary } from "@/lib/push/send";
import { createClient } from "@/lib/supabase/server";

// Every action re-checks the owner itself: a Server Action is a POST to
// whatever route it lives on, so proxy coverage alone is never trusted.
// Writes go through the owner's session, so db/019's RLS applies as well.

export type DeviceState = { subscribed: boolean; label: string | null };

type SubscriptionInput = {
  endpoint: string;
  p256dh: string;
  auth: string;
  standalone: boolean;
};

function valid(input: SubscriptionInput): boolean {
  return (
    typeof input?.endpoint === "string" &&
    input.endpoint.startsWith("https://") &&
    input.endpoint.length < 2048 &&
    typeof input.p256dh === "string" &&
    input.p256dh.length > 0 &&
    input.p256dh.length < 256 &&
    typeof input.auth === "string" &&
    input.auth.length > 0 &&
    input.auth.length < 256
  );
}

/** Is this browser's subscription saved and active? */
export async function getDeviceState(endpoint: string | null): Promise<DeviceState> {
  await requireOwner();
  if (!endpoint) return { subscribed: false, label: null };
  const supabase = await createClient();
  const { data } = await supabase
    .from("push_subscriptions")
    .select("device_label, active")
    .eq("endpoint", endpoint)
    .maybeSingle();
  return { subscribed: !!data?.active, label: data?.device_label ?? null };
}

/** Save (or revive) this device's subscription. */
export async function saveSubscription(input: SubscriptionInput): Promise<DeviceState> {
  const user = await requireOwner();
  if (!valid(input)) throw new Error("That is not a valid push subscription.");

  const userAgent = (await headers()).get("user-agent") ?? "";
  const label = deviceLabel(userAgent, input.standalone);
  const supabase = await createClient();
  const { error } = await supabase.from("push_subscriptions").upsert(
    {
      owner_id: user.id,
      endpoint: input.endpoint,
      p256dh: input.p256dh,
      auth: input.auth,
      device_label: label,
      user_agent: userAgent.slice(0, 512),
      active: true,
      failed_at: null,
    },
    { onConflict: "endpoint" },
  );
  if (error) throw new Error(`Could not save this device: ${error.message}`);
  return { subscribed: true, label };
}

/** Forget this device. The browser-side unsubscribe happens in the client. */
export async function removeSubscription(endpoint: string): Promise<DeviceState> {
  await requireOwner();
  const supabase = await createClient();
  const { error } = await supabase.from("push_subscriptions").delete().eq("endpoint", endpoint);
  if (error) throw new Error(`Could not remove this device: ${error.message}`);
  return { subscribed: false, label: null };
}

/** The web sender's test: every active device of the owner. */
export async function sendTestPush(): Promise<PushSummary> {
  const user = await requireOwner();
  const supabase = await createClient();
  return pushToOwner(supabase, user.id, {
    kind: "test",
    title: "Valemont test push",
    body: "Sent from the web app. Tap to open onboarding.",
    deepLink: "/onboarding",
    tag: "test-push",
  });
}
