import "server-only";

import type { User } from "@supabase/supabase-js";
import { redirect } from "next/navigation";
import { cache } from "react";

import { isOwner } from "@/lib/owner";
import { LOGIN_PATH } from "@/lib/routes";
import { createClient } from "@/lib/supabase/server";

/**
 * Server-side owner check for layouts, pages and Server Actions. The proxy
 * already gates every route; this is the second lock, because a Server
 * Action is a POST to whatever route it lives on and a matcher change can
 * silently drop proxy coverage.
 *
 * Deduplicated per request with React cache(), so one render costs one
 * getUser() round trip however many components call it.
 */
export const requireOwner = cache(async (): Promise<User> => {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) redirect(LOGIN_PATH);
  // A Server Component cannot clear cookies; the proxy signs a non-owner
  // session out when it reaches /login.
  if (!isOwner(user.id)) redirect(LOGIN_PATH);
  return user;
});
