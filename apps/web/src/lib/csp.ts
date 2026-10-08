// Content-Security-Policy, built per request in src/proxy.ts.
//
// Scripts: nonce + 'strict-dynamic'. Next reads the nonce from the request's
// CSP header and stamps it on its own scripts, so no inline script runs
// without it. Styles allow 'unsafe-inline' because React style attributes
// (progress widths) cannot carry a nonce; style injection is the low-risk
// half of XSS, and scripts stay locked.
export function buildCsp(nonce: string, opts: { dev: boolean; https: boolean }): string {
  const supabase = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const connect = ["'self'"];
  if (supabase) {
    const origin = new URL(supabase).origin;
    connect.push(origin, origin.replace(/^http/, "ws"));
  }
  if (opts.dev) connect.push("ws:"); // dev-server hot reload

  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${opts.dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    `connect-src ${connect.join(" ")}`,
    "manifest-src 'self'",
    "worker-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  if (opts.https) directives.push("upgrade-insecure-requests");
  return directives.join("; ");
}

export function newNonce(): string {
  return Buffer.from(crypto.randomUUID()).toString("base64");
}
