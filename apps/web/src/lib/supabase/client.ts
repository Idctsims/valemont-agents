import { createBrowserClient } from "@supabase/ssr";

import { supabaseEnv } from "@/lib/env";

// Browser client, for Realtime subscriptions in later phases. Auth decisions
// are never made with it; they are made server-side with getUser().
export function createClient() {
  const { url, key } = supabaseEnv();
  return createBrowserClient(url, key);
}
