// Edge of the trust boundary: the one auth.users id allowed in.
//
// Deliberately not `server-only`: src/proxy.ts imports it too. It reads a
// non-NEXT_PUBLIC_ variable, so Next never inlines it into a client bundle;
// a client import would get `undefined` and fail closed.
export function ownerUserId(): string {
  const id = process.env.OWNER_USER_ID;
  if (!id) {
    // Fail loudly and closed. With no owner configured, nobody gets in.
    throw new Error(
      "OWNER_USER_ID is not set (apps/web/.env.local, or the Vercel project's env vars). Refusing every session.",
    );
  }
  return id;
}

export function isOwner(userId: string | undefined | null): boolean {
  return !!userId && userId === ownerUserId();
}
