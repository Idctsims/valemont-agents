import "server-only";

import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

import { supabaseEnv } from "@/lib/env";

import { withSkewRetry } from "./skew-retry";

// One client per request: @supabase/ssr hands its no-cache headers to the
// first cookie write only, so a shared client would leak cacheable auth
// responses. Every request it makes retries once on the gateway's clock skew
// (PGRST303, "JWT issued at future"): src/lib/supabase/skew-retry.ts.
export async function createClient() {
  const cookieStore = await cookies();
  const { url, key } = supabaseEnv();

  return createServerClient(url, key, {
    global: { fetch: withSkewRetry(fetch) },
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Called from a Server Component, where cookies are read-only.
          // src/proxy.ts refreshes the session on every request, so a token
          // refreshed here is also written there.
        }
      },
    },
  });
}
