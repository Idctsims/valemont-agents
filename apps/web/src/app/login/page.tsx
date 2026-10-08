import type { Metadata } from "next";

import { UNAUTHORIZED, safeNext } from "@/lib/routes";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const params = await searchParams;
  const notice = params.error === UNAUTHORIZED ? "Not authorized." : null;

  return (
    // Phone: the wordmark sits high, the form low, where the thumb is.
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col justify-between px-safe pt-safe pb-safe sm:justify-center sm:gap-12">
      <div className="pt-16 sm:pt-0">
        <h1 className="font-display text-display text-text">Valemont</h1>
        <p className="label-mono mt-2 text-text-muted">Command · owner sign-in</p>
      </div>
      <div className="pb-10 sm:pb-0">
        <LoginForm next={safeNext(params.next)} notice={notice} />
      </div>
    </main>
  );
}
