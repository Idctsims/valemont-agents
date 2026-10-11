// embed — gte-small sentence embeddings for the feed (Chat 3 Phase 1).
//
// POST {"texts": string[]} (1–8 texts, each at most 2,000 characters)
//   -> 200 {"embeddings": number[][]}  (384 dims, mean-pooled, normalized)
//   -> 413 above 8 texts
//
// Auth: `Authorization: Bearer <EMBED_TOKEN>`, compared in constant time.
// Deployed with --no-verify-jwt: this token replaces Supabase's JWT check,
// so no Supabase key ever reaches the worker for this call.
//
// The only caller is workers/core/embed.py. Based on Supabase's gte-small
// example (Supabase.ai.Session, mean_pool + normalize). Texts are embedded
// one at a time inside the request.
//
// The cap is 8 because of the Free plan's per-request compute limit, measured
// on 2026-10-11 with real titles and teasers sent back to back:
//   batch 8:  50/50 ok, worst 1,995 ms
//   batch 12:  5/10 ok, every failure 546 WORKER_RESOURCE_LIMIT
//   batch 16:  3/10 ok, 546 (and one 503)
// A bigger request gets a clean 413 instead of a resource-limit failure.

const MAX_TEXTS = 8;
const MAX_CHARS = 2000;

// @ts-ignore Supabase global, provided by the Edge Runtime.
const model = new Supabase.ai.Session("gte-small");

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

async function digest(s: string): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
}

// Equal-length digests, compared without an early exit.
async function sameToken(given: string, expected: string): Promise<boolean> {
  const [a, b] = await Promise.all([digest(given), digest(expected)]);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return json(405, { error: "POST only" });

  const expected = Deno.env.get("EMBED_TOKEN") ?? "";
  if (expected.length < 32) return json(500, { error: "EMBED_TOKEN is not configured" });
  const header = req.headers.get("authorization") ?? "";
  const given = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (!(await sameToken(given, expected))) return json(401, { error: "unauthorized" });

  let texts: unknown;
  try {
    texts = (await req.json())?.texts;
  } catch {
    return json(400, { error: "body must be JSON" });
  }
  if (!Array.isArray(texts) || texts.length === 0) {
    return json(400, { error: "texts must be a non-empty array" });
  }
  if (texts.length > MAX_TEXTS) {
    return json(413, { error: `at most ${MAX_TEXTS} texts per request` });
  }
  if (!texts.every((t) => typeof t === "string" && t.trim().length > 0 && t.length <= MAX_CHARS)) {
    return json(400, { error: `each text must be a non-empty string of at most ${MAX_CHARS} characters` });
  }

  const embeddings: number[][] = [];
  for (const t of texts as string[]) {
    const e = await model.run(t, { mean_pool: true, normalize: true });
    embeddings.push(Array.from(e as ArrayLike<number>));
  }
  return json(200, { embeddings });
});
