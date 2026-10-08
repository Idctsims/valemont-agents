import { NextResponse, type NextRequest } from "next/server";

import { buildCsp, newNonce } from "@/lib/csp";
import { isOwner } from "@/lib/owner";
import { LOGIN_PATH, isPublicPath } from "@/lib/routes";
import { updateSession } from "@/lib/supabase/proxy";

export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  const nonce = newNonce();
  const csp = buildCsp(nonce, {
    dev: process.env.NODE_ENV === "development",
    https: request.nextUrl.protocol === "https:",
  });
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("Content-Security-Policy", csp);
  // For the root layout's one inline script (boot splash gate).
  requestHeaders.set("x-nonce", nonce);
  const withCsp = (res: NextResponse) => {
    res.headers.set("Content-Security-Policy", csp);
    return res;
  };

  // Public pages never touch Supabase, so /privacy stays up even if auth is
  // misconfigured or down.
  if (isPublicPath(pathname)) {
    return withCsp(NextResponse.next({ request: { headers: requestHeaders } }));
  }

  const session = await updateSession(request, requestHeaders);
  const { user } = session;

  // A valid session that is not the owner's is ended, not merely bounced.
  // Quietly: "Not authorized." is shown only by a rejected sign-in attempt
  // (login/actions.ts), never on a page load.
  if (user && !isOwner(user.id)) {
    await session.signOut();
    if (pathname === LOGIN_PATH) return withCsp(session.response);
    return session.redirect(new URL(LOGIN_PATH, request.url));
  }

  if (pathname === LOGIN_PATH) {
    return user ? session.redirect(new URL("/", request.url)) : withCsp(session.response);
  }

  if (!user) {
    const to = new URL(LOGIN_PATH, request.url);
    if (pathname !== "/") to.searchParams.set("next", pathname + search);
    return session.redirect(to);
  }

  return withCsp(session.response);
}

export const config = {
  matcher: [
    // Everything except Next's static output and image optimizer, and files
    // with an extension (public/ assets, favicon, robots.txt).
    "/((?!_next/static|_next/image|.*\\.[a-zA-Z0-9]+$).*)",
  ],
};
