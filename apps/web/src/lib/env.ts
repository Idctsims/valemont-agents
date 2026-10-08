// Public Supabase config. Both values ship to the browser by design: the URL
// and the publishable key are safe to expose; RLS is what protects data.
//
// NEXT_PUBLIC_* must be read as literal property accesses so Next can inline
// them into the client bundle.
export function supabaseEnv(): { url: string; key: string } {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY must be set (apps/web/.env.local, or the Vercel project's env vars).",
    );
  }
  return { url, key };
}
