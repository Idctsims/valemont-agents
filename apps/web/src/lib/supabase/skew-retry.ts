// Supabase's API gateway clock runs ahead of PostgREST's, so now and then a
// request is rejected with 401 PGRST303 "JWT issued at future" (CLAUDE.md,
// Known Supabase issue). The watchdog already retries its read once; this
// does the same for every request the web's server-side client makes.
//
// Writes are retried too: PostgREST rejects the token before it touches the
// database, so a skew-rejected request did nothing and a retry cannot write
// twice. Exactly one retry, after SKEW_RETRY_MS, one log line; any other
// failure (another 401, a 4xx, a 5xx, a network error) is returned or thrown
// untouched. Pure, so e2e/supabase-unit.spec.ts tests it without a server.

export const SKEW_RETRY_MS = 250;

/** A 401 whose body is PostgREST's "JWT issued at future" (PGRST303). */
export function isSkewRejection(status: number, body: string): boolean {
  if (status !== 401) return false;
  return /"code"\s*:\s*"PGRST303"/.test(body) || /JWT issued at future/i.test(body);
}

type Fetch = typeof fetch;

export function withSkewRetry(
  base: Fetch,
  opts: { delayMs?: number; sleep?: (ms: number) => Promise<void>; log?: (line: string) => void } = {},
): Fetch {
  const delayMs = opts.delayMs ?? SKEW_RETRY_MS;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const log = opts.log ?? ((line: string) => console.warn(line));

  return async (input, init) => {
    // A Request's body can be read once: keep a copy for the retry.
    const again = input instanceof Request ? input.clone() : input;
    const first = await base(input, init);
    if (first.status !== 401) return first;
    // A one-shot stream body cannot be sent twice; never risk a partial resend.
    if (init?.body instanceof ReadableStream) return first;
    let body = "";
    try {
      body = await first.clone().text();
    } catch {
      return first;
    }
    if (!isSkewRejection(first.status, body)) return first;

    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    const url = input instanceof Request ? input.url : String(input);
    let path = url;
    try {
      path = new URL(url).pathname; // never the query: it can carry filter values
    } catch {}
    log(`supabase: JWT issued at future (PGRST303) on ${method} ${path}; retrying once in ${delayMs} ms`);
    await sleep(delayMs);
    return base(again, init);
  };
}
