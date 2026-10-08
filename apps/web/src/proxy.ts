import { NextResponse, type NextRequest } from "next/server";

import { buildCsp, newNonce } from "@/lib/csp";
import { isOwner } from "@/lib/owner";
import { LOGIN_PATH, UNAUTHORIZED, isPublicPath } from "@/lib/routes";
import { updateSession } from "@/lib/supabase/proxy";

export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  const csp = buildCsp(newNonce(), {
    dev: process.env.NODE_ENV === "development",
    https: request.nextUrl.protocol === "https:",
  });
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("Content-Security-Policy", csp);
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
  if (user && !isOwner(user.id)) {
    await session.signOut();
    // Already on that exact URL: render it (cookies now cleared) rather than
    // redirect to itself.
    if (
      pathname === LOGIN_PATH &&
      request.nextUrl.searchParams.get("error") === UNAUTHORIZED
    ) {
      return withCsp(session.response);
    }
    const to = new URL(LOGIN_PATH, request.url);
    to.searchParams.set("error", UNAUTHORIZED);
    return session.redirect(to);
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
