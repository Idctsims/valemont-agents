import type { Metadata } from "next";

import { safeNext } from "@/lib/routes";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const params = await searchParams;

  return (
    // Phone: the wordmark sits high and the form in the lower middle, within
    // thumb reach without hugging the bottom edge. Desktop: centred column.
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col px-safe pt-safe pb-safe">
      <div className="pt-16 sm:pt-24">
        <h1 className="font-display text-display text-text">Valemont</h1>
        <p className="label-mono mt-2 text-text-muted">Command · owner sign-in</p>
      </div>
      <div aria-hidden className="grow" />
      {/* No error from the URL: a message appears only after a rejected
          sign-in, from the form's own action result. */}
      <LoginForm next={safeNext(params.next)} />
      <div aria-hidden className="grow" />
    </main>
  );
}
