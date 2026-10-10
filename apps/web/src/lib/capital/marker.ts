import { createHash, timingSafeEqual } from "node:crypto";

// The e2e marker (db/027). A request carrying MARKER_HEADER asks for its
// bankroll entry to be written as is_test, which keeps it out of every
// capital number. The server honours that only when the header matches
// E2E_TEST_MARKER, a server-only variable that exists in apps/web/.env.local
// for the local test build and is never set on Vercel, AND the server is not
// a Vercel deployment at all (VERCEL unset).
//
// A request that carries the header and does NOT verify is refused outright,
// never written as a real entry: an e2e run pointed at the wrong server must
// fail loudly, not quietly add to the owner's real record. Imported only by
// server actions; there is no client path to it.

export const MARKER_HEADER = "x-valemont-e2e";

export type MarkerDecision =
  | { kind: "none" }
  | { kind: "test"; marker: string }
  | { kind: "refused"; reason: string };

const digest = (s: string) => createHash("sha256").update(s).digest();

export function decideMarker(
  header: string | null | undefined,
  env: Readonly<Record<string, string | undefined>>,
): MarkerDecision {
  if (header === null || header === undefined || header === "") return { kind: "none" };
  // Local builds only. Vercel sets VERCEL on every deployment, production
  // AND preview, and previews read the same real database, so any Vercel
  // deployment refuses the marker, whatever VERCEL_ENV says.
  if (env.VERCEL) return { kind: "refused", reason: "a Vercel deployment never accepts the test marker" };
  const expected = env.E2E_TEST_MARKER;
  if (!expected) return { kind: "refused", reason: "this server has no test marker" };
  // Compare digests, so the comparison is constant-time and length-blind.
  if (!timingSafeEqual(digest(header), digest(expected))) return { kind: "refused", reason: "the test marker does not match" };
  return { kind: "test", marker: header };
}
