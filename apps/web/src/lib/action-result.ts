import "server-only";

import { unstable_rethrow } from "next/navigation";

// What every Server Action returns. An expected failure (bad input, a row
// that is gone, an RLS or CHECK refusal, a refused test marker) comes back
// as { ok: false, error } and is never thrown: in a production build Next
// redacts a thrown error's message, so the page would show "Minified React
// error" (number 441) instead of the reason (2026-10-10, capital.spec).
//
// Inside an action, say no with `throw new Refusal("…")`; `settle` turns it
// into the result. redirect() and notFound() still propagate (they are
// control flow, not failures). Anything else is a bug: logged here in full,
// and the page gets a generic line rather than a leaked internal message.

export type ActionResult<T extends object = Record<never, never>> = ({ ok: true } & T) | { ok: false; error: string };

/** An expected failure, with a message the owner should read. */
export class Refusal extends Error {}

export async function settle<T extends object = Record<never, never>>(
  work: () => Promise<T | void>,
  fallback = "That didn't save. Try again.",
): Promise<ActionResult<T>> {
  try {
    const value = await work();
    return { ok: true, ...(value ?? {}) } as { ok: true } & T;
  } catch (e) {
    unstable_rethrow(e);
    if (e instanceof Refusal) return { ok: false, error: e.message };
    console.error("server action failed", e);
    return { ok: false, error: fallback };
  }
}
