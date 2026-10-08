// Route policy, shared by src/proxy.ts and the login flow.

export const LOGIN_PATH = "/login";

/** Reachable with no session. Nothing else is. */
export const PUBLIC_PATHS = ["/privacy", "/offline"] as const;

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

export const UNAUTHORIZED = "unauthorized";

/**
 * Where to send the owner after login. Only same-origin absolute paths are
 * accepted, so `?next=` cannot be used as an open redirect.
 */
export function safeNext(next: unknown): string {
  if (typeof next !== "string") return "/";
  if (!next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return "/";
  }
  if (next === LOGIN_PATH || next.startsWith(`${LOGIN_PATH}?`)) return "/";
  return next;
}
