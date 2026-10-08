"use client";

import { useActionState } from "react";

import { button } from "@/components/ui/button";

import { login, type LoginState } from "./actions";

const initial: LoginState = { error: null };

const field =
  "h-12 w-full rounded-inner border border-border bg-surface px-4 text-base text-text transition-colors placeholder:text-text-muted hover:border-wood focus:border-accent focus:outline-none";

export function LoginForm({ next, notice }: { next: string; notice: string | null }) {
  const [state, action, pending] = useActionState(login, initial);
  const message = state.error ?? notice;

  return (
    <form action={action} className="flex flex-col gap-4">
      <input type="hidden" name="next" value={next} />
      <label className="flex flex-col gap-2">
        <span className="label-mono text-text-muted">Email</span>
        <input
          className={field}
          name="email"
          type="email"
          autoComplete="username"
          inputMode="email"
          autoCapitalize="none"
          spellCheck={false}
          required
        />
      </label>
      <label className="flex flex-col gap-2">
        <span className="label-mono text-text-muted">Password</span>
        <input
          className={field}
          name="password"
          type="password"
          autoComplete="current-password"
          required
        />
      </label>

      <p role="alert" aria-live="polite" className="min-h-6 text-sm text-danger">
        {message}
      </p>

      <button
        type="submit"
        disabled={pending}
        aria-busy={pending}
        className={`${button.primary} h-12 w-full text-base`}
      >
        {pending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
