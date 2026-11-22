import { requireOwner } from "@/lib/auth";
import { TmdbTokenError, searchAnchors } from "@/lib/tmdb/client";

// GET /api/screening/anchors?q=...: More like…'s suggestions as the owner
// types (owner, 2026-11-21; docs/screening.md §8): films, series and people
// from TMDB, server side (the token never leaves the server). Owner only;
// same origin only. Under 2 characters, an empty list.

const MAX_QUERY = 100;

export async function GET(req: Request) {
  await requireOwner();
  const url = new URL(req.url);
  const origin = req.headers.get("origin");
  if (origin && origin !== url.origin) return Response.json({ error: "Cross-origin search is refused." }, { status: 403 });
  const q = (url.searchParams.get("q") ?? "").trim().slice(0, MAX_QUERY);
  try {
    return Response.json({ results: await searchAnchors(q) }, { headers: { "cache-control": "no-store" } });
  } catch (e) {
    const message = e instanceof TmdbTokenError ? e.message : "TMDB search failed. Try again.";
    console.error(`screening anchors: ${e instanceof Error ? e.message : String(e)}`);
    return Response.json({ error: message }, { status: e instanceof TmdbTokenError ? 500 : 502 });
  }
}
