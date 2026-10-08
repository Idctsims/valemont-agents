"use client";

import { useActionState } from "react";

import { login, type LoginState } from "./actions";

const initial: LoginState = { error: null };

export function LoginForm({ next, notice }: { next: string; notice: string | null }) {
  const [state, action, pending] = useActionState(login, initial);
  const message = state.error ?? notice;

  return (
    <form action={action} noValidate>
      <input type="hidden" name="next" value={next} />
      <label>
        <span>Email</span>
        <input name="email" type="email" autoComplete="username" required />
      </label>
      <label>
        <span>Password</span>
        <input name="password" type="password" autoComplete="current-password" required />
      </label>
      <p role="alert" aria-live="polite">
        {message}
      </p>
      <button type="submit" disabled={pending} aria-busy={pending}>
        {pending ? "Signing in" : "Sign in"}
      </button>
    </form>
  );
}
