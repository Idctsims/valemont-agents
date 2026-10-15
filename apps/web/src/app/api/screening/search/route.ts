import { requireOwner } from "@/lib/auth";
import { TmdbTokenError, searchTitles } from "@/lib/tmdb/client";

// GET /api/screening/search?q=...: TMDB movies and TV for the Screening Room's
// search box (db/051), server side so the TMDB token never leaves the server
// (it is read only in src/lib/tmdb/client.ts; pnpm check:tmdb-token).
// Owner only; same origin only. 200 with {results: [{tmdbId, kind, name,
// year, posterPath}]}; a query under 2 characters is an empty list. A missing
// or rejected token is a 500 that says so (never the token).

const MAX_QUERY = 100;

export async function GET(req: Request) {
  await requireOwner();
  const url = new URL(req.url);
  const origin = req.headers.get("origin");
  if (origin && origin !== url.origin) return Response.json({ error: "Cross-origin search is refused." }, { status: 403 });
  const q = (url.searchParams.get("q") ?? "").trim().slice(0, MAX_QUERY);
  try {
    return Response.json({ results: await searchTitles(q) }, { headers: { "cache-control": "no-store" } });
  } catch (e) {
    const message = e instanceof TmdbTokenError ? e.message : "TMDB search failed. Try again.";
    console.error(`screening search: ${e instanceof Error ? e.message : String(e)}`);
    return Response.json({ error: message }, { status: e instanceof TmdbTokenError ? 500 : 502 });
  }
}
