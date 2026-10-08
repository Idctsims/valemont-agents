import { createServerClient } from "@supabase/ssr";
import type { User } from "@supabase/supabase-js";
import { NextResponse, type NextRequest } from "next/server";

import { supabaseEnv } from "@/lib/env";

export type ProxySession = {
  user: User | null;
  /** The pass-through response, carrying any refreshed auth cookies. */
  response: NextResponse;
  /** A redirect that carries the same refreshed (or cleared) auth cookies. */
  redirect: (to: URL) => NextResponse;
  signOut: () => Promise<void>;
};

/**
 * Refresh the Supabase session for this request and verify it with the auth
 * server (getUser, never getSession: a cookie alone proves nothing).
 *
 * `requestHeaders` is the header set forwarded upstream (it carries the CSP
 * nonce). Refreshed cookies are written to it as well as to the response, so
 * Server Components rendering this same request see the new tokens instead of
 * the expired ones.
 */
export async function updateSession(
  request: NextRequest,
  requestHeaders: Headers,
): Promise<ProxySession> {
  const { url, key } = supabaseEnv();
  const pendingHeaders: Record<string, string> = {};

  let response = NextResponse.next({ request: { headers: requestHeaders } });

  const supabase = createServerClient(url, key, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet, headers) {
        for (const { name, value } of cookiesToSet) {
          request.cookies.set(name, value);
        }
        requestHeaders.set("cookie", request.cookies.toString());
        response = NextResponse.next({ request: { headers: requestHeaders } });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
        // Cache-Control: private, no-store, ... — a response that sets auth
        // cookies must never be cached by a CDN.
        Object.assign(pendingHeaders, headers);
        for (const [h, v] of Object.entries(headers)) {
          response.headers.set(h, v);
        }
      },
    },
  });

  const {
    data: { user },
  } = await supabase.auth.getUser();

  return {
    user,
    get response() {
      return response;
    },
    redirect(to: URL) {
      const redirect = NextResponse.redirect(to);
      for (const cookie of response.cookies.getAll()) {
        redirect.cookies.set(cookie);
      }
      for (const [h, v] of Object.entries(pendingHeaders)) {
        redirect.headers.set(h, v);
      }
      return redirect;
    },
    async signOut() {
      // scope 'local' ends this session only; it clears the auth cookies
      // through setAll above.
      await supabase.auth.signOut({ scope: "local" });
    },
  };
}
