import type { NextRequest } from "next/server";

import { updateSession } from "@/lib/supabase/proxy";

export async function proxy(request: NextRequest) {
  const requestHeaders = new Headers(request.headers);
  const session = await updateSession(request, requestHeaders);
  return session.response;
}

export const config = {
  matcher: [
    // Everything except Next's static output and image optimizer, and files
    // with an extension (public/ assets, favicon, robots.txt).
    "/((?!_next/static|_next/image|.*\\.[a-zA-Z0-9]+$).*)",
  ],
};
