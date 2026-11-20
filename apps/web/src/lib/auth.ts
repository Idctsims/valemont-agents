import "server-only";

import { redirect } from "next/navigation";
import { cache } from "react";

import { isOwner } from "@/lib/owner";
import { LOGIN_PATH } from "@/lib/routes";
import { createClient } from "@/lib/supabase/server";

/** The verified owner: callers need only the id. */
export type Owner = { id: string };

/**
 * Server-side owner check for layouts, pages and Server Actions. The proxy
 * already gates every route with getUser() (a round trip to the auth server,
 * which also catches a session revoked elsewhere); this is the second lock,
 * because a Server Action is a POST to whatever route it lives on and a
 * matcher change can silently drop proxy coverage.
 *
 * It uses getClaims(): the access token's signature and expiry are verified
 * against the project's JWKS (cached), with no auth-server round trip when the
 * project signs with an asymmetric key, and the same request as getUser()
 * otherwise. A forged or expired token is refused either way. Why: every page
 * load paid two auth round trips in a row before its data (web polish audit,
 * 2026-11-20).
 *
 * Deduplicated per request with React cache().
 */
export const requireOwner = cache(async (): Promise<Owner> => {
  const supabase = await createClient();
  const { data, error } = await supabase.auth.getClaims();
  let id = !error && typeof data?.claims?.sub === "string" ? data.claims.sub : null;
  if (!id) {
    // Never a weaker answer than before: on any getClaims failure (a JWKS
    // fetch error, say) ask the auth server, as this check always did. A
    // wrongful redirect here would loop: the proxy sends a valid session on
    // /login straight back.
    const { data: fallback } = await supabase.auth.getUser();
    id = fallback.user?.id ?? null;
  }

  if (!id) redirect(LOGIN_PATH);
  // A Server Component cannot clear cookies; the proxy signs a non-owner
  // session out when it reaches /login.
  if (!isOwner(id)) redirect(LOGIN_PATH);
  return { id };
});
