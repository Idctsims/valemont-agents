import type { Metadata } from "next";

import { UNAUTHORIZED, safeNext } from "@/lib/routes";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const params = await searchParams;
  const notice = params.error === UNAUTHORIZED ? "Not authorized." : null;

  return (
    <main>
      <h1>Valemont Command</h1>
      <LoginForm next={safeNext(params.next)} notice={notice} />
    </main>
  );
}
